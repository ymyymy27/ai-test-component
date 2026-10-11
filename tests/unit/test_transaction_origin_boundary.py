"""Transaction handles, controlled channel origins and DTOs are separate facts."""

import json

import pytest

from aitest.contracts.errors import ErrorDTO
from aitest.contracts.responses import PageInfo
from aitest.contracts.views import Response
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from tests.unit.test_a_a14_transaction_identity import _api, _command

OWNER = Session("same-name", EntryKind.HUMAN_UI, True)
FOREIGN = (
    Session("same-name", EntryKind.AGENT_RELAY),
    Session("same-name", EntryKind.INTERACTIVE_CLI, True),
    Session("same-name", EntryKind.HUMAN_UI, False),
)


@pytest.mark.parametrize("session", FOREIGN)
@pytest.mark.parametrize("action", ["commit", "rollback", "close"])
def test_same_named_foreign_origin_cannot_end_owners_transaction(session, action):
    api, port = _api()
    assert api.dispatch(_command("begin", request_id="begin-owner"), OWNER).error is None
    if action == "close":
        api.close_session(session)
    else:
        response = api.dispatch(
            _command(
                action, request_id="foreign-end", parameters={"begin_request_id": "begin-owner"}
            ),
            session,
        )
        assert response.error is not None
        assert response.error.code == "NO_ACTIVE_TRANSACTION"
    assert port.calls == [("begin", "begin-owner")]
    api.close_session(OWNER)
    assert port.calls == [("begin", "begin-owner"), ("rollback", "begin-owner")]


@pytest.mark.parametrize("action", ["commit", "rollback"])
@pytest.mark.parametrize("handle", [None, "", False])
def test_end_requires_the_exact_explicit_begin_handle(action, handle):
    api, port = _api()
    assert api.dispatch(_command("begin", request_id="begin-owner"), OWNER).error is None
    parameters = {} if handle is None else {"begin_request_id": handle}
    response = api.dispatch(
        _command(action, request_id="missing-handle", parameters=parameters), OWNER
    )
    assert response.error is not None
    assert response.error.code == "TRANSACTION_NOT_OWNED"
    assert port.calls == [("begin", "begin-owner")]
    api.close_session(OWNER)


@pytest.mark.parametrize("action", ["commit", "rollback"])
def test_end_cannot_omit_the_project_frozen_at_begin(action):
    api, port = _api()
    assert api.dispatch(_command("begin", request_id="begin-owner"), OWNER).error is None
    response = api.dispatch(
        _command(
            action,
            request_id="missing-project",
            project_id=None,
            parameters={"begin_request_id": "begin-owner"},
        ),
        OWNER,
    )
    assert response.error is not None
    assert response.error.code == "TRANSACTION_PROJECT_MISMATCH"
    assert port.calls == [("begin", "begin-owner")]
    api.close_session(OWNER)


def test_a_declined_begin_response_does_not_claim_an_active_transaction():
    calls = []

    class Declined:
        def begin(self, **kwargs):
            calls.append(kwargs["request_id"])
            return Response(
                request_id="internal-refused",
                instance_id="internal",
                error=ErrorDTO(code="WORKSPACE_IN_USE", message="busy", next_step="retry"),
            )

    api = LocalAPI("actual", "workspace", transaction_port=Declined())
    first = api.dispatch(_command("begin", request_id="first"), OWNER)
    assert first.error.code == "WORKSPACE_IN_USE"
    second = api.dispatch(_command("begin", request_id="second"), OWNER)
    assert second.error.code == "WORKSPACE_IN_USE"
    assert calls == ["first", "second"]
    api.close_session(OWNER)  # No nonexistent rollback may be invented.


@pytest.mark.parametrize("failed", [False, True])
def test_transaction_response_dto_uses_core_envelope_and_safe_projection(failed):
    secret = "sk-" + "synthetic-transaction-secret-" * 2

    class RichResponse:
        def recover(self, **kwargs):
            return Response(
                request_id="other-request",
                instance_id="other-instance",
                workspace_id="other-workspace",
                project_id="other-project",
                intent_id="other-intent",
                binding_revision=99,
                result={"api_key": secret, "state": "unknown"},
                error=ErrorDTO(
                    code="RECOVERY_REQUIRED",
                    message=secret,
                    next_step=secret,
                    request_id="other-error",
                    intent_id="other-intent",
                )
                if failed
                else None,
            )

    api = LocalAPI("actual-instance", "actual-workspace", transaction_port=RichResponse())
    command = _command("recover", request_id="actual-request")
    response = api.dispatch(command, OWNER)
    assert secret not in response.model_dump_json()
    assert response.request_id == command.request_id
    assert response.instance_id == "actual-instance"
    assert response.workspace_id == "actual-workspace"
    assert response.project_id == command.project_id
    assert response.intent_id == command.intent_id
    assert response.binding_revision == command.binding_revision
    if failed:
        assert response.error.request_id == command.request_id
        assert response.error.intent_id == command.intent_id


def test_scalar_transaction_result_still_crosses_the_safe_boundary():
    secret = "sk-" + "B" * 32

    class ScalarResponse:
        def recover(self, **kwargs):
            return secret

    api = LocalAPI("actual", "workspace", transaction_port=ScalarResponse())
    result = api.dispatch(_command("recover", request_id="scalar"), OWNER)
    assert secret not in json.dumps(result.result)


def test_rich_page_dto_is_serialized_before_credential_projection():
    secret = "sk-" + "C" * 32

    class PagedResponse:
        def recover(self, **kwargs):
            return Response(
                request_id="inner",
                instance_id="inner",
                page=PageInfo(limit=1, next_cursor=secret),
                result={"state": "idle"},
            )

    def projection(value):
        # Real configured projection supports JSON values, not arbitrary models.
        return json.loads(json.dumps(value))

    api = LocalAPI(
        "actual", "workspace", transaction_port=PagedResponse(), credential_projector=projection
    )
    response = api.dispatch(_command("recover", request_id="paged"), OWNER)
    assert response.error is None
    assert response.page.limit == 1
    assert secret not in response.model_dump_json()


def test_successful_begin_keeps_owner_when_safe_projection_fails():
    from tests.unit.test_a_a14_transaction_identity import FakeTransactionPort

    port = FakeTransactionPort()

    def projection(value):
        raise ValueError("configured safe projection unavailable")

    api = LocalAPI("actual", "workspace", transaction_port=port, credential_projector=projection)
    failed = api.dispatch(_command("begin", request_id="projection-failed"), OWNER)
    assert failed.error is not None
    assert port.calls == [("begin", "projection-failed")]
    api.close_session(FOREIGN[0])
    assert port.calls == [("begin", "projection-failed")]
    api.close_session(OWNER)
    assert port.calls == [("begin", "projection-failed"), ("rollback", "projection-failed")]
