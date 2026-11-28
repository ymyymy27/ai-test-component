"""健康检查、探针目标选择与前端绝对 URL 提取的回归测试。"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from aitest.core.context.contracts import _frontend_api_calls
from aitest.core.probes import (
    ContractReconciliationProbe,
    RouteBoundaryProbe,
    StaticAssetsProbe,
    run_deterministic_probes,
)
from aitest.worker_impl.lifecycle import (
    _probe_targets,
    _service_role,
)
from aitest.worker_impl.services import _healthy


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_healthy_sends_browser_accept_header(monkeypatch) -> None:
    """SPA 开发服务器只在 Accept 含 HTML 时回 index.html，裸 GET 会 404。"""
    captured: dict[str, Any] = {}

    class FakeResponse:
        status = 200

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return False

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0):
        captured["request"] = request
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert _healthy("http://127.0.0.1:5173") is True
    accept = captured["request"].get_header("Accept") or ""
    assert "text/html" in accept.lower()
    assert "*/*" in accept


def test_healthy_still_rejects_server_errors(monkeypatch) -> None:
    """补 Accept 不能把 5xx 当成健康，避免错配健康路径被误判就绪。"""

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert _healthy("http://127.0.0.1:5001/ping") is False


def test_probe_skip_reason_distinguishes_missing_url_and_unhealthy() -> None:
    static_missing = StaticAssetsProbe().run(base_url="", service_ready=False)
    assert "基础 URL" in static_missing["summary"]

    static_unhealthy = StaticAssetsProbe().run(
        base_url="http://127.0.0.1:5173", service_ready=False
    )
    assert "健康检查" in static_unhealthy["summary"]

    route_unhealthy = RouteBoundaryProbe().run(
        base_url="http://127.0.0.1:5001", service_ready=False
    )
    assert "健康检查" in route_unhealthy["summary"]


def test_service_role_uses_tokens() -> None:
    assert _service_role("frontend") == "frontend"
    assert _service_role("web-client") == "frontend"
    assert _service_role("backend") == "backend"
    assert _service_role("web-api") == "backend"
    assert _service_role("default") == ""


def _service_states(*, frontend_ok: bool, backend_ok: bool) -> list[dict[str, Any]]:
    return [
        {
            "name": "backend",
            "ok": backend_ok,
            "config": {"health": "http://127.0.0.1:5001/ping"},
        },
        {
            "name": "frontend",
            "ok": frontend_ok,
            "config": {"health": "http://127.0.0.1:5173"},
        },
    ]


def test_probe_targets_route_by_service_role() -> None:
    """前端不健康只应跳过前端探针，不能连累后端路由边界探针。"""
    targets = _probe_targets(
        _service_states(frontend_ok=False, backend_ok=True),
        base_url="http://127.0.0.1:5001",
        service_ready=False,
    )

    assert targets["static_assets"] == ("http://127.0.0.1:5173", False)
    assert targets["route_boundary"] == ("http://127.0.0.1:5001", True)


def test_probe_targets_keep_single_service_behavior() -> None:
    states = [
        {
            "name": "default",
            "ok": True,
            "config": {"health": "http://127.0.0.1:8080/health"},
        }
    ]

    targets = _probe_targets(
        states, base_url="http://127.0.0.1:8080", service_ready=True
    )

    assert targets["static_assets"] == ("http://127.0.0.1:8080", True)
    assert targets["route_boundary"] == ("http://127.0.0.1:8080", True)


def test_run_deterministic_probes_applies_target_override(monkeypatch) -> None:
    seen: dict[str, dict[str, Any]] = {}

    class FakeProbe:
        def __init__(self, probe_id: str) -> None:
            self.probe_id = probe_id
            self.name = probe_id

        def run(self, **kwargs: Any) -> dict[str, Any]:
            seen[self.probe_id] = kwargs
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "summary": "fake",
                "findings": [],
            }

    monkeypatch.setattr(
        "aitest.core.probes.BUILTIN_PROBES",
        [FakeProbe("static_assets"), FakeProbe("route_boundary")],
    )

    run_deterministic_probes(
        Path("."),
        {},
        base_url="http://127.0.0.1:5001",
        service_ready=True,
        probe_targets={"static_assets": ("http://127.0.0.1:5173", False)},
    )

    assert seen["static_assets"]["base_url"] == "http://127.0.0.1:5173"
    assert seen["static_assets"]["service_ready"] is False
    assert seen["route_boundary"]["base_url"] == "http://127.0.0.1:5001"
    assert seen["route_boundary"]["service_ready"] is True


def test_frontend_extracts_local_absolute_urls(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "client/src/components/Books.vue",
        (
            "<script setup>\n"
            "import axios from 'axios';\n"
            "const listPath = 'http://localhost:5001/books';\n"
            "const onePath = `http://127.0.0.1:5001/books/${bookID}`;\n"
            "function load() { axios.get(listPath); }\n"
            "function remove() { axios.delete(onePath); }\n"
            "</script>\n"
        ),
    )

    calls = _frontend_api_calls(tmp_path)
    got = {(call["method"], call["path"]) for call in calls}

    assert ("GET", "/books") in got
    assert ("DELETE", "/books/{param}") in got


def test_frontend_ignores_external_absolute_urls(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "client/src/components/Repo.vue",
        (
            "<script setup>\n"
            "import axios from 'axios';\n"
            "const remote = 'https://api.github.com/repos';\n"
            "function load() { axios.get(remote); }\n"
            "function direct() { axios.get('https://api.example.com/items'); }\n"
            "</script>\n"
        ),
    )

    assert _frontend_api_calls(tmp_path) == []


def test_frontend_axios_create_absolute_base_url(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "client/src/api.js",
        (
            "import axios from 'axios';\n"
            "const api = axios.create({ baseURL: 'http://localhost:5001/api' });\n"
            "export function listBooks() { return api.get('/books'); }\n"
        ),
    )

    calls = _frontend_api_calls(tmp_path)
    assert ("GET", "/api/books") in {(call["method"], call["path"]) for call in calls}


def test_frontend_const_reassignment_uses_call_site(tmp_path: Path) -> None:
    """同名常量在不同方法里分别指向集合与单条记录时，不能互相覆盖。"""
    _write(
        tmp_path,
        "client/src/components/Books.vue",
        (
            "<script setup>\n"
            "import axios from 'axios';\n"
            "function load() {\n"
            "  const path = 'http://localhost:5001/books';\n"
            "  axios.get(path);\n"
            "}\n"
            "function remove(id) {\n"
            "  const path = `http://localhost:5001/books/${id}`;\n"
            "  axios.delete(path);\n"
            "}\n"
            "</script>\n"
        ),
    )

    got = {(call["method"], call["path"]) for call in _frontend_api_calls(tmp_path)}

    assert ("GET", "/books") in got
    assert ("DELETE", "/books/{param}") in got


def test_contract_probe_passes_with_absolute_frontend_calls(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "client/src/components/Books.vue",
        (
            "<script setup>\n"
            "import axios from 'axios';\n"
            "const path = 'http://localhost:5001/books';\n"
            "axios.get(path);\n"
            "</script>\n"
        ),
    )
    _write(
        tmp_path,
        "server/app.py",
        (
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/books', methods=['GET'])\n"
            "def books():\n"
            "    return {'books': []}\n"
        ),
    )

    calls = _frontend_api_calls(tmp_path)
    assert calls
    result = ContractReconciliationProbe().run(
        root=tmp_path,
        frontend_api_calls=calls,
        api_routes=[{"method": "GET", "path": "/books"}],
        has_dynamic_loader=False,
    )
    assert result["status"] == "passed"
    assert result["checked"] == 1
