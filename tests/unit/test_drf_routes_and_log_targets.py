"""DRF 路由、模板 baseURL、服务日志目标与 http 载荷修复的回归测试。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aitest.core.context.contracts import _api_routes, _frontend_api_calls
from aitest.core.http_spec import normalize_extract
from aitest.core.probes import ContractReconciliationProbe
from aitest.core.service_logs import capture_log_offsets, inspect_service_log_window
from aitest.models import Case
from aitest.runners.http_runner import run_http_sequence
from aitest.runtime.plan_validation import _validate_verify_method
from aitest.worker_impl.lifecycle import _case_expects_auth_failure


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_frontend_template_base_url_is_resolved(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "client/src/api/tasks.api.js",
        (
            'import axios from "axios";\n'
            'const URL = process.env.NODE_ENV === "production" ? import.meta.env.VITE_URL\n'
            '  : "http://localhost:8000";\n'
            "const tasksApi = axios.create({ baseURL: `${URL}/tasks/api/v1/tasks` });\n"
            'export const getAllTasks = () => tasksApi.get("/");\n'
            "export const getTask = (id) => tasksApi.get(`/${id}`);\n"
            "export const updateTask = (id, task) => tasksApi.put(`/${id}/`, task);\n"
        ),
    )

    got = {(call["method"], call["path"]) for call in _frontend_api_calls(tmp_path)}

    assert ("GET", "/tasks/api/v1/tasks") in got
    assert ("GET", "/tasks/api/v1/tasks/{param}") in got
    assert ("PUT", "/tasks/api/v1/tasks/{param}") in got


def test_frontend_base_url_const_fallback_and_concat(tmp_path: Path) -> None:
    """baseURL 是常量、|| 兜底或字符串拼接时也要解析，不只认模板字面量。"""
    _write(
        tmp_path,
        "client/src/a.js",
        (
            'import axios from "axios";\n'
            "const base = 'http://localhost:8000/api';\n"
            "const api = axios.create({ baseURL: base, headers: {} });\n"
            "api.get('/items');\n"
        ),
    )
    _write(
        tmp_path,
        "client/src/b.js",
        (
            'import axios from "axios";\n'
            "const api = axios.create({ baseURL: process.env.API_URL || '/v2' });\n"
            "api.post('/users', {});\n"
        ),
    )
    _write(
        tmp_path,
        "client/src/c.js",
        (
            'import axios from "axios";\n'
            "const api = axios.create({ baseURL: 'http://127.0.0.1:9000' + '/svc' });\n"
            "api.delete('/x');\n"
        ),
    )

    got = {(call["method"], call["path"]) for call in _frontend_api_calls(tmp_path)}

    assert ("GET", "/api/items") in got
    assert ("POST", "/v2/users") in got
    assert ("DELETE", "/svc/x") in got


def _write_drf_project(root: Path) -> None:
    _write(
        root,
        "django_crud_api/urls.py",
        (
            "from django.urls import path, include\n"
            "from django.views.generic import TemplateView\n"
            "urlpatterns = [\n"
            "    path('tasks/', include('tasks.urls')),\n"
            "    path('', TemplateView.as_view(template_name='index.html')),\n"
            "]\n"
        ),
    )
    _write(
        root,
        "tasks/urls.py",
        (
            "from django.urls import include, path\n"
            "from rest_framework import routers\n"
            "from tasks import views\n"
            "router = routers.DefaultRouter()\n"
            "router.register(r'tasks', views.TaskView, 'tasks')\n"
            "urlpatterns = [path('api/v1/', include(router.urls))]\n"
        ),
    )
    _write(
        root,
        "tasks/views.py",
        (
            "from rest_framework import viewsets\n"
            "from tasks.models import Task\n"
            "from tasks.serializer import TaskSerializer\n"
            "class TaskView(viewsets.ModelViewSet):\n"
            "    serializer_class = TaskSerializer\n"
            "    queryset = Task.objects.all()\n"
        ),
    )


def test_drf_router_routes_are_expanded(tmp_path: Path) -> None:
    _write_drf_project(tmp_path)

    got = {(route["method"], route["path"]) for route in _api_routes(tmp_path)}

    assert ("GET", "/tasks/api/v1/tasks") in got
    assert ("POST", "/tasks/api/v1/tasks") in got
    assert ("GET", "/tasks/api/v1/tasks/{param}") in got
    assert ("PUT", "/tasks/api/v1/tasks/{param}") in got
    assert ("PATCH", "/tasks/api/v1/tasks/{param}") in got
    assert ("DELETE", "/tasks/api/v1/tasks/{param}") in got


def test_drf_router_imported_from_module_and_transitive_base(tmp_path: Path) -> None:
    """router 定义在别的模块、ViewSet 继承自定义只读基类时也要正确展开。"""
    _write(
        tmp_path,
        "django_crud_api/urls.py",
        (
            "from django.urls import path, include\n"
            "urlpatterns = [path('tasks/', include('tasks.urls'))]\n"
        ),
    )
    _write(
        tmp_path,
        "tasks/routers.py",
        (
            "from rest_framework import routers\n"
            "from tasks import views\n"
            "router = routers.DefaultRouter()\n"
            "router.register(r'tasks', views.TaskView, 'tasks')\n"
        ),
    )
    _write(
        tmp_path,
        "tasks/urls.py",
        (
            "from django.urls import include, path\n"
            "from tasks.routers import router\n"
            "urlpatterns = [path('api/v1/', include(router.urls))]\n"
        ),
    )
    _write(
        tmp_path,
        "tasks/views.py",
        (
            "from rest_framework import viewsets\n"
            "class BaseViewSet(viewsets.ReadOnlyModelViewSet):\n"
            "    queryset = []\n"
            "class TaskView(BaseViewSet):\n"
            "    pass\n"
        ),
    )

    got = {(route["method"], route["path"]) for route in _api_routes(tmp_path)}

    assert ("GET", "/tasks/api/v1/tasks") in got
    assert ("GET", "/tasks/api/v1/tasks/{param}") in got
    assert ("POST", "/tasks/api/v1/tasks") not in got
    assert ("DELETE", "/tasks/api/v1/tasks/{param}") not in got


def test_contract_probe_matches_drf_routes_despite_root_any(tmp_path: Path) -> None:
    """DRF 项目前端调用应匹配方法级路由，不能被根路由 ANY / 误配。"""
    _write_drf_project(tmp_path)
    _write(
        tmp_path,
        "client/src/api/tasks.api.js",
        (
            'import axios from "axios";\n'
            'const URL = "http://localhost:8000";\n'
            "const tasksApi = axios.create({ baseURL: `${URL}/tasks/api/v1/tasks` });\n"
            'tasksApi.get("/");\n'
            "tasksApi.get(`/${id}`);\n"
            "tasksApi.put(`/${id}/`, {});\n"
        ),
    )

    calls = _frontend_api_calls(tmp_path)
    result = ContractReconciliationProbe().run(
        root=tmp_path,
        frontend_api_calls=calls,
        api_routes=_api_routes(tmp_path),
        has_dynamic_loader=False,
    )

    assert result["status"] == "passed"
    assert result["findings"] == []


def test_zero_hit_only_checked_for_case_target_service(tmp_path: Path) -> None:
    backend_log = tmp_path / "service-backend.log"
    backend_log.write_text(
        '127.0.0.1 - - "GET /api/v1/tasks/ HTTP/1.1" 200 -\n', encoding="utf-8"
    )
    frontend_log = tmp_path / "service-frontend.log"
    frontend_log.write_text("  VITE v4.5.14  ready in 377 ms\n", encoding="utf-8")
    states = [
        {
            "name": "backend",
            "config": {"health": "http://127.0.0.1:8000/tasks/api/v1/tasks/"},
            "log": backend_log,
            "started": True,
            "ok": True,
        },
        {
            "name": "frontend",
            "config": {"health": "http://localhost:5173/"},
            "log": frontend_log,
            "started": True,
            "ok": True,
        },
    ]
    offsets = capture_log_offsets(states)

    frontend_window = inspect_service_log_window(
        states,
        offsets,
        case_method="script:frontend_root_check.py",
        case_targets={"localhost:5173"},
    )
    backend_window = inspect_service_log_window(
        states,
        offsets,
        case_method="script:test_list_tasks.py",
        case_targets={"127.0.0.1:8000"},
    )

    assert frontend_window["zero_server_hit"] is False
    assert backend_window["zero_server_hit"] is True


def test_zero_hit_skipped_when_multi_service_target_unknown(tmp_path: Path) -> None:
    """多服务且无法判断目标时不做零命中告警，避免前端请求被后端日志误判。"""
    backend_log = tmp_path / "service-backend.log"
    backend_log.write_text(
        '127.0.0.1 - - "GET /api/v1/tasks/ HTTP/1.1" 200 -\n', encoding="utf-8"
    )
    frontend_log = tmp_path / "service-frontend.log"
    frontend_log.write_text(
        '127.0.0.1 - - "GET / HTTP/1.1" 200 -\n', encoding="utf-8"
    )
    states = [
        {
            "name": "backend",
            "config": {"health": "http://127.0.0.1:8000/"},
            "log": backend_log,
            "started": True,
            "ok": True,
        },
        {
            "name": "frontend",
            "config": {"health": "http://localhost:5173/"},
            "log": frontend_log,
            "started": True,
            "ok": True,
        },
    ]
    offsets = capture_log_offsets(states)

    window = inspect_service_log_window(
        states, offsets, case_method="script:env_based.py"
    )

    assert window["zero_server_hit"] is False


def test_http_single_object_payload_is_coerced() -> None:
    case = Case(
        case_id="case_http",
        title="http 单对象",
        layer="L2",
        expected="返回 200",
        expectation_source="接口返回 200",
        verify_method='http:{"method": "GET", "url": "http://127.0.0.1:8000/"}',
    )

    _validate_verify_method(case)

    assert case.verify_method.startswith("http:[")
    assert '"method":"GET"' in case.verify_method


def test_http_python_literal_payload_is_coerced() -> None:
    case = Case(
        case_id="case_http2",
        title="http Python 字面量",
        layer="L2",
        expected="返回 200",
        expectation_source="接口返回 200",
        verify_method="http:[{'method': 'GET', 'url': 'http://127.0.0.1:8000/'}]",
    )

    _validate_verify_method(case)

    assert case.verify_method.startswith("http:[")


def test_http_code_fence_and_trailing_comma_are_coerced() -> None:
    case = Case(
        case_id="case_http3",
        title="http 代码块",
        layer="L2",
        expected="返回 200",
        expectation_source="接口返回 200",
        verify_method=(
            "http:```json\\n"
            '{"method": "GET", "url": "http://127.0.0.1:8000/", "verify": true,}\\n'
            "```"
        ).replace("\\n", "\n"),
    )

    _validate_verify_method(case)

    assert case.verify_method.startswith("http:[")



def test_normalize_extract_shapes() -> None:
    assert normalize_extract({"token": "body.token"}) == {"token": "token"}
    assert normalize_extract({"var": "token", "path": "body.token"}) == {"token": "token"}
    assert normalize_extract({"name": "token", "path": "data.token"}) == {
        "token": "data.token"
    }


def test_plan_validation_normalizes_extract_shape_and_body_prefix() -> None:
    case = Case(
        case_id="case_extract",
        title="登录取 token",
        layer="L2",
        expected="登录成功",
        expectation_source="登录返回 token",
        verify_method=(
            'http:[{"method":"POST","url":"http://127.0.0.1:8000/login",'
            '"extract":{"var":"token","path":"body.token"}},'
            '{"method":"GET","url":"http://127.0.0.1:8000/protected",'
            '"headers":{"Authorization":"Bearer ${vars.token}"}}]'
        ),
    )

    _validate_verify_method(case)

    assert '"token":"token"' in case.verify_method
    assert '"var"' not in case.verify_method


def test_http_runner_accepts_var_path_extract() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json.dumps({"token": "abc"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            authorized = self.headers.get("Authorization") == "Bearer abc"
            body = b'{"ok": true}'
            self.send_response(200 if authorized else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        result = run_http_sequence(
            [
                {
                    "method": "POST",
                    "url": f"http://127.0.0.1:{port}/login",
                    "json": {"username": "admin", "password": "admin"},
                    "extract": {"var": "token", "path": "body.token"},
                },
                {
                    "method": "GET",
                    "url": f"http://127.0.0.1:{port}/protected",
                    "headers": {"Authorization": "Bearer ${vars.token}"},
                    "expect": [{"path": "status", "operator": "equals", "value": 200}],
                },
            ]
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert result.status == "passed", result.error


def test_negative_status_item_gets_status_assertion() -> None:
    """验收项要求 400/401 时，http 用例必须断言状态码，不能只断言 error 字段。"""
    case = Case(
        case_id="case_status",
        title="登录缺少 password 返回 400",
        layer="L2",
        expected="返回 400",
        expectation_source="登录缺少 password 返回 400",
        verify_method=(
            'http:[{"method":"POST","url":"http://127.0.0.1:8000/login",'
            '"json":{"username":"admin"},"expect":[{"path":"error",'
            '"operator":"exists","value":true}]}]'
        ),
    )

    _validate_verify_method(case)

    assert '"path":"status"' in case.verify_method
    assert '"value":400' in case.verify_method


def test_case_expects_auth_failure_detection() -> None:
    negative = Case(
        case_id="c1",
        title="未带 Authorization 访问商品列表返回 401",
        layer="L2",
        expected="返回 401",
        expectation_source="未带 Authorization 访问商品列表返回 401",
    )
    positive = Case(
        case_id="c2",
        title="admin 登录返回 200",
        layer="L2",
        expected="返回 200 且 token 非空",
        expectation_source="admin 登录返回 200 且 token 非空",
    )

    assert _case_expects_auth_failure(negative) is True
    assert _case_expects_auth_failure(positive) is False
