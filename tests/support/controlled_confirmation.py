"""Explicit controlled event fixture; never claims actual Trae/user acceptance."""

from dataclasses import replace

from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session


def basis_command(
    project, parameters, *, intent="confirm_basis-intent", request="confirm_basis-request"
):
    return Command(
        action="confirm_basis",
        project_id=project,
        parameters=parameters,
        request_id=request,
        intent_id=intent,
        expected_revision=0,
    )


def prepare_basis_challenge(core, command, *, session=None):
    session = session or Session("controlled-basis-fixture", EntryKind.HUMAN_UI, True)
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
                "target": command.parameters["case_id"],
                "parameters": command.parameters,
            },
        ),
        session,
    )


def controlled_basis_confirm(core, command, *, session=None):
    session = session or Session("controlled-basis-fixture", EntryKind.HUMAN_UI, True)
    # Read the original persistent result first; successful replay requires no new
    # gesture, including after restart. A rejected new action still gets a challenge.
    original = core.api.dispatch(command, session)
    if original.error is None or original.error.code == "INTENT_CONFLICT":
        return original
    prepared = prepare_basis_challenge(core, command, session=session)
    if prepared.error is not None:
        return prepared
    challenge = prepared.result
    approved = command.model_copy(
        update={
            "request_id": "approved-" + command.request_id,
            "parameters": {
                **command.parameters,
                "approval_challenge_id": challenge["challenge_id"],
            },
        }
    )
    return core.api.dispatch_user_confirmation(
        approved,
        replace(session, interactive=True),
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )
