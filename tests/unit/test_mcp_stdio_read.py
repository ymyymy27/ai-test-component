"""MCP lifecycle and read-only scope consume the real shared DTO/verified core."""

import io
import json
import subprocess
import sys
from unittest.mock import Mock

import pytest

from aitest.application.errors import CapabilityUnavailable
from aitest.bootstrap import acquire_endpoint, shutdown_endpoint
from aitest.contracts.responses import Response
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.core_client import CoreClient
from aitest.interfaces.local.editor_host import CoreEndpoint
from aitest.interfaces.tools.agent_relay import McpRelay


def encode(value):
    return json.dumps(value).encode()


def rpc(identity, method, params=None):
    result = {"jsonrpc": "2.0", "id": identity, "method": method}
    if params is not None:
        result["params"] = params
    return result


def initialize(version="2025-11-25"):
    return rpc(
        1,
        "initialize",
        {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "fixture", "version": "1"},
        },
    )


def relay():
    channel = Mock()
    sent = []
    revision = [1]

    def send(payload, **kwargs):
        sent.append(json.loads(payload))

    def read(**kwargs):
        command = sent[-1]
        result = (
            {"supported_actions": ["doctor", "query", "publish_plan"], "status": "READY"}
            if command["action"] == "doctor"
            else {
                "items": [
                    {"aggregate_kind": "binding", "record_id": "binding", "revision": revision[0]}
                ]
            }
        )
        return (
            Response(
                request_id=command["request_id"],
                instance_id="core",
                workspace_id="workspace",
                project_id=command["project_id"],
                binding_revision=command["binding_revision"],
                result=result,
            )
            .model_dump_json()
            .encode()
        )

    channel.write_message.side_effect = send
    channel.read_message.side_effect = read
    client = CoreClient(CoreEndpoint("workspace", "core", channel))
    return McpRelay(client, "project", "binding"), sent, revision


