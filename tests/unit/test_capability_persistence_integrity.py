"""Core rebuild must preserve explicit pauses and real automatic failure facts."""

import json

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.capabilities import (
    MODEL,
    SECRET,
    SOURCE,
    CapabilityGate,
    CapabilityState,
    FileCapabilityConditionStore,
)
from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.security import known_secrets


def _gate(root, workspace_id="workspace-test"):
    return CapabilityGate(state_store=FileCapabilityConditionStore(root, workspace_id=workspace_id))


def test_manual_pause_and_automatic_result_are_distinct_after_restart(tmp_path):
    first = _gate(tmp_path)
    first.configure(MODEL)
    first.degrade(MODEL, "operator paused requests")
    first.report(MODEL, healthy=False, reason="429", classification="rate_limit")
    restarted = _gate(tmp_path)
    assert restarted.condition(MODEL).manual is True
    automatic = {item["key"]: item for item in restarted.snapshot()["automatic_conditions"]}
    assert automatic[MODEL]["classification"] == "rate_limit"
    restarted.restore(MODEL)
    assert restarted.condition(MODEL).state is CapabilityState.DEGRADED
    assert restarted.condition(MODEL).manual is False
    assert restarted.condition(MODEL).classification == "rate_limit"
    restarted.report(MODEL, healthy=True)
    assert restarted.condition(MODEL).state is CapabilityState.READY


def test_healthy_fact_during_pause_is_saved_without_lifting_it(tmp_path):
    first = _gate(tmp_path)
    first.degrade(MODEL, "operator paused requests")
    first.report(MODEL, healthy=True)
    assert first.condition(MODEL).manual is True
    saved = json.loads((tmp_path / "core/capability-state.json").read_text())
    assert next(item for item in saved["automatic"] if item["key"] == MODEL)["state"] == "ready"
    restarted = _gate(tmp_path)
    restarted.configure(MODEL)
    restarted.restore(MODEL)
    assert restarted.condition(MODEL).state is CapabilityState.READY


def test_historical_success_cannot_configure_the_new_core(tmp_path):
    first = _gate(tmp_path)
    first.configure(MODEL)
    second = _gate(tmp_path)
    assert second.condition(MODEL).state is CapabilityState.NOT_CONFIGURED
    second.restore(MODEL)
    assert second.condition(MODEL).state is CapabilityState.NOT_CONFIGURED
    second.configure(MODEL)
    assert second.condition(MODEL).state is CapabilityState.READY


def test_configuration_does_not_replace_a_saved_actual_failure(tmp_path):
    first = _gate(tmp_path)
    first.report(MODEL, healthy=False, reason="401", classification="auth")
    restarted = _gate(tmp_path)
    restarted.configure(MODEL)
    assert restarted.condition(MODEL).classification == "auth"
    assert restarted.condition(MODEL).state is CapabilityState.DEGRADED


def test_default_core_rebuild_preserves_pause_and_keeps_local_actions_available(tmp_path):
    first = assemble_workspace_core(tmp_path, instance_id="core-first")
    first.gate.degrade(MODEL, "operator paused model")
    first.gate.report(MODEL, healthy=True)
    first.lifetime_lock.release()
    second = assemble_workspace_core(tmp_path, instance_id="core-second")
    try:
        assert second.gate.condition(MODEL).manual is True
        assert not second.gate.check_command("generate_draft", {"generation_mode": "model"}).allowed
        assert second.gate.check_command("generate_draft", {"generation_mode": "template"}).allowed
        assert second.gate.check("query").allowed
        assert second.gate.condition(SECRET).state is CapabilityState.READY
        assert second.gate.condition(SOURCE).state is CapabilityState.READY
    finally:
        second.lifetime_lock.release()


def test_persisted_failure_after_save_error_blocks_and_cannot_drop_a_pause(tmp_path, monkeypatch):
    store = FileCapabilityConditionStore(tmp_path, workspace_id="workspace-test")
    gate = CapabilityGate(state_store=store, action_dependencies={"send": frozenset({MODEL})})
    gate.configure(MODEL)
    original = store.save

    def fail_save(automatic, manual):
        raise OSError("simulated storage error")

    monkeypatch.setattr(store, "save", fail_save)
    with pytest.raises(OSError):
        gate.degrade(MODEL, "operator paused requests")
    assert not gate.check("send").allowed
    monkeypatch.setattr(store, "save", original)
    gate.report(SOURCE, healthy=True)
    assert gate.condition(MODEL).manual is True
    assert _gate(tmp_path).condition(MODEL).manual is True


@pytest.mark.parametrize(
    "change", ["schema", "workspace_id", "missing", "duplicate", "unknown", "manual_type", "state"]
)
def test_unverifiable_saved_state_cannot_be_reinterpreted_as_ready(tmp_path, change):
    gate = _gate(tmp_path)
    gate.configure(MODEL)
    path = tmp_path / "core/capability-state.json"
    payload = json.loads(path.read_text())
    if change in {"schema", "workspace_id"}:
        payload[change] = "foreign"
    elif change == "missing":
        payload["automatic"].pop()
    elif change == "duplicate":
        payload["automatic"].append(payload["automatic"][0])
    elif change == "unknown":
        payload["automatic"][0]["key"] = "unknown-capability"
    elif change == "manual_type":
        payload["automatic"][0]["manual"] = 0
    else:
        payload["automatic"][0]["state"] = "unknown-state"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        _gate(tmp_path)


def test_capability_reason_is_filtered_before_even_temporary_json_write(tmp_path):
    secret = "fictional-capability-key"
    known_secrets().register(secret)
    gate = _gate(tmp_path)
    gate.report(MODEL, healthy=False, reason="provider: " + secret, classification="auth")
    for path in (tmp_path / "core").iterdir():
        assert secret.encode() not in path.read_bytes()
    restarted = _gate(tmp_path)
    assert secret not in restarted.condition(MODEL).reason


def test_backup_restore_keeps_workspace_bound_pause_fact(tmp_path):
    workspace = tmp_path / "workspace"
    gate = _gate(workspace)
    gate.configure(MODEL)
    gate.degrade(MODEL, "operator paused requests")
    store = FileBackupStore(workspace)
    backup = store.create(tmp_path / "backup")
    restored = tmp_path / "restored"
    assert store.restore(backup=backup, target=restored).verified
    assert _gate(restored).condition(MODEL).manual is True
    with pytest.raises(ValueError, match="workspace"):
        _gate(restored, workspace_id="foreign-workspace")
