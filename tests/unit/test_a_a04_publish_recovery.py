"""A-04：records 权威边界发布后故障的恢复闭环。

覆盖：
- 发布后投影（commit.json/indexes.json/事件边界）全部丢失，重启恢复后
  committed_sequences 以 records 台账为准且非空，投影可查询、可重复恢复；
- records 发布之后 journal.commit_boundary 故障注入：事实不回滚，恢复编排
  补完成已确认边界；
- records 发布之前故障：不存在已确认提交，暂存被隔离，恢复不伪造序列；
- commit.json 落后于台账时，并集兜底 + 重建补齐。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.application.planning.substrate import RecordQuery
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

_INSTANCE = "instance-a04"


def _commit_one(
    root: Path,
    *,
    request_id: str = "req-1",
    record_id: str = "case-1",
    project_id: str = "project-1",
) -> tuple[FileUnitOfWork, FileEventJournal]:
    journal = FileEventJournal(root, instance_id=_INSTANCE)
    unit = FileUnitOfWork(root, journal=journal)
    unit.begin(request_id, project_id)
    unit.stage_record(
        aggregate_kind="case",
        record_id=record_id,
        expected_revision=None,
        payload={"project_id": project_id, "summary": request_id},
    )
    result = unit.commit(request_id)
    assert result["state"] == "committed"
    return unit, journal


def _fresh_journal(root: Path) -> FileEventJournal:
    return FileEventJournal(root, instance_id=_INSTANCE)


def test_post_publish_projection_loss_is_repaired_after_restart(tmp_path: Path) -> None:
    _, journal = _commit_one(tmp_path)

    # 模拟掉电：records.json 已原子发布，但提交清单/索引/边界标记都未写成，
    # journal 事件尚未追加，活动标记仍在。
    (tmp_path / "commit.json").unlink()
    (tmp_path / "indexes.json").unlink()
    marker_dir = tmp_path / "event-log" / "boundaries"
    (marker_dir / "1.json").unlink()
    staging = tmp_path / "event-log" / "staging" / "1.jsonl"
    staging.write_bytes((tmp_path / "event-log" / "journal.jsonl").read_bytes())
    (tmp_path / "event-log" / "journal.jsonl").write_bytes(b"")
    active = tmp_path / "transactions" / "active.json"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(
        '{"request_id": "req-1", "project_id": "project-1", "intent_id": null, '
        '"commit_sequence": 1, "state": "in_progress"}',
        encoding="utf-8",
    )

    # “重启”：全新对象实例执行恢复编排。
    result = RecoveryOrchestrator(tmp_path, instance_id=_INSTANCE).run()

    assert result.state == "repaired"
    assert result.committed_sequences == (1,)
    assert not active.exists()
    assert any("commit.json" in action for action in result.actions)
    assert any("indexes.json" in action for action in result.actions)
    assert result.reconcile is not None
    assert result.reconcile.completed_boundaries == (1,)

    # 投影恢复后业务记录可读、列表查询 ok、事件可见。
    repo = FileRecordRepository(tmp_path)
    record = repo.read(aggregate_kind="case", record_id="case-1", revision=1)
    assert record.payload["summary"] == "req-1"
    listed = repo.query(RecordQuery(project_id="project-1"))
    assert listed.status == "ok"
    assert len(listed.items) == 1
    assert listed.next_cursor is None
    assert len(_fresh_journal(tmp_path).read().events) == 1

    # 再次“重启”：健康工作空间不产生重复修复动作。
    second = RecoveryOrchestrator(tmp_path, instance_id=_INSTANCE).run()
    assert second.state == "healthy"
    assert "无需恢复" in second.actions
    assert second.committed_sequences == (1,)
    listed_again = FileRecordRepository(tmp_path).query(
        RecordQuery(project_id="project-1")
    )
    assert listed_again.status == "ok"
    assert len(listed_again.items) == 1


def test_journal_boundary_failure_after_records_published_completes_on_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = FileEventJournal(tmp_path, instance_id=_INSTANCE)

    def _boom(*, commit_sequence: int) -> dict[str, object]:
        raise OSError("simulated boundary write failure")

    monkeypatch.setattr(journal, "commit_boundary", _boom)

    unit = FileUnitOfWork(tmp_path, journal=journal)
    unit.begin("req-boundary", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1", "summary": "durable"},
    )
    with pytest.raises(OSError, match="boundary write failure"):
        unit.commit("req-boundary")

    # 调用方看到失败，但权威事实已落盘；活动标记已清，边界暂存仍在。
    assert not (tmp_path / "transactions" / "active.json").exists()
    assert (tmp_path / "event-log" / "staging" / "1.jsonl").exists()
    assert FileRecordRepository(tmp_path).committed_sequences() == (1,)

    result = RecoveryOrchestrator(tmp_path, instance_id=_INSTANCE).run()

    assert result.state == "repaired"
    assert result.committed_sequences == (1,)
    assert result.reconcile is not None
    assert result.reconcile.completed_boundaries == (1,)
    events = _fresh_journal(tmp_path).read().events
    assert len(events) == 1
    assert events[0].record_id == "case-1"
    assert not (tmp_path / "event-log" / "staging" / "1.jsonl").exists()


def test_failure_before_records_published_leaves_no_committed_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = FileEventJournal(tmp_path, instance_id=_INSTANCE)

    def _boom(self: FileRecordRepository, data: dict[str, object]) -> None:
        raise OSError("simulated records publish failure")

    monkeypatch.setattr(FileRecordRepository, "_save", _boom)

    unit = FileUnitOfWork(tmp_path, journal=journal)
    unit.begin("req-lost", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1", "summary": "never published"},
    )
    with pytest.raises(OSError, match="records publish failure"):
        unit.commit("req-lost")

    # 无权威事实、无修订、暂存被隔离留证、活动标记清除。
    repo = FileRecordRepository(tmp_path)
    assert repo.committed_sequences() == ()
    assert repo.current_revision("case", "case-1") == 0
    assert not (tmp_path / "transactions" / "active.json").exists()
    quarantine = list((tmp_path / "event-log" / "quarantine").glob("staging-1.jsonl"))
    assert len(quarantine) == 1
    assert _fresh_journal(tmp_path).read().events == ()

    result = RecoveryOrchestrator(tmp_path, instance_id=_INSTANCE).run()
    assert result.committed_sequences == ()
    assert "无需恢复" in result.actions
    # 恢复绝不重放未发布事务。
    assert FileRecordRepository(tmp_path).current_revision("case", "case-1") == 0


def test_stale_commit_list_is_unioned_and_rebuilt(tmp_path: Path) -> None:
    _commit_one(tmp_path, request_id="req-1", record_id="case-1")
    _commit_one(tmp_path, request_id="req-2", record_id="case-2")

    # commit.json 停留在 seq 1（seq 2 发布时写清单失败）。
    import json

    raw = json.loads((tmp_path / "commit.json").read_text(encoding="utf-8"))
    raw["commits"] = [entry for entry in raw["commits"] if entry["commit_sequence"] == 1]
    (tmp_path / "commit.json").write_text(json.dumps(raw), encoding="utf-8")

    result = RecoveryOrchestrator(tmp_path, instance_id=_INSTANCE).run()

    assert result.committed_sequences == (1, 2)
    assert any("commit.json" in action for action in result.actions)
    repaired = json.loads((tmp_path / "commit.json").read_text(encoding="utf-8"))
    assert [entry["commit_sequence"] for entry in repaired["commits"]] == [1, 2]
