"""Exact UTF-8 and Unicode scalars are checked before I/O, comparison or stdout."""

import io
import json
import subprocess
import sys
from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.evidence.evidence_review import EvidenceReviewService
from aitest.application.ports import VerificationRequest
from aitest.contracts.responses import Response
from aitest.domain.evidence.evidence import VerificationObservation
from aitest.domain.execution.assertions import (
    HttpAssertion,
    HttpAssertionOperator,
    freeze_json_value,
)
from aitest.domain.json_material import decode_json
from aitest.infrastructure.adapters.execution.http import (
    HttpAdapter,
    HttpExchangeResult,
    HttpRequestSpec,
)
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter
from aitest.interfaces.local.core_client import CoreClient, CoreResultUnverified, read_command
from aitest.interfaces.local.editor_host import CoreEndpoint
from tests.unit.test_mcp_stdio_read import encode, relay, rpc


@pytest.mark.parametrize(
    "encoding", ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"]
)
def test_json_material_and_cli_reject_other_encodings(encoding):
    raw = json.dumps({"request_id": "original", "action": "doctor"}).encode(encoding)
    with pytest.raises(ValueError):
        decode_json(raw)
    with pytest.raises(ValueError):
        read_command(raw)


@pytest.mark.parametrize("raw", [b'"\\ud800"', b'{"\\udfff":1}', b'{"nested":["\\udc00"]}'])
def test_decoded_json_refuses_unpaired_surrogates(raw):
    with pytest.raises(ValueError):
        decode_json(raw)


@pytest.mark.parametrize("value", ["\ud800", {"key": ["\udc00"]}, {"\udfff": 1}])
def test_comparison_material_cannot_preserve_unserializable_text(value):
    with pytest.raises(ValueError):
        freeze_json_value(value)


def test_valid_multilingual_astral_characters_and_escaped_pairs_keep_exact_values():
    expected = {"中文": "袁😀é\u2028"}
    assert decode_json(json.dumps(expected, ensure_ascii=False).encode()) == expected
    assert decode_json(b'{"astral":"\\ud83d\\ude00"}') == {"astral": "😀"}
    assert freeze_json_value(expected) == expected


@pytest.mark.parametrize("mode", ["expected", "observed"])
@pytest.mark.parametrize(
    "value", [{"value": "\ud800"}, {"\udfff": 1}, {"value": {"nested": ["\udc00"]}}]
)
def test_independent_query_does_not_match_or_query_invalid_material(mode, value):
    query = Mock()
    query.read_business_object.return_value = value if mode == "observed" else {"value": "normal"}
    request = VerificationRequest(
        "attempt",
        "object",
        "readonly",
        "immediate",
        "deployment",
        expected_facts=value if mode == "expected" else {"value": "normal"},
    )
    result = BusinessVerificationAdapter(query).capture(request)
    assert result.actual_fields is None and result.verification.actual_result_ref is None
    assert result.verification.observation is (
        VerificationObservation.NO_RESULT
        if mode == "expected"
        else VerificationObservation.QUERY_ERROR
    )
    if mode == "expected":
        query.read_business_object.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "verification_of",
        "business_object_id",
        "query_method",
        "deadline_condition",
        "target_deployment_ref",
        "query_interval",
        "evidence_refs",
    ],
)
def test_frozen_query_identity_refuses_surrogates_before_provider(field):
    request = VerificationRequest(
        "attempt", "object", "readonly", "immediate", "deployment", expected_facts={"paid": True}
    )
    request = replace(request, **{field: ("\ud800",) if field == "evidence_refs" else "\ud800"})
    provider = Mock()
    with pytest.raises(ValueError):
        EvidenceReviewService(provider).review(request)
    provider.verify.assert_not_called()


