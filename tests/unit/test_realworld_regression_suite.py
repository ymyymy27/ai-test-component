"""阶段 4-A：11 项真实世界坑点回归靶场测试。

全部场景基于本地真实临时 HTTP 服务（ThreadingHTTPServer）、真实磁盘目录夹具
与真实 worker/CLI 调用链验证，不使用纯 mock，亦不修改任何外部或已有产品代码。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

from aitest.adapters.link import _MAX_LOG_READ_BYTES, _read_text
from aitest.command_handlers.map_task_commands import cmd_task_add
from aitest.command_handlers.project_commands import cmd_project_add
from aitest.core.context.contracts import parse_openapi_spec
from aitest.core.issues import draft_failed_issues
from aitest.core.preflight import check_service_ports
from aitest.core.probes import ContractReconciliationProbe
from aitest.core.repo_index import detect_package_runner, discover_monorepo_packages
from aitest.core.service_logs import capture_log_offsets, inspect_service_log_window
from aitest.models import Attempt, Case, Issue, Plan, Run, RunCase, Scope
from aitest.runners.http_runner import run_http_sequence
from aitest.runtime.plan_commands import (
    cmd_plan_generate,
    cmd_plan_repair_case,
)
from aitest.runtime.plan_validation import _broad_test_command, _test_node_key
from aitest.runtime.run_commands import cmd_run_start
from aitest.security import redact, redact_tree
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace
from aitest.worker import main as worker_main
from aitest.worker_impl.env import _run_env, _venv_python
from aitest.worker_impl.execution import verify_setup_only_repair
from aitest.worker_impl.services import _start_service, _stop_service


@contextlib.contextmanager
def _serve(handler_cls: type[BaseHTTPRequestHandler]) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_rw_01_multi_login_endpoints_and_cookies() -> None:
    """场景 1：JSON、OAuth2 表单与 Cookie 会话三种登录端点串联通过。"""

    class _MultiAuthHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length)
            ctype = (self.headers.get("Content-Type") or "").lower()
            if self.path == "/api/auth/json":
                payload = json.loads(raw.decode("utf-8"))
                assert payload["username"] == "alice"
                body = json.dumps({"token": "tok_json_1"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/auth/oauth2":
                assert "application/x-www-form-urlencoded" in ctype
                form = parse_qs(raw.decode("utf-8"))
                assert form.get("username") == ["bob"]
                assert form.get("grant_type") == ["password"]
                body = json.dumps({"access_token": "tok_oauth_2"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/auth/cookie":
                body = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "sid=sess_cookie_9; Path=/; HttpOnly")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/api/session/me":
                cookie = self.headers.get("Cookie") or ""
                auth = self.headers.get("Authorization") or ""
                if "sid=sess_cookie_9" in cookie and auth == "Bearer tok_oauth_2":
                    body = json.dumps({"user": "bob", "auth_ok": True}).encode("utf-8")
                    self.send_response(200)
                else:
                    body = b'{"error":"unauthorized"}'
                    self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    with _serve(_MultiAuthHandler) as base_url:
        res = run_http_sequence(
            [
                {
                    "name": "json_login",
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base_url}/api/auth/json",
                    "json": {"username": "alice", "password": "pw1"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                    "extract": {"jtok": "body.token"},
                },
                {
                    "name": "oauth2_login",
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base_url}/api/auth/oauth2",
                    "form": {
                        "username": "bob",
                        "password": "pw2",
                        "grant_type": "password",
                    },
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                    "extract": {"otok": "body.access_token"},
                },
                {
                    "name": "cookie_login",
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base_url}/api/auth/cookie",
                    "json": {"remember": True},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
                {
                    "name": "verify_me",
                    "stage": "assert",
                    "method": "GET",
                    "url": f"{base_url}/api/session/me",
                    "headers": {"Authorization": "Bearer ${vars.otok}"},
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "body.user", "operator": "equals", "value": "bob"},
                        {"path": "body.auth_ok", "operator": "equals", "value": True},
                    ],
                },
            ],
            allowed_hosts=["127.0.0.1"],
        )
    assert res.status == "passed", res


def test_rw_02_code_data_wrapper_and_uuid_email_datetime_openapi() -> None:
    """场景 2：{code, data} 包装与 uuid/email/date-time Schema 格式解析。"""
    spec = {
        "openapi": "3.0.3",
        "servers": [{"url": "/api/v1"}],
        "components": {
            "schemas": {
                "CreateUserReq": {
                    "type": "object",
                    "required": ["email", "trace_id", "joined_at"],
                    "properties": {
                        "email": {"type": "string", "format": "email"},
                        "trace_id": {"type": "string", "format": "uuid"},
                        "joined_at": {"type": "string", "format": "date-time"},
                    },
                    "additionalProperties": False,
                },
                "UserOut": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "format": "uuid"},
                        "email": {"type": "string", "format": "email"},
                    },
                },
                "WrappedUserResp": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "integer"},
                        "data": {"$ref": "#/components/schemas/UserOut"},
                    },
                },
            }
        },
        "paths": {
            "/users": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateUserReq"}
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/WrappedUserResp"}
                                }
                            }
                        }
                    },
                }
            }
        },
    }
    routes = parse_openapi_spec(
        spec,
        source="live:http://127.0.0.1:8001/openapi",
        confidence="openapi_live",
    )
    assert len(routes) == 1
    route = routes[0]
    assert route["method"] == "POST"
    assert route["path"] == "/api/v1/users"
    assert set(route["required_fields"]) == {"email", "trace_id", "joined_at"}
    assert route["field_formats"] == {
        "email": "email",
        "joined_at": "date-time",
        "trace_id": "uuid",
    }
    assert "data.user_id" in route["response_fields"]
    assert "data.email" in route["response_fields"]


def test_rw_03_rbac_multi_role_isolation() -> None:
    """场景 3：admin 与 employee 独立运行时 CookieJar/Vars 不串号。"""

    class _RbacHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            body_in = json.loads(self.rfile.read(length).decode("utf-8"))
            role = body_in.get("username")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", f"role_cookie={role}_sid; Path=/")
            out = json.dumps({"token": f"{role}_token"}).encode("utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self) -> None:
            cookie = self.headers.get("Cookie") or ""
            auth = self.headers.get("Authorization") or ""
            if self.path == "/api/admin/audit":
                allowed = ("role_cookie=admin_sid" in cookie) and (auth == "Bearer admin_token")
                status = 200 if allowed else 403
                out = json.dumps({"ok": allowed}).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            self.send_response(404)
            self.end_headers()

    with _serve(_RbacHandler) as base_url:
        admin_res = run_http_sequence(
            [
                {
                    "name": "login_admin",
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base_url}/api/login",
                    "json": {"username": "admin"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                    "extract": {"tok": "body.token"},
                },
                {
                    "name": "admin_audit",
                    "stage": "assert",
                    "method": "GET",
                    "url": f"{base_url}/api/admin/audit",
                    "headers": {"Authorization": "Bearer ${vars.tok}"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
            ],
            allowed_hosts=["127.0.0.1"],
        )
        emp_res = run_http_sequence(
            [
                {
                    "name": "login_employee",
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base_url}/api/login",
                    "json": {"username": "employee"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                    "extract": {"tok": "body.token"},
                },
                {
                    "name": "employee_denied",
                    "stage": "assert",
                    "method": "GET",
                    "url": f"{base_url}/api/admin/audit",
                    "headers": {"Authorization": "Bearer ${vars.tok}"},
                    "expect": [{"path": "status", "operator": "equals", "value": 403}],
                },
            ],
            allowed_hosts=["127.0.0.1"],
        )
    assert admin_res.status == "passed", admin_res
    assert emp_res.status == "passed", emp_res


def test_rw_04_ghost_source_routes_vs_runtime_openapi() -> None:
    """场景 4：运行态权威 OpenAPI 路由与前端调用对账，识别未挂载幽灵路径。"""
    live_openapi = parse_openapi_spec(
        {
            "openapi": "3.0.0",
            "paths": {
                "/api/v1/orders": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"item_id": {"type": "string"}},
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        },
        source="live:http://127.0.0.1:8001/openapi",
        confidence="openapi_live",
    )
    fe_calls = [
        {
            "method": "POST",
            "path": "/api/v1/orders",
            "fields": "item_id",
            "source": "frontend/api.ts:12",
        },
        {"method": "GET", "path": "/api/v1/ghost-unmounted", "source": "frontend/legacy.ts:9"},
    ]
    probe_out = ContractReconciliationProbe().run(
        frontend_api_calls=fe_calls,
        api_routes=live_openapi,
    )
    assert probe_out["status"] == "failed"
    assert any(
        "/api/v1/ghost-unmounted" in str(item.get("detail") or "")
        or "/api/v1/ghost-unmounted" in str(item.get("path") or "")
        for item in probe_out["findings"]
    )


def test_rw_05_monorepo_and_isolated_venv_discovery(tmp_path: Path) -> None:
    """场景 5：Monorepo 原生发现与独立 .venv 不爬父目录劫持。"""
    parent_venv = tmp_path / ".venv" / "Scripts" / "python.exe"
    parent_venv.parent.mkdir(parents=True)
    parent_venv.write_text("poison-parent", encoding="utf-8")

    repo = tmp_path / "monorepo"
    (repo / "pnpm-workspace.yaml").parent.mkdir(parents=True)
    (repo / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n", encoding="utf-8")
    (repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    api_pkg = repo / "packages" / "api"
    web_pkg = repo / "packages" / "web"
    api_pkg.mkdir(parents=True)
    web_pkg.mkdir(parents=True)
    (api_pkg / "package.json").write_text('{"name":"@demo/api"}', encoding="utf-8")
    (web_pkg / "package.json").write_text('{"name":"@demo/web"}', encoding="utf-8")

    # 独立仓自身无 .venv 时，绝不能向上爬到 tmp_path/.venv
    assert _venv_python(repo) is None
    # 但如果在 backend/.venv 有独立虚拟环境，应精确命中
    sub_py = repo / "backend" / ".venv" / "Scripts" / "python.exe"
    sub_py.parent.mkdir(parents=True)
    sub_py.write_text("real-sub", encoding="utf-8")
    assert _venv_python(repo) == sub_py

    pkgs = discover_monorepo_packages(repo)
    assert api_pkg.resolve() in pkgs
    assert web_pkg.resolve() in pkgs
    assert detect_package_runner(web_pkg, workspace_root=repo) == "pnpm"


def test_rw_06_low_entropy_short_secret_and_env_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """场景 6：低熵短密码精准脱敏不伤正文/键名，_run_env 留业务库密码剔插件密钥。"""
    sample = {
        "title": "test case 1234 summary",
        "headers": {"Authorization": "Bearer test"},
        "db_url": "postgresql://admin:1234@127.0.0.1:5432/app",
        "password": "test",
    }
    cleaned = redact_tree(sample, ["test", "1234"])
    assert cleaned["title"] == "test case 1234 summary"
    assert cleaned["headers"]["Authorization"] == "Bearer ***"
    assert "1234@" not in cleaned["db_url"]
    assert cleaned["password"] == "***"
    assert redact("普通文本 test 和密码 password=test 混排", ["test"]) == (
        "普通文本 test 和密码 password=*** 混排"
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("DATABASE_PASSWORD=proj_db_secret\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-plugin-must-drop")
    monkeypatch.setenv("AITEST_MODEL_API_KEY", "sk-aitest-must-drop")
    monkeypatch.setenv("DATABASE_PASSWORD", "host_db_old")

    env_out = _run_env(proj, Path(sys.executable), "run_env_01")
    assert env_out.get("DATABASE_PASSWORD") == "proj_db_secret"
    assert "OPENAI_API_KEY" not in env_out
    assert "AITEST_MODEL_API_KEY" not in env_out


def test_rw_07_parametrized_pytest_nodes_and_vitest_whitelist() -> None:
    """场景 7：@pytest.mark.parametrize [params] 节点映射与 vitest 精确命令校验。"""
    case = Case(
        case_id="c_param",
        acceptance_item_id="ai_1",
        layer="L2",
        title="参数化测试",
        expected="通过",
        expectation_source="支持参数化用例",
        verify_method="command:pytest tests/unit/test_login.py::test_roles[admin-200]",
    )
    node_key = _test_node_key(
        case,
        {"test_functions": {"tests/unit/test_login.py": ["test_roles"]}},
    )
    assert node_key == "tests/unit/test_login.py::test_roles[admin-200]"

    assert _broad_test_command("command:npx vitest run") != ""
    assert _broad_test_command("command:npx vitest run tests/login.spec.ts") == ""


def test_rw_08_large_log_tail_and_service_log_rotation(tmp_path: Path) -> None:
    """场景 8：>32 MiB 日志只尾部读取最新片段，服务日志轮转后重头抓取不漏报。"""
    huge_log = tmp_path / "huge.log"
    tail_line = b"INFO request_id=req_rw_08_tail status=200 ok\n"
    with huge_log.open("wb") as handle:
        handle.seek(_MAX_LOG_READ_BYTES + 1024 * 1024)
        handle.write(b"\n" + tail_line)
    text = _read_text(huge_log)
    assert text is not None
    assert "req_rw_08_tail" in text

    svc_log = tmp_path / "svc.log"
    svc_log.write_bytes(b"A" * 2048)
    states = [{"name": "api", "log": svc_log, "url": "http://127.0.0.1:8001"}]
    offsets = capture_log_offsets(states)
    assert offsets[str(svc_log)] == 2048

    # 模拟日志轮转：截断重建为更小的最新文件
    svc_log.write_bytes(
        b'127.0.0.1 - "POST /api/items HTTP/1.1" 500 Internal Server Error\n'
        b"Traceback (most recent call last):\n  RuntimeError: rotated boom\n"
    )
    window = inspect_service_log_window(
        states,
        offsets,
        case_method="http:[{\"method\":\"POST\",\"url\":\"http://127.0.0.1:8001/api/items\"}]",
        expects_server_error=False,
        case_targets={"127.0.0.1:8001"},
        secrets=[],
    )
    assert any(
        "500" in err or "Traceback" in err or "异常" in err
        for err in window["fatal_errors"]
    )


def test_rw_09_plan_repair_case_batch_setup_ast_guard_and_rerun(tmp_path: Path) -> None:
    """场景 9：setup 失败触发 plan.repair_case 批量草稿（AST 防篡改 verify）+ 定向重跑通过。"""

    class _RegHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            body_in = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/api/register":
                pw = str(body_in.get("password") or "")
                if len(pw) < 10:
                    err = json.dumps({"detail": "password length must be >= 10"}).encode("utf-8")
                    self.send_response(422)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
                    return
                ok = json.dumps({"token": "tok_strong_ok"}).encode("utf-8")
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(ok)))
                self.end_headers()
                self.wfile.write(ok)
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/api/profile":
                if self.headers.get("Authorization") == "Bearer tok_strong_ok":
                    out = json.dumps({"ready": True}).encode("utf-8")
                    self.send_response(200)
                else:
                    out = b"{}"
                    self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            self.send_response(404)
            self.end_headers()

    ws = Workspace(tmp_path / "ws")
    root = tmp_path / "repo"
    root.mkdir()
    proj = cmd_project_add(ws, {"name": "rw09", "root": str(root)}, False)
    pid = proj["project_id"]
    task = cmd_task_add(
        ws,
        {
            "project_id": pid,
            "title": "注册并查询资料",
            "goal": "验证注册后获取资料",
            "acceptance_items": [
                {"text": "注册用户后访问资料接口返回 200", "observable": True},
                {"text": "另一条稳定健康验收项", "observable": True},
            ],
        },
        False,
    )
    items = task["acceptance_items"]

    with _serve(_RegHandler) as base_url:
        bad_http = json.dumps(
            [
                {
                    "name": "register_weak",
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base_url}/api/register",
                    "json": {"username": "u1", "password": "short"},
                    "expect": [{"path": "status", "operator": "equals", "value": 201}],
                    "extract": {"tok": "body.token"},
                },
                {
                    "name": "get_profile",
                    "stage": "assert",
                    "method": "GET",
                    "url": f"{base_url}/api/profile",
                    "headers": {"Authorization": "Bearer ${vars.tok}"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
            ]
        )
        ok_http = json.dumps(
            [
                {
                    "name": "ping_profile",
                    "stage": "assert",
                    "method": "GET",
                    "url": f"{base_url}/api/profile",
                    "expect": [{"path": "status", "operator": "equals", "value": 401}],
                }
            ]
        )
        plan1 = cmd_plan_generate(
            ws,
            {
                "project_id": pid,
                "task_id": task["task_id"],
                "scope": {"preset": "all_applicable"},
                "cases": [
                    {
                        "case_id": "c_setup_fail",
                        "acceptance_item_id": items[0]["item_id"],
                        "layer": "L2",
                        "title": "弱密码导致 setup 阻塞",
                        "expected": "200",
                        "expectation_source": items[0]["text"],
                        "preconditions": ["服务已启动"],
                        "verify_method": f"http:{bad_http}",
                    },
                    {
                        "case_id": "c_keep_ok",
                        "acceptance_item_id": items[1]["item_id"],
                        "layer": "L2",
                        "title": "未携带 Token 访问返回 401",
                        "expected": "401",
                        "expectation_source": items[1]["text"],
                        "preconditions": ["无"],
                        "verify_method": f"http:{ok_http}",
                    },
                ],
            },
            True,
        )
        r1 = cmd_run_start(ws, {"project_id": pid, "plan_id": plan1["plan_id"]}, True)
        rc1 = worker_main(["--workspace", str(ws.root), "--project", pid, "--run", r1["run_id"]])
        assert rc1 == 0

        # AST 守卫：禁止在 setup 修复草稿中偷改 assert 断言阶段或 expected
        tamper_http = json.dumps(
            [
                {
                    "name": "register_strong",
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base_url}/api/register",
                    "json": {"username": "u1", "password": "strong_password_123"},
                    "expect": [{"path": "status", "operator": "equals", "value": 201}],
                    "extract": {"tok": "body.token"},
                },
                {
                    "name": "get_profile",
                    "stage": "assert",
                    "method": "GET",
                    "url": f"{base_url}/api/profile",
                    "headers": {"Authorization": "Bearer ${vars.tok}"},
                    "expect": [{"path": "status", "operator": "equals", "value": 500}],
                },
            ]
        )
        old_case = Case(
            case_id="c_setup_fail",
            acceptance_item_id=items[0]["item_id"],
            layer="L2",
            title="t",
            expected="200",
            expectation_source=items[0]["text"],
            verify_method=f"http:{bad_http}",
        )
        _, _, guards, _ = verify_setup_only_repair(
            old_case, repaired_verify_method=f"http:{tamper_http}"
        )
        assert guards

        fixed_http = json.dumps(
            [
                {
                    "name": "register_strong",
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base_url}/api/register",
                    "json": {"username": "u1", "password": "strong_password_123"},
                    "expect": [{"path": "status", "operator": "equals", "value": 201}],
                    "extract": {"tok": "body.token"},
                },
                {
                    "name": "get_profile",
                    "stage": "assert",
                    "method": "GET",
                    "url": f"{base_url}/api/profile",
                    "headers": {"Authorization": "Bearer ${vars.tok}"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
            ]
        )
        run1 = json.loads(
            (ws.project_dir(pid) / "runs" / r1["run_id"] / "run.json").read_text(encoding="utf-8")
        )
        setup_rc = next(c for c in run1["cases"] if c["case_id"] == "c_setup_fail")
        assert setup_rc["result"] == "blocked"
        assert "password length" in setup_rc["attempts"][-1]["message"]

        preview = cmd_plan_repair_case(
            ws,
            {
                "project_id": pid,
                "plan_id": plan1["plan_id"],
                "case_ids": ["c_setup_fail"],
                "run_id": r1["run_id"],
                "mode": "preview",
                "repaired_methods": {"c_setup_fail": f"http:{fixed_http}"},
            },
            False,
        )
        assert preview["all_ok"] is True
        applied = cmd_plan_repair_case(
            ws,
            {
                "project_id": pid,
                "plan_id": plan1["plan_id"],
                "case_ids": ["c_setup_fail"],
                "run_id": r1["run_id"],
                "mode": "apply",
                "repaired_methods": {"c_setup_fail": f"http:{fixed_http}"},
            },
            True,
        )
        assert applied["updated_case_ids"] == ["c_setup_fail"]
        assert applied["rerun_acceptance_item_ids"] == [items[0]["item_id"]]

        # 按 rerun_acceptance_item_ids 收窄重跑新修订计划（apply 已升版并维持 published）
        new_pid_plan = applied["plan_id"]
        r2 = cmd_run_start(
            ws,
            {
                "project_id": pid,
                "plan_id": new_pid_plan,
                "acceptance_item_ids": applied["rerun_acceptance_item_ids"],
            },
            True,
        )
        rc2 = worker_main(["--workspace", str(ws.root), "--project", pid, "--run", r2["run_id"]])
        assert rc2 == 0
        run2 = json.loads(
            (ws.project_dir(pid) / "runs" / r2["run_id"] / "run.json").read_text(encoding="utf-8")
        )
        assert [c["case_id"] for c in run2["cases"]] == ["c_setup_fail"]
        assert run2["cases"][0]["result"] == "passed"


def test_rw_10_mixed_pass_fail_issue_dedup_no_unstable(tmp_path: Path) -> None:
    """场景 10：同验收项混合通过/失败同轮去重不误标 unstable，不同 case_id 不撞签。"""
    ws = Workspace(tmp_path / "ws")
    pid = "p_rw10"
    (ws.project_dir(pid) / "issues").mkdir(parents=True)
    plan = Plan(
        plan_id="plan_10",
        task_id="task_10",
        scope=Scope(preset="all_applicable", acceptance_item_ids=["ai_1"]),
        applicable_count=1,
        cases=[
            Case(
                case_id="c_ok",
                acceptance_item_id="ai_1",
                layer="L2",
                title="正常读取",
                expected="200",
                expectation_source="正常读取",
            ),
            Case(
                case_id="c_fail_1",
                acceptance_item_id="ai_1",
                layer="L2",
                title="创建边界报错 A",
                expected="200",
                expectation_source="创建边界",
            ),
            Case(
                case_id="c_fail_2",
                acceptance_item_id="ai_1",
                layer="L2",
                title="创建边界报错 B",
                expected="200",
                expectation_source="创建边界",
            ),
        ],
    )
    run = Run(
        run_id="run_10",
        plan_id="plan_10",
        project_id=pid,
        scope=plan.scope,
        status="completed",
        cases=[
            RunCase(case_id="c_ok", result="passed"),
            RunCase(
                case_id="c_fail_1",
                result="failed",
                confidence_tier="confirmed",
                attempts=[
                    Attempt(
                        attempt=1,
                        result="failed",
                        stage="verify",
                        message="AssertionError: expected 200",
                    )
                ],
                evidence_refs=["ev/f1.txt"],
            ),
            RunCase(
                case_id="c_fail_2",
                result="failed",
                confidence_tier="confirmed",
                attempts=[
                    Attempt(
                        attempt=1,
                        result="failed",
                        stage="verify",
                        message="AssertionError: expected 200",
                    )
                ],
                evidence_refs=["ev/f2.txt"],
            ),
        ],
    )
    run_dir = ws.project_dir(pid) / "runs" / "run_10"
    write_model(run_dir / "plan.json", plan)
    write_model(run_dir / "run.json", run)
    created = draft_failed_issues(ws, pid, run_dir)
    assert len(created) == 2
    issues = [read_model(ws.project_dir(pid) / "issues" / f"{iid}.json", Issue) for iid in created]
    assert len({i.signature for i in issues}) == 2
    assert all(not i.unstable for i in issues)


def test_rw_11_port_conflict_preflight_and_windows_job_tree(tmp_path: Path) -> None:
    """场景 11：端口被非健康进程占用触发 preflight 阻断，以及服务启停回收进程树。"""

    class _UnhealthyHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def do_GET(self) -> None:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"unhealthy")

    with _serve(_UnhealthyHandler) as bad_url:
        pf = check_service_ports(
            [
                (
                    "api",
                    {
                        "url": bad_url,
                        "health": f"{bad_url}/health",
                        "start": "python -m http.server",
                    },
                )
            ]
        )
    assert pf["conflicted_services"] == ["api"]
    assert pf["blocking_reasons"]

    # 真实拉起短暂服务并通过健康检查与 _stop_service（含 Windows Job Object 回收）干净关闭
    srv_script = tmp_path / "mini_srv.py"
    port_file = tmp_path / "srv_ready.txt"
    srv_script.write_text(
        "import time\n"
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        f"from pathlib import Path\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def log_message(self, *a): pass\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'ok')\n"
        "s = HTTPServer(('127.0.0.1', 0), H)\n"
        f"Path({str(port_file)!r}).write_text(str(s.server_address[1]), encoding='utf-8')\n"
        "s.serve_forever()\n",
        encoding="utf-8",
    )
    # 先探测一个临时可用端口或让子进程自报后只验证 _start_service/_stop_service 不挂死
    with _serve(_UnhealthyHandler) as temp_addr:
        pass
    unused_port = int(temp_addr.rsplit(":", 1)[1])
    srv_script.write_text(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    def log_message(self, *a): pass\n"
        "    def do_GET(self):\n"
        "        self.send_response(200)\n"
        "        self.end_headers()\n"
        "        self.wfile.write(b'ok')\n"
        f"HTTPServer(('127.0.0.1', {unused_port}), H).serve_forever()\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "svc_job.log"
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()
    started, ok, proc = _start_service(
        {
            "url": f"http://127.0.0.1:{unused_port}",
            "health": f"http://127.0.0.1:{unused_port}/health",
            "start": f'"{sys.executable}" "{srv_script}"',
            "timeout_seconds": 10,
        },
        tmp_path,
        log_path,
        secrets=[],
        env=dict(os.environ),
        run_dir=run_dir,
        service_name="mini",
    )
    assert started is True and ok is True and proc is not None
    try:
        assert proc.poll() is None
    finally:
        _stop_service(
            {},
            tmp_path,
            log_path,
            secrets=[],
            env=dict(os.environ),
            process=proc,
            run_dir=run_dir,
            service_name="mini",
        )
    time.sleep(0.2)
    assert proc.poll() is not None
