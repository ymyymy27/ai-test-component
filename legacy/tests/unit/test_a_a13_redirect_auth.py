import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.application.planning.model_ports import (
    ModelCall,
    ModelCallStatus,
    ProjectedMaterial,
)
from aitest.contracts.secrets import ResolvedSecret
from aitest.domain.planning.model_outbound import MaterialKind
from aitest.infrastructure.adapters.model import HttpModelProvider

_TOKEN = "Bearer super-secret-token"


class _RedirectHandler(BaseHTTPRequestHandler):
    target_location: str = ""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(302)
        self.send_header("Location", self.target_location)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class _SinkHandler(BaseHTTPRequestHandler):
    received_auth: list[str] = []
    hit_count: int = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        _SinkHandler.hit_count += 1
        _SinkHandler.received_auth.append(self.headers.get("Authorization", ""))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            b'{"id":"p-1","choices":[{"message":{"content":"draft"}}]}'
        )

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture
def servers() -> Iterator[tuple[ThreadingHTTPServer, ThreadingHTTPServer]]:
    origin = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    sink = ThreadingHTTPServer(("127.0.0.1", 0), _SinkHandler)
    _RedirectHandler.target_location = (
        f"http://127.0.0.1:{sink.server_address[1]}/chat/completions"
    )
    _SinkHandler.received_auth = []
    _SinkHandler.hit_count = 0
    threads = [
        threading.Thread(target=srv.serve_forever, daemon=True)
        for srv in (origin, sink)
    ]
    for thread in threads:
        thread.start()
    yield origin, sink
    for srv in (origin, sink):
        srv.shutdown()
        srv.server_close()


def _provider(endpoint: str) -> HttpModelProvider:
    secret = ResolvedSecret(
        purpose="model", reference="deepseek", source="environment", _value=_TOKEN
    )
    return HttpModelProvider(endpoint, secret=secret)


def _call(endpoint: str) -> ModelCall:
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
        endpoint_address=endpoint,
        model_id="test-model",
        timeout_seconds=10,
        policy_revision=1,
    )


def test_redirect_is_not_followed_and_authorization_not_forwarded(
    servers: tuple[ThreadingHTTPServer, ThreadingHTTPServer],
) -> None:
    origin, sink = servers
    endpoint = f"http://127.0.0.1:{origin.server_address[1]}"
    result = _provider(endpoint).call(_call(endpoint))

    assert result.status is ModelCallStatus.FAILED
    assert result.error_kind == "unconfirmed_redirect"
    # 重定向目标从未被请求：Authorization 不会被转发到未确认的另一域。
    assert _SinkHandler.hit_count == 0
    assert _SinkHandler.received_auth == []


def test_non_redirect_success_still_works(
    servers: tuple[ThreadingHTTPServer, ThreadingHTTPServer],
) -> None:
    _origin, sink = servers
    endpoint = f"http://127.0.0.1:{sink.server_address[1]}"
    result = _provider(endpoint).call(_call(endpoint))
    assert result.status is ModelCallStatus.OK
    assert _SinkHandler.hit_count == 1
