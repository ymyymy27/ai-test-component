"""Checkpoint observations cannot withdraw a saved run control decision."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.current import project_current_update
from aitest.application.execution.runner import SerialRunner
from aitest.contracts.execution_facts import RunControlStateFact, StepStateFact
from aitest.domain.execution.runs import AttemptState, StepRevisionRef
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import SavedFixtureExecutionAuthority, fixture_coordinator
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_current_execution_snapshot import _batch
from tests.unit.test_serial_runner import _attempt, _request

CONTROLLED_STATES = tuple(
    state
    for state in RunControlStateFact
    if state not in {RunControlStateFact.NOT_STARTED, RunControlStateFact.RUNNING}
)


@pytest.mark.parametrize("control", CONTROLLED_STATES)
@pytest.mark.parametrize(
    "state", [AttemptState.RUNNING, AttemptState.COLLECTING, AttemptState.UNKNOWN]
)
def test_observation_preserves_saved_control_and_updates_step_facts(control, state):
    batch = _batch()
    before = batch.facts.model_copy(
        update={"run": batch.facts.run.model_copy(update={"control_state": control})}
    )
    attempt = replace(batch.checkpoint.attempt, state=state)
    after = project_current_update(before, attempt, committed_at=datetime.now(UTC))
    assert after.run.control_state is control
    assert after.attempts[0].state.value == state.value
    expected_step = (
        StepStateFact.PENDING_VERIFICATION
        if state is AttemptState.UNKNOWN
        else StepStateFact.RUNNING
    )
    assert after.steps[0].state is expected_step
    assert after.coverage.unknown_step_ids == (
        (attempt.step_id,) if state is AttemptState.UNKNOWN else ()
    )
    assert not after.coverage.executed_attempt_ids
    assert after.run.result_ref is None
    assert before.run.control_state is control


@pytest.mark.parametrize("control", CONTROLLED_STATES)
def test_one_completed_attempt_does_not_confirm_a_global_control_boundary(control):
    batch = _batch()
    before = batch.facts.model_copy(
        update={"run": batch.facts.run.model_copy(update={"control_state": control})}
    )
    after = project_current_update(before, batch.checkpoint.attempt, committed_at=datetime.now(UTC))
    assert after.run.control_state is control
    assert after.steps[0].state is StepStateFact.COMPLETED


@pytest.mark.parametrize("control", [RunControlStateFact.NOT_STARTED, RunControlStateFact.RUNNING])
@pytest.mark.parametrize("state", [AttemptState.RUNNING, AttemptState.UNKNOWN])
def test_admitted_run_still_projects_running_or_unknown_execution(control, state):
    batch = _batch()
    before = batch.facts.model_copy(
        update={"run": batch.facts.run.model_copy(update={"control_state": control})}
    )
    after = project_current_update(
        before, replace(batch.checkpoint.attempt, state=state), committed_at=datetime.now(UTC)
    )
    assert after.run.control_state is (
        RunControlStateFact.PENDING_VERIFICATION
        if state is AttemptState.UNKNOWN
        else RunControlStateFact.RUNNING
    )


@pytest.mark.parametrize(
    "control",
    [
        RunControlStateFact.PAUSE_REQUESTED,
        RunControlStateFact.CANCELLING,
        RunControlStateFact.RECOVERING,
        RunControlStateFact.PENDING_VERIFICATION,
    ],
)
def test_saved_active_collection_cannot_reopen_dispatch_after_restart(tmp_path, control):
    first, request = _attempt(), _request()
    second = replace(
        first,
        attempt_id="attempt-2",
        step_id="step-2",
        intent_id="intent-2",
        step_revision_ref=StepRevisionRef("step-revision-2", 1, "sha256:step-revision-2"),
    )
    second_request = replace(
        request,
        attempt_id=second.attempt_id,
        step_id=second.step_id,
        intent_id=second.intent_id,
        authorization_ref=replace(
            request.authorization_ref,
            authorization_id="authorization-2",
            step_id=second.step_id,
            intent_id=second.intent_id,
            step_revision_ref=second.step_revision_ref,
        ),
    )
    unit = FileUnitOfWork(tmp_path)
    coordinator = fixture_coordinator(unit, ((first, request), (second, second_request)))
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=5)
    )
    port.register(FakeExecutionSpec("attempt-2", "run-1", "step-2"))
    runner = SerialRunner(port, commit_coordinator=coordinator, poll_interval_seconds=0)
    active = runner.execute_attempt(first, request, max_polls=1)
    assert active.state is AttemptState.RUNNING
    saved = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    # Fixture setup saves a control decision; this is not a real pause/cancel UI acceptance.
    unit.begin("fixture-saved-control", "project-1")
    coordinator._stage_snapshot(
        saved.model_copy(update={"run": saved.run.model_copy(update={"control_state": control})})
    )
    unit.commit()
    restarted_unit = FileUnitOfWork(tmp_path)
    restarted = ExecutionCommitCoordinator(
        restarted_unit, execution_authorizations=SavedFixtureExecutionAuthority(restarted_unit)
    )
    resumed = SerialRunner(port, commit_coordinator=restarted, poll_interval_seconds=0)
    collected = resumed.execute_attempt(active, request, max_polls=1)
    assert collected.state is AttemptState.RUNNING
    after = restarted.read_current_facts(project_id="project-1", run_id="run-1")
    assert after.run.control_state is control
    assert after.current_attempt_by_step == saved.current_attempt_by_step
    assert after.current_attempt_by_step["step-2"] is None
    sequence = restarted_unit.current_commit_sequence()
    with pytest.raises(ValueError, match="current run control state"):
        resumed.start_attempt(second, second_request)
    assert port.execution_order == ["attempt-1"]
    assert restarted_unit.current_commit_sequence() == sequence
    assert (
        restarted_unit.current_revision(
            aggregate_kind="execution_checkpoint", record_id="attempt-2"
        )
        == 0
    )
    assert (
        restarted_unit.current_revision(
            aggregate_kind="execution_authorization",
            record_id=SavedFixtureExecutionAuthority.key("authorization-2") + ":state",
        )
        == 1
    )
    assert unit.read(
        aggregate_kind="execution_facts", record_id=saved.snapshot_commit_id, revision=1
    ).payload == saved.model_dump(mode="json")
