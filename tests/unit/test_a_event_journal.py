"""Unit tests for the formal file event journal (infrastructure/file_store/events.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aitest.infrastructure.file_store.events import (
    EventReadResult,
    FileEventJournal,
    decode_cursor,
    encode_cursor,
)

_INSTANCE = "instance-1"
_WORKSPACE = "workspace-1"


def _record(
    journal: FileEventJournal,
    *,
    commit_sequence: int = 1,
    event_type: str = "record_created",
    project_id: str = "project-1",
    record_id: str = "rec-1",
    revision: int = 1,
    request_id: str | None = "req-1",
    intent_id: str | None = "intent-1",
    writer_epoch: int = 1,
):
    return journal.record_event(
        commit_sequence=commit_sequence,
        event_type=event_type,
        project_id=project_id,
        record_id=record_id,
        revision=revision,
        request_id=request_id,
        intent_id=intent_id,
        workspace_id=_WORKSPACE,
        writer_epoch=writer_epoch,
    )


def _begin(
    journal: FileEventJournal,
    commit_sequence: int = 1,
) -> None:
    journal.begin_boundary(
        commit_sequence=commit_sequence,
        request_id="req-1",
        intent_id="intent-1",
        workspace_id=_WORKSPACE,
        project_id="project-1",
        writer_epoch=1,
    )


@pytest.fixture
def journal(tmp_path: Path) -> FileEventJournal:
    return FileEventJournal(tmp_path, instance_id=_INSTANCE)


def test_cursor_round_trip() -> None:
    token = encode_cursor(7)
    assert decode_cursor(token) == 7


@pytest.mark.parametrize("bad", ["not-base64!!!", "eyJhIjogMX0=", "", "encodeddummy"])
def test_decode_cursor_rejects_garbage(bad: str) -> None:
    if bad == "":
        with pytest.raises(ValueError):
            decode_cursor(bad)
    else:
        with pytest.raises(ValueError):
            decode_cursor(bad)


def test_begin_record_commit_publishes_formal_events(journal: FileEventJournal) -> None:
    _begin(journal)
    first = _record(journal, record_id="rec-1", revision=1)
    second = _record(journal, record_id="rec-2", revision=1)

    assert first.event_sequence == 1
    assert second.event_sequence == 2
    assert first.event_id != second.event_id

    result = journal.commit_boundary(commit_sequence=1)
    assert result["state"] == "committed"

    read = journal.read()
    assert read.status == "ok"
    assert len(read.events) == 2
    assert read.events[0].instance_id == _INSTANCE
    assert read.events[0].schema_version == "aitest.event/2.0"


def test_event_continuation_with_cursor(journal: FileEventJournal) -> None:
    _begin(journal)
    _record(journal, record_id="rec-1", revision=1)
    _record(journal, record_id="rec-2", revision=1)
    journal.commit_boundary(commit_sequence=1)

    page_one = journal.read(limit=1)
    assert len(page_one.events) == 1
    assert page_one.events[0].record_id == "rec-1"
    assert page_one.next_cursor is not None

    page_two = journal.read(cursor=page_one.next_cursor, limit=1)
    assert len(page_two.events) == 1
    assert page_two.events[0].record_id == "rec-2"

    tail = journal.read(cursor=page_two.next_cursor)
    assert isinstance(tail, EventReadResult)
    assert tail.events == ()
    assert tail.next_cursor is None


def test_invalid_cursor_returns_structured_status(journal: FileEventJournal) -> None:
    result = journal.read(cursor="garbage-token")
    assert result.status == "invalid_cursor"


def test_duplicate_event_in_boundary_is_idempotent(journal: FileEventJournal) -> None:
    _begin(journal)
    first = _record(journal)
    duplicate = _record(journal)  # identical identity tuple

    assert duplicate == first
    journal.commit_boundary(commit_sequence=1)
    assert len(journal.read().events) == 1


def test_commit_boundary_is_idempotent(journal: FileEventJournal) -> None:
    _begin(journal)
    _record(journal)
    journal.commit_boundary(commit_sequence=1)

    repeat = journal.commit_boundary(commit_sequence=1)
    assert repeat["state"] == "already_committed"
    assert len(journal.read().events) == 1


def test_rollback_quarantines_events_and_keeps_journal_empty(journal: FileEventJournal) -> None:
    _begin(journal)
    _record(journal)
    result = journal.rollback_boundary(commit_sequence=1)

    assert result["state"] == "rolled_back"
    assert result["retained_events"] == 1
    assert journal.read().events == ()
    quarantine = list((journal._dir / "quarantine").glob("staging-1.jsonl"))
    assert len(quarantine) == 1


def test_snapshot_cursor_points_to_boundary_end(journal: FileEventJournal) -> None:
    _begin(journal, commit_sequence=1)
    _record(journal, commit_sequence=1, record_id="rec-1")
    _record(journal, commit_sequence=1, record_id="rec-2")
    journal.commit_boundary(commit_sequence=1)

    cursor = journal.snapshot_cursor(commit_sequence=1)
    assert cursor is not None
    assert decode_cursor(cursor) == 2
    assert journal.snapshot_cursor(commit_sequence=99) is None


def test_sequences_continue_across_two_boundaries(journal: FileEventJournal) -> None:
    _begin(journal, commit_sequence=1)
    _record(journal, commit_sequence=1, record_id="rec-1")
    journal.commit_boundary(commit_sequence=1)

    journal.begin_boundary(
        commit_sequence=2,
        request_id="req-2",
        intent_id="intent-2",
        workspace_id=_WORKSPACE,
        project_id="project-1",
        writer_epoch=2,
    )
    event = _record(
        journal,
        commit_sequence=2,
        record_id="rec-2",
        request_id="req-2",
        intent_id="intent-2",
        writer_epoch=2,
    )
    journal.commit_boundary(commit_sequence=2)

    assert event.event_sequence == 2
    assert event.writer_epoch == 2
    assert len(journal.read().events) == 2


def test_torn_tail_is_quarantined_and_good_events_remain(journal: FileEventJournal) -> None:
    _begin(journal)
    _record(journal)
    journal.commit_boundary(commit_sequence=1)

    journal_path = journal._dir / "journal.jsonl"
    with journal_path.open("ab") as handle:
        handle.write(b'{"event_id": "evt_incomplete', )

    repaired = FileEventJournal(journal._root, instance_id=_INSTANCE)
    read = repaired.read()
    assert len(read.events) == 1
    quarantine = list((journal._dir / "quarantine").glob("torn-tail-*.jsonl"))
    assert len(quarantine) == 1
    assert repaired.health()["repaired_tail_lines"] == 1


def test_reconcile_completes_confirmed_boundary_and_flags_orphan(journal: FileEventJournal) -> None:
    # Simulate crash: events staged and appended to journal before marker write
    # is modeled more simply here by staging for a committed sequence.
    journal.begin_boundary(
        commit_sequence=5,
        request_id="req-5",
        intent_id="intent-5",
        workspace_id=_WORKSPACE,
        project_id="project-1",
        writer_epoch=1,
    )
    _record(journal, commit_sequence=5, record_id="rec-5", request_id="req-5", intent_id="intent-5")

    # Another staging with no matching committed sequence.
    journal.begin_boundary(
        commit_sequence=6,
        request_id="req-6",
        intent_id="intent-6",
        workspace_id=_WORKSPACE,
        project_id="project-1",
        writer_epoch=1,
    )
    _record(journal, commit_sequence=6, record_id="rec-6", request_id="req-6", intent_id="intent-6")

    report = journal.reconcile(committed_sequences={5})

    assert report.completed_boundaries == (5,)
    assert report.orphaned_staging == (6,)
    assert journal._load_boundary(5) is not None
    # Orphan staging is retained on disk, not deleted or replayed.
    assert (journal._dir / "staging" / "6.jsonl").exists()
