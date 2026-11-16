import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.file_store.events import (
    FileEventJournal,
    derive_event_id,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork


def test_distinct_kinds_same_id_and_revision_emit_two_events(tmp_path: Path) -> None:
    journal = FileEventJournal(tmp_path, instance_id="event-core")
    unit = FileUnitOfWork(tmp_path, journal=journal)
    unit.begin(request_id="request", project_id="p", intent_id="intent")
    for kind in ("rule_draft", "rule_version"):
        unit.stage_record(
            aggregate_kind=kind,
            record_id="same-id",
            expected_revision=None,
            payload={"project_id": "p", "kind": kind},
        )
    commit = unit.commit()
    events = journal.read().events

    assert [item[0] for item in commit["created"]] == ["rule_draft", "rule_version"]
    assert len(events) == 2
    assert {event.record_id for event in events} == {"same-id"}
    assert len({event.event_id for event in events}) == 2


def test_event_id_includes_aggregate_kind_namespace() -> None:
    common = dict(
        instance_id="core",
        commit_sequence=1,
        event_type="record_created",
        project_id="p",
        record_id="same-id",
        revision=1,
    )
    draft = derive_event_id(aggregate_kind="rule_draft", **common)
    version = derive_event_id(aggregate_kind="rule_version", **common)
    assert draft != version
    # 同身份确定性派生（重放幂等）。
    assert draft == derive_event_id(aggregate_kind="rule_draft", **common)


def test_duplicate_event_in_boundary_remains_idempotent(tmp_path: Path) -> None:
    journal = FileEventJournal(tmp_path, instance_id="core")
    journal.begin_boundary(
        commit_sequence=1,
        request_id="r",
        intent_id="i",
        workspace_id="ws",
        project_id="p",
        writer_epoch=1,
    )
    kwargs = dict(
        commit_sequence=1,
        event_type="record_created",
        project_id="p",
        record_id="rec",
        revision=1,
        request_id="r",
        intent_id="i",
        workspace_id="ws",
        writer_epoch=1,
    )
    first = journal.record_event(aggregate_kind="rule_draft", **kwargs)
    second = journal.record_event(aggregate_kind="rule_draft", **kwargs)
    assert first.event_id == second.event_id
    journal.commit_boundary(commit_sequence=1)
    assert len(journal.read().events) == 1
