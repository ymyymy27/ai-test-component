"""Actual file authority through serial start, rollback and historical restart.

Execution and human events are explicit synthetic fixtures; no Trae acceptance.
"""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.runner import SerialRunner
from aitest.bootstrap import assemble_workspace_core
from aitest.domain.execution.authorization import AuthorizationState
from aitest.domain.execution.runs import AttemptState
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_execution_authorization_origin import resolved as resolved
from tests.unit.test_execution_authorization_origin import review, save
from tests.unit.test_serial_runner import FakeExecutionPort, _attempt, _request


@pytest.mark.parametrize("persistent", [False, True])
def test_caller_authorization_ref_cannot_start_without_original_proof(tmp_path, persistent):
    port = FakeExecutionPort()
    unit = FileUnitOfWork(tmp_path)
    runner = SerialRunner(
        port, commit_coordinator=ExecutionCommitCoordinator(unit) if persistent else None
    )
    with pytest.raises(ValueError, match="original authorization|persistent authorization"):
        runner.start_attempt(_attempt(), _request())
    assert port.started == []
    assert unit.current_commit_sequence() == 0


@pytest.mark.parametrize("revision", [True, 1.0, "1", -1])
def test_warehouse_revision_cannot_be_coerced_to_an_integer(revision):
    unit = Mock()
    unit.current_revision.return_value = revision
    with pytest.raises(ValueError, match="integer"):
        ExecutionCommitCoordinator(unit)._revision("execution_authorization", "grant")


def test_original_authority_is_consumed_atomically_and_replays_after_source_change(
    resolved, monkeypatch
):
    core, inputs, source, service, parameters, action = resolved
    unit = core.unit_of_work
    port = FakeExecutionPort()
    coordinator = ExecutionCommitCoordinator(
        unit, records=unit.repo, execution_authorizations=service
    )
    runner = SerialRunner(port, commit_coordinator=coordinator)
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="original immutable"):
        runner.start_attempt(action.attempt, action.request)
    assert port.started == [] and unit.current_commit_sequence() == before
    actor, challenge = review(service, inputs.project_id, action, parameters)
    save(service, inputs.project_id, action, parameters, actor, challenge)
    before = unit.current_commit_sequence()
    bad = replace(
        action.request,
        registered_entry=replace(action.request.registered_entry, entrypoint="substituted-command"),
    )
    with pytest.raises(ValueError, match="authorized action"):
        runner.start_attempt(action.attempt, bad)
    assert port.started == [] and unit.current_commit_sequence() == before

    original_stage = unit.stage_record

    def fail_checkpoint(**kwargs):
        if kwargs["aggregate_kind"] == "execution_checkpoint":
            raise OSError("injected claim checkpoint failure")
        return original_stage(**kwargs)

    monkeypatch.setattr(unit, "stage_record", fail_checkpoint)
    with pytest.raises(OSError, match="injected claim"):
        runner.start_attempt(action.attempt, action.request)
    identity = action.request.authorization_ref.authorization_id
    assert service._state(inputs.project_id, identity) == (1, AuthorizationState.UNUSED, None)
    assert unit.current_commit_sequence() == before and port.started == []
    monkeypatch.setattr(unit, "stage_record", original_stage)
    original_start = port.start

    def start(request):
        assert unit.project is None
        record = coordinator.read_checkpoint(
            project_id=inputs.project_id, attempt_id=action.attempt.attempt_id
        )
        assert record.attempt.state is AttemptState.INTENT_RECORDED
        assert (
            coordinator.find_start(
                project_id=inputs.project_id,
                intent_id=request.intent_id,
                fingerprint=record.attempt.intent_digest,
            )
            == record.attempt
        )
        current = coordinator.read_current_facts(
            project_id=inputs.project_id, run_id=request.run_id
        )
        assert current.current_attempt_by_step[request.step_id] == request.attempt_id
        assert service._state(inputs.project_id, identity) == (
            2,
            AuthorizationState.OCCUPIED,
            request.attempt_id,
        )
        return original_start(request)

    monkeypatch.setattr(port, "start", start)
    first = runner.start_attempt(action.attempt, action.request)
    assert first.state is AttemptState.RUNNING and len(port.started) == 1
    (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(core.workspace.root, instance_id="authorized-restart")
    try:
        # No fresh resolver/probe configuration is supplied for historical replay.
        restored = SerialRunner(port, commit_coordinator=restarted.execution_coordinator)
        assert restored.start_attempt(action.attempt, action.request) == first
        assert len(port.started) == 1
    finally:
        restarted.lifetime_lock.release()
