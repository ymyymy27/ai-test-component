"""Unit tests for the HTTP model provider and DeepSeek configuration."""

from __future__ import annotations

import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from aitest.application.planning.model_ports import (
    ModelCall,
    ModelCallStatus,
    ProjectedMaterial,
)
from aitest.contracts.secrets import ResolvedSecret
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.infrastructure.adapters.deepseek import (
    DeepSeekModelProvider,
    ModelProfile,
)
from aitest.infrastructure.adapters.model import (
    HttpModelProvider,
    HttpResponse,
    ModelAdapterError,
)


class _Handler(BaseHTTPRequestHandler):
    response_status: ClassVar[int] = 200
    response_body: ClassVar[bytes] = b""
    last_request: ClassVar[dict[str, object]] = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _Handler.last_request = {
            "path": self.path,
            "headers": dict(self.headers),
            "body": self.rfile.read(length),
        }
        self.send_response(_Handler.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_Handler.response_body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _Handler.response_status = 200
    _Handler.response_body = b"{}"
    yield httpd
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


@pytest.fixture
def secret() -> ResolvedSecret:
    return ResolvedSecret(
        purpose="model", reference="deepseek", source="environment", _value="key-123"
    )


def _call(model_id: str = "test-model") -> ModelCall:
    material = ProjectedMaterial(
        material_kind=MaterialKind.PROJECT_CONTEXT,
        field_path="material.project_context",
        projected_text="safe content",
        digest="sha256:abc",
    )
    return ModelCall(
        task_type="draft_checks",
        projected=(material,),
        projection_digest="sha256:proj",
        endpoint_address="http://endpoint",
        model_id=model_id,
        timeout_seconds=10,
        policy_revision=1,
    )


def _provider(server: ThreadingHTTPServer, secret: ResolvedSecret) -> HttpModelProvider:
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    return HttpModelProvider(endpoint, secret=secret)


def test_success_returns_draft_and_request_id(
    server: ThreadingHTTPServer, secret: ResolvedSecret
) -> None:
    _Handler.response_body = json.dumps(
        {"id": "provider-9", "choices": [{"message": {"content": "proposed draft"}}]}
    ).encode("utf-8")

    result = _provider(server, secret).call(_call())

    assert result.status is ModelCallStatus.OK
    assert result.draft_text == "proposed draft"
    assert result.provider_request_id == "provider-9"
    # 请求事实：路径、凭据只在头、正文只含投影材料
    assert _Handler.last_request["path"] == "/chat/completions"
    headers = _Handler.last_request["headers"]
    assert headers["Authorization"] == "Bearer key-123"
    body = json.loads(_Handler.last_request["body"])
    assert body["messages"][0]["content"] == "safe content"


def test_auth_failure(server: ThreadingHTTPServer, secret: ResolvedSecret) -> None:
    _Handler.response_status = 401
    _Handler.response_body = b'{"error":"bad key"}'
    result = _provider(server, secret).call(_call())
    assert result.status is ModelCallStatus.FAILED
    assert result.error_kind == "auth"


def test_rate_limit(server: ThreadingHTTPServer, secret: ResolvedSecret) -> None:
    _Handler.response_status = 429
    result = _provider(server, secret).call(_call())
    assert result.error_kind == "rate_limit"


def test_input_limit(server: ThreadingHTTPServer, secret: ResolvedSecret) -> None:
    _Handler.response_status = 413
    result = _provider(server, secret).call(_call())
    assert result.error_kind == "input_limit"


def test_structure_error_on_bad_json(
    server: ThreadingHTTPServer, secret: ResolvedSecret
) -> None:
    _Handler.response_body = b"not json"
    result = _provider(server, secret).call(_call())
    assert result.error_kind == "structure"


def test_connectivity_error(secret: ResolvedSecret) -> None:
    class BrokenTransport:
        def post(self, *args: object, **kwargs: object) -> HttpResponse:
            raise urllib.error.URLError("dns failure")

    provider = HttpModelProvider(
        "https://unreachable.invalid", transport=BrokenTransport(), secret=secret  # type: ignore[arg-type]
    )
    result = provider.call(_call())
    assert result.error_kind == "connectivity"


def test_missing_secret_raises(server: ThreadingHTTPServer) -> None:
    provider = HttpModelProvider(
        f"http://127.0.0.1:{server.server_address[1]}", secret=None
    )
    with pytest.raises(ModelAdapterError):
        provider.call(_call())


def test_deepseek_provider_profile_and_delegation(
    server: ThreadingHTTPServer, secret: ResolvedSecret
) -> None:
    _Handler.response_body = json.dumps(
        {"id": "p1", "choices": [{"message": {"content": "d"}}]}
    ).encode("utf-8")
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    provider = DeepSeekModelProvider("deepseek-chat", secret=secret, base_address=endpoint)

    profile = provider.profile
    assert isinstance(profile, ModelProfile)
    assert profile.model_id == "deepseek-chat"
    assert profile.provider == "deepseek"

    result = provider.call(_call("deepseek-chat"))
    assert result.status is ModelCallStatus.OK


def test_deepseek_requires_model_id(secret: ResolvedSecret) -> None:
    with pytest.raises(ValueError):
        DeepSeekModelProvider("   ", secret=secret)
