"""MCP business commands share original intents; fake transport checks are labeled separately."""

import json
import subprocess
import sys
from copy import deepcopy
from unittest.mock import Mock

import pytest

from aitest.application.errors import CapabilityUnavailable
from aitest.application.record_write import ORDINARY_WRITE_ACTIONS
from aitest.bootstrap import acquire_endpoint, shutdown_endpoint
from aitest.contracts.commands import Command
from aitest.contracts.responses import Response
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.editor_host import CoreEndpoint
from aitest.interfaces.tools import agent_relay
from aitest.interfaces.tools.agent_relay import McpRelay
from tests.contracts.test_b_use_case_registration import _scope_payload
from tests.unit.test_controlled_environment_input import declaration
from tests.unit.test_mcp_stdio_read import encode, ready, rpc

SCHEMA = "aitest.record-write-intent/1.0"


def relay(*, contract=SCHEMA):
    client = Mock()
    client.endpoint = CoreEndpoint("workspace", "core", Mock())
    sent = []
    revision = [1]

    def send(command):
        sent.append(command)
        if command.action == "doctor":
            result = {
                "supported_actions": ["doctor", "query", "save_acceptance", "publish_plan"],
                "intent_contracts": {"save_acceptance": contract, "publish_plan": contract},
            }
        elif command.action == "query":
            result = {
                "items": [
                    {"aggregate_kind": "binding", "record_id": "binding", "revision": revision[0]}
                ]
            }
        else:
            result = {"aggregate_kind": "acceptance_scope", "record_id": "scope", "revision": 1}
        return Response(
            request_id=command.request_id,
            instance_id="core",
            workspace_id="workspace",
            project_id=command.project_id,
            binding_revision=command.binding_revision,
            intent_id=command.intent_id,
            result=result,
        )

    client.send.side_effect = send
    instance = McpRelay(client, "project", "binding")
    ready(instance)
    return instance, client, sent, revision


def call(identity=2, arguments=None):
    return rpc(
        identity,
        "tools/call",
        {
            "name": "aitest_dispatch",
            "arguments": arguments
            or {
                "action": "save_acceptance",
                "intent_id": "original-business",
                "expected_revision": 0,
                "parameters": {"acceptance_scope": {}},
            },
        },
    )


def test_write_tool_requires_exact_core_contract_and_advertises_fixed_inputs():
    instance, _, _, _ = relay()
    tools = instance.handle(encode(rpc(2, "tools/list")))["result"]["tools"]
    tool = next(item for item in tools if item["name"] == "aitest_dispatch")
    schema = tool["inputSchema"]
    assert schema["properties"]["action"]["enum"] == ["save_acceptance"]
    assert set(schema["properties"]) == {
        "action",
        "intent_id",
        "expected_revision",
        "target",
        "parameters",
    }
    assert tool["annotations"]["readOnlyHint"] is False


def test_forwarding_preserves_business_intent_and_allocates_a_separate_transport_request():
    instance, _, sent, _ = relay()
    result = instance.handle(encode(call()))
    assert "error" not in result and result["result"]["isError"] is False
    command = next(item for item in sent if item.action == "save_acceptance")
    assert command.project_id == "project" and command.binding_revision == 1
    assert command.intent_id == "original-business" and command.expected_revision == 0
    assert command.request_id != command.intent_id
    assert json.loads(result["result"]["content"][0]["text"])["intent_id"] == command.intent_id


def test_transport_catalog_matches_the_implemented_ordinary_write_contract():
    assert agent_relay._WRITE_ACTIONS == ORDINARY_WRITE_ACTIONS


@pytest.mark.parametrize("contract", [None, "unknown", 1, True, {}, []])
def test_unknown_or_old_intent_contract_keeps_writes_unavailable(contract):
    instance, _, sent, _ = relay(contract=contract)
    tools = instance.handle(encode(rpc(2, "tools/list")))["result"]["tools"]
    assert {item["name"] for item in tools} == {"aitest_doctor", "aitest_query"}
    response = instance.handle(encode(call(3)))
    assert response["error"]["code"] == -32602 and len(sent) == 2


