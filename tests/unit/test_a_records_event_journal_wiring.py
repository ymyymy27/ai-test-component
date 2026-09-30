"""A 包 records/UOW 切换正式事件日志接线测试。

验证：

1. 注入 journal 后 commit_transaction 写 ``event-log/journal.jsonl``，
   不再写旧版 ``events.json``；
2. 一个事务对应一个 boundary，事务内每条 pending 对应一个 event；
3. boundary.commit_sequence = 事务最后一条记录的 commit_sequence，
   与 commit.json 的 commit_sequence 对齐；
4. UOW 透传 journal 后 commit 也走正式路径；
5. 未注入 journal 时保持旧路径写 ``events.json``（向后兼容）。
"""

from __future__ import annotations

import json
from pathlib import Path

from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork


def test_commit_transaction_writes_to_formal_journal_when_injected(
    tmp_path: Path,
) -> None:
    journal = FileEventJournal(tmp_path, instance_id="core-test-1")
    repo = FileRecordRepository(tmp_path, journal=journal)

    pending: list[tuple[str, str, int | None, dict[str, object]]] = [
        ("case", "case-1", 0, {"project_id": "p1", "summary": "first"}),
        ("case", "case-2", 0, {"project_id": "p1", "summary": "second"}),
    ]
    created, commit_sequence = repo.commit_transaction(
        pending,
        request_id="req-1",
        intent_id=None,
        project_id="p1",
        workspace_id="ws-journal",
        writer_epoch=1,
    )

    # 两条 pending → commit_sequence 1, 2；事务最终 commit_sequence = 2
    assert commit_sequence == 2
    assert len(created) == 2

    # 不应写旧版 events.json
    assert not (tmp_path / "events.json").exists()

    # 应写正式事件日志 journal.jsonl，含 2 行
    journal_path = tmp_path / "event-log" / "journal.jsonl"
    assert journal_path.exists()
    lines = [
        line for line in journal_path.read_bytes().splitlines() if line.strip()
    ]
    assert len(lines) == 2

    event1 = json.loads(lines[0].decode("utf-8"))
    event2 = json.loads(lines[1].decode("utf-8"))
    # 两条事件共享同一 commit_sequence（事务级别）
    assert event1["commit_sequence"] == 2
    assert event2["commit_sequence"] == 2
    # event_sequence 单调递增
    assert event1["event_sequence"] == 1
    assert event2["event_sequence"] == 2
    # 事件身份字段
    assert event1["record_id"] == "case-1"
    assert event1["revision"] == 1
    assert event2["record_id"] == "case-2"
    assert event2["revision"] == 1
    # schema 版本
    assert event1["schema_version"] == "aitest.event/2.0"
    assert event2["schema_version"] == "aitest.event/2.0"

    # commit.json 的 commit_sequence 与 boundary 一致
    commit_json = json.loads((tmp_path / "commit.json").read_text("utf-8"))
    assert commit_json["commits"][0]["commit_sequence"] == 2

    # boundary 标记存在
    boundary = json.loads(
        (tmp_path / "event-log" / "boundaries" / "2.json").read_text("utf-8")
    )
    assert boundary["state"] == "committed"
    assert boundary["last_sequence"] == 2
    assert len(boundary["event_ids"]) == 2


def test_uow_propagates_journal_to_records(tmp_path: Path) -> None:
    journal = FileEventJournal(tmp_path, instance_id="core-uow-1")
    unit = FileUnitOfWork(tmp_path, journal=journal)

    unit.begin("req-uow", "p1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-uow",
        expected_revision=0,
        payload={"project_id": "p1", "summary": "via-uow"},
    )
    committed = unit.commit("req-uow")
    assert committed["state"] == "committed"
    assert committed["commit_sequence"] == 1

    # 走正式日志：events.json 不应存在
    assert not (tmp_path / "events.json").exists()
    # journal.jsonl 应有 1 行
    journal_path = tmp_path / "event-log" / "journal.jsonl"
    lines = [
        line for line in journal_path.read_bytes().splitlines() if line.strip()
    ]
    assert len(lines) == 1

    # 用 journal.read 能读到事件
    result = journal.read()
    assert result.status == "ok"
    assert len(result.events) == 1
    assert result.events[0].record_id == "case-uow"


def test_commit_transaction_writes_legacy_events_json_when_no_journal(
    tmp_path: Path,
) -> None:
    """未注入 journal 时保持旧路径写 events.json（迁移过渡期兼容）。"""
    repo = FileRecordRepository(tmp_path, journal=None)
    pending: list[tuple[str, str, int | None, dict[str, object]]] = [
        ("case", "case-legacy", 0, {"project_id": "p1", "summary": "legacy"}),
    ]
    repo.commit_transaction(
        pending,
        request_id="req-legacy",
        intent_id=None,
        project_id="p1",
        workspace_id="ws-legacy",
        writer_epoch=1,
    )
    # 旧版 events.json 应存在
    legacy = tmp_path / "events.json"
    assert legacy.exists()
    payload = json.loads(legacy.read_text("utf-8"))
    assert payload["schema"] == "aitest.events/1.0"
    assert len(payload["events"]) == 1
    # 正式 event-log/journal.jsonl 不应被创建
    assert not (tmp_path / "event-log" / "journal.jsonl").exists()


def test_commit_transaction_idempotent_replay_via_derived_event_id(
    tmp_path: Path,
) -> None:
    """事件日志 reconcile 在崩溃后核对：已 committed 的 boundary
    不会被二次 begin，从而保证幂等。"""
    journal = FileEventJournal(tmp_path, instance_id="core-idem")
    repo = FileRecordRepository(tmp_path, journal=journal)

    pending: list[tuple[str, str, int | None, dict[str, object]]] = [
        ("case", "case-idem", 0, {"project_id": "p1", "summary": "idem"}),
    ]
    created, sequence = repo.commit_transaction(
        pending,
        request_id="req-idem",
        intent_id="intent-idem",
        project_id="p1",
        workspace_id="ws-idem",
        writer_epoch=1,
    )
    assert sequence == 1
    assert len(created) == 1

    # 再次 begin_boundary 同一 commit_sequence 应失败（已 committed）
    try:
        journal.begin_boundary(
            commit_sequence=1,
            request_id="req-idem-2",
            intent_id=None,
            workspace_id="ws-idem",
            project_id="p1",
            writer_epoch=1,
        )
    except ValueError as error:
        assert "already committed" in str(error)
    else:
        raise AssertionError("expected ValueError on re-beginning committed boundary")

    # reconcile 应识别该 boundary 已 committed，不重复重放
    report = journal.reconcile(committed_sequences={1})
    assert 1 not in report.orphaned_staging
