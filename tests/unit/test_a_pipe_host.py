"""Tests for the Windows named pipe channel and editor host lifecycle."""

from __future__ import annotations

import sys
import threading

import pytest

from aitest.interfaces.local.editor_host import (
    CoreEndpoint,
    EditorHost,
    WorkspaceInUse,
)
from aitest.interfaces.local.pipe import (
    NamedPipeClient,
    NamedPipeServer,
    PipeUnavailable,
    validate_workspace_id,
)

win_only = pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="命名管道仅 Windows"
)


def _run_server(
    workspace_id: str, instance_id: str, *, ready: threading.Event | None = None
) -> threading.Thread:
    def serve() -> None:
        server = NamedPipeServer(workspace_id, instance_id=instance_id)
        server.start()
        if ready is not None:
            ready.set()
        server.wait_for_client()
        server.validate_peer()
        message = server.read_message()
        server.write_message(b"echo:" + message)
        server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


@win_only
def test_pipe_round_trip() -> None:
    ready = threading.Event()
    thread = _run_server("wspipert", "core-1", ready=ready)
    assert ready.wait(timeout=2)
    client = NamedPipeClient("wspipert", instance_id="core-1")
    client.connect()
    client.write_message(b'{"command":"ping"}')
    reply = client.read_message()
    client.close()
    thread.join(timeout=3)

    assert reply == b'echo:{"command":"ping"}'


@win_only
def test_second_server_is_rejected() -> None:
    # 服务端等待连接，期间同名第二实例必须失败。
    ready = threading.Event()

    def hold() -> None:
        server = NamedPipeServer("wsholdpipe", instance_id="core-hold")
        server.start()
        ready.set()
        server.wait_for_client()
        server.close()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)

    with pytest.raises(PipeUnavailable):
        NamedPipeServer("wsholdpipe", instance_id="core-hold").start()

    # 让等待中的服务器退出
    taker = NamedPipeClient("wsholdpipe", instance_id="core-hold")
    taker.connect()
    taker.close()
    thread.join(timeout=3)


@win_only
def test_large_message_framing() -> None:
    ready = threading.Event()
    thread = _run_server("wsbigpipe", "core-big", ready=ready)
    assert ready.wait(timeout=2)
    payload = b"x" * 20000
    client = NamedPipeClient("wsbigpipe", instance_id="core-big")
    client.connect()
    client.write_message(payload)
    reply = client.read_message()
    client.close()
    thread.join(timeout=3)
    assert reply == b"echo:" + payload


@win_only
def test_client_to_nonexistent_pipe() -> None:
    client = NamedPipeClient("wsghostpipe", instance_id="nope")
    with pytest.raises(PipeUnavailable):
        client.connect(timeout_ms=100)


def test_invalid_workspace_id() -> None:
    with pytest.raises(PipeUnavailable):
        validate_workspace_id("../bad/name")


# ----- editor host -------------------------------------------------


class _FakeLauncher:
    def __init__(self, instance_id: str = "new-core") -> None:
        self.instance_id = instance_id
        self.started: list[str] = []

    def start(self, workspace_id: str) -> str:
        self.started.append(workspace_id)
        return self.instance_id


def test_host_joins_existing_core() -> None:
    def connector(workspace_id: str) -> tuple[object, str] | None:
        return ("connection-object", "existing-core")

    launcher = _FakeLauncher()
    host = EditorHost(connector=connector, launcher=launcher)

    endpoint = host.acquire("ws1")

    assert isinstance(endpoint, CoreEndpoint)
    assert endpoint.instance_id == "existing-core"
    assert endpoint.connection == "connection-object"
    assert launcher.started == []


def test_host_launches_and_connects() -> None:
    state = {"connected": False}

    def connector(workspace_id: str) -> tuple[object, str] | None:
        return ("new-connection", "new-core") if state["connected"] else None

    class LaunchOnStart:
        def start(self, workspace_id: str) -> str:
            state["connected"] = True
            return "new-core"

    host = EditorHost(connector=connector, launcher=LaunchOnStart())
    endpoint = host.acquire("ws2")
    assert endpoint.instance_id == "new-core"
    assert endpoint.connection == "new-connection"


def test_host_times_out_when_core_never_appears() -> None:
    host = EditorHost(
        connector=lambda workspace_id: None,
        launcher=_FakeLauncher(),
        wait_timeout_seconds=0.2,
        poll_interval_seconds=0.02,
    )
    with pytest.raises(WorkspaceInUse):
        host.acquire("ws3")


def test_host_rejects_instance_mismatch() -> None:
    state = {"probed": False}

    def connector(workspace_id: str) -> tuple[object, str] | None:
        # 首次探测无核心；启动后应答的却是不同实例
        if not state["probed"]:
            state["probed"] = True
            return None
        return ("conn", "different-core")

    host = EditorHost(
        connector=connector,
        launcher=_FakeLauncher("expected-core"),
        wait_timeout_seconds=0.2,
    )
    with pytest.raises(WorkspaceInUse):
        host.acquire("ws4")
