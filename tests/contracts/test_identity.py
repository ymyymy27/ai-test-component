import pytest
from pydantic import ValidationError

from aitest.contracts.commands import Command


def test_identity_types_keep_wire_values_as_strings() -> None:
    command = Command(request_id="req-1", action="doctor")

    assert isinstance(command.request_id, str)
    assert command.request_id == "req-1"


@pytest.mark.parametrize("field", ["request_id", "intent_id"])
def test_identity_rejects_blank_or_whitespace(field: str) -> None:
    payload = {"request_id": "req-1", "action": "doctor"}
    if field == "request_id":
        payload[field] = "bad id"
    else:
        payload.update(
            {
                "action": "start_run",
                "project_id": "project-1",
                "expected_revision": 1,
                field: "bad id",
            }
        )

    with pytest.raises(ValidationError):
        Command.model_validate(payload)


def test_write_command_cannot_reuse_transport_identity_as_business_intent() -> None:
    with pytest.raises(ValidationError, match="request_id must not be used"):
        Command(
            request_id="same-id",
            action="start_run",
            project_id="project-1",
            expected_revision=1,
            intent_id="same-id",
        )