@pytest.mark.parametrize("encoding", ["utf-16-be", "utf-32-le"])
def test_unverified_reply_encoding_closes_connection_and_is_not_retried(encoding):
    channel = Mock()
    channel.read_message.return_value = (
        Response(
            request_id="request", instance_id="core", workspace_id="workspace", result={"ok": True}
        )
        .model_dump_json()
        .encode(encoding)
    )
    client = CoreClient(CoreEndpoint("workspace", "core", channel))
    command = read_command(b'{"request_id":"request","action":"doctor"}')
    with pytest.raises(CoreResultUnverified):
        client.send(command)
    channel.write_message.assert_called_once()
    channel.close.assert_called_once()


def test_mcp_bad_unicode_identity_emits_static_error_then_handles_valid_frame():
    instance, sent, _ = relay()
    bad = b'{"jsonrpc":"2.0","id":"\\ud800","method":"ping"}\n'
    sink = io.BytesIO()
    instance.serve(io.BytesIO(bad + encode(rpc("good", "ping")) + b"\n"), sink)
    replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert replies[0]["id"] is None and replies[0]["error"]["code"] == -32700
    assert replies[1] == {"jsonrpc": "2.0", "id": "good", "result": {}}
    assert len(sent) == 2


def test_mcp_utf16_be_frame_is_rejected_without_core_dispatch():
    instance, sent, _ = relay()
    response = instance.handle(encode(rpc(7, "ping")).decode().encode("utf-16-be"))
    assert response["id"] is None and response["error"]["code"] == -32700
    assert len(sent) == 2


def test_http_invalid_unicode_body_keeps_actual_bytes_and_unknown_assertion():
    spec = HttpRequestSpec(
        "request",
        "GET",
        "https://synthetic.invalid",
        assertions=(HttpAssertion("value", "value", HttpAssertionOperator.EQUALS, "normal"),),
    )
    body = b'{"value":"\\ud800"}'
    result = HttpAdapter._enrich(
        HttpExchangeResult("request", "GET", spec.url, 200, body=body), spec
    )
    assert result.body == body and result.body_complete is True
    assert result.assertion_results[0].matched is None
    assert result.assertion_results[0].value_available is False


@pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="Actual CLI/MCP and same Windows core"
)
def test_actual_cli_and_stdio_reject_bad_unicode_then_share_unchanged_core(tmp_path):
    from aitest.bootstrap import acquire_endpoint, shutdown_endpoint
    from aitest.contracts.commands import Command
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
    from tests.unit.test_mcp_stdio_read import initialize

    root = tmp_path / "data"
    unit = FileUnitOfWork(root)
    unit.begin("seed", "project")
    for kind, identity in (("project", "project"), ("binding", "binding")):
        unit.stage_record(
            aggregate_kind=kind,
            record_id=identity,
            expected_revision=0,
            payload={
                "project_id": "project",
                **({"binding_id": "binding"} if kind == "binding" else {}),
            },
        )
    unit.commit()
    endpoint = acquire_endpoint(root)
    endpoint.connection.close()
    before = (root / "workspace.json").read_bytes()
    try:
        for encoding in ("utf-16-be", "utf-32-le"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "aitest.interfaces.tools.cli",
                    "dispatch",
                    "--workspace",
                    str(root),
                ],
                input=Command(request_id="invalid-encoding", action="doctor")
                .model_dump_json()
                .encode(encoding),
                capture_output=True,
                timeout=30,
            )
            assert result.returncode == 2 and not result.stdout
        messages = [
            initialize(),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            rpc("中文😀", "tools/call", {"name": "aitest_doctor"}),
        ]
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
            input=b'{"jsonrpc":"2.0","id":"\\ud800","method":"ping"}\n'
            + b"\n".join(encode(message) for message in messages)
            + b"\n",
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        assert replies[0]["id"] is None and replies[0]["error"]["code"] == -32700
        assert replies[-1]["id"] == "中文😀"
        actual = replies[-1]["result"]["structuredContent"]
        assert (
            actual["instance_id"] == endpoint.instance_id
            and actual["workspace_id"] == endpoint.workspace_id
        )
        assert (root / "workspace.json").read_bytes() == before
    finally:
        shutdown_endpoint(root)