@pytest.mark.parametrize(
    "update",
    [
        {"project_id": "other"},
        {"binding_revision": 2},
        {"workspace_id": "other"},
        {"request_id": "external"},
        {"protocol_version": "other"},
        {"origin": "human_ui"},
        {"action": "publish_plan"},
        {"action": "begin"},
        {"action": "migrate"},
        {"action": "prepare_run"},
        {"action": {}},
        {"intent_id": None},
        {"intent_id": ""},
        {"intent_id": "bad intent"},
        {"intent_id": "x" * 129},
        {"expected_revision": True},
        {"expected_revision": None},
        {"expected_revision": "0"},
        {"expected_revision": -1},
        {"parameters": []},
        {"target": []},
    ],
)
def test_invalid_context_identity_or_action_never_dispatches(update):
    instance, _, sent, _ = relay()
    message = call()
    message["params"]["arguments"].update(update)
    response = instance.handle(encode(message))
    assert response["error"]["code"] == -32602 and len(sent) == 2


def test_two_rpc_requests_keep_same_intent_and_duplicate_rpc_never_replays():
    instance, _, sent, _ = relay()
    first = instance.handle(encode(call(2)))
    second = instance.handle(encode(call(3)))
    writes = [command for command in sent if command.action == "save_acceptance"]
    assert len(writes) == 2 and writes[0].intent_id == writes[1].intent_id
    assert writes[0].request_id != writes[1].request_id
    assert (
        first["result"]["structuredContent"]["result"]
        == second["result"]["structuredContent"]["result"]
    )
    count = len(sent)
    assert instance.handle(encode(call(3)))["error"]["code"] == -32600
    assert len(sent) == count


def test_binding_changed_before_dispatch_does_not_send_a_write():
    instance, _, sent, revision = relay()
    revision[0] = 2
    result = instance.handle(encode(call()))["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["code"] == "B_REPREPARE_REQUIRED"
    assert all(command.action != "save_acceptance" for command in sent)


@pytest.mark.parametrize("unreadable", [False, True])
def test_after_write_context_failure_preserves_the_verified_original_response(unreadable):
    instance, client, sent, revision = relay()
    send = client.send.side_effect
    written = []

    def dispatch(command):
        if written and command.action == "query" and unreadable:
            raise OSError("post-write binding observation unavailable")
        response = send(command)
        if command.action == "save_acceptance":
            written.append(command)
            revision[0] = 2
        return response

    client.send.side_effect = dispatch
    result = instance.handle(encode(call()))["result"]
    assert result["isError"] is False and len(written) == 1
    assert result["structuredContent"]["intent_id"] == "original-business"
    assert result["structuredContent"]["result"]["revision"] == 1
    assert result["_meta"]["aitest/context"]["state"] == "reselect_required"
    assert len(result["content"]) == 2


def test_lost_core_receipt_does_not_retry_or_invent_a_success():
    instance, client, sent, _ = relay()
    send = client.send.side_effect

    def dispatch(command):
        response = send(command)
        if command.action == "save_acceptance":
            raise OSError("unknown original write result")
        return response

    client.send.side_effect = dispatch
    result = instance.handle(encode(call()))["result"]
    assert result["isError"] is True and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["code"] == "CORE_RESULT_UNVERIFIED"
    assert len([command for command in sent if command.action == "save_acceptance"]) == 1


def test_legacy_write_result_keeps_readable_original_response_without_new_content_fields():
    instance, _, _, _ = relay()
    instance.version = "2024-11-05"
    result = instance.handle(encode(call()))["result"]
    assert result["isError"] is False and "structuredContent" not in result
    assert json.loads(result["content"][0]["text"])["intent_id"] == "original-business"


@pytest.mark.parametrize(
    "action", ["save_environment", "save_dependency_graph", "save_case", "save_task"]
)
def test_all_four_existing_save_names_enforce_the_shared_transport_write_identity(action):
    with pytest.raises(ValueError):
        Command(request_id="transport", action=action, project_id="project")
    with pytest.raises(ValueError):
        Command(
            request_id="same",
            intent_id="same",
            expected_revision=0,
            action=action,
            project_id="project",
        )


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Real Windows core, CLI and stdio")
def test_actual_stdio_write_replay_cli_and_restart_share_original_saved_result(tmp_path):
    root = tmp_path / "data"
    raw = FileUnitOfWork(root)
    raw.begin("seed", "project")
    for kind, identity, payload in [
        ("project", "project", {"project_id": "project"}),
        ("binding", "binding", {"project_id": "project", "binding_id": "binding"}),
    ]:
        raw.stage_record(
            aggregate_kind=kind, record_id=identity, expected_revision=0, payload=payload
        )
    raw.commit()
    endpoint = acquire_endpoint(root)
    endpoint.connection.close()
    source_identity = (root / "workspace.json").read_bytes()
    before = FileUnitOfWork(root).current_commit_sequence()
    scope = dict(_scope_payload())
    scope["name"] = "实际stdio范围"
    arguments = {
        "action": "save_acceptance",
        "intent_id": "stdio-original",
        "expected_revision": 0,
        "parameters": {"acceptance_scope": scope},
    }
    messages = [
        rpc(
            1,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "actual-fixture", "version": "1"},
            },
        ),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        call(2, arguments),
        call(3, deepcopy(arguments)),
        call(
            4,
            {
                "action": "save_environment",
                "intent_id": "forged-confirmation",
                "expected_revision": 0,
                "parameters": {
                    "environment": declaration(),
                    "role": "human_ui",
                    "approval_challenge_id": "self-claimed-click",
                },
            },
        ),
    ]
    command = [
        sys.executable,
        "-m",
        "aitest.interfaces.tools.cli",
        "mcp-relay",
        "--workspace",
        str(root),
        "--project",
        "project",
        "--binding",
        "binding",
    ]
    try:
        result = subprocess.run(
            command,
            input=b"\n".join(encode(item) for item in messages) + b"\n",
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert [reply["id"] for reply in replies] == [1, 2, 3, 4]
        denied = replies[-1]["result"]["structuredContent"]
        assert replies[-1]["result"]["isError"] is True
        assert denied["error"]["code"] == "AWAITING_USER_CONFIRMATION"
        assert FileUnitOfWork(root).repo.current_revision("environment", "env") == 0
        responses = [reply["result"]["structuredContent"] for reply in replies[1:3]]
        assert all(response["instance_id"] == endpoint.instance_id for response in responses)
        assert responses[0]["result"] == responses[1]["result"]
        assert all(response["intent_id"] == "stdio-original" for response in responses)
        assert FileUnitOfWork(root).current_commit_sequence() == before + 2
        assert (root / "workspace.json").read_bytes() == source_identity
        cli_command = {
            **arguments,
            "request_id": "cli-original-query",
            "project_id": "project",
            "binding_revision": 1,
        }
        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "aitest.interfaces.tools.cli",
                "dispatch",
                "--workspace",
                str(root),
            ],
            input=encode(cli_command),
            capture_output=True,
            timeout=60,
        )
        assert cli.returncode == 0, cli.stderr.decode(errors="replace")
        cli_response = json.loads(cli.stdout)
        assert cli_response["result"] == responses[0]["result"]
        assert cli_response["intent_id"] == "stdio-original"
        assert FileUnitOfWork(root).current_commit_sequence() == before + 2
        shutdown_endpoint(root)
        reopened = acquire_endpoint(root)
        reopened.connection.close()
        assert reopened.instance_id != endpoint.instance_id
        replay = subprocess.run(
            command,
            input=b"\n".join(encode(item) for item in messages) + b"\n",
            capture_output=True,
            timeout=60,
        )
        assert replay.returncode == 0, replay.stderr.decode(errors="replace")
        replies = [json.loads(line) for line in replay.stdout.splitlines()]
        for item in replies[1:3]:
            saved = item["result"]["structuredContent"]
            assert (
                saved["instance_id"] == reopened.instance_id
                and saved["result"] == responses[0]["result"]
            )
        assert FileUnitOfWork(root).current_commit_sequence() == before + 2
    finally:
        shutdown_endpoint(root)


