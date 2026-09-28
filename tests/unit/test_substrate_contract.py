"""薄底座协议的九条不变量（Sprint 2／5／6 共用）。

依据：`docs/文档-feix-a/B包/11-薄底座与prepare_run编排设计说明.md` 第 3.4 节。
测试对象是内存实现（`tests/support/memory_substrate.py`），不是真实存储。
"""

import pytest

from aitest.application.planning.preparation import (
    InputRevisions,
    PreparationRecord,
    PreparationRequest,
)
from aitest.application.planning.substrate import (
    ConcurrentEditError,
    PreparationConflictError,
    RecordQuery,
)
from tests.support.memory_substrate import MemoryReader, MemoryStore, MemoryUnitOfWork


def _revisions(**overrides: int) -> InputRevisions:
    base = {
        "project_revision": 1,
        "binding_revision": 1,
        "snapshot_revision": 1,
        "environment_revision": 1,
        "plan_revision": 1,
        "rules_revision": 1,
        "template_revision": 1,
        "scope_revision": 1,
    }
    base.update(overrides)
    return InputRevisions(**base)


def _preparation(
    *, prepare_request_id: str = "req-1", digest: str = "sha256:a"
) -> PreparationRecord:
    return PreparationRecord(
        request=PreparationRequest(
            project_id="p1",
            client_id="c1",
            prepare_request_id=prepare_request_id,
            payload_hash=digest,
            input_revisions=_revisions(),
        ),
        intent_id=f"intent:{prepare_request_id}",
        created_at_commit="commit-1",
    )


def _pair() -> tuple[MemoryUnitOfWork, MemoryReader, MemoryStore]:
    store = MemoryStore()
    return MemoryUnitOfWork(store), MemoryReader(store), store


# ------------------------------------------------------- 不变量 1：事务边界


def test_staging_before_open_is_rejected() -> None:
    unit_of_work, _, _ = _pair()
    with pytest.raises(ValueError, match="no open transaction"):
        unit_of_work.stage_record(
            aggregate_kind="project",
            record_id="p1",
            expected_revision=None,
            payload={"project_id": "p1"},
        )


def test_commit_before_open_is_rejected() -> None:
    unit_of_work, _, _ = _pair()
    with pytest.raises(ValueError, match="no open transaction"):
        unit_of_work.commit()


def test_open_requires_a_project() -> None:
    unit_of_work, _, _ = _pair()
    with pytest.raises(ValueError, match="project_id"):
        unit_of_work.open("  ")


# ------------------------------------------------------- 不变量 2、3：修订


def test_first_write_creates_revision_one() -> None:
    unit_of_work, _, _ = _pair()
    unit_of_work.open("p1")
    staged = unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    assert staged.revision == 1
    assert unit_of_work.commit().revision_of("project", "p1").revision == 1


def test_expected_revision_must_match_current() -> None:
    unit_of_work, _, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    unit_of_work.commit()

    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=1, payload={}
    )
    assert unit_of_work.commit().revision_of("project", "p1").revision == 2


def test_stale_revision_is_rejected_with_the_current_revision() -> None:
    unit_of_work, _, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    unit_of_work.commit()

    unit_of_work.open("p1")
    with pytest.raises(ConcurrentEditError) as error:
        unit_of_work.stage_record(
            aggregate_kind="project", record_id="p1", expected_revision=5, payload={}
        )
    assert error.value.current_revision == 1
    assert error.value.expected_revision == 5


def test_creating_over_an_existing_record_is_a_stale_revision() -> None:
    """`expected_revision=None` 是"新建"；已有记录时必须报冲突，不能静默覆盖。"""
    unit_of_work, _, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    unit_of_work.commit()

    unit_of_work.open("p1")
    with pytest.raises(ConcurrentEditError, match="expected revision None"):
        unit_of_work.stage_record(
            aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
        )


# ------------------------------------------------------- 不变量 4：重复暂存


def test_staging_the_same_record_twice_in_one_transaction_is_rejected() -> None:
    unit_of_work, _, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    with pytest.raises(ValueError, match="staged twice"):
        unit_of_work.stage_record(
            aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
        )


# ------------------------------------------------------- 不变量 5：准备记录


def test_preparation_is_registered_once_and_reused_for_the_same_digest() -> None:
    unit_of_work, reader, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_preparation(
        record=_preparation(), payload={"prepare_request_id": "req-1"}
    )
    unit_of_work.commit()

    stored = reader.find_preparation(
        project_id="p1", client_id="c1", prepare_request_id="req-1"
    )
    assert stored is not None
    assert stored.intent_id == "intent:req-1"
    assert (
        reader.find_preparation_by_intent(intent_id="intent:req-1") is not None
    )

    # 同键同摘要：复用原修订，不新建。
    unit_of_work.open("p1")
    reused = unit_of_work.stage_preparation(
        record=_preparation(), payload={"prepare_request_id": "req-1"}
    )
    assert reused.revision == 1


def test_same_key_with_a_different_digest_conflicts() -> None:
    unit_of_work, _, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_preparation(
        record=_preparation(digest="sha256:a"), payload={"prepare_request_id": "req-1"}
    )
    unit_of_work.commit()

    unit_of_work.open("p1")
    with pytest.raises(PreparationConflictError) as error:
        unit_of_work.stage_preparation(
            record=_preparation(digest="sha256:b"),
            payload={"prepare_request_id": "req-1"},
        )
    assert error.value.existing_intent_id == "intent:req-1"
    assert error.value.existing_payload_hash == "sha256:a"


