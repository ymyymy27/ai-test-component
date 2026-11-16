"""Explicit fixture interaction for policy consent; no real user/provider acceptance."""

from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session


def prepare_policy_challenge(core, command, session):
    return core.api.dispatch(
        Command(
            action="prepare_approval",
            project_id=command.project_id,
            expected_revision=0,
            request_id="prepare-" + command.request_id,
            intent_id="prepare-" + command.intent_id,
            parameters={
                "action": command.action,
                "action_intent_id": command.intent_id,
                "target": "model-policy:" + command.project_id,
                "parameters": command.parameters,
            },
        ),
        session,
    )


def controlled_policy_confirm(core, command, *, session=None):
    session = session or Session("controlled-policy-fixture", EntryKind.HUMAN_UI, True)
    original = core.api.dispatch(command, session)
    if original.error is None or original.error.code == "INTENT_CONFLICT":
        return original
    challenge = prepare_policy_challenge(core, command, session)
    if challenge.error is not None:
        return challenge
    approved = command.model_copy(
        update={
            "request_id": "approved-" + command.request_id,
            "parameters": dict(command.parameters)
            | {"approval_challenge_id": challenge.result["challenge_id"]},
        }
    )
    return core.api.dispatch_user_confirmation(
        approved,
        session,
        challenge_id=challenge.result["challenge_id"],
        input_digest=challenge.result["basis"]["input_digest"],
    )


def controlled_policy_save(
    core,
    *,
    project,
    parameters,
    expected=0,
    intent="save_model_outbound_policy-intent",
    request="save_model_outbound_policy-request",
    session=None,
):
    return controlled_policy_confirm(
        core,
        Command(
            action="save_model_outbound_policy",
            project_id=project,
            parameters=parameters,
            request_id=request,
            intent_id=intent,
            expected_revision=expected,
        ),
        session=session,
    )
