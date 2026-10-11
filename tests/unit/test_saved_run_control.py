"""Saved controls use real file transactions; process/gesture fixtures stay explicit."""

import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.application.execution.control import RunControlService, boundary_pending
from aitest.application.execution.runner import SerialRunner
from aitest.application.execution.saved_control import SavedRunControl
from aitest.contracts.commands import Command
from aitest.domain.execution.runs import (
    AttemptState,
    ExecutionInspectionState,
    RunControlState,
    StopRequestResult,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_serial_execution_slices import coordinator, items


def command(service, action, intent, *, base=None, request=None):
    current = service.coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    return Command(
        action=action,
        project_id="project-1",
        intent_id=intent,
        request_id=request or "request-" + intent,
        expected_revision=0,
        target="run-1",
        parameters={
            "run_id": "run-1",
            "base_snapshot_commit_id": base or current.snapshot_commit_id,
        },
    )


def configured(root, *, running=True):
    saved = coordinator(root)

    # This explicit component authority has no product grants to revoke.
    def revoke(**kwargs):
        assert saved._uow.project == "project-1"
        assert kwargs["changed_step_ids"] == ("step-1", "step-2")

    saved._execution_authorizations.stage_revoke_affected = revoke
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=999)
    )
    if running:
        SerialRunner(port, commit_coordinator=saved, poll_interval_seconds=0).execute_attempt(
            items()[0].attempt, items()[0].request, max_polls=1
        )
    execution = SimpleNamespace(execution_port=port, spool=FileSpoolStore(root))
    return SavedRunControl(saved, execution, "explicit-component-fixture"), port


def test_pause_persists_before_observation_and_old_intent_cannot_replace_resume(
    tmp_path, monkeypatch
):
    service, port = configured(tmp_path)
    original = port.inspect

    def inspect(handle):
        assert service.unit.project is None
        facts = service.coordinator.read_current_facts(project_id="project-1", run_id="run-1")
        assert facts.run.control_state.value == "pause_requested"
        assert service._read(
            "run_control_intent", service._identity("project-1", "pause"), "project-1"
        )
        return original(handle)

    monkeypatch.setattr(port, "inspect", inspect)
    pause = command(service, "pause_run", "pause")
    paused = service.apply(pause)
    assert paused["run"]["control_state"] == "pause_requested"
    before = service.unit.current_commit_sequence()
    with pytest.raises(ValueError, match="control state"):
        SerialRunner(port, commit_coordinator=service.coordinator).start_attempt(
            items()[1].attempt, items()[1].request
        )
    assert service.unit.current_commit_sequence() == before
    resumed = service.apply(command(service, "resume_run", "resume"))
    assert resumed["run"]["control_state"] == "running"
    before = service.unit.current_commit_sequence()
    monkeypatch.setattr(
        port, "inspect", lambda _: pytest.fail("superseded pause must be read-only")
    )
    recalled = service.apply(pause.model_copy(update={"request_id": "old-pause-recall"}))
    assert recalled == paused
    assert service.unit.current_commit_sequence() == before
    assert (
        service.coordinator.read_current_facts(
            project_id="project-1", run_id="run-1"
        ).run.control_state.value
        == "running"
    )
    assert port.execution_order == ["attempt-1"]


