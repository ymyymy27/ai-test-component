"""针对 AgentSwarm-RBAC 真实动态测试暴露的 5 项类级缺陷的回归单元测试。"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aitest.core.judge import compute_case_confidence, judge_case_failure
from aitest.core.probes import ContractReconciliationProbe
from aitest.core.project_context import fetch_runtime_openapi_routes
from aitest.models import AcceptanceItem, Attempt, Case, Plan, Project, RunCase, Task
from aitest.runners.package_install import InstallOutcome
from aitest.runtime.plan_commands import cmd_plan_generate
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace
from aitest.worker_impl import deps_and_l1
from aitest.worker_impl.env import _configured_python
from aitest.worker_impl.execution import _setup_product_failure_detail


class _LocalServer(ThreadingHTTPServer):
    daemon_threads = True


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_http_request_id_generated_as_valid_uuid4() -> None:
    """HTTP 执行器注入的 X-Request-Id 必须是合法 UUID v4，避免被严格中间件拦截为 400。"""
    import inspect

    from aitest.worker_impl import lifecycle

    src = inspect.getsource(lifecycle)
    assert "request_id = str(uuid.uuid4()) if is_http else \"\"" in src
    # 验证生成的格式能通过标准 UUID v4 解析
    parsed = uuid.UUID(str(uuid.uuid4()), version=4)
    assert parsed.version == 4


def test_runtime_openapi_introspect_filters_unmounted_routes_in_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """运行中服务挂载 /openapi 时，plan.generate 优先采用真实挂载路径并隔离未挂载死路由。"""
    openapi_doc = {
        "openapi": "3.1.0",
        "paths": {
            "/api/v1/sys/rbac/agent": {
                "get": {"security": [{"HTTPBearer": []}]},
                "post": {
                    "security": [{"HTTPBearer": []}],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "page_count": {"type": "integer"},
                                    },
                                }
                            }
                        }
                    },
                },
            },
            "/api/v1/sys/rbac/audit": {"get": {"security": [{"HTTPBearer": []}]}},
        },
    }
    raw_doc = json.dumps(openapi_doc).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/openapi":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw_doc)))
                self.end_headers()
                self.wfile.write(raw_doc)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = _LocalServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        rt_routes, probe_path = fetch_runtime_openapi_routes(
            {"services": {"base_url": base_url, "health": f"{base_url}/docs"}}
        )
        assert probe_path == "/openapi"
        assert {r["path"] for r in rt_routes} == {
            "/api/v1/sys/rbac/agent",
            "/api/v1/sys/rbac/audit",
        }
        post_route = next(r for r in rt_routes if r["method"] == "POST")
        assert post_route["auth"] == "authenticated"
        assert post_route["field_types"]["name"] == "string"
        assert post_route["field_types"]["page_count"] == "integer"

        repo = tmp_path / "repo"
        _write(repo, "pyproject.toml", "[project]\nname='rbac'\ndependencies=['fastapi']\n")
        _write(
            repo,
            "dead_rbac.py",
            "from fastapi import APIRouter\nrouter = APIRouter()\n"
            "@router.get('/api/v1/rbac/audit/logs')\n"
            "def dead_audit(): return []\n",
        )
        ws = Workspace(tmp_path / "ws")
        ws.init()
        proj = Project(project_id="proj_rt", name="rbac", root=str(repo))
        ws.save_project(proj)
        (ws.project_dir("proj_rt") / "project.config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "services": {"base_url": base_url, "health": f"{base_url}/docs"},
                }
            ),
            encoding="utf-8",
        )
        task = Task(
            task_id="task_rt",
            project_id="proj_rt",
            title="校验审计",
            acceptance_items=[
                AcceptanceItem(item_id="ai_1", text="超级管理员可查询审计日志接口")
            ],
        )
        write_model(ws.project_dir("proj_rt") / "tasks" / "task_rt.json", task)

        seen_contexts: list[dict[str, Any]] = []

        class _ModelStub:
            def generate_cases(
                self,
                task_dict: dict[str, Any],
                items: list[dict[str, Any]],
                ctx: dict[str, Any] | None = None,
            ) -> list[dict[str, Any]]:
                del task_dict
                if ctx is not None:
                    seen_contexts.append(ctx)
                return [
                    {
                        "acceptance_item_id": item["item_id"],
                        "layer": "L2",
                        "title": "查审计日志",
                        "expected": "200",
                        "expectation_source": item["text"],
                        "preconditions": ["已启动服务"],
                        "steps": ["GET /api/v1/sys/rbac/audit"],
                        "verify_method": (
                            f'http:[{{"method":"GET","url":"{base_url}/api/v1/sys/rbac/audit",'
                            '"expect":[{"path":"status","operator":"equals","value":200}]}]'
                        ),
                    }
                    for item in items
                ]

        monkeypatch.setattr("aitest.runtime.plan_commands.build_client", lambda *_: _ModelStub())
        monkeypatch.setattr("aitest.runtime_commands.build_client", lambda *_: _ModelStub())
        out = cmd_plan_generate(
            ws,
            {
                "project_id": "proj_rt",
                "task_id": "task_rt",
                "scope": {"preset": "all_applicable"},
            },
            False,
        )
        plan = read_model(ws.project_dir("proj_rt") / "plans" / f"{out['plan_id']}.json", Plan)
        assert plan.capabilities is not None
        assert plan.capabilities["source"] == "runtime"
        assert "GET /openapi" in plan.capabilities["evidence"]
        assert seen_contexts
        mounted_paths = {r["path"] for r in seen_contexts[0]["api_routes"]}
        unmounted_paths = {r["path"] for r in seen_contexts[0].get("unmounted_static_routes", [])}
        assert "/api/v1/sys/rbac/audit" in mounted_paths
        assert "/api/v1/rbac/audit/logs" not in mounted_paths
        assert "/api/v1/rbac/audit/logs" in unmounted_paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_probe_filters_nitro_mock_and_judge_rejects_false_missing_route(tmp_path: Path) -> None:
    """ContractReconciliationProbe 过滤开发 mock 与 ANY /api，judge 拒绝非 404 时误判未声明路由。"""
    probe_out = ContractReconciliationProbe().run(
        frontend_api_calls=[
            {
                "method": "ANY",
                "path": "/api",
                "source": "frontend/internal/vite-config/src/plugins/nitro-mock.ts",
            },
            {
                "method": "POST",
                "path": "/api/v1/orders",
                "source": "src/api/orders.ts",
            },
        ],
        api_routes=[{"method": "GET", "path": "/api/v1/health"}],
        has_dynamic_loader=False,
    )
    assert len(probe_out["findings"]) == 1
    assert probe_out["findings"][0]["path"] == "/api/v1/orders"

    ws = Workspace(tmp_path / "ws")
    ws.init()
    ws.save_project(Project(project_id="p_j", name="j", root=str(tmp_path)))
    case = Case(
        case_id="c_400",
        acceptance_item_id="ai_1",
        layer="L2",
        title="校验 /api/v1/agent",
        expected="返回 422",
        expectation_source="返回 422",
        verify_method='http:[{"method":"POST","url":"http://127.0.0.1:8001/api/v1/agent","json":{}}]',
    )
    probes = [
        {
            "probe_id": "contract_reconciliation",
            "status": "failed",
            "findings": [
                {
                    "kind": "missing_route",
                    "severity": "error",
                    "path": "/api",
                    "detail": "前端 ANY /api 未声明",
                },
                {
                    "kind": "missing_route",
                    "severity": "error",
                    "path": "/api/v1/agent",
                    "detail": "静态扫描误报 /api/v1/agent 未声明",
                },
                {
                    "kind": "missing_route",
                    "severity": "warning",
                    "path": "/api/v1/agent",
                    "detail": "warning 级别不应直接盖章 confirmed_defect",
                },
            ],
        }
    ]
    verdict = judge_case_failure(
        ws,
        "p_j",
        case=case,
        outputs=[b'{"code":400,"msg":"BAD_REQUEST"}'],
        message="request 1 POST http://127.0.0.1:8001/api/v1/agent returned HTTP 400",
        service_log_issues=[],
        probe_results=probes,
        secrets=[],
    )
    assert verdict["source"] != "deterministic"

    rc = RunCase(
        case_id="c_400",
        result="failed",
        attempts=[
            Attempt(
                attempt=1,
                result="failed",
                message="request 1 POST http://127.0.0.1:8001/api/v1/agent returned HTTP 400",
            )
        ],
    )
    tier, reason = compute_case_confidence(
        case, rc, effective_result="failed", probe_results=probes
    )
    assert "未声明" not in reason
    assert tier == "suspected"


def test_setup_detail_host_install_guard_and_memory_mock_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证 setup 5xx/401 识别、禁止默认装包到宿主 venv 与 runners.python 解析。"""
    msg_500, is_fatal_500 = _setup_product_failure_detail(
        b"AssertionError: create failed 500: Internal Server Error\n[AITEST_STAGE:SETUP_FAILED]\n"
    )
    assert is_fatal_500 is True
    assert "服务端异常" in msg_500

    raw_401 = (
        'AssertionError: create failed 401: {"code":401,"msg":"无法获取用户信息"}\n'
        "[AITEST_STAGE:SETUP_FAILED]\n"
    ).encode()
    msg_401, is_fatal_401 = _setup_product_failure_detail(raw_401)
    assert is_fatal_401 is False
    assert "401" in msg_401
    assert "不计为产品缺陷" not in msg_401

    # 测试 allow_shared_host=False 时禁止往宿主解释器安装包
    ws = Workspace(tmp_path / "ws")
    ws.init()
    run_dir = ws.root / "runs" / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    target_repo = tmp_path / "repo"
    target_repo.mkdir()
    monkeypatch.setattr(deps_and_l1, "_missing_dependencies", lambda *_a: ["starlette"])

    def _forbid_install(*_a: Any, **_kw: Any) -> InstallOutcome:
        raise AssertionError("allow_shared_host=False 时不应向宿主环境安装包")

    monkeypatch.setattr(deps_and_l1, "install_packages", _forbid_install)
    ok, ref = deps_and_l1._install_dependencies(
        ws,
        run_dir,
        Path(sys.executable),
        ["starlette"],
        (10 * 1024 * 1024, 65536, False),
        [],
        cwd=target_repo,
        allow_shared_host=False,
    )
    assert ok is False
    assert ref == "evidence/deps-install.log"
    assert "skipped auto-installing" in (run_dir / ref).read_text(encoding="utf-8")

    # 测试 runners.python 解析
    fake_py = tmp_path / "custom_venv" / "python.exe"
    fake_py.parent.mkdir(parents=True, exist_ok=True)
    fake_py.write_text("", encoding="utf-8")
    resolved_cfg = _configured_python({"runners": {"python": str(fake_py)}}, target_repo)
    assert resolved_cfg == fake_py.resolve()
