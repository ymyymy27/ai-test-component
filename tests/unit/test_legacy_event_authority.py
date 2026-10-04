"""Legacy event recovery must prove an actual committed business record."""

import json

import pytest

from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.infrastructure.security import known_secrets


def pending_business_event(root, monkeypatch):
    journal = FileEventJournal(root, instance_id="old-core")
    unit = FileUnitOfWork(root, journal=journal)
    unit.begin("saved-request", "project", intent_id="saved-intent")
    unit.stage_record(
        aggregate_kind="case",
        record_id="saved-case",
        expected_revision=0,
        payload={"project_id": "project", "summary": "saved business record"},
    )

    def fail_boundary(*args, **kwargs):
        raise OSError("controlled crash after business publication")

    with monkeypatch.context() as patch:
        patch.setattr(journal, "commit_boundary", fail_boundary)
        with pytest.raises(OSError, match="controlled crash"):
            unit.commit("saved-request")
    unit.rollback("saved-request")
    return journal, root / "event-log" / "staging" / "1.jsonl"


def test_projection_without_authority_cannot_publish_event(tmp_path):
    workspace = Workspace(tmp_path)
    with workspace.acquire():
        pass
    journal = FileEventJournal(tmp_path, instance_id="old-core")
    args = dict(
        commit_sequence=1,
        request_id="never-saved",
        intent_id="never-saved",
        workspace_id=workspace.workspace_id,
        project_id="project",
        writer_epoch=1,
    )
    journal.begin_boundary(**args)
    journal.record_event(
        **args,
        aggregate_kind="case",
        event_type="record_created",
        record_id="never-saved",
        revision=1,
    )
    (tmp_path / "commit.json").write_text(
        json.dumps({"commits": [{"commit_sequence": 1}]}), encoding="utf-8"
    )
    staging = tmp_path / "event-log" / "staging" / "1.jsonl"
    before = staging.read_bytes()
    result = RecoveryOrchestrator(tmp_path, instance_id="recovery").run()
    assert result.state == "blocked"
    assert journal.read().events == ()
    assert staging.read_bytes() == before
    assert not (tmp_path / "event-log" / "boundaries" / "1.json").exists()


@pytest.mark.parametrize(
    "damage",
    ["nonobject_boundary", "duplicate_boundary", "duplicate_event", "duplicate_authority"],
)
def test_ambiguous_recovery_json_cannot_be_overwritten_or_published(tmp_path, monkeypatch, damage):
    journal, staging = pending_business_event(tmp_path, monkeypatch)
    boundary = tmp_path / "event-log" / "boundaries" / "1.json"
    if damage == "nonobject_boundary":
        boundary.write_bytes(b"[]")
    elif damage == "duplicate_boundary":
        event = json.loads(staging.read_bytes())
        body = {
            "schema": "aitest.event-boundary/1.0",
            "state": "committed",
            "commit_sequence": 1,
            "first_sequence": 1,
            "last_sequence": 1,
            "event_ids": [event["event_id"]],
        }
        boundary.write_text('{"state":"foreign",' + json.dumps(body)[1:], encoding="utf-8")
    elif damage == "duplicate_event":
        staging.write_bytes(b'{"request_id":"foreign",' + staging.read_bytes()[1:])
    else:
        authority = tmp_path / "records.json"
        authority.write_bytes(b'{"records":{},' + authority.read_bytes()[1:])
    before = staging.read_bytes()
    boundary_before = boundary.read_bytes() if boundary.exists() else None
    result = RecoveryOrchestrator(tmp_path, instance_id="recovery").run()
    assert result.state == "blocked"
    assert journal.read().events == ()
    assert staging.read_bytes() == before
    if boundary_before is not None:
        assert boundary.read_bytes() == boundary_before


def test_staging_replacement_cannot_reuse_prior_verification(tmp_path, monkeypatch):
    journal, staging = pending_business_event(tmp_path, monkeypatch)
    original_repair = FileEventJournal._repair_tail
    count = 0

    def replace_after_verification(self):
        nonlocal count
        count += 1
        if count == 2:  # constructor reads first, reconcile runs after checking authority
            event = json.loads(staging.read_bytes())
            event["record_id"] = "replaced-after-verification"
            staging.write_text(json.dumps(event) + "\n", encoding="utf-8")
        return original_repair(self)

    monkeypatch.setattr(FileEventJournal, "_repair_tail", replace_after_verification)
    result = RecoveryOrchestrator(tmp_path, instance_id="recovery").run()
    assert count == 2
    assert result.state == "blocked"
    assert journal.read().events == ()
    assert json.loads(staging.read_bytes())["record_id"] == "replaced-after-verification"


