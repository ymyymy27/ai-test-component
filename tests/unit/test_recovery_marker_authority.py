"""Both recovery modes must preserve every unverified activity marker."""

import hashlib
import json
from pathlib import Path

import pytest

from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.publication_backend import FilePublicationBackend
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator, recover_workspace
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.security import known_secrets
from tests.unit.test_incremental_core_startup import initialized_workspace


def marker_path(root, raw=None, **changes):
    marker = {
        "request_id": "save",
        "intent_id": "save",
        "project_id": "project",
        "commit_sequence": 1,
        "state": "in_progress",
        **changes,
    }
    path = root / "transactions" / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw if raw is not None else json.dumps(marker).encode())
    return path


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": "unknown"},
        {"intent_id": "other-intent"},
        {"project_id": "another-project"},
        {"commit_sequence": True},
        {"commit_sequence": 999},
    ],
)
def test_full_maintenance_preserves_unverified_marker(tmp_path, changes):
    initialized_workspace(tmp_path)
    path = marker_path(tmp_path, **changes)
    before = path.read_bytes()
    result = RecoveryOrchestrator(tmp_path, instance_id="maintenance").run()
    assert result.state == "blocked"
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "raw",
    [
        b"[]",
        b"null",
        b'{"request_id":"unknown","request_id":"save",'
        b'"intent_id":"save","project_id":"project","commit_sequence":1,"state":"in_progress"}',
    ],
)
def test_startup_does_not_interpret_malformed_identity_as_absence(tmp_path, raw):
    initialized_workspace(tmp_path)
    path = marker_path(tmp_path, raw=raw)
    result = RecoveryOrchestrator(tmp_path, instance_id="restart").run(startup=True)
    assert result.state == "blocked"
    assert path.read_bytes() == raw


def legacy_workspace(root):
    unit = FileUnitOfWork(root)
    unit.begin("save", "project", intent_id="save")
    unit.stage_record(
        aggregate_kind="case",
        record_id="saved",
        expected_revision=0,
        payload={"project_id": "project", "summary": "saved"},
    )
    unit.commit("save")
    assert FileCommitStore(root).read_current() is None


@pytest.mark.parametrize("legacy", [False, True])
def test_full_exact_marker_is_resolved_once_with_durable_authority_evidence(tmp_path, legacy):
    (legacy_workspace if legacy else initialized_workspace)(tmp_path)
    path = marker_path(tmp_path)
    records_digest = hashlib.sha256((tmp_path / "records.json").read_bytes()).hexdigest()
    recovery = RecoveryOrchestrator(tmp_path, instance_id="maintenance")
    result = recovery.run()
    assert result.state == "repaired" and not path.exists()
    facts = list((tmp_path / "diagnostics" / "recovery").glob("*.json"))
    assert len(facts) == 1
    fact = json.loads(facts[0].read_bytes())
    assert fact["marker"] == {
        "request_id": "save",
        "project_id": "project",
        "intent_id": "save",
        "commit_sequence": 1,
    }
    if legacy:
        assert fact["legacy_authority_digest"] == records_digest
        assert fact["manifest_digest"] is None
    else:
        assert (
            fact["manifest_digest"]
            == FileCommitStore(tmp_path).read_current()["pointer"]["manifest_digest"]
        )
        assert fact["legacy_authority_digest"] is None
    assert recovery.run().state == "healthy"
    assert list((tmp_path / "diagnostics" / "recovery").glob("*.json")) == facts


@pytest.mark.parametrize("startup", [False, True])
def test_receipt_failure_keeps_exact_marker(tmp_path, monkeypatch, startup):
    initialized_workspace(tmp_path)
    path = marker_path(tmp_path)
    raw = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("controlled recovery receipt publication failure")

    monkeypatch.setattr(FilePublicationBackend, "publish_immutable", fail)
    result = RecoveryOrchestrator(tmp_path, instance_id="maintenance").run(startup=startup)
    assert result.state == "blocked"
    assert path.read_bytes() == raw


