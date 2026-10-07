"""Default API with real file records and synthetic trusted resolver/user events."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.approvals import EntryKind
from aitest.domain.execution.authorization import AuthorizationState
from aitest.interfaces.local.api import Session
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_execution_authorization_origin import FixtureActionResolver

HUMAN = Session("execution-public-fixture", EntryKind.HUMAN_UI, True)
RELAY = Session("execution-relay-fixture", EntryKind.AGENT_RELAY)


def command(action, *, parameters, request="request", intent="execute-intent", target="step"):
    return Command(
        action=action,
        request_id=request,
        project_id="project",
        intent_id=intent,
        expected_revision=0,
        target=target,
        parameters=parameters,
    )


def test_default_capabilities_and_missing_resolver_are_honest(tmp_path):
    core = assemble_workspace_core(tmp_path / "workspace", instance_id="public-execution")
    try:
        doctor = core.api.dispatch(Command(action="doctor", request_id="doctor"), RELAY)
        assert {"register_run", "prepare_execution", "authorize_step"} <= set(
            doctor.result["supported_actions"]
        )
        assert "start_run" in doctor.result["supported_actions"]
        assert "verify_pending" in doctor.result["supported_actions"]
        before = core.unit_of_work.current_commit_sequence()
        result = core.api.dispatch(
            command("prepare_execution", parameters={"run_id": "run", "step_id": "step"}),
            RELAY,
        )
        assert result.error.code == "CAPABILITY_UNAVAILABLE"
        assert core.unit_of_work.current_commit_sequence() == before
        missing = core.api.dispatch(
            command(
                "register_run",
                request="missing-registration",
                target="missing",
                parameters={
                    "prepared_run_id": "missing",
                    "record_revision": 1,
                },
            ),
            RELAY,
        )
        assert missing.error.code == "RUN_REGISTRATION_BLOCKED"
        assert core.unit_of_work.current_commit_sequence() == before
    finally:
        core.lifetime_lock.release()


@pytest.mark.parametrize("action", ["register_run", "prepare_execution", "authorize_step"])
def test_default_execution_actions_cannot_be_overridden(tmp_path, action):
    with pytest.raises(ValueError, match="conflicts with built-in"):
        assemble_workspace_core(
            tmp_path / "workspace",
            instance_id="conflicting-execution",
            extra_handlers={action: lambda _: {"forged": True}},
        )


@pytest.mark.parametrize("action", ["register_run", "prepare_execution"])
@pytest.mark.parametrize("missing", ["project_id", "intent_id", "expected_revision"])
def test_preparation_is_a_known_write_action(missing, action):
    from pydantic import ValidationError

    value = command(action, parameters={"run_id": "run", "step_id": "step"})
    raw = value.model_dump()
    raw.pop(missing)
    with pytest.raises(ValidationError, match="write actions require"):
        Command.model_validate(raw)


@pytest.mark.parametrize(
    ("action", "parameters", "target", "expected"),
    [
        ("register_run", {"prepared_run_id": "prepared", "record_revision": True}, "prepared", 0),
        ("register_run", {"prepared_run_id": "prepared", "record_revision": 1}, "other", 0),
        ("register_run", {"prepared_run_id": "prepared", "record_revision": 1}, "prepared", 1),
        (
            "register_run",
            {"prepared_run_id": "prepared", "record_revision": 1, "run": {}},
            "prepared",
            0,
        ),
        ("prepare_execution", {"run_id": "run", "step_id": "step", "command": "echo"}, "step", 0),
        ("prepare_execution", {"run_id": "", "step_id": "step"}, "step", 0),
        ("prepare_execution", {"run_id": "run", "step_id": "step"}, "other", 0),
        ("prepare_execution", {"run_id": "run", "step_id": "step"}, None, 0),
        ("prepare_execution", {"run_id": "run", "step_id": "step"}, "step", 1),
        (
            "authorize_step",
            {
                "execution_action_id": "action",
                "record_revision": True,
                "approval_challenge_id": "challenge",
            },
            "step",
            0,
        ),
        (
            "authorize_step",
            {
                "execution_action_id": "action",
                "record_revision": 2,
                "approval_challenge_id": "challenge",
            },
            "step",
            0,
        ),
        (
            "authorize_step",
            {"execution_action_id": "action", "record_revision": 1, "approval_challenge_id": ""},
            "step",
            0,
        ),
        (
            "authorize_step",
            {
                "execution_action_id": "action",
                "record_revision": 1,
                "approval_challenge_id": "challenge",
                "user_confirmed": True,
            },
            "step",
            0,
        ),
        (
            "authorize_step",
            {
                "execution_action_id": "action",
                "record_revision": 1,
                "approval_challenge_id": "challenge",
            },
            None,
            0,
        ),
        (
            "authorize_step",
            {
                "execution_action_id": "action",
                "record_revision": 1,
                "approval_challenge_id": "challenge",
            },
            "step",
            1,
        ),
    ],
)
def test_invalid_public_shapes_fail_before_service_effects(action, parameters, target, expected):
    from aitest.application.execution.commands import ExecutionCommands

    service = Mock()
    registration = Mock()
    commands = ExecutionCommands(service, registration)
    value = command(action, parameters=parameters, target=target).model_copy(
        update={"expected_revision": expected}
    )
    with pytest.raises(ValueError) as caught:
        {
            "register_run": commands.register,
            "prepare_execution": commands.prepare,
            "authorize_step": commands.grant,
        }[action](value)
    assert caught.value.code == "INVALID_REQUEST"
    assert service.mock_calls == []
    assert registration.mock_calls == []


def test_grant_target_is_checked_against_saved_step():
    from aitest.application.execution.commands import ExecutionCommands

    service = Mock()
    service.resolver.read.return_value = (
        {},
        SimpleNamespace(request=SimpleNamespace(step_id="other")),
    )
    value = command(
        "authorize_step",
        parameters={
            "execution_action_id": "action",
            "record_revision": 1,
            "approval_challenge_id": "challenge",
        },
    )
    with pytest.raises(ValueError) as caught:
        ExecutionCommands(service).grant(value)
    assert caught.value.code == "INVALID_REQUEST"
    service.grant.assert_not_called()


@pytest.mark.parametrize("inside_transaction", [False, True])
def test_saved_grant_recall_rejects_a_different_challenge(inside_transaction):
    from aitest.application.execution.authorization import ExecutionAuthorizationService
    from aitest.domain.approvals import ApprovalConflict

    service = object.__new__(ExecutionAuthorizationService)
    service.approvals = Mock()
    service.approvals.read_confirmation.return_value = SimpleNamespace(challenge_id="original")
    service.resolver = Mock()
    from tests.unit.test_serial_runner import _request

    action = SimpleNamespace(request=_request())
    service.resolver.read.return_value = ({}, action)
    parameters = {"execution_action_id": "action", "record_revision": 1}
    service.unit = Mock()
    service._actual = Mock()
    service._current_action = Mock()
    service._revision = Mock(side_effect=[0, 1] if inside_transaction else [1])
    service._origin = Mock(
        return_value=({"parameters": parameters, "confirmation_id": "proof"}, action)
    )
    with pytest.raises(ApprovalConflict, match="challenge"):
        service.grant(
            project_id=action.request.project_id,
            intent_id=action.request.intent_id,
            request_id="different-challenge",
            parameters=parameters,
            challenge_id="changed",
        )
    service.unit.stage_record.assert_not_called()
    service.unit.commit.assert_not_called()
    if inside_transaction:
        service.unit.rollback.assert_called_once_with("different-challenge")


def test_public_prepare_consent_grant_and_restart_preserve_originals(authoritative):
    core, inputs, source = authoritative
    from aitest.contracts.execution_facts import ExecutionFacts

    prepared = prepare(core, inputs).result
    register_command = command(
        "register_run",
        request="registration-request",
        intent="registration-intent",
        target=prepared["prepared_run_id"],
        parameters={
            "prepared_run_id": prepared["prepared_run_id"],
            "record_revision": 1,
        },
    ).model_copy(update={"project_id": inputs.project_id})
    registered = core.api.dispatch(register_command, RELAY)
    assert registered.error is None, registered.error
    facts = ExecutionFacts.model_validate(registered.result)
    assert facts.run.control_state.value == "not_started" and not facts.attempts
    assert not facts.coverage.executed_attempt_ids and not facts.verifications
    seq = core.unit_of_work.current_commit_sequence()
    again = core.api.dispatch(
        register_command.model_copy(update={"request_id": "registration-repeat"}), RELAY
    )
    assert again.result == registered.result and core.unit_of_work.current_commit_sequence() == seq
    service = core.execution_authorizations
    resolver = FixtureActionResolver(core.unit_of_work)
    service.action_resolver = resolver
    step = facts.steps[0].step_id
    value = command(
        "prepare_execution",
        target=step,
        parameters={"run_id": facts.run_id, "step_id": step},
    ).model_copy(update={"project_id": inputs.project_id})
    response = core.api.dispatch(value, RELAY)
    assert response.error is None, response.error
    parameters = response.result
    _, action = service.resolver.read(inputs.project_id, parameters["execution_action_id"])
    assert resolver.calls == 1
    before = core.unit_of_work.current_commit_sequence()
    recall = core.api.dispatch(value.model_copy(update={"request_id": "retransmit"}), RELAY)
    assert recall.result == parameters and resolver.calls == 1
    assert core.unit_of_work.current_commit_sequence() == before
    assert service._revision(action.request.authorization_ref.authorization_id) == 0
    assert not core.execution_coordinator.read_current_facts(
        project_id=inputs.project_id, run_id=facts.run_id
    ).attempts

    approval = command(
        "prepare_approval",
        request="review",
        intent="review-intent",
        parameters={
            "action": "authorize_step",
            "action_intent_id": value.intent_id,
            "target": step,
            "parameters": parameters,
        },
    ).model_copy(update={"project_id": inputs.project_id})
    challenge = core.api.dispatch(approval, HUMAN)
    assert challenge.error is None, challenge.error
    grant = command(
        "authorize_step",
        request="grant",
        target=step,
        parameters={
            **parameters,
            "approval_challenge_id": challenge.result["challenge_id"],
        },
    ).model_copy(update={"project_id": inputs.project_id})
    for session in (
        RELAY,
        Session("no-gesture", EntryKind.HUMAN_UI, True),
        Session("noninteractive-cli", EntryKind.INTERACTIVE_CLI, False),
    ):
        denied = core.api.dispatch(
            grant.model_copy(update={"request_id": session.session_id}), session
        )
        assert denied.error.code == "AWAITING_USER_CONFIRMATION"
    before = core.unit_of_work.current_commit_sequence()
    original = core.api.dispatch_user_confirmation(
        grant,
        HUMAN,
        challenge_id=challenge.result["challenge_id"],
        input_digest=challenge.result["basis"]["input_digest"],
    )
    assert original.error is None, original.error
    assert core.unit_of_work.current_commit_sequence() == before + 7
    identity = original.result["authorization_id"]
    assert identity == action.request.authorization_ref.authorization_id
    assert service._state(inputs.project_id, identity) == (1, AuthorizationState.UNUSED, None)
    assert not core.execution_coordinator.read_current_facts(
        project_id=inputs.project_id, run_id=facts.run_id
    ).attempts
    before = core.unit_of_work.current_commit_sequence()
    repeated = core.api.dispatch(grant.model_copy(update={"request_id": "grant-repeat"}), HUMAN)
    assert repeated.result == original.result
    assert core.unit_of_work.current_commit_sequence() == before
    changed = core.api.dispatch(
        grant.model_copy(
            update={
                "request_id": "changed-challenge",
                "parameters": {**grant.parameters, "approval_challenge_id": "another-challenge"},
            }
        ),
        HUMAN,
    )
    assert changed.error.code == "INTENT_CONFLICT"
    assert core.unit_of_work.current_commit_sequence() == before

    core.lifetime_lock.release()
    (source / "main.py").write_bytes(b"VALUE = 2\n")
    restarted = assemble_workspace_core(core.workspace.root, instance_id="authorization-restart")
    try:
        seq = restarted.unit_of_work.current_commit_sequence()
        recalled = restarted.api.dispatch(
            value.model_copy(update={"request_id": "restart-prepare"}), RELAY
        )
        saved = restarted.api.dispatch(
            grant.model_copy(update={"request_id": "restart-grant"}), HUMAN
        )
        initial = restarted.api.dispatch(
            register_command.model_copy(update={"request_id": "restart-registration"}), RELAY
        )
        assert initial.result == registered.result
        assert recalled.result == parameters and saved.result == original.result
        assert restarted.unit_of_work.current_commit_sequence() == seq
        assert restarted.execution_authorizations._state(inputs.project_id, identity) == (
            1,
            AuthorizationState.UNUSED,
            None,
        )
        fresh = restarted.api.dispatch(
            value.model_copy(update={"request_id": "new-request", "intent_id": "new-intent"}), RELAY
        )
        assert fresh.error.code == "CAPABILITY_UNAVAILABLE"
        assert restarted.unit_of_work.current_commit_sequence() == seq
    finally:
        restarted.lifetime_lock.release()
