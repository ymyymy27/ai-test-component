"""MCP binds exact reads to the selected context; simulated core responses."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.contracts.responses import Response
from aitest.interfaces.tools.agent_relay import McpRelay
from tests.unit.test_mcp_stdio_read import encode, initialize, ready, rpc


def relay(*, advertised=True):
    sent = []
    binding = [1]
    preview = {"schema_version": "aitest.case-reuse-inspection/1.1", "status": "unverified",
               "denial_reasons": ["verification_valid_unverified"]}
    client = Mock(endpoint=SimpleNamespace(workspace_id="workspace"))

    def send(command):
        sent.append(command)
        result = (
            {"supported_actions": ["doctor", "query"] + (
                ["inspect_case_reuse"] if advertised else []
            )}
            if command.action == "doctor" else
            {"items": [{"aggregate_kind": "binding", "record_id": "binding",
                        "revision": binding[0]}]} if command.action == "query" else preview
        )
        return Response(request_id=command.request_id, workspace_id="workspace",
                        instance_id="core", project_id=command.project_id,
                        binding_revision=command.binding_revision, result=result)

    client.send.side_effect = send
    instance = McpRelay(client, "project", "binding")
    ready(instance)
    values = {
        "case_id": "case", "source_run_id": "source", "target_run_id": "target",
        "source_snapshot": {"snapshot_commit_id": "source-snapshot", "snapshot_cursor": 3,
                            "digest": "sha256:" + "a" * 64},
        "target_snapshot": {"snapshot_commit_id": "target-snapshot", "snapshot_cursor": 9,
                            "digest": "sha256:" + "b" * 64},
    }
    return instance, sent, binding, values, preview


def call(instance, values, *, identity=2):
    return instance.handle(encode(rpc(identity, "tools/call", {
        "name": "aitest_inspect_case_reuse", "arguments": values,
    })))


def test_read_tool_is_negotiated_and_uses_shared_snapshot_schema():
    instance, _, _, _, _ = relay()
    tools = instance.handle(encode(rpc(2, "tools/list")))["result"]["tools"]
    tool = next(x for x in tools if x["name"] == "aitest_inspect_case_reuse")
    assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False}
    assert set(tool["inputSchema"]["required"]) == {
        "case_id", "source_run_id", "source_snapshot", "target_run_id", "target_snapshot",
    }
    assert tool["inputSchema"]["additionalProperties"] is False
    assert tool["inputSchema"]["properties"]["source_snapshot"]["additionalProperties"] is False


def test_unadvertised_read_never_dispatches_but_ordinary_reads_remain():
    instance, sent, _, values, _ = relay(advertised=False)
    tools = instance.handle(encode(rpc(2, "tools/list")))["result"]["tools"]
    assert {x["name"] for x in tools} == {"aitest_query", "aitest_doctor"}
    assert call(instance, values, identity=3)["error"]["code"] == -32602
    assert len(sent) == 2
    assert instance.handle(encode(rpc(4, "tools/call", {
        "name": "aitest_query", "arguments": {"limit": 1},
    })))["result"]["isError"] is False


def test_result_and_exact_references_are_forwarded_without_qualification_or_business_intent():
    instance, sent, _, values, preview = relay()
    first = call(instance, values)
    second = call(instance, values, identity=3)
    assert first["result"]["structuredContent"]["result"] == preview
    assert second["result"]["structuredContent"]["result"] == preview
    actual = [x for x in sent if x.action == "inspect_case_reuse"]
    assert len(actual) == 2 and actual[0].request_id != actual[1].request_id
    for command in actual:
        assert command.parameters == values
        assert command.project_id == "project" and command.binding_revision == 1
        assert command.target == "case"
        assert command.intent_id is None and command.expected_revision is None
    before = len(sent)
    assert call(instance, values, identity=3)["error"]["code"] == -32600
    assert len(sent) == before


@pytest.mark.parametrize("damage", [
    "project", "binding", "eligible", "confirmation", "missing", "same_run", "empty_case",
    "bool_cursor", "float_cursor", "extra_snapshot", "snapshot_list",
])
def test_untrusted_context_or_qualification_fields_stop_before_core_reads(damage):
    instance, sent, _, values, _ = relay()
    values = deepcopy(values)
    if damage in ("project", "binding", "eligible", "confirmation"):
        values[{"project": "project_id", "binding": "binding_revision",
                "eligible": "eligible", "confirmation": "basis_confirmed"}[damage]] = True
    elif damage == "missing":
        values.pop("source_snapshot")
    elif damage == "same_run":
        values["source_run_id"] = values["target_run_id"]
    elif damage == "empty_case":
        values["case_id"] = "  "
    elif damage in ("bool_cursor", "float_cursor"):
        values["source_snapshot"]["snapshot_cursor"] = True if damage == "bool_cursor" else 3.0
    elif damage == "extra_snapshot":
        values["source_snapshot"]["qualified"] = True
    else:
        values["source_snapshot"] = [values["source_snapshot"]]
    assert call(instance, values)["error"]["code"] == -32602
    assert len(sent) == 2


@pytest.mark.parametrize("after", [False, True])
def test_binding_drift_never_returns_a_current_preview(after):
    instance, sent, binding, values, _ = relay()
    original = instance.client.send.side_effect

    def send(command):
        response = original(command)
        if command.action == "inspect_case_reuse":
            binding[0] = 2
        return response

    if after:
        instance.client.send.side_effect = send
    else:
        binding[0] = 2
    result = call(instance, values)["result"]
    assert result["isError"] is True and "B_REPREPARE_REQUIRED" in result["content"][0]["text"]
    assert sum(x.action == "inspect_case_reuse" for x in sent) == int(after)


def test_lost_receipt_remains_unverified_and_does_not_retry():
    instance, sent, _, values, _ = relay()
    original = instance.client.send.side_effect

    def send(command):
        response = original(command)
        if command.action == "inspect_case_reuse":
            raise OSError("synthetic private transport failure")
        return response

    instance.client.send.side_effect = send
    result = call(instance, values)["result"]
    assert result["isError"] is True and "CORE_RESULT_UNVERIFIED" in result["content"][0]["text"]
    assert "private" not in result["content"][0]["text"]
    assert sum(x.action == "inspect_case_reuse" for x in sent) == 1


def test_legacy_protocol_keeps_exact_response_text_without_structured_content():
    instance, _, _, values, preview = relay()
    instance.version = initialize("2024-11-05")["params"]["protocolVersion"]
    result = call(instance, values)["result"]
    assert "structuredContent" not in result
    import json
    assert json.loads(result["content"][0]["text"])["result"] == preview