def test_unsafe_event_identity_is_retained_without_new_copy(tmp_path, monkeypatch):
    journal, staging = pending_business_event(tmp_path, monkeypatch)
    from aitest.infrastructure.file_store.events import derive_event_id

    event = json.loads(staging.read_bytes())
    secret = "synthetic-legacy-event-credential"
    known_secrets().register(secret)
    event["instance_id"] = secret
    event["event_id"] = derive_event_id(
        instance_id=secret,
        commit_sequence=1,
        event_type="record_created",
        project_id="project",
        aggregate_kind="case",
        record_id="saved-case",
        revision=1,
    )
    staging.write_text(json.dumps(event) + "\n", encoding="utf-8")
    before = staging.read_bytes()
    result = RecoveryOrchestrator(tmp_path, instance_id="recovery").run()
    assert result.state == "blocked"
    assert secret not in str(result)
    assert journal.read().events == ()
    assert staging.read_bytes() == before
    assert not (tmp_path / "event-log" / "boundaries" / "1.json").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "other-request"),
        ("intent_id", "other-intent"),
        ("project_id", "other-project"),
        ("workspace_id", "other-workspace"),
        ("writer_epoch", 999),
        ("record_id", "missing-record"),
        ("revision", 2),
        ("event_type", "run_passed"),
        ("event_id", "evt_forged"),
    ],
)
def test_foreign_staged_event_cannot_borrow_known_commit(tmp_path, monkeypatch, field, value):
    journal, staging = pending_business_event(tmp_path, monkeypatch)
    event = json.loads(staging.read_bytes())
    event[field] = value
    staging.write_text(json.dumps(event) + "\n", encoding="utf-8")
    before = staging.read_bytes()
    result = RecoveryOrchestrator(tmp_path, instance_id="recovery").run()
    assert result.state == "blocked"
    assert journal.read().events == ()
    assert staging.read_bytes() == before


def test_actual_committed_event_is_recovered_once(tmp_path, monkeypatch):
    journal, staging = pending_business_event(tmp_path, monkeypatch)
    event = journal._deserialize(staging.read_bytes().strip())
    recovery = RecoveryOrchestrator(tmp_path, instance_id="recovery")
    assert recovery.run().state == "repaired"
    assert journal.read().events == (event,)
    assert not staging.exists()
    assert recovery.run().state == "healthy"
    assert journal.read().events == (event,)


@pytest.mark.parametrize(
    "damage",
    ["empty_events", "duplicate_event", "missing_record", "foreign_owner", "false_boundary"],
)
def test_partial_or_foreign_recovery_material_is_retained(tmp_path, monkeypatch, damage):
    journal, staging = pending_business_event(tmp_path, monkeypatch)
    if damage == "empty_events":
        staging.write_bytes(b"")
    elif damage == "duplicate_event":
        staging.write_bytes(staging.read_bytes() * 2)
    elif damage in {"missing_record", "foreign_owner"}:
        authority = tmp_path / "records.json"
        payload = json.loads(authority.read_bytes())
        if damage == "missing_record":
            payload["records"]["case"]["saved-case"] = []
        else:
            payload["records"]["case"]["saved-case"][0]["project_id"] = "other-project"
        authority.write_text(json.dumps(payload), encoding="utf-8")
    else:
        boundary = tmp_path / "event-log" / "boundaries" / "1.json"
        boundary.write_text(
            json.dumps(
                {
                    "schema": "aitest.event-boundary/1.0",
                    "state": "committed",
                    "commit_sequence": 1,
                    "first_sequence": 1,
                    "last_sequence": 1,
                    "event_ids": ["foreign-event"],
                }
            ),
            encoding="utf-8",
        )
    before = staging.read_bytes()
    result = RecoveryOrchestrator(tmp_path, instance_id="recovery").run()
    assert result.state == "blocked"
    assert journal.read().events == ()
    assert staging.read_bytes() == before
