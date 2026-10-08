"""Real independent outbound GET/JSON with synthetic registered deployment."""

import json
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.ports import VerificationRequest
from aitest.contracts.secrets import ResolvedSecret
from aitest.domain.evidence.evidence import VerificationObservation
from aitest.infrastructure.adapters.execution import business_query as module
from aitest.infrastructure.adapters.execution.business_query import (
    HttpBusinessQueryReader,
    HttpBusinessQuerySpec,
)
from aitest.infrastructure.adapters.execution.http_transport import NetworkExchange
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter
from aitest.infrastructure.security import known_secrets
from tests.unit.test_http_transport_limits import server


def body_reply(body, status=200, extra_headers=b""):
    def reply(connection, stopped):
        connection.sendall(
            f"HTTP/1.1 {status} Result\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + extra_headers
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )

    return reply


def request():
    return VerificationRequest(
        "attempt-1",
        "order-1",
        "http-read-order",
        "poll_deadline_ms:500",
        "deployment-1",
        query_interval="poll_interval_ms:20",
        expected_facts={"paid": True},
    )


def reader(url, **changes):
    return HttpBusinessQueryReader(
        HttpBusinessQuerySpec("deployment-1", url + "orders/{business_object_id}", **changes)
    )


def read(value, business_object_id="order-1", cutoff=None):
    return value.read_business_object_before(
        business_object_id=business_object_id,
        target_deployment_ref="deployment-1",
        deadline_monotonic=cutoff or time.monotonic() + 1,
    )


def test_actual_independent_http_get_reads_complete_same_object_without_request_body():
    with server(body_reply(b'{"object_id":"order-1","paid":true}')) as (url, calls):
        result = BusinessVerificationAdapter(reader(url)).capture(request())
        assert result.verification.observation is VerificationObservation.MATCHED
        assert result.actual_fields == {"object_id": "order-1", "paid": True}
        assert len(result.query_observations) == 1 and len(calls) == 1
        assert calls[0].startswith(b"GET /orders/order-1 HTTP/1.1\r\n")
        assert calls[0].endswith(b"\r\n\r\n")


def test_actual_not_found_is_requeried_then_visible_under_same_cutoff():
    count = 0

    def reply(connection, stopped):
        nonlocal count
        count += 1
        body_reply(b"{}", 404)(connection, stopped) if count == 1 else body_reply(
            b'{"object_id":"order-1","paid":true}'
        )(connection, stopped)

    with server(reply) as (url, calls):
        result = BusinessVerificationAdapter(reader(url)).capture(request())
        assert result.verification.observation is VerificationObservation.MATCHED
        assert len(calls) == len(result.query_observations) == 2
        assert result.query_observations[0].actual_fields is None


def test_actual_object_identifier_is_encoded_without_changing_target_or_query_fields():
    identity = "order/中文?extra=1&other=2"
    with server(body_reply(json.dumps({"object_id": identity}).encode())) as (url, calls):
        assert read(reader(url), identity)["object_id"] == identity
        assert b"/orders/order%2F%E4%B8%AD%E6%96%87%3Fextra%3D1%26other%3D2" in calls[0]
        assert len(calls) == 1


@pytest.mark.parametrize(
    "body,headers",
    [
        (b'{"object_id":"other","paid":true}', b""),
        (b'{"object_id":1,"paid":true}', b""),
        (b'{"object_id":"order-1","paid":true,"paid":false}', b""),
        (b'{"object_id":"order-1","paid":NaN}', b""),
        (b'[{"object_id":"order-1","paid":true}]', b""),
        ('{"object_id":"order-1","paid":true}'.encode("utf-16"), b""),
        (b'{"object_id":"order-1","paid":true}', b"Content-Type: text/plain\r\n"),
    ],
)
def test_actual_wrong_identity_ambiguous_json_or_content_type_is_query_error(body, headers):
    with server(body_reply(body, extra_headers=headers)) as (url, calls):
        result = BusinessVerificationAdapter(reader(url)).capture(request())
        assert result.verification.observation is VerificationObservation.QUERY_ERROR
        assert result.actual_fields is None and len(calls) == 1


@pytest.mark.parametrize("status", [302, 500])
def test_actual_redirect_or_server_error_does_not_retry_or_accept_json(status):
    with server(
        body_reply(
            b'{"object_id":"order-1","paid":true}',
            status,
            b"Location: http://127.0.0.1:1/wrong\r\n",
        )
    ) as (url, calls):
        result = BusinessVerificationAdapter(reader(url)).capture(request())
        assert result.verification.observation is VerificationObservation.QUERY_ERROR
        assert result.actual_fields is None and len(calls) == 1