@pytest.mark.parametrize("startup", [False, True])
def test_marker_replaced_after_receipt_is_preserved(tmp_path, monkeypatch, startup):
    initialized_workspace(tmp_path)
    path = marker_path(tmp_path)
    changed = b'{"request_id":"another-activity","state":"unknown"}'
    original = RecoveryOrchestrator._save_marker_resolution

    def replace_after_receipt(recovery, current, marker):
        original(recovery, current, marker)
        path.write_bytes(changed)

    monkeypatch.setattr(RecoveryOrchestrator, "_save_marker_resolution", replace_after_receipt)
    result = RecoveryOrchestrator(tmp_path, instance_id="maintenance").run(startup=startup)
    assert result.state == "blocked"
    assert path.read_bytes() == changed
    fact = json.loads(next((tmp_path / "diagnostics" / "recovery").glob("*.json")).read_bytes())
    assert fact["marker"]["request_id"] == "save"


def test_unknown_legacy_activity_blocks_before_projection_repair(tmp_path):
    legacy_workspace(tmp_path)
    projection = tmp_path / "commit.json"
    projection.unlink()
    path = marker_path(tmp_path, request_id="unknown")
    raw = path.read_bytes()
    result = RecoveryOrchestrator(tmp_path, instance_id="maintenance").run()
    assert result.state == "blocked"
    assert path.read_bytes() == raw and not projection.exists()


def test_live_writer_blocks_recovery_even_for_previously_committed_identity(tmp_path):
    initialized_workspace(tmp_path)
    path = marker_path(tmp_path)
    raw = path.read_bytes()
    unit = FileUnitOfWork(tmp_path)
    unit.begin("active-writer", "project", intent_id="active-writer")
    try:
        result = RecoveryOrchestrator(tmp_path, instance_id="maintenance").run()
        assert result.state == "blocked" and path.read_bytes() == raw
    finally:
        unit.rollback("active-writer")


@pytest.mark.parametrize("startup", [False, True])
def test_oversized_marker_is_rejected_before_reading_body(tmp_path, monkeypatch, startup):
    initialized_workspace(tmp_path)
    path = marker_path(tmp_path, raw=b" " * 16385)
    original = Path.open

    def watched_open(target, *args, **kwargs):
        assert target != path, "over-budget marker body must not be read"
        return original(target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", watched_open)
        result = RecoveryOrchestrator(tmp_path, instance_id="maintenance").run(startup=startup)
        assert result.state == "blocked"
        assert recover_workspace(tmp_path)["active_marker_state"] == "unverified"
        assert RecoveryOrchestrator(tmp_path, instance_id="inspect").inspect()["active_marker"] == {
            "state": "unverified"
        }
    assert path.stat().st_size == 16385


@pytest.mark.parametrize("startup", [False, True])
@pytest.mark.parametrize(
    "changes", [{"state": "unknown"}, {"intent_id": 123}, {"extra": "unknown"}]
)
def test_unknown_marker_fields_never_authorize_cleanup(tmp_path, startup, changes):
    initialized_workspace(tmp_path)
    path = marker_path(tmp_path, **changes)
    raw = path.read_bytes()
    result = RecoveryOrchestrator(tmp_path, instance_id="maintenance").run(startup=startup)
    assert result.state == "blocked" and path.read_bytes() == raw


@pytest.mark.parametrize("damage", ["owner", "missing_record"])
def test_legacy_commit_must_prove_created_record_owner_and_existence(tmp_path, damage):
    legacy_workspace(tmp_path)
    path = marker_path(tmp_path)
    raw = path.read_bytes()
    authority = tmp_path / "records.json"
    payload = json.loads(authority.read_bytes())
    if damage == "owner":
        payload["records"]["case"]["saved"][0]["project_id"] = "another-project"
    else:
        payload["records"]["case"]["saved"] = []
    authority.write_text(json.dumps(payload), encoding="utf-8")
    result = RecoveryOrchestrator(tmp_path, instance_id="maintenance").run()
    assert result.state == "blocked" and path.read_bytes() == raw


def test_unsafe_marker_is_preserved_without_echoing_known_credential(tmp_path):
    initialized_workspace(tmp_path)
    secret = "synthetic-recovery-credential-value"
    known_secrets().register(secret)
    path = marker_path(tmp_path, request_id=secret)
    raw = path.read_bytes()
    recovery = RecoveryOrchestrator(tmp_path, instance_id="maintenance")
    assert recovery.run().state == "blocked"
    inspect = recovery.inspect()
    report = recover_workspace(tmp_path)
    assert inspect["active_marker"] == {"state": "unverified"}
    assert report["active_marker_state"] == "unverified"
    assert secret not in json.dumps(inspect) + json.dumps(report)
    assert path.read_bytes() == raw
