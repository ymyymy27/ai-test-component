"""Actual Windows client backpressure, response deadlines and cancellation."""

import sys
import threading
import time

import pytest

from aitest.interfaces.local.pipe import MAX_MESSAGE_BYTES, PipeUnavailable
from tests.unit.test_server_pipe_deadlines import connected_pair

pytestmark = pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows client I/O")


def test_client_request_backpressure_has_a_deadline():
    server, client = connected_pair()
    try:
        started = time.monotonic()
        with pytest.raises(PipeUnavailable, match="超时"):
            client.write_message(b"x" * MAX_MESSAGE_BYTES, timeout_ms=80)
        assert time.monotonic() - started < 2
        assert not client._pending_io
    finally:
        client.close()
        server.close()


def test_client_close_cancels_owned_pending_io():
    for operation in ["read", "write"]:
        server, client = connected_pair()
        started = threading.Event()
        outcomes = []

        def work():
            started.set()  # noqa: B023
            try:
                if operation == "read":  # noqa: B023
                    client.read_message(timeout_ms=2000)  # noqa: B023
                else:
                    client.write_message(b"x" * MAX_MESSAGE_BYTES, timeout_ms=2000)  # noqa: B023
            except PipeUnavailable:
                outcomes.append("cancelled")  # noqa: B023

        thread = threading.Thread(target=work)
        thread.start()
        try:
            assert started.wait(1)
            until = time.monotonic() + 1
            while (
                not getattr(client, "_pending_io", {})
                and thread.is_alive()
                and time.monotonic() < until
            ):
                threading.Event().wait(0.01)
            assert client._pending_io
            client.close()
            thread.join(2)
            assert not thread.is_alive() and outcomes == ["cancelled"]
            assert not client._pending_io and client._handle is None
        finally:
            server.close()
            client.close()
            thread.join(2)


def test_client_write_rejects_invalid_deadline_before_native_io():
    for timeout in [True, -1, 1.2, "1"]:
        server, client = connected_pair()
        try:
            with pytest.raises(ValueError):
                client.write_message(b"{}", timeout_ms=timeout)
            assert not client._pending_io
        finally:
            server.close()
            client.close()


def test_client_large_frame_preserves_the_actual_received_bytes():
    server, client = connected_pair()
    payload = b"abcd" * (1024 * 1024)
    received = []
    worker = threading.Thread(target=lambda: received.append(server.read_message(timeout_ms=2000)))
    worker.start()
    try:
        client.write_message(payload, timeout_ms=2000)
        worker.join(3)
        assert received == [payload]
        assert not worker.is_alive() and not client._pending_io
    finally:
        server.close()
        client.close()
        worker.join(2)


@pytest.mark.parametrize(
    "action",
    ["prepare_run", "generate_draft", "create_report"],
)
def test_long_running_receipt_survives_more_than_ten_seconds(action):
    """Real pipe delay, synthetic response: not a human/Trae acceptance claim."""
    import json

    from aitest.contracts.commands import Command
    from aitest.contracts.responses import Response
    from aitest.interfaces.local.core_client import CoreClient
    from aitest.interfaces.local.editor_host import CoreEndpoint

    server, channel = connected_pair()
    failures = []

    def serve():
        try:
            command = json.loads(server.read_message())
            time.sleep(10.5)
            server.write_message(Response(
                request_id=command["request_id"], instance_id="core", workspace_id="workspace",
                project_id=command["project_id"], intent_id=command["intent_id"],
                result={"committed_revision": 1},
            ).model_dump_json().encode())
        except Exception as error:
            failures.append(error)

    worker = threading.Thread(target=serve)
    worker.start()
    client = CoreClient(CoreEndpoint("workspace", "core", channel))
    try:
        result = client.send(Command(
            request_id="late-receipt", action=action,
            project_id="project",
            intent_id="stable-intent", expected_revision=0,
        ))
        assert result.error is None and result.result == {"committed_revision": 1}
        worker.join(2)
        assert not worker.is_alive() and not failures
    finally:
        client.close()
        server.close()
        worker.join(2)
