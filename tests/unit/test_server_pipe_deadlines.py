"""Actual Windows server I/O deadlines, cancellation and fragmented frames."""

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from uuid import uuid4

import pytest

from aitest.interfaces.local.pipe import NamedPipeClient, NamedPipeServer, PipeUnavailable

pytestmark = pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows pipe I/O")


def connected_pair():
    identity = uuid4().hex
    server = NamedPipeServer("server-deadline-" + identity, instance_id="core")
    server.start()
    client = NamedPipeClient("server-deadline-" + identity, instance_id="core")
    client.connect()
    server.wait_for_client()
    server.validate_peer()
    return server, client


def write_raw(client, data):
    buffer = (wintypes.BYTE * len(data)).from_buffer_copy(data)
    written = wintypes.DWORD(0)
    if not client._kernel.kernel32.WriteFile(
        client._handle, buffer, len(buffer), ctypes.byref(written), None
    ) or written.value != len(data):
        raise PipeUnavailable("test raw write failed")


@pytest.mark.parametrize("prefix", [b"", b"\x00", (10).to_bytes(4, "big") + b"short"])
def test_server_partial_frame_deadline_covers_header_and_body(prefix):
    server, client = connected_pair()
    try:
        if prefix:
            write_raw(client, prefix)
        started = time.monotonic()
        with pytest.raises(PipeUnavailable, match="超时"):
            server.read_message(timeout_ms=100)
        assert time.monotonic() - started < 2
        assert not server._pending_io
    finally:
        client.close()
        server.close()


def test_idle_accept_is_cancellable_on_the_owning_thread():
    server = NamedPipeServer("cancel-accept-" + uuid4().hex, instance_id="core")
    server.start()
    calls = []

    def idle():
        calls.append(threading.get_ident())
        server.cancel_wait()

    try:
        with pytest.raises(PipeUnavailable):
            server.wait_for_client(on_wait=idle)
        assert calls == [threading.get_ident()]
        assert not server._pending_io
    finally:
        server.close()


def test_accept_deadline_does_not_leave_an_outstanding_operation():
    server = NamedPipeServer("accept-deadline-" + uuid4().hex, instance_id="core")
    server.start()
    try:
        with pytest.raises(PipeUnavailable, match="超时"):
            server.wait_for_client(timeout_ms=80)
        assert not server._pending_io
    finally:
        server.close()


def test_server_response_backpressure_has_a_deadline():
    server, client = connected_pair()
    try:
        started = time.monotonic()
        with pytest.raises(PipeUnavailable, match="超时"):
            server.write_message(b"x" * (8 * 1024 * 1024), timeout_ms=100)
        assert time.monotonic() - started < 2
        assert not server._pending_io
    finally:
        client.close()
        server.close()


def test_fragmented_frame_does_not_reset_the_original_deadline():
    server, client = connected_pair()
    stop = threading.Event()

    def trickle():
        try:
            write_raw(client, (20).to_bytes(4, "big"))
            for _ in range(20):
                if stop.wait(0.025):
                    return
                write_raw(client, b"x")
        except PipeUnavailable:
            pass

    writer = threading.Thread(target=trickle, daemon=True)
    writer.start()
    try:
        started = time.monotonic()
        with pytest.raises(PipeUnavailable, match="超时"):
            server.read_message(timeout_ms=100)
        assert time.monotonic() - started < 0.4
    finally:
        stop.set()
        client.close()
        server.close()
        writer.join(timeout=2)
    assert not writer.is_alive()


def test_idle_callback_failure_cancels_before_releasing_overlapped_storage():
    server, client = connected_pair()

    def idle():
        raise LookupError("test callback failed")

    try:
        with pytest.raises(LookupError, match="callback failed"):
            server.read_message(timeout_ms=300, on_wait=idle)
        assert not server._pending_io
        client.write_message(b"following valid frame")
        assert server.read_message(timeout_ms=300) == b"following valid frame"
    finally:
        client.close()
        server.close()


def test_cross_thread_close_cancels_accept_and_retains_storage_until_completion():
    server = NamedPipeServer("close-accept-" + uuid4().hex, instance_id="core")
    server.start()
    started = threading.Event()
    outcomes = []

    def accept():
        try:
            server.wait_for_client(on_wait=started.set)
        except PipeUnavailable:
            outcomes.append("cancelled")

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        assert started.wait(2)
        server.close()
        thread.join(2)
        assert not thread.is_alive() and outcomes == ["cancelled"]
        assert not server._pending_io and server._handle is None
    finally:
        server.close()


@pytest.mark.parametrize("budget", [True, -1, 0.5, "100"])
def test_invalid_io_budget_is_rejected_before_connect_or_read(budget):
    server = NamedPipeServer("invalid-budget-" + uuid4().hex, instance_id="core")
    server.start()
    try:
        with pytest.raises(ValueError):
            server.wait_for_client(timeout_ms=budget)
        assert not server._pending_io
    finally:
        server.close()


@pytest.mark.parametrize("payload", [b"", b"abc", b"x" * 20000], ids=["empty", "small", "large"])
def test_overlapped_server_keeps_existing_byte_framing(payload):
    server, client = connected_pair()
    try:
        client.write_message(payload)
        assert server.read_message(timeout_ms=1000) == payload
        server.write_message(b"echo:" + payload, timeout_ms=1000)
        assert client.read_message(timeout_ms=1000) == b"echo:" + payload
    finally:
        client.close()
        server.close()


def test_default_worker_releases_half_frame_and_accepts_same_core_reconnect(tmp_path):
    from aitest.bootstrap import acquire_endpoint, shutdown_endpoint
    from aitest.contracts.commands import Command
    from aitest.contracts.responses import Response
    from aitest.infrastructure.file_store.records import FileRecordRepository

    address = "half-frame-" + uuid4().hex
    first = acquire_endpoint(tmp_path, workspace_id=address, wait_timeout_seconds=8)
    second = None
    try:
        before = FileRecordRepository(tmp_path).current_commit_sequence()
        write_raw(first.connection, (20).to_bytes(4, "big") + b"unfinished")
        available = wintypes.DWORD(0)
        until = time.monotonic() + 13
        while first.connection._kernel.kernel32.PeekNamedPipe(
            first.connection._handle, None, 0, None, ctypes.byref(available), None
        ):
            assert time.monotonic() < until, "server did not end its partial frame"
            time.sleep(0.025)
        # Keep the timed-out client open: the server, rather than client cleanup,
        # released the one-instance pipe and its unfinished command.
        second = acquire_endpoint(tmp_path, workspace_id=address, wait_timeout_seconds=8)
        assert second.instance_id == first.instance_id
        second.connection.write_message(
            Command(request_id="following-doctor", action="doctor").model_dump_json().encode()
        )
        response = Response.model_validate_json(second.connection.read_message(timeout_ms=2000))
        assert response.error is None and response.instance_id == first.instance_id
        assert response.result["status"] == "READY"
        assert FileRecordRepository(tmp_path).current_commit_sequence() == before
    finally:
        first.connection.close()
        if second is not None:
            second.connection.close()
        assert shutdown_endpoint(tmp_path, workspace_id=address)
