"""Ambiguous transport identities cannot dispatch business or stop the core."""

import sys
from unittest.mock import Mock

import pytest

from aitest.bootstrap import acquire_endpoint, shutdown_endpoint
from aitest.contracts.commands import Command
from aitest.contracts.responses import Response
from aitest.domain import json_material
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.core_worker import dispatch_frame


@pytest.mark.parametrize(
    "raw",
    [
        b'{"action":"doctor","action":"doctor","request_id":"req"}',
        b'{"action":"doctor","request_id":"foreign","request_id":"req"}',
        b'{"action":"doctor","request_id":"req","parameters":{"run_id":"foreign","run_id":"run"}}',
        b'{"action":"doctor","request_id":"req","parameters":{"rows":[{"id":"foreign","id":"valid"}]}}',
        b'{"action":"doctor","request_id":"req","parameters":{"value":NaN}}',
        b'{"action":"doctor","request_id":"req","parameters":{"value":Infinity}}',
        b'{"action":"doctor","request_id":"req","parameters":{"value":-Infinity}}',
        b'{"action":"doctor","request_id":"req","parameters":{"value":1e999}}',
        b'{"__aitest_control__":"unknown","__aitest_control__":"shutdown","request_id":"req"}',
    ],
)
def test_ambiguous_frame_is_rejected_before_business_or_shutdown(raw):
    api = LocalAPI(instance_id="core", workspace_id="workspace")
    api.dispatch = Mock(
        return_value=Response(request_id="req", instance_id="core", result={"status": "READY"})
    )
    result = dispatch_frame(api, Session("relay", EntryKind.AGENT_RELAY), raw)
    assert isinstance(result, Response)
    assert result.error is not None and result.error.code == "MALFORMED_MESSAGE"
    assert result.request_id == "invalid-request"
    api.dispatch.assert_not_called()


def test_excessive_json_nesting_does_not_terminate_the_serving_core():
    api = LocalAPI(instance_id="core", workspace_id="workspace")
    api.dispatch = Mock()
    depth = sys.getrecursionlimit() + 100
    raw = (
        b'{"action":"doctor","request_id":"req","parameters":{"v":'
        + b"[" * depth
        + b"0"
        + b"]" * depth
        + b"}}"
    )
    result = dispatch_frame(api, Session("relay", EntryKind.AGENT_RELAY), raw)
    assert isinstance(result, Response) and result.error.code in {
        "MALFORMED_MESSAGE",
        "INVALID_COMMAND",
    }
    api.dispatch.assert_not_called()


def test_parser_recursion_failure_is_a_protocol_error(monkeypatch):
    def exhausted(*args, **kwargs):
        raise RecursionError("untrusted JSON parser exhausted its stack")

    monkeypatch.setattr(json_material.json, "loads", exhausted)
    api = LocalAPI(instance_id="core", workspace_id="workspace")
    api.dispatch = Mock()
    result = dispatch_frame(api, Session("relay", EntryKind.AGENT_RELAY), b"{}")
    assert isinstance(result, Response) and result.error.code == "MALFORMED_MESSAGE"
    api.dispatch.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows core and pipe")
def test_actual_core_rejects_ambiguous_frames_and_keeps_the_same_connection(tmp_path):
    address = "strict-json-core"
    endpoint = acquire_endpoint(tmp_path, workspace_id=address, wait_timeout_seconds=8)
    try:
        before = FileRecordRepository(tmp_path).current_commit_sequence()
        for frame in (
            b'{"action":"doctor","action":"doctor","request_id":"req"}',
            b'{"action":"doctor","request_id":"req","parameters":{"value":1e999}}',
            b'{"__aitest_control__":"unknown","__aitest_control__":"shutdown","request_id":"req"}',
        ):
            endpoint.connection.write_message(frame)
            response = Response.model_validate_json(
                endpoint.connection.read_message(timeout_ms=2000)
            )
            assert response.error.code == "MALFORMED_MESSAGE"
            assert response.instance_id == endpoint.instance_id
        endpoint.connection.write_message(
            Command(action="doctor", request_id="after-rejected-frames").model_dump_json().encode()
        )
        response = Response.model_validate_json(endpoint.connection.read_message(timeout_ms=2000))
        assert response.error is None and response.result["status"] == "READY"
        assert response.instance_id == endpoint.instance_id
        assert FileRecordRepository(tmp_path).current_commit_sequence() == before
    finally:
        endpoint.connection.close()
        assert shutdown_endpoint(tmp_path, workspace_id=address)