@pytest.mark.parametrize("catalog", [None, True, 1, "unknown", []])
def test_malformed_write_catalog_degrades_only_writes_and_keeps_finite_reads(catalog):
    _, client, sent, _ = relay()
    send = client.send.side_effect

    def changed(command):
        response = send(command)
        if command.action == "doctor":
            return response.model_copy(
                update={"result": dict(response.result) | {"intent_contracts": catalog}}
            )
        return response

    client.send.side_effect = changed
    instance = McpRelay(client, "project", "binding")
    ready(instance)
    tools = instance.handle(encode(rpc(2, "tools/list")))["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"aitest_doctor", "aitest_query"}
    result = instance.handle(encode(rpc(3, "tools/call", {"name": "aitest_query"})))
    assert result["result"]["isError"] is False
    assert all(command.action != "save_acceptance" for command in sent)


@pytest.mark.parametrize(
    "actions",
    [
        ["query", "query"],
        ["query", True],
        ["query", {}],
        ["query", " "],
        ["query", "bad\nname"],
        ["query"] * 257,
    ],
)
def test_unverified_base_action_catalog_never_queries_or_dispatches(actions):
    client = Mock()
    client.send.return_value = Response(
        request_id="doctor", instance_id="core", result={"supported_actions": actions}
    )
    with pytest.raises(CapabilityUnavailable):
        McpRelay(client, "project", "binding")
    assert client.send.call_count == 1