@pytest.mark.parametrize("damage", ["short", "oversize", "trickle"])
def test_actual_incomplete_oversize_or_late_http_prefix_cannot_verify(damage):
    body = b'{"object_id":"order-1","paid":true}'

    def reply(connection, stopped):
        length = len(body) + 4 if damage == "short" else len(body)
        connection.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {length}\r\n\r\n".encode()
        )
        if damage != "trickle":
            connection.sendall(body)
        else:
            for byte in body:
                if stopped.wait(0.03):
                    return
                connection.sendall(bytes([byte]))

    with server(reply) as (url, calls):
        value = reader(url, max_response_bytes=10 if damage == "oversize" else 1024)
        spec = replace(request(), deadline_condition="poll_deadline_ms:100")
        began = time.monotonic()
        result = BusinessVerificationAdapter(value).capture(spec)
        assert time.monotonic() - began < 0.8
        assert result.verification.observation is (
            VerificationObservation.DEADLINE_REACHED
            if damage == "trickle"
            else VerificationObservation.QUERY_ERROR
        )
        assert result.actual_fields is None and len(calls) == 1


@pytest.mark.parametrize(
    "template",
    [
        "http://localhost/orders",
        "http://{business_object_id}/orders",
        "http://localhost/{business_object_id}/{business_object_id}",
        "http://localhost/{other}/{business_object_id}",
        "http://user:pass@localhost/{business_object_id}",
        "http://localhost/{business_object_id}#fragment",
        "http://localhost/{business_object_id}?token=private",
    ],
)
def test_unsafe_or_ambiguous_configuration_is_refused_before_io(template):
    with pytest.raises(ValueError):
        HttpBusinessQuerySpec("deployment-1", template)


@pytest.mark.parametrize(
    "damage",
    [
        "deployment",
        "expired",
        "bool_time",
        "nan_time",
        "excessive_time",
        "overflow_time",
        "dot",
        "surrogate",
    ],
)
def test_wrong_scope_or_cutoff_never_reaches_network(monkeypatch, damage):
    fetch = Mock()
    monkeypatch.setattr(module, "exchange", fetch)
    value = reader("http://localhost/")
    kwargs = {
        "business_object_id": "order-1",
        "target_deployment_ref": "deployment-1",
        "deadline_monotonic": time.monotonic() + 1,
    }
    if damage == "deployment":
        kwargs["target_deployment_ref"] = "other"
    elif damage in ("expired", "bool_time", "nan_time", "excessive_time", "overflow_time"):
        kwargs["deadline_monotonic"] = {
            "expired": 0,
            "bool_time": True,
            "nan_time": float("nan"),
            "excessive_time": time.monotonic() + 61,
            "overflow_time": 10**400,
        }[damage]
    else:
        kwargs["business_object_id"] = ".." if damage == "dot" else "\ud800"
    with pytest.raises((ValueError, TimeoutError)):
        value.read_business_object_before(**kwargs)
    fetch.assert_not_called()


def test_exact_http_purpose_credential_is_memory_only_and_revocation_blocks_sending(monkeypatch):
    secret = ResolvedSecret("http", "query-ref", "fixture", "query-private-credential-value")
    spec = HttpBusinessQuerySpec(
        "deployment-1",
        "https://example.test/{business_object_id}",
        credential_reference="query-ref",
    )
    value = HttpBusinessQueryReader(spec, secret=secret)
    fetch = Mock(
        return_value=NetworkExchange(
            200, (("Content-Type", "application/json"),), b'{"object_id":"order-1"}', True
        )
    )
    monkeypatch.setattr(module, "exchange", fetch)
    try:
        assert read(value) == {"object_id": "order-1"}
        assert fetch.call_args.kwargs["headers"][-1] == (
            "Authorization",
            "Bearer query-private-credential-value",
        )
        assert "query-private-credential-value" not in repr(value) + repr(spec) + repr(secret)
        secret.clear()
        with pytest.raises(ValueError):
            read(value)
        assert fetch.call_count == 1
    finally:
        known_secrets().clear()


@pytest.mark.parametrize("damage", ["http_url", "purpose", "reference", "inline_control"])
def test_credential_transport_or_scope_mismatch_is_refused(damage):
    spec = HttpBusinessQuerySpec(
        "deployment-1",
        "https://example.test/{business_object_id}",
        credential_reference="query-ref",
    )
    secret = ResolvedSecret("http", "query-ref", "fixture", "query-safe-credential")
    try:
        with pytest.raises(ValueError):
            if damage == "http_url":
                replace(spec, url_template="http://localhost/{business_object_id}")
            else:
                secret.purpose = "model" if damage == "purpose" else secret.purpose
                secret.reference = "other" if damage == "reference" else secret.reference
                secret._value = (
                    "value\r\nInjected: yes" if damage == "inline_control" else secret._value
                )
                HttpBusinessQueryReader(spec, secret=secret)
    finally:
        known_secrets().clear()
