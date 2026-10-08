"""Actual POST deadlines, response integrity and credentials at the model boundary."""

import json
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.ports import ModelCallStatus
from aitest.contracts.secrets import ResolvedSecret
from aitest.infrastructure.adapters.model import HttpModelProvider, HttpResponse, UrllibTransport
from tests.unit.test_a_model_provider import _call
from tests.unit.test_http_transport_limits import server

GOOD = b'{"id":"request-1","choices":[{"message":{"content":"safe draft"}}]}'


def provider(url, **kwargs):
    return HttpModelProvider(
        url.rstrip("/"),
        secret=ResolvedSecret("model", "fixture", "environment", "fixture-key"),
        **kwargs,
    )


@pytest.mark.parametrize("status", [200, 500])
def test_actual_model_success_and_error_trickle_share_one_total_cutoff(status):
    def reply(connection, stopped):
        connection.sendall(
            f"HTTP/1.1 {status} Result\r\nContent-Length: {len(GOOD)}\r\n\r\n".encode()
        )
        for byte in GOOD:
            if stopped.wait(0.01):
                return
            connection.sendall(bytes([byte]))

    with server(reply) as (url, calls):
        started = time.monotonic()
        result = provider(url).call(replace(_call(endpoint=url), timeout_seconds=0.12))
        assert time.monotonic() - started < 0.4
        assert result.status is ModelCallStatus.FAILED
        assert result.error_kind == "connectivity" and not result.draft_text
        assert len(calls) == 1


@pytest.mark.parametrize("framing", ["length", "chunked"])
def test_complete_json_prefix_is_not_a_complete_model_response(framing):
    def reply(connection, stopped):
        if framing == "length":
            data = f"Content-Length: {len(GOOD) + 9}\r\n\r\n".encode() + GOOD
        else:
            data = (
                b"Transfer-Encoding: chunked\r\n\r\n"
                + f"{len(GOOD):x}\r\n".encode() + GOOD + b"\r\n"
            )
        connection.sendall(b"HTTP/1.1 200 OK\r\n" + data)

    with server(reply) as (url, calls):
        result = provider(url).call(_call(endpoint=url))
        assert result.status is ModelCallStatus.FAILED and result.error_kind == "structure"
        assert not result.draft_text and len(calls) == 1


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
@pytest.mark.parametrize("extra", [0, 1])
def test_actual_model_body_budget_includes_error_and_exact_eof(status, framing, extra):
    body = GOOD + b" " * extra

    def reply(connection, stopped):
        head = f"HTTP/1.1 {status} Result\r\n".encode()
        data = body
        if framing == "length":
            head += f"Content-Length: {len(body)}\r\n".encode()
        elif framing == "chunked":
            head += b"Transfer-Encoding: chunked\r\n"
            data = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        connection.sendall(head + b"\r\n" + data)

    with server(reply) as (url, calls):
        response = UrllibTransport(max_response_bytes=len(GOOD)).post(
            url, headers={"Content-Type": "application/json"}, body=b"{}", timeout_seconds=1
        )
        assert response.body == GOOD and response.status == status
        assert response.body_complete is (not extra)
        assert response.error_class == ("response_too_large" if extra else None)
        assert len(calls) == 1 and calls[0].startswith(b"POST ")


@pytest.mark.parametrize(
    "body",
    [
        b'{"choices":[],"choices":[{"message":{"content":"last draft"}}]}',
        b'{"choices":[{"message":{"content":"first","content":"last"}}]}',
        b'{"extra":NaN,"choices":[{"message":{"content":"draft"}}]}',
        b'{"extra":1e9999,"choices":[{"message":{"content":"draft"}}]}',
        b'{"choices":[{"message":{"content":"\\ud800"}}]}',
        GOOD.decode().encode("utf-16"),
    ],
)
def test_ambiguous_or_non_wire_json_never_becomes_a_draft(body):
    transport = Mock()
    transport.post.return_value = HttpResponse(200, body)
    result = provider("https://example.invalid", transport=transport).call(
        _call(endpoint="https://example.invalid")
    )
    assert result.status is ModelCallStatus.FAILED and result.error_kind == "structure"
    assert not result.draft_text and transport.post.call_count == 1


@pytest.mark.parametrize("value", [True, 12, {}, [], "", "   "])
def test_provider_request_id_is_an_exact_nonempty_string(value):
    transport = Mock()
    transport.post.return_value = HttpResponse(
        200, json.dumps({"id": value, "choices": [{"message": {"content": "draft"}}]}).encode()
    )
    result = provider("https://example.invalid", transport=transport).call(
        _call(endpoint="https://example.invalid")
    )
    assert result.status is ModelCallStatus.FAILED and result.error_kind == "structure"


