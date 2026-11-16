"""Publication fixture gestures are synthetic, never real user/host acceptance."""

from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session


def prepare_publication(core, command, session):
    target = (
        command.parameters["draft"]["rule_id"]
        if command.action == "publish_rules"
        else command.parameters["plan_id"]
    )
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
                "target": target,
                "parameters": command.parameters,
            },
        ),
        session,
    )


def controlled_publication_confirm(core, command, *, session=None):
    session = session or Session("controlled-publication-fixture", EntryKind.HUMAN_UI, True)
    original = core.api.dispatch(command, session)
    if original.error is None or original.error.code == "INTENT_CONFLICT":
        return original
    challenge = prepare_publication(core, command, session)
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


def controlled_publication_save(
    core,
    action,
    *,
    project,
    parameters,
    expected=0,
    project_revision=1,
    request="publication-request",
    intent="publication-intent",
    session=None,
):
    return controlled_publication_confirm(
        core,
        Command(
            action=action,
            project_id=project,
            request_id=request,
            intent_id=intent,
            expected_revision=expected,
            parameters=dict(parameters)
            | {"project_revision": project_revision, "expected_revision": expected},
        ),
        session=session,
    )
