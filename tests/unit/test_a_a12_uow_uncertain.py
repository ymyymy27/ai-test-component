import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.file_store import records as records_module
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork


def _stage(
    unit: FileUnitOfWork, kind: str, record_id: str, project: str = "p", rev=None
) -> None:
    unit.stage_record(
        aggregate_kind=kind,
        record_id=record_id,
        expected_revision=rev,
        payload={"project_id": project, "v": record_id},
    )


def _begin(
    unit: FileUnitOfWork, request_id: str = "req-a", project: str = "p", intent="i"
) -> None:
    unit.begin(request_id=request_id, project_id=project, intent_id=intent)


def _break_projections() -> None:
    def _fail_publish(  # noqa: ARG001 - 替身必须保持绑定方法签名
        self,
        new_rows,
        *,
        commit_sequence,
        all_rows=None,
    ):
        raise RuntimeError("projection disk failure")

    FileQueryIndex.publish = _fail_publish  # type: ignore[assignment]


def test_failed_commit_blocks_lockless_retry_and_staging(tmp_path: Path) -> None:
    journal = FileEventJournal(tmp_path, instance_id="core")
    unit = FileUnitOfWork(tmp_path, journal=journal)
    _begin(unit)
    _stage(unit, "case", "c1")

    original_publish = FileQueryIndex.publish
    _break_projections()
    try:
        with pytest.raises(RuntimeError, match="projection disk failure"):
            unit.commit()
    finally:
        FileQueryIndex.publish = original_publish  # type: ignore[assignment]

    # 锁已释放：另一个 UOW 可以立即准入，不会被失败实例长期占用。
    other = FileUnitOfWork(tmp_path, journal=journal)
    _begin(other, "req-b", intent="i-b")
    _stage(other, "case", "c2")
    committed_b = other.commit()
    assert committed_b["state"] == "committed"

    # 失败实例进入“仅核实/回滚”状态：禁止无锁重试与继续暂存/开新事务。
    with pytest.raises(RuntimeError, match="unknown"):
        unit.commit()
    with pytest.raises(RuntimeError, match="unknown"):
        unit.stage_record(
            aggregate_kind="case",
            record_id="c3",
            expected_revision=1,
            payload={"project_id": "p"},
        )
    with pytest.raises(RuntimeError, match="unknown"):
        unit.begin(request_id="req-c", project_id="p")
    assert unit.recover(unit.workspace.workspace_id)["state"] == "uncertain"


def test_failed_commit_rollback_reports_committed_without_losing_records(
    tmp_path: Path,
) -> None:
    journal = FileEventJournal(tmp_path, instance_id="core")
    unit = FileUnitOfWork(tmp_path, journal=journal)
    _begin(unit)
    _stage(unit, "case", "c1")

    original_publish = FileQueryIndex.publish
    _break_projections()
    try:
        with pytest.raises(RuntimeError):
            unit.commit()
    finally:
        FileQueryIndex.publish = original_publish  # type: ignore[assignment]

    # 交错 UOW 成功提交 c2（权威提交序号 2）。
    other = FileUnitOfWork(tmp_path, journal=journal)
    _begin(other, "req-b", intent="i-b")
    _stage(other, "case", "c2")
    other.commit()

    # 失败实例 rollback 必须重新持锁核实权威边界：a 实际已发布，
    # 如实报 committed，且不能覆盖/丢失交错提交的 c2。
    resolved = unit.rollback()
    assert resolved["state"] == "committed"
    created_keys = {
        (item["aggregate_kind"], item["record_id"], item["revision"])
        for item in resolved["created"]
    }
    assert ("case", "c1", 1) in created_keys

    fresh = FileUnitOfWork(tmp_path, journal=journal)
    assert fresh.repo.current_revision("case", "c1") == 1
    assert fresh.repo.current_revision("case", "c2") == 1
    assert fresh.repo.current_commit_sequence() == 2


def test_failed_commit_before_publish_resolves_as_rolled_back(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    _begin(unit)
    _stage(unit, "case", "c1")

    original = records_module.FileRecordRepository._save

    def boom(self, data):  # type: ignore[no-untyped-def]
        raise OSError("records.json unwritable")

    records_module.FileRecordRepository._save = boom  # type: ignore[assignment]
    try:
        with pytest.raises(OSError):
            unit.commit()
    finally:
        records_module.FileRecordRepository._save = original  # type: ignore[assignment]

    resolved = unit.rollback()
    assert resolved["state"] == "rolled_back"

    # 核实未发布后实例可重新准入，新事务正常提交。
    _begin(unit, "req-a2", intent="intent-2")
    unit.stage_record(
        aggregate_kind="case",
        record_id="c1",
        expected_revision=None,
        payload={"project_id": "p", "v": 9},
    )
    result = unit.commit()
    assert result["state"] == "committed"
    assert unit.repo.current_revision("case", "c1") == 1


def test_commit_without_lock_is_rejected(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    unit.open("p")
    unit.pending.append(("case", "c1", 0, {"project_id": "p"}))
    with pytest.raises(RuntimeError, match="writer lock"):
        unit.commit()
