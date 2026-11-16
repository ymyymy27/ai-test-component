"""Synthetic environment gesture; never actual user or Trae acceptance."""

from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session


def environment_command(
    project, environment, *, expected=0, intent="environment-intent", request="environment-request"
):
    return Command(
        action="save_environment",
        project_id=project,
        expected_revision=expected,
        intent_id=intent,
        request_id=request,
        parameters={
            "environment": environment,
            "project_revision": 1,
            "expected_revision": expected,
        },
    )


def prepare_environment(core, command, session):
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
                "target": command.parameters["environment"]["environment_id"],
                "parameters": command.parameters,
            },
        ),
        session,
    )


def confirm_environment(core, command, *, session=None):
    session = session or Session("controlled-environment-fixture", EntryKind.HUMAN_UI, True)
    response = core.api.dispatch(command, session)
    if response.error is None or response.error.code == "INTENT_CONFLICT":
        return response
    prepared = prepare_environment(core, command, session)
    if prepared.error is not None:
        return prepared
    challenge = prepared.result
    command = command.model_copy(
        update={
            "request_id": "approved-" + command.request_id,
            "parameters": dict(command.parameters)
            | {"approval_challenge_id": challenge["challenge_id"]},
        }
    )
    return core.api.dispatch_user_confirmation(
        command,
        session,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )
