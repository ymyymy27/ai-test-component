"""CLI forwarding must use one real core and reject counterfeit or ambiguous receipts."""

import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from aitest.bootstrap import acquire_endpoint, shutdown_endpoint
from aitest.contracts.commands import Command
from aitest.contracts.responses import Response
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.interfaces.local.core_client import CoreClient, CoreResultUnverified, read_command
from aitest.interfaces.local.editor_host import CoreEndpoint


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": "other"},
        {"instance_id": "other"},
        {"workspace_id": "other"},
        {"project_id": "other"},
        {"binding_revision": 2},
        {"binding_revision": True},
        {"intent_id": "other"},
        {"protocol_version": "aitest.local/1.0"},
        {"result": None},
        {"error": {"code": "x", "message": "x"}},
    ],
)
def test_wrong_receipt_cannot_be_returned_and_connection_is_closed(changes):
    command = Command(
        request_id="request",
        action="save_context",
        project_id="project",
        binding_revision=1,
        expected_revision=0,
        intent_id="intent",
    )
    payload = Response(
        request_id="request",
        instance_id="instance",
        workspace_id="workspace",
        project_id="project",
        binding_revision=1,
        intent_id="intent",
        result={},
    ).model_dump()
    channel = Mock()
    channel.read_message.return_value = json.dumps({**payload, **changes}).encode()
    client = CoreClient(CoreEndpoint("workspace", "instance", channel))
    with pytest.raises(CoreResultUnverified):
        client.send(command)
    channel.write_message.assert_called_once()
    channel.close.assert_called_once()
    with pytest.raises(CoreResultUnverified):
        client.send(command)
    channel.write_message.assert_called_once()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"request_id":"r","action":"doctor","action":"save_context"}',
        b'{"request_id":"r","action":"doctor","interactive":true}',
        b'{"__aitest_control__":"shutdown","request_id":"r"}',
        b'{"request_id":"r","action":"doctor","parameters":{"x":NaN}}',
    ],
)
def test_invalid_or_self_authorized_command_stops_before_connection(raw):
    with pytest.raises(ValueError):
        read_command(raw)


def test_duplicate_receipt_identity_is_rejected():
    channel = Mock()
    channel.read_message.return_value = b'{"request_id":"wrong","request_id":"request"}'
    with pytest.raises(CoreResultUnverified):
        CoreClient(CoreEndpoint("workspace", "instance", channel)).send(
            Command(request_id="request", action="doctor")
        )
    channel.close.assert_called_once()


def test_successful_handler_response_keeps_the_original_business_intent():
    from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

    api = LocalAPI("core", "workspace", handlers={"save_context": lambda command: {"saved": True}})
    command = Command(
        request_id="request",
        action="save_context",
        project_id="project",
        expected_revision=0,
        intent_id="intent",
    )
    response = api.dispatch(command, Session("agent", EntryKind.AGENT_RELAY))
    assert response.error is None and response.intent_id == "intent"


def cli(args, raw=None):
    return subprocess.run(
        [sys.executable, "-m", "aitest.interfaces.tools.cli", *args],
        input=raw,
        capture_output=True,
        timeout=30,
    )


def test_cli_rejects_missing_workspace_without_creating_files(tmp_path):
    missing = tmp_path / "missing" / "data"
    result = cli(["doctor", "--workspace", str(missing)])
    assert result.returncode == 2 and result.stdout == b""
    assert not missing.parent.exists()


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Actual verified Windows pipe")
def test_actual_cli_doctor_and_dispatch_share_the_verified_core(tmp_path):
    root = tmp_path / "user-data"
    Workspace(root)
    endpoint = acquire_endpoint(root)
    endpoint.connection.close()
    identity_before = (root / "workspace.json").read_bytes()
    try:
        doctor = cli(["doctor", "--workspace", str(root)])
        assert doctor.returncode == 0, doctor.stderr.decode(errors="replace")
        result = json.loads(doctor.stdout)
        assert result["instance_id"] == endpoint.instance_id
        assert result["workspace_id"] == endpoint.workspace_id
        assert result["result"]["status"] == "READY"
        command = Command(request_id="original-request", action="doctor")
        query = cli(["dispatch", "--workspace", str(root)], command.model_dump_json().encode())
        assert query.returncode == 0
        answer = json.loads(query.stdout)
        assert answer["request_id"] == "original-request"
        assert answer["instance_id"] == endpoint.instance_id
        assert (root / "workspace.json").read_bytes() == identity_before
        # Python CLI is not a trusted human host and must not manufacture a grant.
        command = Command(
            request_id="human-request",
            action="authorize_step",
            project_id="project",
            expected_revision=0,
            intent_id="authorization-intent",
        )
        denied = cli(["dispatch", "--workspace", str(root)], command.model_dump_json().encode())
        assert denied.returncode == 2
        assert denied.stdout, denied.stderr.decode(errors="replace")
        assert json.loads(denied.stdout)["error"]["code"] == "AWAITING_USER_CONFIRMATION"
        assert (root / "workspace.json").read_bytes() == identity_before
    finally:
        shutdown_endpoint(root)
