"""Actual HTTP workflow chain against a local server; no mocked exchange."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aitest.contracts.http_workflow import (
    HttpWorkflowAssertion,
    HttpWorkflowRequest,
    HttpWorkflowSpec,
)
from aitest.contracts.secrets import ResolvedSecret
from aitest.infrastructure.adapters.execution.http_workflow import HttpWorkflowAdapter


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        assert json.loads(body) == {"name": "demo"}
        self._send({"data": {"id": "order-1"}})

    def do_GET(self) -> None:
        if self.path == "/orders/order-1":
            self._send({"data": {"id": "order-1", "status": "new"}})
        elif self.path == "/secure":
            if self.headers.get("Authorization") != "Bearer token-value":
                self._send({"error": "unauthorized"}, status=401)
            else:
                self._send({"data": {"ok": True}})
        else:
            self._send({"error": "not found"}, status=404)

    def _send(self, payload: object, *, status: int = 200) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def server():
    instance = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.shutdown()
        thread.join(timeout=5)


def _spec(base: str, *, assertion_expected: str = "new") -> HttpWorkflowSpec:
    return HttpWorkflowSpec(
        requests=(
            HttpWorkflowRequest(
                request_id="create",
                method="POST",
                url=base + "/orders",
                headers=(("Content-Type", "application/json"),),
                body='{"name":"demo"}',
                extract={"order_id": "data.id"},
            ),
            HttpWorkflowRequest(
                request_id="read",
                method="GET",
                url=base + "/orders/{{order_id}}",
                assertions=(
                    HttpWorkflowAssertion(
                        assertion_id="status",
                        json_path="data.status",
                        operator="equals",
                        expected=assertion_expected,
                    ),
                ),
            ),
        )
    )


def test_actual_chain_extracts_and_reuses_the_identifier(server: str) -> None:
    capture = HttpWorkflowAdapter().execute(_spec(server))
    assert capture.observation == "matched"
    assert capture.gaps == ()
    assert [item.request_id for item in capture.exchanges] == ["create", "read"]
    assert capture.variables["order_id"] == "order-1"


def test_assertion_mismatch_is_a_failure_not_a_gap(server: str) -> None:
    capture = HttpWorkflowAdapter().execute(_spec(server, assertion_expected="done"))
    assert capture.observation == "mismatched"
    assert capture.gaps == ()


def test_missing_extraction_stops_the_chain_with_a_gap(server: str) -> None:
    spec = HttpWorkflowSpec(
        requests=(
            HttpWorkflowRequest(
                request_id="create",
                method="POST",
                url=server + "/orders",
                headers=(("Content-Type", "application/json"),),
                body='{"name":"demo"}',
                extract={"missing": "data.nope"},
            ),
        )
    )
    capture = HttpWorkflowAdapter().execute(spec)
    assert capture.observation == "no_result"
    assert any(item.startswith("http_extraction_missing:create") for item in capture.gaps)


def test_forward_reference_is_refused_before_any_request(server: str) -> None:
    with pytest.raises(ValueError, match="before extraction"):
        HttpWorkflowSpec(
            requests=(
                HttpWorkflowRequest(
                    request_id="read",
                    method="GET",
                    url=server + "/orders/{{order_id}}",
                ),
            )
        )


def test_no_assertions_is_an_explicit_gap(server: str) -> None:
    spec = HttpWorkflowSpec(
        requests=(
            HttpWorkflowRequest(
                request_id="read",
                method="GET",
                url=server + "/orders/order-1",
            ),
        )
    )
    capture = HttpWorkflowAdapter().execute(spec)
    assert capture.observation == "no_result"
    assert "http_assertions_missing" in capture.gaps


class _Secrets:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def resolve(self, reference: str, *, purpose: str) -> ResolvedSecret:
        if not self.available:
            raise ValueError("secret unavailable")
        return ResolvedSecret(purpose, reference, "fixture", "Bearer token-value")


def test_http_secret_reference_is_resolved_into_the_request(server: str) -> None:
    spec = HttpWorkflowSpec(
        requests=(
            HttpWorkflowRequest(
                request_id="secure",
                method="GET",
                url=server + "/secure",
                headers=(("Authorization", "{{secret:token}}"),),
                assertions=(
                    HttpWorkflowAssertion(
                        assertion_id="ok",
                        json_path="data.ok",
                        operator="equals",
                        expected=True,
                    ),
                ),
            ),
        ),
        secret_refs={"token": "tested-http-token"},
    )
    capture = HttpWorkflowAdapter(secrets=_Secrets()).execute(spec)
    assert capture.observation == "matched"
    assert capture.gaps == ()


def test_missing_http_secret_blocks_before_sending(server: str) -> None:
    spec = HttpWorkflowSpec(
        requests=(
            HttpWorkflowRequest(
                request_id="secure",
                method="GET",
                url=server + "/secure",
                headers=(("Authorization", "{{secret:token}}"),),
                assertions=(
                    HttpWorkflowAssertion(
                        assertion_id="ok",
                        json_path="data.ok",
                        operator="equals",
                        expected=True,
                    ),
                ),
            ),
        ),
        secret_refs={"token": "tested-http-token"},
    )
    capture = HttpWorkflowAdapter(secrets=_Secrets(available=False)).execute(spec)
    assert capture.observation == "no_result"
    assert "http_secret_unavailable:token" in capture.gaps
