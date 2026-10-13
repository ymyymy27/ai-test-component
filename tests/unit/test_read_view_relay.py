"""Negotiated finite view reads use original MCP context; synthetic transport only."""

from unittest.mock import Mock

import pytest

from aitest.contracts.responses import Response
from aitest.interfaces.local.editor_host import CoreEndpoint
from aitest.interfaces.tools.agent_relay import McpRelay
from tests.unit.test_mcp_stdio_read import encode, ready, rpc


def relay(advertised=True):
    client = Mock()
    client.endpoint = CoreEndpoint("workspace", "core", Mock())
    sent = []

    def send(command):
        sent.append(command)
        if command.action == "doctor":
            result = {
                "supported_actions": ["doctor", "query"] + (["read_view"] if advertised else [])
            }
        elif command.action == "query":
            result = {
                "items": [{"aggregate_kind": "binding", "record_id": "binding", "revision": 1}]
            }
        else:
            result = {
                "view_type": command.parameters["view"],
                "view": {"project_id": "project"},
                "commit_id": 3,
                "event_cursor": "cursor-3",
            }
        return Response(
            request_id=command.request_id,
            project_id=command.project_id,
            instance_id="core",
            workspace_id="workspace",
            binding_revision=command.binding_revision,
            result=result,
        )

    client.send.side_effect = send
    instance = McpRelay(client, "project", "binding")
    ready(instance)
    return instance, sent


@pytest.mark.parametrize("advertised", [False, True])
def test_view_tool_is_only_advertised_by_core_capability(advertised):
    instance, _ = relay(advertised)
    tools = instance.handle(encode(rpc(2, "tools/list")))["result"]["tools"]
    views = [item for item in tools if item["name"] == "aitest_read_view"]
    assert bool(views) is advertised
    if views:
        assert views[0]["annotations"]["readOnlyHint"] is True
        assert "project_id" not in views[0]["inputSchema"]["properties"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"view": "run", "run_id": "r"},
        {"view": "panel", "run_id": "r"},
        {"view": "delivery", "delivery_id": "d", "revision": 2, "submission_id": "s"},
    ],
)
def test_read_view_preserves_finite_selection_and_original_context(arguments):
    instance, sent = relay()
    result = instance.handle(
        encode(rpc(2, "tools/call", {"name": "aitest_read_view", "arguments": arguments}))
    )
    assert result["result"]["isError"] is False
    command = next(command for command in sent if command.action == "read_view")
    assert command.parameters == arguments
    assert command.project_id == "project" and command.binding_revision == 1
    assert command.intent_id is None and command.expected_revision is None


@pytest.mark.parametrize(
    "arguments",
    [
        {"view": "panel"},
        {"view": "panel", "run_id": "r", "project_id": "other"},
        {"view": "panel", "run_id": "r", "latest": True},
        {"view": "panel", "run_id": "r", "revision": None},
        {"view": "delivery", "delivery_id": "d", "revision": True},
        {"view": "delivery", "delivery_id": "d", "revision": 0},
        {"view": "delivery", "delivery_id": "d", "run_id": "r"},
        {"view": "other"},
        {"view": "run", "run_id": " "},
        {"view": "run", "run_id": "r\n"},
    ],
)
def test_invalid_or_foreign_selectors_are_refused_before_core_reads(arguments):
    instance, sent = relay()
    before = len(sent)
    result = instance.handle(
        encode(rpc(2, "tools/call", {"name": "aitest_read_view", "arguments": arguments}))
    )
    assert result["error"]["code"] == -32602
    assert len(sent) == before


def test_old_core_never_receives_new_view_request():
    instance, sent = relay(False)
    before = len(sent)
    result = instance.handle(
        encode(
            rpc(
                2,
                "tools/call",
                {"name": "aitest_read_view", "arguments": {"view": "run", "run_id": "r"}},
            )
        )
    )
    assert result["error"]["code"] == -32602 and len(sent) == before
