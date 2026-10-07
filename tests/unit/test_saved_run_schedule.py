"""Saved serial scheduling: fixture gestures/loading, actual file transactions."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.execution.run_schedule import SavedRunSchedule, dispatch_plan
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import RunControlStateFact, StepStateFact
from aitest.contracts.prepared_run import RunDriverFact
from aitest.domain.execution.authorization import AuthorizationState
from aitest.domain.execution.runs import AttemptState
from tests.support.environment_resolution import FixtureEnvironmentResolver
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_current_execution_snapshot import _batch
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_execution_authorization_origin import FixtureActionResolver, review, save
from tests.unit.test_initial_run_registration import register
from tests.unit.test_serial_runner import _attempt, _request


def schedule_command(facts, *, intent="schedule-intent", request="schedule-request"):
    return Command(
        action="start_run",
        project_id=facts.project_id,
        request_id=request,
        intent_id=intent,
        target=facts.run_id,
        expected_revision=0,
        parameters={"run_id": facts.run_id, "base_snapshot_commit_id": facts.snapshot_commit_id},
    )


class Resolver(FixtureActionResolver):
    def resolve(self, **kwargs):
        action = super().resolve(**kwargs)
        return replace(
            action,
            attempt=replace(action.attempt, adapter_version=FakeExecutionPort.adapter_version),
        )


def grant_step(core, facts, step, *, intent, running=0):
    service = core.execution_authorizations
    parameters = service.prepare(
        project_id=facts.project_id,
        run_id=facts.run_id,
        step_id=step.step_id,
        intent_id=intent,
        request_id="prepare-" + intent,
    )
    _, action = service.resolver.read(facts.project_id, parameters["execution_action_id"])
    actor, challenge = review(service, facts.project_id, action, parameters)
    actor = replace(
        actor, interaction=replace(actor.interaction, interaction_id="gesture-" + intent)
    )
    save(service, facts.project_id, action, parameters, actor, challenge, request="grant-" + intent)
    core.step_execution.execution_port.register(
        FakeExecutionSpec(
            action.attempt.attempt_id,
            facts.run_id,
            step.step_id,
            running_observations_before_exit=running,
        )
    )
    return action


@pytest.mark.parametrize(
    "parameters",
    [
        {"run_id": "run", "base_snapshot_commit_id": "commit", "command": "injected"},
        {"run_id": "run"},
        {"run_id": "", "base_snapshot_commit_id": "commit"},
        {"run_id": "run", "base_snapshot_commit_id": 1},
    ],
)
def test_schedule_rejects_executable_input_before_storage(parameters):
    coordinator, execution = Mock(), Mock()
    service = SavedRunSchedule(coordinator, execution, "workspace")
    command = Command(
        action="start_run",
        project_id="project",
        intent_id="intent",
        request_id="request",
        target="run",
        expected_revision=0,
        parameters=parameters,
    )
    with pytest.raises(ValueError) as error:
        service.apply(command)
    assert error.value.code == "INVALID_REQUEST"
    assert not coordinator.mock_calls and not execution.mock_calls


@pytest.mark.parametrize(
    "state", ["pending", "running", "completed", "invalidated", "execution_error"]
)
def test_schedule_dependency_projection_uses_the_shared_domain_rule(state):
    facts = _batch().facts
    first = facts.steps[0].model_copy(update={"state": StepStateFact(state)})
    dependent = first.model_copy(
        update={
            "step_id": "dependent",
            "state": StepStateFact.PENDING,
            "ordinal": first.ordinal + 1,
            "dependency_step_ids": (first.step_id,),
        }
    )
    result = dispatch_plan(facts.model_copy(update={"steps": (dependent, first)}))
    if state == "completed":
        assert result.ready_step_ids == (dependent.step_id,)
    elif state in {"invalidated", "execution_error"}:
        assert dependent.step_id in result.blocked_step_ids
    else:
        assert dependent.step_id in result.waiting_step_ids


@pytest.mark.parametrize(
    "state",
    [RunControlStateFact.PAUSED, RunControlStateFact.CANCELLED, RunControlStateFact.RECOVERING],
)
def test_saved_schedule_cannot_release_later_controls(state):
    facts = _batch().facts
    facts = facts.model_copy(update={"run": facts.run.model_copy(update={"control_state": state})})
    service = SavedRunSchedule(Mock(), Mock(), facts.run.origin_workspace_id)
    service._current = Mock(return_value=facts)
    result = service._advance(facts.project_id, facts.run_id, "original-schedule")
    assert result["status"] == "controlled"
    assert result["execution_facts"]["run"]["control_state"] == state.value
    assert not service.execution.mock_calls and not service.unit.mock_calls


def test_saved_schedule_cannot_restore_planned_driver():
    facts = _batch().facts
    facts = facts.model_copy(
        update={
            "run": facts.run.model_copy(
                update={
                    "driver": RunDriverFact.STEPWISE,
                    "control_state": RunControlStateFact.RUNNING,
                }
            )
        }
    )
    service = SavedRunSchedule(Mock(), Mock(), facts.run.origin_workspace_id)
    service._current = Mock(return_value=facts)
    assert service._advance(facts.project_id, facts.run_id, "original")["status"] == "controlled"
    assert not service.execution.mock_calls


def advance_service(facts):
    facts = facts.model_copy(
        update={"run": facts.run.model_copy(update={"control_state": RunControlStateFact.RUNNING})}
    )
    service = SavedRunSchedule(Mock(), Mock(), facts.run.origin_workspace_id)
    service._current = Mock(return_value=facts)
    service._attempts = Mock(return_value=())
    return service, facts


def test_unknown_handleless_activity_does_not_hide_other_historical_activity():
    service, facts = advance_service(_batch().facts)
    from tests.unit.test_execution_observation_identity import observation

    active, _, _ = observation()
    unknown = replace(
        active, attempt_id="unknown", execution_handle_ref=None, state=AttemptState.UNKNOWN
    )
    historical = replace(active, state=AttemptState.INVALIDATED, exit_fact_ref=None)
    service._attempts.return_value = (unknown, historical)
    action = Mock()
    action.request.intent_id, action.request.step_id = "original-intent", historical.step_id
    service._original_action = Mock(return_value=("original-action", action))
    result = service._advance(facts.project_id, facts.run_id, "schedule")
    assert result["status"] == "pending_verification"
    assert result["observed_attempt_ids"] == [historical.attempt_id]
    assert not result["dispatched_attempt_ids"]
    service.execution.execute.assert_called_once_with(
        project_id=facts.project_id,
        intent_id="original-intent",
        action_id="original-action",
        step_id=historical.step_id,
        max_polls=1,
    )


def test_schedule_does_not_clear_pending_verification_when_activity_ends():
    service, facts = advance_service(_batch().facts)
    facts = facts.model_copy(
        update={
            "run": facts.run.model_copy(
                update={"control_state": RunControlStateFact.PENDING_VERIFICATION}
            )
        }
    )
    service._current.return_value = facts
    assert (
        service._advance(facts.project_id, facts.run_id, "schedule")["status"]
        == "pending_verification"
    )
    assert not service.execution.mock_calls and not service.unit.mock_calls


def test_empty_run_does_not_become_completed():
    service, facts = advance_service(_batch().facts)
    service._current.return_value = facts.model_copy(update={"steps": ()})
    service._unused_actions = Mock(return_value={})
    assert (
        service._advance(facts.project_id, facts.run_id, "schedule")["status"]
        == "waiting_dependencies"
    )
    assert not service.execution.mock_calls and not service.unit.mock_calls


def ready_service():
    service, facts = advance_service(_batch().facts)
    facts = facts.model_copy(
        update={"steps": (facts.steps[0].model_copy(update={"state": StepStateFact.PENDING}),)}
    )
    service._current.return_value = facts
    action = Mock(attempt=_attempt(), request=_request())
    return service, facts, action


def test_multiple_unused_grants_do_not_select_arbitrary_execution():
    service, facts, action = ready_service()
    service._unused_actions = Mock(
        return_value={facts.steps[0].step_id: [("first", action), ("second", action)]}
    )
    assert (
        service._advance(facts.project_id, facts.run_id, "schedule")["status"]
        == "ambiguous_authorization"
    )
    assert not service.execution.mock_calls


def test_control_changed_during_adapter_callback_stops_next_dispatch():
    service, facts, action = ready_service()
    service._unused_actions = Mock(return_value={facts.steps[0].step_id: [("saved", action)]})

    def execute(**kwargs):
        service._current.return_value = facts.model_copy(
            update={
                "run": facts.run.model_copy(update={"control_state": RunControlStateFact.PAUSED})
            }
        )

    service.execution.execute.side_effect = execute
    assert service._advance(facts.project_id, facts.run_id, "schedule")["status"] == "controlled"
    assert service.execution.execute.call_count == 1
    assert not service.unit.mock_calls


def test_schedule_slice_has_a_finite_budget(monkeypatch):
    import aitest.application.execution.run_schedule as module

    service, facts, action = ready_service()
    service._unused_actions = Mock(return_value={facts.steps[0].step_id: [("saved", action)]})
    monkeypatch.setattr(module, "_SLICE_BUDGET", 1)
    result = service._advance(facts.project_id, facts.run_id, "schedule")
    assert result["status"] == "slice_exhausted"
    service.execution.execute.assert_called_once()
    assert service.execution.execute.call_args.kwargs["max_polls"] == 1


def test_authorization_limit_blocks_before_reading_individual_grants(monkeypatch):
    import aitest.application.execution.run_schedule as module

    service, facts, _ = ready_service()
    monkeypatch.setattr(
        module,
        "read_authorization_index",
        Mock(return_value=(1, tuple(str(i) for i in range(101)))),
    )
    with pytest.raises(ValueError, match="bounded schedule limit"):
        service._unused_actions(facts)
    assert not service.execution.authorizations._origin.mock_calls


@pytest.mark.parametrize("problem", ["no_exit", "gap", "history_active"])
def test_completed_run_label_requires_reliable_current_and_historical_boundary(problem):
    facts = _batch().facts
    service = SavedRunSchedule(Mock(), Mock(), facts.run.origin_workspace_id)
    service._current = Mock(return_value=facts)
    saved = _batch().checkpoint.attempt
    if problem == "no_exit":
        saved = replace(saved, exit_fact_ref=None)
    elif problem == "gap":
        from aitest.domain.execution.runs import CaptureCompleteness

        saved = replace(saved, capture_completeness=CaptureCompleteness.GAP)
    attempts = (saved,)
    if problem == "history_active":
        attempts += (
            replace(
                saved, attempt_id="old-live", state=AttemptState.INVALIDATED, exit_fact_ref=None
            ),
        )
    service._attempts = Mock(return_value=attempts)
    result = service._advance(facts.project_id, facts.run_id, "original-schedule")
    assert result["status"] == "unverified_completion"
    assert not service.execution.mock_calls and not service.unit.mock_calls


def test_reliable_completed_run_recall_proves_original_intent_without_adapter():
    from aitest.domain.execution.runs import CaptureCompleteness
    from tests.unit.test_execution_observation_identity import observation

    saved, _, collection = observation()
    saved = replace(
        saved,
        state=AttemptState.COMPLETED,
        exit_fact_ref=collection.exit_fact_ref,
        capture_completeness=CaptureCompleteness.COMPLETE,
    )
    facts = _batch().facts
    facts = facts.model_copy(
        update={
            "steps": (facts.steps[0].model_copy(update={"current_attempt_id": saved.attempt_id}),)
        }
    )
    service = SavedRunSchedule(Mock(), Mock(), facts.run.origin_workspace_id)
    service._current = Mock(return_value=facts)
    service._attempts = Mock(return_value=(saved,))
    service._original_action = Mock()
    assert (
        service._advance(facts.project_id, facts.run_id, "original")["status"]
        == "execution_completed"
    )
    service._original_action.assert_called_once_with(facts.project_id, saved)
    assert not service.execution.mock_calls and not service.unit.mock_calls


def test_default_schedule_resumes_original_activity_and_completes_without_business_pass(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    facts = register(core, prepared)
    core.execution_authorizations.action_resolver = Resolver(core.unit_of_work)
    port = FakeExecutionPort()
    core.step_execution.execution_port = port
    coordinator = core.execution_coordinator
    value = schedule_command(facts)

    empty = core.api.dispatch(value, RELAY)
    assert empty.error is None, empty.error
    assert empty.result["status"] == "waiting_authorization"
    assert not empty.result["execution_facts"]["attempts"] and not port.execution_order
    admission_id = empty.result["schedule_intent_id"]
    assert (
        core.unit_of_work.repo.current_revision(
            aggregate_kind="execution_intent", record_id=admission_id
        )
        == 1
    )
    before = core.unit_of_work.current_commit_sequence()
    conflict = core.api.dispatch(
        value.model_copy(
            update={
                "request_id": "changed-base",
                "parameters": {"run_id": facts.run_id, "base_snapshot_commit_id": "foreign"},
            }
        ),
        RELAY,
    )
    assert conflict.error.code == "INTENT_CONFLICT"
    assert core.unit_of_work.current_commit_sequence() == before

    actions = [
        grant_step(
            core, facts, step, intent="run-step-" + str(index), running=2 if index == 0 else 0
        )
        for index, step in enumerate(facts.steps)
    ]
    original_start = port.start

    def start(request):
        assert core.unit_of_work.project is None
        assert (
            core.execution_authorizations._state(
                facts.project_id, request.authorization_ref.authorization_id
            )[1]
            is AuthorizationState.OCCUPIED
        )
        return original_start(request)

    monkeypatch.setattr(port, "start", start)
    first = core.api.dispatch(value.model_copy(update={"request_id": "first-slice"}), RELAY)
    assert first.error is None, first.error
    assert first.result["status"] == "active"
    assert port.execution_order == [actions[0].attempt.attempt_id]
    current = coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
    pause = core.api.dispatch(
        Command(
            action="pause_run",
            project_id=facts.project_id,
            intent_id="pause-schedule",
            request_id="pause-schedule-request",
            expected_revision=0,
            target=facts.run_id,
            parameters={
                "run_id": facts.run_id,
                "base_snapshot_commit_id": current.snapshot_commit_id,
            },
        ),
        RELAY,
    )
    assert pause.error is None, pause.error
    assert pause.result["run"]["control_state"] == "pause_requested"
    before = core.unit_of_work.current_commit_sequence()
    controlled = core.api.dispatch(value.model_copy(update={"request_id": "paused-slice"}), RELAY)
    assert controlled.error is None, controlled.error
    assert controlled.result["status"] == "controlled"
    assert core.unit_of_work.current_commit_sequence() == before
    assert len(port.execution_order) == 1
    core.continue_work()
    current = coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
    assert current.run.control_state is RunControlStateFact.PAUSED
    assert not core.unit_of_work.repo.active_execution_schedules(
        workspace_id=core.workspace.workspace_id
    )
    assert len(port.execution_order) == 1
    current = coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
    resumed = core.api.dispatch(
        # The original pause is first settled through the worker's main-thread
        # hook; its active inventory must disappear without another start.
        Command(
            action="resume_run",
            project_id=facts.project_id,
            intent_id="resume-schedule",
            request_id="resume-schedule-request",
            expected_revision=0,
            target=facts.run_id,
            parameters={
                "run_id": facts.run_id,
                "base_snapshot_commit_id": current.snapshot_commit_id,
            },
        ),
        RELAY,
    )
    assert resumed.error is None, resumed.error
    assert resumed.result["run"]["control_state"] == "running"
    assert (
        len(
            core.unit_of_work.repo.active_execution_schedules(
                workspace_id=core.workspace.workspace_id
            )
        )
        == 1
    )
    core.lifetime_lock.release()
    core = assemble_workspace_core(
        core.workspace.root,
        instance_id="schedule-continued",
        execution_port=port,
        environment_resolver=FixtureEnvironmentResolver(),
    )
    core.execution_authorizations.action_resolver = Resolver(core.unit_of_work)
    coordinator = core.execution_coordinator
    for _ in range(len(actions) * 3 + 5):
        previous_count = len(port.execution_order)
        core.continue_work()
        assert len(port.execution_order) <= previous_count + 1
        latest = coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
        if latest.run.control_state is RunControlStateFact.COMPLETED:
            break
    else:
        pytest.fail("bounded original-work ticks did not complete the saved Run")
    assert not core.unit_of_work.repo.active_execution_schedules(
        workspace_id=core.workspace.workspace_id
    )
    assert port.execution_order == [action.attempt.attempt_id for action in actions]
    final = coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
    assert final.run.control_state is RunControlStateFact.COMPLETED
    assert final.run.result_ref is None and final.run.evidence_level is None
    assert not final.verifications and not final.source_verifications
    assert all(
        coordinator.read_checkpoint(
            project_id=facts.project_id, attempt_id=action.attempt.attempt_id
        ).attempt.state
        is AttemptState.COMPLETED
        for action in actions
    )
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(core.workspace.root, instance_id="schedule-recalled")
    try:
        before = restarted.unit_of_work.current_commit_sequence()
        recalled = restarted.api.dispatch(
            value.model_copy(update={"request_id": "recalled-after-restart"}), RELAY
        )
        assert recalled.error is None, recalled.error
        assert recalled.result["status"] == "execution_completed"
        assert recalled.result["execution_facts"] == final.model_dump(mode="json")
        assert restarted.unit_of_work.current_commit_sequence() == before
    finally:
        restarted.lifetime_lock.release()
