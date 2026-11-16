import pytest
from pydantic import ValidationError

from aitest.bootstrap import create_api
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session


def test_doctor_is_honest_and_transport_retry_is_deduplicated() -> None:
    api = create_api()
    session = Session("host", EntryKind.INTERACTIVE_CLI)
    request = Command(request_id="req", action="doctor")
    first = api.dispatch(request, session)
    assert first.result is not None and first.result["status"] == "NOT_READY"
    assert api.dispatch(request, session) == first
    conflict = api.dispatch(Command(request_id="req", action="query"), session)
    assert conflict.error is not None and conflict.error.code == "REQUEST_CONFLICT"


def test_agent_cannot_confirm_and_human_cannot_execute_unimplemented_actions() -> None:
    api = create_api()
    command = Command(
        request_id="a", action="authorize_step", project_id="p", expected_revision=1, intent_id="i"
    )
    agent = api.dispatch(command, Session("agent", EntryKind.AGENT_RELAY))
    human = api.dispatch(command, Session("human", EntryKind.HUMAN_UI))
    assert agent.error is not None and agent.error.code == "AWAITING_USER_CONFIRMATION"
    assert human.error is not None and human.error.code == "CAPABILITY_UNAVAILABLE"


def test_write_requires_intent_and_revision() -> None:
    with pytest.raises(ValidationError):
        Command(request_id="r", action="start_run")


def test_version_and_client_supplied_entry_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        Command.model_validate(
            {"request_id": "r", "action": "doctor", "protocol_version": "aitest.local/1.0"}
        )
    with pytest.raises(ValidationError):
        Command.model_validate({"request_id": "r", "action": "doctor", "entry_kind": "human_ui"})