def test_cancel_saves_intent_before_stop_and_restart_collects_without_second_stop(
    tmp_path, monkeypatch
):
    service, port = configured(tmp_path)
    value = command(service, "cancel_run", "cancel")
    original = port.request_stop
    stops = []

    def interrupted(handle):
        assert service.unit.project is None
        assert service._read(
            "run_control_intent", service._identity("project-1", "cancel"), "project-1"
        )
        assert (
            service.coordinator.read_current_facts(
                project_id="project-1", run_id="run-1"
            ).run.control_state.value
            == "cancelling"
        )
        stops.append(handle.handle_id)
        original(handle)
        raise RuntimeError("transport lost after actual stop")

    monkeypatch.setattr(port, "request_stop", interrupted)
    with pytest.raises(RuntimeError, match="transport lost"):
        service.apply(value)
    restarted, _ = configured(tmp_path, running=False)
    restarted.execution.execution_port = port
    result = restarted.apply(value.model_copy(update={"request_id": "recover-cancel"}))
    assert result["run"]["control_state"] == "cancelled"
    assert result["attempts"][0]["state"] == "cancelled"
    assert result["attempts"][0]["exit_fact"]["termination_reason"] == "confirmed_stop"
    assert len(stops) == 1 and port.execution_order == ["attempt-1"]
    restarted.execution.execution_port = None
    before = restarted.unit.current_commit_sequence()
    assert restarted.apply(value.model_copy(update={"request_id": "history"})) == result
    assert restarted.unit.current_commit_sequence() == before
    assert result["run"]["result_ref"] is None and not result["verifications"]


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_admission_failure_rolls_back_and_never_calls_stop(tmp_path, monkeypatch, index):
    service, port = configured(tmp_path)
    value = command(service, "cancel_run", "cancel")
    before = service.unit.current_commit_sequence()
    original = service.unit.stage_record
    calls = []

    def fail(**kwargs):
        calls.append(kwargs["aggregate_kind"])
        if len(calls) == index:
            raise ValueError("injected save failure")
        return original(**kwargs)

    monkeypatch.setattr(service.unit, "stage_record", fail)
    monkeypatch.setattr(
        port, "request_stop", lambda _: pytest.fail("uncommitted cancel must not stop")
    )
    with pytest.raises(ValueError, match="injected save failure"):
        service.apply(value)
    assert service.unit.current_commit_sequence() == before and service.unit.project is None
    assert (
        service._read("run_control_intent", service._identity("project-1", "cancel"), "project-1")
        is None
    )
    assert (
        service.coordinator.read_current_facts(
            project_id="project-1", run_id="run-1"
        ).run.control_state.value
        == "running"
    )


@pytest.mark.parametrize("change", ["base", "intent_action", "extra", "target"])
def test_wrong_control_scope_does_not_mutate_or_stop(tmp_path, monkeypatch, change):
    service, port = configured(tmp_path)
    original = command(service, "pause_run", "pause")
    if change == "intent_action":
        service.execution.execution_port = None
        service.apply(original)
        value = original.model_copy(update={"action": "cancel_run", "request_id": "changed-action"})
    elif change == "base":
        value = original.model_copy(
            update={"parameters": {**original.parameters, "base_snapshot_commit_id": "stale"}}
        )
    elif change == "target":
        value = original.model_copy(update={"target": "other"})
    else:
        value = original.model_copy(
            update={"parameters": {**original.parameters, "state": "cancelled"}}
        )
    before = service.unit.current_commit_sequence()
    monkeypatch.setattr(port, "request_stop", lambda _: pytest.fail("wrong scope cannot stop"))
    with pytest.raises(ValueError):
        service.apply(value)
    assert service.unit.current_commit_sequence() == before and service.unit.project is None


def test_missing_adapter_still_saves_pause_but_cannot_confirm_boundary(tmp_path):
    service, port = configured(tmp_path)
    service.execution.execution_port = None
    result = service.apply(command(service, "pause_run", "pause"))
    assert result["run"]["control_state"] == "pause_requested"
    assert result["attempts"][0]["state"] == "running"
    assert port.execution_order == ["attempt-1"]


@pytest.mark.parametrize(
    "state", [AttemptState.COMPLETED, AttemptState.CANCELLED, AttemptState.INVALIDATED]
)
def test_no_handle_terminal_label_cannot_confirm_control_boundary(state):
    from tests.unit.test_execution_control import _attempt

    assert boundary_pending(replace(_attempt(state), execution_handle_ref=None, exit_fact_ref=None))