@pytest.mark.parametrize("status", [201, 202, 204, 206, True])
def test_only_complete_exact_200_can_return_a_draft(status):
    transport = Mock()
    transport.post.return_value = HttpResponse(status, GOOD)
    result = provider("https://example.invalid", transport=transport).call(
        _call(endpoint="https://example.invalid")
    )
    assert result.status is ModelCallStatus.FAILED and not result.draft_text


@pytest.mark.parametrize("mutation", ["clear", "purpose", "reference", "header"])
def test_credential_is_rechecked_before_every_send(mutation):
    secret = ResolvedSecret("model", "fixture", "environment", "fixture-key")
    transport = Mock()
    value = HttpModelProvider("https://example.invalid", secret=secret, transport=transport)
    if mutation == "clear":
        secret.clear()
    elif mutation == "purpose":
        secret.purpose = "http"
    elif mutation == "reference":
        secret.reference = "different"
    else:
        secret._value = "fixture-key\r\nX-Injected: yes"
    result = value.call(_call(endpoint="https://example.invalid"))
    assert result.status is ModelCallStatus.FAILED
    assert not result.draft_text and not transport.post.called
    assert "fixture-key" not in result.error_detail


@pytest.mark.parametrize("status", [200, 500])
def test_actual_model_response_headers_and_silence_have_same_cutoff(status):
    def reply(connection, stopped):
        for byte in f"HTTP/1.1 {status} Result\r\nContent-Length: 0\r\n\r\n".encode():
            if stopped.wait(0.02):
                return
            connection.sendall(bytes([byte]))

    with server(reply) as (url, calls):
        started = time.monotonic()
        result = provider(url).call(replace(_call(endpoint=url), timeout_seconds=0.12))
        assert time.monotonic() - started < 0.4
        assert result.error_kind == "connectivity" and not result.draft_text
        assert len(calls) == 1


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 10**400])
def test_invalid_model_cutoff_is_rejected_before_post(value):
    transport = Mock()
    result = provider("https://example.invalid", transport=transport).call(
        replace(_call(endpoint="https://example.invalid"), timeout_seconds=value)
    )
    assert result.status is ModelCallStatus.FAILED and not transport.post.called


@pytest.mark.parametrize("value", [True, 0, -1, 1.0, 4 * 1024 * 1024 + 1])
def test_response_budget_is_an_actual_bounded_positive_integer(value):
    with pytest.raises(ValueError):
        UrllibTransport(max_response_bytes=value)


def test_error_secret_crossing_detail_limit_is_filtered_before_truncation():
    transport = Mock()
    transport.post.return_value = HttpResponse(
        401, b'{"error":"' + b"x" * 498 + b'fixture-key"}'
    )
    result = provider("https://example.invalid", transport=transport).call(
        _call(endpoint="https://example.invalid")
    )
    assert result.error_kind == "auth" and len(result.error_detail) <= 512
    assert not result.error_detail.endswith("fixt")
    assert "[RE" in result.error_detail and "fixture-key" not in result.error_detail


def test_error_json_escaped_secret_is_decoded_and_filtered_before_detail():
    escaped = "".join(f"\\u{ord(char):04x}" for char in "fixture-key")
    transport = Mock()
    transport.post.return_value = HttpResponse(401, ('{"message":"' + escaped + '"}').encode())
    result = provider("https://example.invalid", transport=transport).call(
        _call(endpoint="https://example.invalid")
    )
    assert result.error_kind == "auth" and "[REDACTED]" in result.error_detail
    assert "fixture-key" not in result.error_detail and "\\u0066" not in result.error_detail


def test_malformed_error_json_is_a_gap_without_raw_body_in_detail():
    transport = Mock()
    transport.post.return_value = HttpResponse(401, b'{"message":"fixture-key",bad}')
    result = provider("https://example.invalid", transport=transport).call(
        _call(endpoint="https://example.invalid")
    )
    assert result.error_kind == "auth" and "HTTP 401" in result.error_detail
    assert "fixture-key" not in result.error_detail and "message" not in result.error_detail


def test_actual_redirect_does_not_send_to_unconfirmed_target():
    def reply(connection, stopped):
        connection.sendall(
            b"HTTP/1.1 302 Found\r\nLocation: http://other.invalid/\r\nContent-Length: 0\r\n\r\n"
        )

    with server(reply) as (url, calls):
        result = provider(url).call(_call(endpoint=url))
        assert result.error_kind == "unconfirmed_redirect" and not result.draft_text
        assert len(calls) == 1
