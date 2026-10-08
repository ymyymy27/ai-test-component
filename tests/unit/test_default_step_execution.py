"""Default execution uses saved synthetic consent and real Windows command output."""

import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from aitest.application.execution.checkpoint_refs import KIND, read_checkpoint_refs
from aitest.application.execution.commands import ExecutionCommands
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.reuse_sources import CaseReuseSourceReader
from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts, StepStateFact
from aitest.domain.execution.authorization import AuthorizationState
from aitest.domain.execution.runs import (
    AdapterKind,
    AttemptState,
    CaptureCompleteness,
    RegisteredEntryRef,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_current_execution_snapshot import _batch
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_execution_authorization_origin import FixtureActionResolver, review, save
from tests.unit.test_initial_run_registration import register


def execution_command(project, action, parameters, *, request="execute-request"):
    return Command(
        action="execute_step",
        project_id=project,
        request_id=request,
        intent_id=action.request.intent_id,
        expected_revision=0,
        target=action.request.step_id,
        parameters=parameters,
    )


class ActualCommandResolver(FixtureActionResolver):
    """Fixture environment/source claims remain synthetic; the process/output are real."""

    def resolve(self, **kwargs):
        action = super().resolve(**kwargs)
        return replace(
            action,
            attempt=replace(action.attempt, adapter_version=CommandAdapter.adapter_version),
            request=replace(
                action.request,
                registered_entry=RegisteredEntryRef(
                    entry_id="public-python",
                    adapter_kind=AdapterKind.COMMAND,
                    entrypoint=str(Path(sys.executable).resolve()),
                    arguments=("-c", "print('api-output')"),
                ),
            ),
        )


@pytest.mark.parametrize(
    "parameters",
    [
        {"execution_action_id": "action", "record_revision": True},
        {"execution_action_id": "action", "record_revision": 1, "command": "echo"},
        {"execution_action_id": "", "record_revision": 1},
    ],
)
def test_public_execution_rejects_injected_input_before_reading(parameters):
    service, execution = Mock(), Mock()
    entry = ExecutionCommands(service, execution=execution)
    value = Command(
        action="execute_step",
        project_id="project",
        request_id="req",
        intent_id="intent",
        expected_revision=0,
        target="step",
        parameters=parameters,
    )
    with pytest.raises(ValueError) as caught:
        entry.execute(value)
    assert caught.value.code == "INVALID_REQUEST"
    assert not service.mock_calls and not execution.mock_calls


@pytest.mark.parametrize(
    "state", [AttemptState.RUNNING, AttemptState.UNKNOWN, AttemptState.PENDING_VERIFICATION]
)
def test_serial_start_blocks_other_active_or_unknown_saved_attempts(state):
    batch = _batch()
    old = replace(batch.checkpoint.attempt, state=state, exit_fact_ref=None)
    new = replace(old, attempt_id="new-attempt", step_id="step-2")
    with pytest.raises(ValueError, match="active or unverified"):
        ExecutionCommitCoordinator._require_serial_start(batch.facts, new, (old,))


@pytest.mark.parametrize("invalid", ["no_attempt", "pending_step", "capture_gap", "missing_exit"])
def test_serial_dependency_needs_actual_complete_current_basis(invalid):
    batch = _batch()
    step = batch.facts.steps[0]
    next_step = step.model_copy(
        update={
            "step_id": "step-2",
            "dependency_step_ids": (step.step_id,),
            "state": StepStateFact.PENDING,
        }
    )
    old = batch.checkpoint.attempt
    if invalid == "capture_gap":
        old = replace(old, capture_completeness=CaptureCompleteness.GAP)
    elif invalid == "missing_exit":
        old = replace(old, exit_fact_ref=None)
    if invalid == "pending_step":
        step = step.model_copy(update={"state": StepStateFact.PENDING})
    current = batch.facts.model_copy(update={"steps": (step, next_step)})
    saved = () if invalid == "no_attempt" else (old,)
    new = replace(old, attempt_id="new-attempt", step_id="step-2")
    with pytest.raises(ValueError):
        ExecutionCommitCoordinator._require_serial_start(current, new, saved)


def test_serial_dependency_accepts_a_reliably_completed_empty_output():
    from tests.unit.test_execution_observation_identity import observation

    observed, _, collection = observation()
    old = replace(
        observed,
        state=AttemptState.COMPLETED,
        exit_fact_ref=collection.exit_fact_ref,
        capture_completeness=CaptureCompleteness.COMPLETE,
    )
    base = _batch().facts
    step = base.steps[0].model_copy(
        update={"state": StepStateFact.COMPLETED, "current_attempt_id": old.attempt_id}
    )
    next_step = step.model_copy(
        update={
            "step_id": "step-2",
            "current_attempt_id": None,
            "dependency_step_ids": (step.step_id,),
            "state": StepStateFact.PENDING,
        }
    )
    current = base.model_copy(update={"steps": (step, next_step)})
    ExecutionCommitCoordinator._require_serial_start(
        current, replace(old, attempt_id="next-attempt", step_id="step-2"), (old,)
    )


def test_dependency_admission_happens_in_start_transaction_before_occupation(tmp_path, monkeypatch):
    from aitest.application.execution.runner import SerialRunner
    from tests.support.fake_execution import FakeExecutionPort
    from tests.unit.test_serial_execution_slices import coordinator, items

    saved = coordinator(tmp_path)
    saved._serial_execution = True
    port = FakeExecutionPort()
    before = saved._uow.current_commit_sequence()
    admission = saved._require_serial_start
    contexts = []

    def inside(*args):
        contexts.append(saved._uow.project)
        return admission(*args)

    monkeypatch.setattr(saved, "_require_serial_start", inside)
    runner = SerialRunner(port, commit_coordinator=saved)
    with pytest.raises(ValueError, match="completed, captured"):
        runner.start_attempt(items()[1].attempt, items()[1].request)
    assert contexts == [items()[1].request.project_id]
    assert saved._uow.current_commit_sequence() == before
    assert saved._uow.project is None
    assert not port.execution_order


def test_saved_consent_is_consumed_by_default_api_once_and_recalled_without_new_port(
    authoritative, monkeypatch
):
    core, inputs, source = authoritative
    facts = register(core, prepare(core, inputs).result)
    service = core.execution_authorizations
    service.action_resolver = ActualCommandResolver(core.unit_of_work)
    parameters = service.prepare(
        project_id=inputs.project_id,
        run_id=facts.run_id,
        step_id=facts.steps[0].step_id,
        intent_id="actual-execution-intent",
        request_id="resolve-actual",
    )
    _, action = service.resolver.read(inputs.project_id, parameters["execution_action_id"])
    actor, challenge = review(service, inputs.project_id, action, parameters)
    save(service, inputs.project_id, action, parameters, actor, challenge)
    value = execution_command(inputs.project_id, action, parameters)
    before = core.unit_of_work.current_commit_sequence()
    missing = core.api.dispatch(value, RELAY)
    assert missing.error.code == "CAPABILITY_UNAVAILABLE"
    assert core.unit_of_work.current_commit_sequence() == before
    assert (
        service._state(inputs.project_id, action.request.authorization_ref.authorization_id)[1]
        is AuthorizationState.UNUSED
    )

    port = CommandAdapter(handle_store=FileExecutionHandleStore(core.workspace.root))
    port.register(CommandRegistration("public-python", sys.executable, source))
    core.step_execution.execution_port = port
    original_start = port.start
    starts = []

    def start(request):
        assert core.unit_of_work.project is None
        saved = core.execution_coordinator.read_checkpoint(
            project_id=inputs.project_id, attempt_id=request.attempt_id
        )
        assert saved.attempt.state is AttemptState.INTENT_RECORDED
        assert (
            service._state(inputs.project_id, request.authorization_ref.authorization_id)[1]
            is AuthorizationState.OCCUPIED
        )
        starts.append(request.attempt_id)
        return original_start(request)

    monkeypatch.setattr(port, "start", start)
    result = core.api.dispatch(
        value.model_copy(update={"request_id": "configured-execution"}), RELAY
    )
    assert result.error is None, result.error
    assert result.result["attempt"]["state"] == "completed"
    assert result.result["attempt"]["capture_completeness"] == "complete"
    assert starts == [action.attempt.attempt_id]
    spool = FileSpoolStore(core.workspace.root)
    manifest = spool.read_manifest(action.attempt.attempt_id)
    assert b"api-output" in b"".join(spool.read_block(block) for block in manifest.blocks)
    assert not result.result["execution_facts"]["verifications"]
    assert result.result["execution_facts"]["run"]["result_ref"] is None
    before = core.unit_of_work.current_commit_sequence()
    repeated = core.api.dispatch(value.model_copy(update={"request_id": "repeat-execution"}), RELAY)
    assert repeated.result == result.result and starts == [action.attempt.attempt_id]
    assert core.unit_of_work.current_commit_sequence() == before

    core.lifetime_lock.release()
    (source / "main.py").write_bytes(b"VALUE = 9\n")
    restarted = assemble_workspace_core(core.workspace.root, instance_id="execute-recall")
    try:
        before = restarted.unit_of_work.current_commit_sequence()
        recalled = restarted.api.dispatch(
            value.model_copy(update={"request_id": "restart-execution"}), RELAY
        )
        assert recalled.error is None, recalled.error
        assert recalled.result == result.result
        assert restarted.unit_of_work.current_commit_sequence() == before
        assert restarted.step_execution.execution_port is None
        current = recalled.result["execution_facts"]
        saved_facts = ExecutionFacts.model_validate(current)
        repo = restarted.unit_of_work.repo
        reuse_reader = CaseReuseSourceReader(
            repo, PortsRecordReader(repo), objects=FileObjectStore(restarted.workspace.root),
            spool=FileSpoolStore(restarted.workspace.root),
        )
        source_proof = reuse_reader.read(
            project_id=inputs.project_id, run_id=saved_facts.run_id,
            case_id=saved_facts.steps[0].case_id, reference=SnapshotContentRef.of(saved_facts),
        )
        assert source_proof.facts == saved_facts
        assert source_proof.steps[0].attempt.attempt_id == action.attempt.attempt_id
        with pytest.raises(ValueError, match="output_material"):
            CaseReuseSourceReader(repo, PortsRecordReader(repo)).read(
                project_id=inputs.project_id, run_id=saved_facts.run_id,
                case_id=saved_facts.steps[0].case_id, reference=SnapshotContentRef.of(saved_facts),
            )
        # Corrupt actual temporary project bytes, retaining all saved metadata.
        block = source_proof.steps[0].attempt.output_blocks[0]
        object_path = (
            restarted.workspace.root / "spool" / block.attempt_id
            / (block.stream_name + ".log")
        ).resolve()
        assert object_path.is_relative_to(restarted.workspace.root.resolve())
        saved_bytes = object_path.read_bytes()
        try:
            object_path.write_bytes(b"corrupt saved source output")
            with pytest.raises(ValueError, match="output_material"):
                reuse_reader.read(
                    project_id=inputs.project_id, run_id=saved_facts.run_id,
                    case_id=saved_facts.steps[0].case_id,
                    reference=SnapshotContentRef.of(saved_facts),
                )
        finally:
            object_path.write_bytes(saved_bytes)
        assert reuse_reader.read(
            project_id=inputs.project_id, run_id=saved_facts.run_id,
            case_id=saved_facts.steps[0].case_id, reference=SnapshotContentRef.of(saved_facts),
        ) == source_proof
        refs = read_checkpoint_refs(repo, saved_facts)
        assert refs[action.attempt.attempt_id].revision >= 1
        original_revision = repo.current_revision
        with monkeypatch.context() as patch:
            patch.setattr(repo, "current_revision", lambda **kwargs: 0 if (
                kwargs["aggregate_kind"] == KIND
            ) else original_revision(**kwargs))
            with pytest.raises(ValueError, match="checkpoint reference map"):
                reuse_reader.read(
                    project_id=inputs.project_id, run_id=saved_facts.run_id,
                    case_id=saved_facts.steps[0].case_id,
                    reference=SnapshotContentRef.of(saved_facts),
                )
            assert restarted.execution_coordinator.read_current_facts(
                project_id=inputs.project_id, run_id=saved_facts.run_id,
            ) == saved_facts
        assert restarted.unit_of_work.current_commit_sequence() == before
        controls = []
        for name, state in (
            ("pause_run", "paused"),
            ("resume_run", "running"),
            ("cancel_run", "cancelled"),
        ):
            control = Command(
                action=name,
                project_id=inputs.project_id,
                request_id="public-" + name,
                intent_id="control-" + name,
                target=current["run_id"],
                expected_revision=0,
                parameters={
                    "run_id": current["run_id"],
                    "base_snapshot_commit_id": current["snapshot_commit_id"],
                },
            )
            response = restarted.api.dispatch(control, RELAY)
            assert response.error is None, response.error
            current = response.result
            assert current["run"]["control_state"] == state
            assert not current["verifications"] and current["run"]["result_ref"] is None
            controls.append((control, current))
        before = restarted.unit_of_work.current_commit_sequence()
        replay = restarted.api.dispatch(
            controls[0][0].model_copy(update={"request_id": "old-public-pause"}), RELAY
        )
        assert replay.result == controls[0][1]
        assert restarted.unit_of_work.current_commit_sequence() == before
        assert (
            restarted.execution_coordinator.read_current_facts(
                project_id=inputs.project_id, run_id=current["run_id"]
            ).run.control_state.value
            == "cancelled"
        )
    finally:
        restarted.lifetime_lock.release()