def test_process_boundary_can_finish_while_business_verification_is_pending():
    from tests.unit.test_execution_control import _run
    from tests.unit.test_execution_observation_identity import observation

    attempt, _, collection = observation()
    pending = replace(
        attempt, state=AttemptState.PENDING_VERIFICATION, exit_fact_ref=collection.exit_fact_ref
    )
    assert not boundary_pending(pending)
    result = RunControlService(Mock()).pause(
        replace(_run(), control_state=RunControlState.RUNNING), (pending,)
    )
    assert result.run_state is RunControlState.PAUSED and not result.requires_verification


@pytest.mark.parametrize("fault", ["unknown_inspection", "false_stop_only", "wrong_exit"])
def test_stop_acknowledgement_alone_cannot_confirm_run_cancel(tmp_path, monkeypatch, fault):
    service, port = configured(tmp_path)
    if fault == "unknown_inspection":
        original = port.inspect
        monkeypatch.setattr(
            port,
            "inspect",
            lambda h: replace(
                original(h), state=ExecutionInspectionState.UNKNOWN, identity_matches=False
            ),
        )
        monkeypatch.setattr(
            port, "request_stop", lambda _: pytest.fail("unknown handle cannot be stopped")
        )
    elif fault == "false_stop_only":
        monkeypatch.setattr(
            port,
            "request_stop",
            lambda h: StopRequestResult(h.handle_id, True, ExecutionInspectionState.STOPPED),
        )
    else:
        original = port.collect
        monkeypatch.setattr(
            port,
            "collect",
            lambda h, cursors: replace(
                original(h, cursors),
                exit_fact_ref=replace(original(h, cursors).exit_fact_ref, attempt_id="foreign"),
            ),
        )
    result = service.apply(command(service, "cancel_run", "cancel"))
    assert result["run"]["control_state"] == "cancelling"
    assert result["run"]["result_ref"] is None and not result["coverage"]["executed_attempt_ids"]


def test_real_command_cancel_collects_actual_exit_and_output(tmp_path, monkeypatch):
    service, _ = configured(tmp_path, running=False)
    item = items()[0]
    attempt = replace(item.attempt, adapter_version=CommandAdapter.adapter_version)
    request = replace(
        item.request,
        registered_entry=replace(
            item.request.registered_entry,
            entrypoint=sys.executable,
            arguments=("-c", "import time; print('before-stop',flush=True); time.sleep(30)"),
        ),
        timeout_ms=10000,
    )
    # Separate explicit component workspace with the actual command frozen as fixture authority.
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
    from tests.support.execution_authority import fixture_coordinator

    root = tmp_path / "actual"
    saved = fixture_coordinator(FileUnitOfWork(root), ((attempt, request),))
    saved._execution_authorizations.stage_revoke_affected = lambda **_: None
    port = CommandAdapter(handle_store=FileExecutionHandleStore(root))
    port.register(CommandRegistration(request.registered_entry.entry_id, sys.executable, tmp_path))
    running = SerialRunner(
        port, FileSpoolStore(root), commit_coordinator=saved, poll_interval_seconds=0
    ).execute_attempt(attempt, request, max_polls=1)
    assert running.state is AttemptState.RUNNING
    controlled = SavedRunControl(
        saved,
        SimpleNamespace(execution_port=port, spool=FileSpoolStore(root)),
        "explicit-component-fixture",
    )
    original_stop = port.request_stop
    try:
        value = command(controlled, "cancel_run", "real-cancel")
        result = controlled.apply(value)
        assert result["run"]["control_state"] == "cancelled"
        assert result["attempts"][0]["exit_fact"]["termination_reason"] == "confirmed_stop"
        manifest = FileSpoolStore(root).read_manifest(running.attempt_id)
        assert b"before-stop" in b"".join(
            FileSpoolStore(root).read_block(b) for b in manifest.blocks
        )
        before = controlled.unit.current_commit_sequence()
        monkeypatch.setattr(
            port, "request_stop", lambda _: pytest.fail("completed cancellation is read-only")
        )
        assert controlled.apply(value.model_copy(update={"request_id": "real-recall"})) == result
        assert controlled.unit.current_commit_sequence() == before
    finally:
        original_stop(running.execution_handle_ref)