def ready(instance):
    instance.handle(encode(initialize()))
    assert (
        instance.handle(encode({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None
    )


@pytest.mark.parametrize(
    "version", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "future"]
)
def test_version_negotiation_and_fixed_read_tools(version):
    instance, sent, _ = relay()
    result = instance.handle(encode(initialize(version)))
    assert result["result"]["protocolVersion"] == (version if version != "future" else "2025-11-25")
    instance.handle(encode({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    tools = instance.handle(encode(rpc(2, "tools/list")))["result"]["tools"]
    assert {item["name"] for item in tools} == {"aitest_doctor", "aitest_query"}
    assert len(sent) == 2
    assert all("project_id" not in item["inputSchema"].get("properties", {}) for item in tools)


@pytest.mark.parametrize(
    "message",
    [
        b'{"jsonrpc":"2.0","id":1,"method":"tools/list","method":"initialize"}',
        b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}',
        b"[1,2]",
        b"not json",
        b"\xff",
    ],
)
def test_invalid_json_never_dispatches(message):
    instance, sent, _ = relay()
    answer = instance.handle(message)
    assert "error" in answer and len(sent) == 2


@pytest.mark.parametrize("method", ["tools/list", "tools/call", "unknown"])
def test_normal_requests_wait_for_initialized_notification(method):
    instance, sent, _ = relay()
    answer = instance.handle(encode(rpc(2, method)))
    assert answer["error"]["code"] == -32000 and len(sent) == 2


@pytest.mark.parametrize(
    "params",
    [
        {"name": "publish_plan"},
        {"name": {}},
        {"name": "aitest_query", "arguments": {"project_id": "other"}},
        {"name": "aitest_query", "arguments": {"binding_revision": 2}},
        {"name": "aitest_query", "arguments": {"limit": True}},
        {"name": "aitest_query", "arguments": {"limit": 501}},
        {"name": "aitest_query", "arguments": {"sort": "anything"}},
        {"name": "aitest_doctor", "arguments": {"path": "private"}},
    ],
)
def test_invalid_tools_overrides_and_filters_do_not_reach_core(params):
    instance, sent, _ = relay()
    ready(instance)
    answer = instance.handle(encode(rpc(2, "tools/call", params)))
    assert answer["error"]["code"] == -32602 and len(sent) == 2


def test_query_uses_the_frozen_project_and_exact_binding_revision():
    instance, sent, _ = relay()
    ready(instance)
    answer = instance.handle(
        encode(rpc(2, "tools/call", {"name": "aitest_query", "arguments": {"limit": 1}}))
    )
    assert answer["result"]["isError"] is False
    actual = [command for command in sent if command["parameters"] == {"limit": 1}]
    assert len(actual) == 1
    assert actual[0]["project_id"] == "project" and actual[0]["binding_revision"] == 1
    assert answer["result"]["structuredContent"]["workspace_id"] == "workspace"


def test_changed_binding_and_duplicate_rpc_id_do_not_repeat_a_tool_call():
    instance, sent, revision = relay()
    ready(instance)
    revision[0] = 2
    message = encode(rpc(2, "tools/call", {"name": "aitest_doctor"}))
    answer = instance.handle(message)
    assert answer["result"]["isError"] is True and len(sent) == 3
    assert "B_REPREPARE_REQUIRED" in answer["result"]["content"][0]["text"]
    assert instance.handle(message)["error"]["code"] == -32600 and len(sent) == 3


def test_eof_only_finishes_stdio_and_emits_no_extra_json():
    instance, sent, _ = relay()
    source = io.BytesIO(
        encode(initialize())
        + b"\n"
        + encode({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + b"\n"
        + encode(rpc(2, "tools/list"))
        + b"\n"
    )
    sink = io.BytesIO()
    instance.serve(source, sink)
    replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2]
    assert all(reply["jsonrpc"] == "2.0" for reply in replies) and len(sent) == 2


def test_unterminated_frame_is_rejected_without_dispatch():
    instance, sent, _ = relay()
    sink = io.BytesIO()
    instance.serve(io.BytesIO(encode(initialize())), sink)
    assert json.loads(sink.getvalue())["error"]["code"] == -32700 and len(sent) == 2


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Real Windows core and stdio")
def test_actual_stdio_process_uses_selected_binding_same_core_and_clean_eof(tmp_path):
    root = tmp_path / "data"
    raw = FileUnitOfWork(root)
    raw.begin("seed", "project")
    raw.stage_record(
        aggregate_kind="project",
        record_id="project",
        expected_revision=0,
        payload={"project_id": "project"},
    )
    raw.stage_record(
        aggregate_kind="binding",
        record_id="binding",
        expected_revision=0,
        payload={"project_id": "project", "binding_id": "binding"},
    )
    raw.commit()
    endpoint = acquire_endpoint(root)
    endpoint.connection.close()
    before = (root / "workspace.json").read_bytes()
    messages = [
        initialize(),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        rpc(2, "tools/list"),
        rpc(3, "tools/call", {"name": "aitest_doctor"}),
        rpc(
            4,
            "tools/call",
            {
                "name": "aitest_query",
                "arguments": {"aggregate_kind": "binding", "record_id": "binding", "limit": 1},
            },
        ),
    ]
    try:
        result = subprocess.run(
            [
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
            ],
            input=b"\n".join(encode(m) for m in messages) + b"\n",
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert [response["id"] for response in responses] == [1, 2, 3, 4]
        for response in responses[2:]:
            material = response["result"]["structuredContent"]
            assert material["instance_id"] == endpoint.instance_id
            assert material["workspace_id"] == endpoint.workspace_id
            assert material["project_id"] == "project" and material["binding_revision"] == 1
        assert (
            responses[-1]["result"]["structuredContent"]["result"]["items"][0]["record_id"]
            == "binding"
        )
        assert (root / "workspace.json").read_bytes() == before
    finally:
        shutdown_endpoint(root)


def test_initialization_notification_cannot_skip_initialize_and_never_gets_a_response():
    instance, sent, _ = relay()
    assert (
        instance.handle(encode({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None
    )
    assert instance.ready is False
    answer = instance.handle(encode(rpc(7, "tools/list")))
    assert answer["error"]["code"] == -32000 and len(sent) == 2
    assert (
        instance.handle(
            encode(
                {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7}}
            )
        )
        is None
    )
    assert len(sent) == 2


def test_legacy_version_returns_readable_text_without_claiming_new_content_fields():
    instance, sent, _ = relay()
    instance.handle(encode(initialize("2024-11-05")))
    instance.handle(encode({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    response = instance.handle(encode(rpc(2, "tools/call", {"name": "aitest_doctor"})))
    assert "structuredContent" not in response["result"]
    assert json.loads(response["result"]["content"][0]["text"])["project_id"] == "project"


def test_stdio_oversized_line_and_large_response_are_bounded(monkeypatch):
    from aitest.interfaces.tools import agent_relay

    instance, sent, _ = relay()
    monkeypatch.setattr(agent_relay, "MAX_COMMAND_BYTES", 512)
    sink = io.BytesIO()
    instance.serve(io.BytesIO(b"x" * 1000 + b"\n" + encode(initialize()) + b"\n"), sink)
    replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert len(replies) == 1 and replies[0]["error"]["code"] == -32700 and len(sent) == 2
    ready(instance)
    sink = io.BytesIO()
    instance.serve(io.BytesIO(encode(rpc(3, "tools/list")) + b"\n"), sink)
    assert json.loads(sink.getvalue())["error"]["code"] == -32000
    assert len(sink.getvalue()) < 512


def test_request_identity_capacity_is_finite_without_business_dispatch(monkeypatch):
    from aitest.interfaces.tools import agent_relay

    instance, sent, _ = relay()
    monkeypatch.setattr(agent_relay, "_MAX_REQUESTS", 2)
    assert instance.handle(encode(rpc(1, "ping")))["result"] == {}
    assert instance.handle(encode(rpc(2, "ping")))["result"] == {}
    assert instance.handle(encode(rpc(3, "ping")))["error"]["code"] == -32600
    assert len(instance._seen) == 2 and len(sent) == 2


def test_selected_binding_receipt_cannot_use_bool_or_another_record():
    for row in (
        {"aggregate_kind": "binding", "record_id": "binding", "revision": True},
        {"aggregate_kind": "binding", "record_id": "other", "revision": 1},
    ):
        client = Mock()
        client.send.side_effect = [
            Response(
                request_id="health", instance_id="core", result={"supported_actions": ["query"]}
            ),
            Response(request_id="binding", instance_id="core", result={"items": [row]}),
        ]
        with pytest.raises(CapabilityUnavailable):
            McpRelay(client, "project", "binding")


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Actual project boundary via stdio")
def test_actual_stdio_wrong_project_has_no_tool_stream_or_new_writer_epoch(tmp_path):
    root = tmp_path / "data"
    raw = FileUnitOfWork(root)
    raw.begin("seed", "project")
    raw.stage_record(
        aggregate_kind="project",
        record_id="project",
        expected_revision=0,
        payload={"project_id": "project"},
    )
    raw.stage_record(
        aggregate_kind="binding",
        record_id="binding",
        expected_revision=0,
        payload={"project_id": "project", "binding_id": "binding"},
    )
    raw.commit()
    endpoint = acquire_endpoint(root)
    endpoint.connection.close()
    identity = (root / "workspace.json").read_bytes()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "aitest.interfaces.tools.cli",
                "mcp-relay",
                "--workspace",
                str(root),
                "--project",
                "other",
                "--binding",
                "binding",
            ],
            input=encode(initialize()) + b"\n",
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 2 and result.stdout == b""
        assert (root / "workspace.json").read_bytes() == identity
    finally:
        shutdown_endpoint(root)


def test_binding_drift_during_read_does_not_publish_mixed_results():
    instance, sent, revision = relay()
    ready(instance)
    original = instance._send

    def send(action, parameters, **kwargs):
        result = original(action, parameters, **kwargs)
        if action == "query" and parameters == {"limit": 1}:
            revision[0] = 2
        return result

    instance._send = send
    answer = instance.handle(
        encode(rpc(2, "tools/call", {"name": "aitest_query", "arguments": {"limit": 1}}))
    )
    assert answer["result"]["isError"] is True
    assert "structuredContent" not in answer["result"]
    assert "B_REPREPARE_REQUIRED" in answer["result"]["content"][0]["text"]


@pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="Real idle stdio and independent CLI"
)
def test_waiting_stdio_does_not_occupy_the_only_core_pipe(tmp_path):
    import queue
    import threading

    root = tmp_path / "data"
    raw = FileUnitOfWork(root)
    raw.begin("seed", "project")
    raw.stage_record(
        aggregate_kind="project",
        record_id="project",
        expected_revision=0,
        payload={"project_id": "project"},
    )
    raw.stage_record(
        aggregate_kind="binding",
        record_id="binding",
        expected_revision=0,
        payload={"project_id": "project", "binding_id": "binding"},
    )
    raw.commit()
    endpoint = acquire_endpoint(root)
    endpoint.connection.close()
    process = subprocess.Popen(
        [
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
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    readers = []

    def read_line():
        output = queue.Queue()
        worker = threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True)
        readers.append(worker)
        worker.start()
        return json.loads(output.get(timeout=15))

    try:
        process.stdin.write(encode(initialize()) + b"\n")
        process.stdin.flush()
        assert read_line()["id"] == 1
        other = subprocess.run(
            [
                sys.executable,
                "-m",
                "aitest.interfaces.tools.cli",
                "doctor",
                "--workspace",
                str(root),
            ],
            capture_output=True,
            timeout=15,
        )
        assert other.returncode == 0, other.stderr.decode(errors="replace")
        assert json.loads(other.stdout)["instance_id"] == endpoint.instance_id
        process.stdin.write(
            encode({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + b"\n"
            + encode(rpc(2, "tools/call", {"name": "aitest_doctor"}))
            + b"\n"
        )
        process.stdin.flush()
        result = read_line()
        assert result["result"]["structuredContent"]["instance_id"] == endpoint.instance_id
        process.stdin.close()
        process.stdin = None
        remaining, error = process.communicate(timeout=15)
        assert process.returncode == 0 and remaining == b"", error.decode(errors="replace")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(5)
        for reader in readers:
            reader.join(2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        shutdown_endpoint(root)
