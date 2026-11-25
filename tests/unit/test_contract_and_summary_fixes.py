"""契约解析、代码图构建、AI 摘要纠偏与宿主环境提示回归测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from aitest.ai_features import summarize_report
from aitest.core.code_graph import build_code_graph
from aitest.core.context.contracts import (
    _api_routes,
    _frontend_api_calls,
    _frontend_assets,
)
from aitest.core.evidence import DEFAULT_FILE_CAP_BYTES, DEFAULT_MEDIA_BUDGET_MB
from aitest.runners.package_install import InstallOutcome
from aitest.storage.workspace import Workspace
from aitest.worker_impl import deps_and_l1


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class _SeqClient:
    """按调用次序回放模型响应的轻量桩对象。"""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls.append((system, user))
        if self._responses:
            return self._responses.pop(0)
        return {"summary": ""}


def test_frontend_assets_checks_subproject_public_and_static_dirs(tmp_path: Path) -> None:
    """子目录前端的 public/ 与 static/ 资源应识别为存在，真缺失资源仍告警。"""
    _write(
        tmp_path,
        "frontend/index.html",
        (
            '<link rel="icon" href="/assets/images/favicon.png" />'
            '<link rel="stylesheet" href="/css/theme.css" />'
            '<script src="/vite.svg"></script>'
        ),
    )
    _write(tmp_path, "frontend/public/assets/images/favicon.png", "png")
    _write(tmp_path, "frontend/static/css/theme.css", "body{}")

    assets = _frontend_assets(tmp_path)
    by_href = {item["href"]: item["exists_in_repo"] for item in assets}
    assert by_href["/assets/images/favicon.png"] is True
    assert by_href["/css/theme.css"] is True
    assert by_href["/vite.svg"] is False


def test_frontend_api_calls_filters_non_http_and_supports_ts_sdk_generics(
    tmp_path: Path,
) -> None:
    """过滤 startsWith 等非 HTTP 方法、跳过后端/route.ts，并识别带 TS 泛型的 SDK 调用。"""
    _write(
        tmp_path,
        "frontend/src/middleware.ts",
        (
            "export function guard(pathname: string) {\n"
            "  if (pathname.startsWith('/api/auth')) return true;\n"
            "  return pathname.includes('/api/ghost');\n"
            "}\n"
        ),
    )
    _write(
        tmp_path,
        "frontend/src/client/sdk.gen.ts",
        (
            "export function login(options: any) {\n"
            "  return (options.client ?? client).post<LoginResp, ErrorResp, false>({\n"
            "    url: '/api/v1/login/access-token',\n"
            "  });\n"
            "}\n"
        ),
    )
    _write(
        tmp_path,
        "src/app/api/orders/route.ts",
        "export async function GET() { return fetch('/api/internal/should-skip'); }\n",
    )
    _write(
        tmp_path,
        "backend/server/helper.js",
        "fetch('/api/server-only-call');\n",
    )

    calls = _frontend_api_calls(tmp_path)
    paths = {item["path"]: item["method"] for item in calls}
    assert "/api/v1/login/access-token" in paths
    assert paths["/api/v1/login/access-token"] == "POST"
    assert "/api/auth" not in paths
    assert "/api/ghost" not in paths
    assert "/api/internal/should-skip" not in paths
    assert "/api/server-only-call" not in paths


def test_api_routes_supports_nested_fastapi_nextjs_express_and_no_low_truncation(
    tmp_path: Path,
) -> None:
    """支持 FastAPI 多级含包路由、Next.js route.ts、Express 与超 120 路由不截断。"""
    _write(
        tmp_path,
        "backend/app/core/config.py",
        'class Settings:\n    API_V1_STR: str = "/api/v1"\nsettings = Settings()\n',
    )
    _write(
        tmp_path,
        "backend/app/api/routes/__init__.py",
        "from fastapi import APIRouter\n",
    )
    _write(
        tmp_path,
        "backend/app/api/routes/login.py",
        (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            '@router.post("/login/access-token")\n'
            "def access_token():\n"
            "    return {}\n"
        ),
    )
    _write(
        tmp_path,
        "backend/app/api/main.py",
        (
            "from fastapi import APIRouter\n"
            "from app.api.routes import login\n"
            "api_router = APIRouter()\n"
            "api_router.include_router(login.router)\n"
        ),
    )
    _write(
        tmp_path,
        "backend/app/main.py",
        (
            "from fastapi import FastAPI\n"
            "from app.api.main import api_router\n"
            "from app.core.config import settings\n"
            "app = FastAPI()\n"
            "app.include_router(api_router, prefix=settings.API_V1_STR)\n"
        ),
    )
    _write(
        tmp_path,
        "src/app/api/tasks/[id]/route.ts",
        "export async function GET() {}\nexport async function PATCH() {}\n",
    )
    express_lines = ["const express = require('express');", "const router = express.Router();"]
    for idx in range(135):
        express_lines.append(f"router.get('/api/items/item_{idx}', (req, res) => res.json({{}}));")
    _write(tmp_path, "server/routes/items.js", "\n".join(express_lines) + "\n")

    routes = _api_routes(tmp_path, max_routes=500)
    route_set = {(r["method"], r["path"]) for r in routes}
    assert ("POST", "/api/v1/login/access-token") in route_set
    assert ("GET", "/api/tasks/{id}") in route_set
    assert ("PATCH", "/api/tasks/{id}") in route_set
    assert ("GET", "/api/items/item_134") in route_set


def test_code_graph_filters_empty_init_and_links_socketio_events(tmp_path: Path) -> None:
    """过滤极短空 __init__.py 样板，并能抽取 Socket.IO 前端调用与后端监听器连线。"""
    _write(tmp_path, "backend/pkg/__init__.py", "")
    _write(tmp_path, "backend/pkg_keep/__init__.py", "# 真实初始化逻辑\nEXPORTED = 1\n" * 6)
    _write(
        tmp_path,
        "frontend/src/socket.ts",
        "export function emitPing(socket: any) { socket.emit('heartbeatPing', { ts: 1 }); }\n",
    )
    _write(
        tmp_path,
        "server/socket-handler.js",
        "function bind(socket) { socket.on('heartbeatPing', () => {}); }\n",
    )

    graph = build_code_graph(tmp_path, {})
    nodes = graph["nodes"]
    assert all(n["file"] != "backend/pkg/__init__.py" for n in nodes)
    assert any(n["file"] == "backend/pkg_keep/__init__.py" for n in nodes)
    ws_nodes = [n for n in nodes if n.get("kind") == "socket"]
    assert any("heartbeatPing" in str(n.get("label", "")) for n in ws_nodes)
    assert graph["conflicts"] == []


def test_summarize_report_retries_once_on_banned_word_and_drops_double_violation() -> None:
    """首次 AI 摘要命中结论词时应自动纠偏重试 1 次；两次都违规仍返回 None。"""
    facts = {"outcome": "passed", "counts": {"passed": 3, "failed": 0}}
    client_retry_ok = _SeqClient(
        [
            {"summary": "全部 3 项用例执行通过，建议上线。"},
            {"summary": "本次共执行 3 项，符合预期 3 项、出现失败 0 项。"},
        ]
    )
    result = summarize_report(facts, client=client_retry_ok)  # type: ignore[arg-type]
    assert result == "本次共执行 3 项，符合预期 3 项、出现失败 0 项。"
    assert len(client_retry_ok.calls) == 2
    retry_user = json.loads(client_retry_ok.calls[1][1])
    assert "correction" in retry_user

    client_double_bad = _SeqClient(
        [
            {"summary": "测试已通过"},
            {"summary": "依然包含passed结论词"},
        ]
    )
    assert summarize_report(facts, client=client_double_bad) is None  # type: ignore[arg-type]


def test_install_dependencies_marks_shared_host_env_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """使用项目外宿主 Python 时应在安装日志与动作记录中标明 shared_host_env。"""
    ws = Workspace(tmp_path / "ws")
    ws.init()
    run_dir = ws.root / "runs" / "run_host_1"
    run_dir.mkdir(parents=True, exist_ok=True)
    proj_dir = tmp_path / "target_repo"
    proj_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(deps_and_l1, "_missing_dependencies", lambda *_args: ["pytest"])
    monkeypatch.setattr(
        deps_and_l1,
        "install_packages",
        lambda *_args, **_kwargs: InstallOutcome(
            ok=True,
            exit_code=0,
            output=b"Successfully installed pytest",
            installer="uv",
            attempts=["uv"],
        ),
    )

    settings = (DEFAULT_MEDIA_BUDGET_MB * 1024 * 1024, DEFAULT_FILE_CAP_BYTES, False)
    ok, ref = deps_and_l1._install_dependencies(
        ws,
        run_dir,
        Path(sys.executable),
        ["pytest"],
        settings,
        [],
        cwd=proj_dir,
    )
    assert ok is True
    assert ref == "evidence/deps-install.log"
    log_text = (run_dir / "evidence" / "deps-install.log").read_text(encoding="utf-8")
    assert "shared host interpreter" in log_text
    action_files = list((ws.root / "actions").glob("*.jsonl"))
    assert len(action_files) == 1
    action_entry = json.loads(action_files[0].read_text(encoding="utf-8").strip().splitlines()[-1])
    assert action_entry["shared_host_env"] is True
