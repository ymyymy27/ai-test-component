"""Explicit binding fixture gesture, never evidence of real human/host acceptance."""

from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session


def prepare_binding_challenge(core, command, session):
    return core.api.dispatch(
        Command(
            action="prepare_approval",
            project_id=command.project_id,
            request_id="prepare-" + command.request_id,
            intent_id="prepare-" + command.intent_id,
            expected_revision=0,
            parameters={
                "action": command.action,
                "action_intent_id": command.intent_id,
                "target": command.parameters["binding"]["binding_id"],
                "parameters": command.parameters,
            },
        ),
        session,
    )


def controlled_binding_confirm(core, command, *, session=None):
    session = session or Session("controlled-binding-fixture", EntryKind.HUMAN_UI, True)
    original = core.api.dispatch(command, session)
    if original.error is None or original.error.code == "INTENT_CONFLICT":
        return original
    challenge = prepare_binding_challenge(core, command, session)
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


def controlled_binding_save(
    core,
    *,
    project,
    binding,
    project_revision=1,
    expected=0,
    intent="binding-intent",
    request="binding-request",
    session=None,
):
    return controlled_binding_confirm(
        core,
        Command(
            action="save_binding",
            project_id=project,
            intent_id=intent,
            request_id=request,
            expected_revision=expected,
            parameters={
                "binding": binding,
                "project_revision": project_revision,
                "expected_revision": expected,
            },
        ),
        session=session,
    )
