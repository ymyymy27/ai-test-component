"""阶段 2 专项回归测试：统一索引、完整 OpenAPI、HTTP/断言重构、环境与服务管理。"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aitest.commands import cmd_project_add, cmd_project_config
from aitest.config import known_secret_values
from aitest.core.context.contracts import _api_routes, parse_openapi_spec
from aitest.core.evidence import DEFAULT_FILE_CAP_BYTES, DEFAULT_MEDIA_BUDGET_MB
from aitest.core.preflight import check_service_tools, run_preflight
from aitest.core.project_context import build_project_health_summary, cached_project_context
from aitest.core.repo_index import (
    compute_repo_fingerprint,
    detect_package_runner,
    discover_monorepo_packages,
    run_git_porcelain_z,
)
from aitest.core.service_logs import inspect_service_log_window
from aitest.errors import AiTestError
from aitest.runners.assertions import MISSING, evaluate
from aitest.runners.command_runner import run_command
from aitest.runners.http_runner import run_http_sequence
from aitest.storage.workspace import Workspace
from aitest.worker_impl import deps_and_l1
from aitest.worker_impl.env import _run_env, _venv_python
from aitest.worker_impl.services import _healthy


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_repo_index_chinese_dirty_file_second_edit_invalidates_cache(tmp_path: Path) -> None:
    """Git 中文路径 -z 解析及同一脏文件二次修改必须引起指纹变化与旧缓存淘汰。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")
    cn_file = repo / "业务模块" / "用户接口.py"
    cn_file.parent.mkdir(parents=True)
    cn_file.write_text("def hello():\n    return 'v1'\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "init")

    # 第一次修改中文文件
    cn_file.write_text("def hello():\n    return 'v2'\n", encoding="utf-8")
    ok, entries, timed_out = run_git_porcelain_z(repo)
    assert ok and not timed_out
    assert any(rel == "业务模块/用户接口.py" for _xy, rel in entries)

    ws = Workspace(tmp_path / "ws")
    ws.init()
    fp1, c1 = compute_repo_fingerprint(repo)
    assert c1 is True
    ctx1 = cached_project_context(ws.root, "proj_cn", repo, {})
    assert isinstance(ctx1, dict)

    # 第二次修改同一个已脏的文件：指纹与缓存必须失效重算
    cn_file.write_text("def hello():\n    return 'v3'\n", encoding="utf-8")
    fp2, c2 = compute_repo_fingerprint(repo)
    assert c2 is True
    assert fp2 != fp1

    cached_project_context(ws.root, "proj_cn", repo, {})
    (repo / "业务模块" / "更多.py").write_text("x = 1\n", encoding="utf-8")
    cached_project_context(ws.root, "proj_cn", repo, {})
    slots = list((ws.root / ".cache" / "context").glob("proj_cn-*.json"))
    assert len(slots) <= 2


def test_monorepo_discovery_and_runner_detection(tmp_path: Path) -> None:
    """支持 pnpm-workspace.yaml 多级包发现与真实 lockfile 运行器选取。"""
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'apps/*'\n  - 'packages/*'\n",
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text('{"name":"web"}', encoding="utf-8")
    pkgs = discover_monorepo_packages(tmp_path)
    assert web.resolve() in pkgs
    assert detect_package_runner(web, tmp_path) == "pnpm"


def test_openapi_yaml_full_features_and_normalized_uncovered_routes(tmp_path: Path) -> None:
    """支持 YAML、servers basePath、全局 security、表单、format 与路径归一化覆盖计算。"""
    (tmp_path / "openapi.yaml").write_text(
        "openapi: 3.0.3\n"
        "servers:\n"
        "  - url: https://api.example.com/v1\n"
        "security:\n"
        "  - bearerAuth: []\n"
        "paths:\n"
        "  /users/{id}:\n"
        "    post:\n"
        "      parameters:\n"
        "        - name: id\n"
        "          in: path\n"
        "          required: true\n"
        "        - name: dry_run\n"
        "          in: query\n"
        "      requestBody:\n"
        "        content:\n"
        "          application/x-www-form-urlencoded:\n"
        "            schema:\n"
        "              type: object\n"
        "              required: [email]\n"
        "              properties:\n"
        "                email:\n"
        "                  type: string\n"
        "                  format: email\n"
        "      responses:\n"
        "        '200':\n"
        "          description: ok\n",
        encoding="utf-8",
    )
    routes = _api_routes(tmp_path)
    target = next(r for r in routes if r["path"] == "/v1/users/{id}")
    assert target["auth"] == "user"
    assert target["query_params"] == ["dry_run"]
    assert target["path_params"] == ["id"]
    assert target["required_fields"] == ["email"]
    assert target["field_formats"]["email"] == "email"
    assert "application/x-www-form-urlencoded" in target["content_types"]

    # 包装体响应 {code, data} 识别
    parsed = parse_openapi_spec(
        {
            "paths": {
                "/orders": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "properties": {
                                                "code": {"type": "integer"},
                                                "data": {
                                                    "properties": {
                                                        "order_id": {"type": "string"}
                                                    }
                                                },
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        source="runtime",
        confidence="runtime",
    )
    assert "data.order_id" in parsed[0]["response_fields"]

    # 归一化后 /v1/users/{id} 与前端 /v1/users/${id} 正确匹配，且健康摘要不再含假 missing_packages
    summary = build_project_health_summary(
        {"level": "T2", "source": "static"},
        {
            "api_routes": routes,
            "frontend_api_calls": [{"method": "POST", "path": "/v1/users/{param}"}],
            "services": {},
        },
    )
    assert "missing_packages" not in summary
    assert summary["uncovered_route_count"] == 0


def test_test_roles_config_redaction_and_readonly_project_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """test_roles 仅存环境变量引用，允许元后缀配置且在脱敏与 preflight 闭环校验中生效。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("AITEST_ADMIN_PASS", "SuperAdmin#2026")
    monkeypatch.delenv("AITEST_STAFF_PASS", raising=False)
    ws = Workspace(tmp_path / "ws")
    ws.init()
    repo = tmp_path / "readonly_repo"
    repo.mkdir()
    res = cmd_project_add(ws, {"root": str(repo), "name": "ro"}, True)
    pid = res["project_id"]

    cfg_res = cmd_project_config(
        ws,
        {
            "project_id": pid,
            "action": "update",
            "patch": {
                "test_roles": {
                    "admin": {
                        "username_env": "AITEST_ADMIN_USER",
                        "password_env": "AITEST_ADMIN_PASS",
                        "auth_header": "Authorization",
                    },
                    "staff": {"password_env": "AITEST_STAFF_PASS"},
                }
            },
        },
        True,
    )
    assert "admin" in cfg_res["config"]["test_roles"]
    secrets = known_secret_values([cfg_res["config"]])
    assert "SuperAdmin#2026" in secrets

    # 禁止直接写明文 password 键
    with pytest.raises(AiTestError):
        cmd_project_config(
            ws,
            {
                "project_id": pid,
                "action": "update",
                "patch": {"test_roles": {"bad": {"password": "plain-secret-value"}}},
            },
            True,
        )

    pf = run_preflight(repo, cfg_res["config"], [], {"AITEST_ADMIN_PASS": "SuperAdmin#2026"})
    assert any("AITEST_STAFF_PASS" in reason for reason in pf["blocking_reasons"])


def test_http_runner_cookie_multipart_env_types_and_assertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """http_runner 应支持 Cookie 持久、multipart 文件上传、${env.*}、响应头、文本体与全量操作符。"""
    upload_file = tmp_path / "demo.txt"
    upload_file.write_text("hello multipart", encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length)
            if self.path == "/login":
                body = json.dumps({"ok": True, "count": 3, "tag": "vip"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "sid=cookie-ok-123; Path=/")
                self.send_header("X-Custom-Token", "hdr-tok-999")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/upload":
                cookie = self.headers.get("Cookie") or ""
                ok_mp = b"hello multipart" in raw and "sid=cookie-ok-123" in cookie
                body = f"UPLOAD={'OK' if ok_mp else 'NO'}".encode()
                self.send_response(200 if ok_mp else 400)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{int(server.server_address[1])}"
        monkeypatch.setenv("AITEST_DEMO_PASS", "top-secret-pw")
        res = run_http_sequence(
            [
                {
                    "stage": "setup",
                    "method": "POST",
                    "url": f"{base}/login",
                    "form": {"password": "${env.AITEST_DEMO_PASS}"},
                    "extract": {"cnt": "count", "hdr": "headers.x-custom-token"},
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "count", "operator": "type_is", "value": "int"},
                        {"path": "count", "operator": "gt", "value": 1},
                        {"path": "tag", "operator": "in", "value": ["vip", "svip"]},
                        {"path": "missing_field", "operator": "not_exists", "value": None},
                        {"path": "headers.x-custom-token", "operator": "regex", "value": r"^hdr-"},
                    ],
                },
                {
                    "method": "POST",
                    "url": f"{base}/upload",
                    "multipart": {
                        "count_echo": "${vars.cnt}",
                        "file": {"path": str(upload_file), "content_type": "text/plain"},
                    },
                    "expect": [
                        {"path": "status", "operator": "equals", "value": 200},
                        {"path": "body_text", "operator": "contains", "value": "UPLOAD=OK"},
                    ],
                },
            ],
            env={"AITEST_DEMO_PASS": "top-secret-pw"},
        )
        assert res.status == "passed", res.error
        assert "top-secret-pw" not in res.stdout.decode("utf-8", errors="ignore")
    finally:
        server.shutdown()

    assert evaluate(MISSING, "not_exists", None) is True
    assert evaluate("abc", "length_equals", 3) is True
    assert evaluate(5, "lte", 5) is True
    assert evaluate("dev", "not_in", ["prod", "staging"]) is True
    assert evaluate("hello", "not_contains", "xyz") is True


def test_env_isolation_deps_preflight_and_log_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证业务环境变量保留、不爬父目录 .venv、跳过宿主机安装返 False、401不健康、日志轮转。"""
    parent_venv = tmp_path / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    parent_venv.mkdir(parents=True)
    parent_py = parent_venv / ("python.exe" if sys.platform == "win32" else "python")
    parent_py.write_text("", encoding="utf-8")

    child_proj = tmp_path / "workspace" / "app"
    child_proj.mkdir(parents=True)
    assert _venv_python(child_proj) is None

    monkeypatch.setenv("DATABASE_PASSWORD", "biz-db-secret-99")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-plugin-key-hidden")
    monkeypatch.setenv("AITEST_ADMIN_PASS", "role-pass")
    monkeypatch.setenv("AITEST_INTERNAL_X", "internal-only")
    built_env = _run_env(child_proj, None, "run_1", allowed_test_role_envs={"AITEST_ADMIN_PASS"})
    assert built_env.get("DATABASE_PASSWORD") == "biz-db-secret-99"
    assert built_env.get("AITEST_ADMIN_PASS") == "role-pass"
    assert "OPENAI_API_KEY" not in built_env
    assert "AITEST_INTERNAL_X" not in built_env

    # 跳过宿主机安装绝不能伪造 ok=True
    ws = Workspace(tmp_path / "ws")
    ws.init()
    run_dir = ws.root / "runs" / "r1"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(deps_and_l1, "_missing_dependencies", lambda *_a: ["fastapi"])
    settings = (DEFAULT_MEDIA_BUDGET_MB * 1024 * 1024, DEFAULT_FILE_CAP_BYTES, False)
    ok, _ref = deps_and_l1._install_dependencies(
        ws,
        run_dir,
        Path(sys.executable),
        ["fastapi"],
        settings,
        [],
        cwd=child_proj,
        allow_shared_host=False,
    )
    assert ok is False

    # preflight 解析 cd subdir && ./tool.cmd
    sub_bin = child_proj / "backend" / "bin"
    sub_bin.mkdir(parents=True)
    tool = sub_bin / "start.cmd"
    tool.write_text("echo ok", encoding="utf-8")
    tool_res = check_service_tools(
        [("api", {"start": "cd backend && bin/start.cmd"})],
        child_proj,
        built_env,
    )
    assert tool_res["blocking_reasons"] == []

    # _healthy 不把 401 当成健康
    class Auth401Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(401)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Auth401Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert _healthy(f"http://127.0.0.1:{int(srv.server_address[1])}/health") is False
    finally:
        srv.shutdown()

    # 日志轮转：size < offset 时从 0 重读
    log_path = tmp_path / "service.log"
    log_path.write_text("old" * 200, encoding="utf-8")
    offsets = {str(log_path): 600}
    log_path.write_text('"GET /err HTTP/1.1" 500 -\n', encoding="utf-8")
    win = inspect_service_log_window(
        [{"name": "api", "log": log_path, "started": True, "ok": True}],
        offsets,
        case_method="http:[]",
    )
    assert any("500" in err for err in win["fatal_errors"])

    # 大日志截断保留尾部错误堆栈
    big_cmd = run_command(
        f'"{sys.executable}" -c "print(\'HEAD\'); print(\'X\'*5000); print(\'FINAL_TRACE_TAIL\')"',
        cwd=tmp_path,
        max_output_bytes=1200,
    )
    out_txt = big_cmd.stdout.decode("utf-8", errors="ignore")
    assert "HEAD" in out_txt and "FINAL_TRACE_TAIL" in out_txt
