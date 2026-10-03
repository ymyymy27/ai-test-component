import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork


def _commit(
    tmp_path: Path,
    *,
    request_id: str,
    project: str,
    kind: str = "case",
    record_id: str = "case-1",
    expected_revision=None,
    payload_extra: dict | None = None,
):
    unit = FileUnitOfWork(tmp_path)
    unit.begin(request_id=request_id, project_id=project, intent_id=request_id + "-i")
    payload: dict[str, object] = {"project_id": project}
    if payload_extra:
        payload.update(payload_extra)
    unit.stage_record(
        aggregate_kind=kind,
        record_id=record_id,
        expected_revision=expected_revision,
        payload=payload,
    )
    return unit, unit.commit()


def test_same_project_legitimate_revision_accepted(tmp_path: Path) -> None:
    _, first = _commit(tmp_path, request_id="r1", project="project-a")
    assert first["commit_sequence"] == 1
    _, second = _commit(
        tmp_path,
        request_id="r2",
        project="project-a",
        expected_revision=1,
        payload_extra={"v": 2},
    )
    assert second["commit_sequence"] == 2
    unit = FileUnitOfWork(tmp_path)
    assert unit.repo.current_revision("case", "case-1") == 2


def test_cross_project_revision_is_rejected(tmp_path: Path) -> None:
    _commit(tmp_path, request_id="r1", project="project-a")

    unit = FileUnitOfWork(tmp_path)
    unit.begin(request_id="r2", project_id="project-b", intent_id="i-b")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=1,
        payload={"project_id": "project-b"},
    )
    with pytest.raises(ValueError, match="cross-project ownership"):
        unit.commit()

    # 被拒事务回滚后，原项目记录完好，提交序号没有被污染。
    unit.rollback()
    fresh = FileUnitOfWork(tmp_path)
    assert fresh.repo.current_revision("case", "case-1") == 1
    assert fresh.repo.current_commit_sequence() == 1


def test_same_record_id_revision_from_other_project_rejected(tmp_path: Path) -> None:
    # 同名记录在 project-a 首修订后，project-b 借 expected_revision=1
    # 追加修订必须被归属校验拒绝（探针 A-OWNERSHIP-01 的攻击形态）。
    _commit(
        tmp_path,
        request_id="a1",
        project="project-a",
        kind="plan",
        record_id="plan-x",
    )
    with pytest.raises(ValueError, match="cross-project ownership"):
        _commit(
            tmp_path,
            request_id="b1",
            project="project-b",
            kind="plan",
            record_id="plan-x",
            expected_revision=1,
        )


def test_payload_project_mismatch_within_transaction_rejected(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    unit.begin(request_id="r1", project_id="project-a", intent_id="i1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-9",
        expected_revision=None,
        payload={"project_id": "project-b"},
    )
    with pytest.raises(ValueError, match="cross-project ownership"):
        unit.commit()


def test_legacy_append_paths_enforce_ownership(tmp_path: Path) -> None:
    repo = FileUnitOfWork(tmp_path).repo
    repo.append("case", "c1", 0, {"project_id": "project-a", "n": 1})
    with pytest.raises(ValueError, match="cross-project ownership"):
        repo.append(
            "case", "c1", 1, {"project_id": "project-b", "n": 2}
        )
    with pytest.raises(ValueError, match="cross-project ownership"):
        repo.append_batch(
            [
                (
                    "case",
                    "c1",
                    1,
                    {"project_id": "project-b", "n": 2},
                )
            ]
        )
    assert repo.current_revision("case", "c1") == 1