def test_conflicting_preparation_does_not_overwrite() -> None:
    unit_of_work, reader, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_preparation(
        record=_preparation(digest="sha256:a"), payload={"prepare_request_id": "req-1"}
    )
    unit_of_work.commit()

    unit_of_work.open("p1")
    with pytest.raises(PreparationConflictError):
        unit_of_work.stage_preparation(
            record=_preparation(digest="sha256:b"),
            payload={"prepare_request_id": "req-1"},
        )

    stored = reader.find_preparation(
        project_id="p1", client_id="c1", prepare_request_id="req-1"
    )
    assert stored is not None
    assert stored.request.payload_hash == "sha256:a"


def test_identical_preparation_under_a_different_request_id_is_a_new_record() -> None:
    unit_of_work, reader, _ = _pair()
    for request_id in ("req-1", "req-2"):
        unit_of_work.open("p1")
        unit_of_work.stage_preparation(
            record=_preparation(prepare_request_id=request_id),
            payload={"prepare_request_id": request_id},
        )
        unit_of_work.commit()

    assert (
        reader.find_preparation(
            project_id="p1", client_id="c1", prepare_request_id="req-2"
        )
        is not None
    )


# ------------------------------------------------------- 不变量 6：提交序号


def test_commit_seq_starts_at_commit_zero_and_advances() -> None:
    unit_of_work, _, _ = _pair()
    assert unit_of_work.commit_seq() == "commit-0"

    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    assert unit_of_work.commit().commit_seq == "commit-1"
    assert unit_of_work.commit_seq() == "commit-1"

    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=1, payload={}
    )
    assert unit_of_work.commit().commit_seq == "commit-2"


def test_committed_records_are_visible_to_later_reads() -> None:
    unit_of_work, reader, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project",
        record_id="p1",
        expected_revision=None,
        payload={"project_id": "p1", "name": "demo"},
    )
    unit_of_work.commit()

    record = reader.read(aggregate_kind="project", record_id="p1", revision=1)
    assert record.payload["name"] == "demo"


def test_committing_nothing_is_rejected() -> None:
    unit_of_work, _, _ = _pair()
    unit_of_work.open("p1")
    with pytest.raises(ValueError, match="nothing staged"):
        unit_of_work.commit()


# ------------------------------------------------------- 不变量 7：回滚


def test_rollback_discards_everything_staged() -> None:
    unit_of_work, reader, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    unit_of_work.rollback()

    assert unit_of_work.commit_seq() == "commit-0"
    with pytest.raises(ValueError, match="unknown revision"):
        reader.read(aggregate_kind="project", record_id="p1", revision=1)


def test_rollback_discards_a_staged_preparation_record() -> None:
    unit_of_work, reader, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_preparation(
        record=_preparation(), payload={"prepare_request_id": "req-1"}
    )
    unit_of_work.rollback()

    assert (
        reader.find_preparation(
            project_id="p1", client_id="c1", prepare_request_id="req-1"
        )
        is None
    )


def test_rollback_requires_an_open_transaction() -> None:
    unit_of_work, _, _ = _pair()
    with pytest.raises(ValueError, match="no open transaction"):
        unit_of_work.rollback()


# ------------------------------------------------------- 不变量 8、9：读取


def test_reading_an_unknown_revision_is_rejected() -> None:
    unit_of_work, reader, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project", record_id="p1", expected_revision=None, payload={}
    )
    unit_of_work.commit()

    for revision in (0, 2):
        with pytest.raises(ValueError, match="unknown revision"):
            reader.read(aggregate_kind="project", record_id="p1", revision=revision)


def test_history_is_append_only() -> None:
    unit_of_work, reader, _ = _pair()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project",
        record_id="p1",
        expected_revision=None,
        payload={"name": "first"},
    )
    unit_of_work.commit()
    unit_of_work.open("p1")
    unit_of_work.stage_record(
        aggregate_kind="project",
        record_id="p1",
        expected_revision=1,
        payload={"name": "second"},
    )
    unit_of_work.commit()

    assert reader.read(
        aggregate_kind="project", record_id="p1", revision=1
    ).payload["name"] == "first"
    assert reader.read(
        aggregate_kind="project", record_id="p1", revision=2
    ).payload["name"] == "second"


def test_query_is_scoped_to_the_project_and_can_page() -> None:
    unit_of_work, reader, _ = _pair()
    for index in range(3):
        unit_of_work.open("p1")
        unit_of_work.stage_record(
            aggregate_kind="case",
            record_id=f"case-{index}",
            expected_revision=None,
            payload={"project_id": "p1", "case_id": f"case-{index}"},
        )
        unit_of_work.commit()
    unit_of_work.open("p2")
    unit_of_work.stage_record(
        aggregate_kind="case",
        record_id="other",
        expected_revision=None,
        payload={"project_id": "p2"},
    )
    unit_of_work.commit()

    page = reader.query(RecordQuery(project_id="p1", aggregate_kind="case", limit=2))
    assert len(page.items) == 2
    assert page.next_cursor == "2"

    full = reader.query(RecordQuery(project_id="p1", aggregate_kind="case", limit=10))
    assert [item.record_id for item in full.items] == ["case-0", "case-1", "case-2"]
    assert full.next_cursor is None


def test_query_requires_a_project_and_a_positive_limit() -> None:
    with pytest.raises(ValueError, match="project_id"):
        RecordQuery(project_id="  ")
    with pytest.raises(ValueError, match="limit"):
        RecordQuery(project_id="p1", limit=0)
