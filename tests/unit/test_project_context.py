"""Project context detection for multi-language monorepos."""

from __future__ import annotations

from pathlib import Path

from aitest.commands import _task_signal_context
from aitest.core.project_context import build_project_context, detect_services
from aitest.models import Project
from aitest.runtime.plan_commands import _workspace_source_fingerprint
from aitest.storage.workspace import Workspace


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _monorepo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(root / "pyproject.toml", "[project]\nname='root'\ndependencies=[]\n")
    _write(root / "package.json", '{"private": true, "workspaces": ["frontend"]}')
    _write(
        root / "backend/pyproject.toml",
        """
[project]
name = "backend"
dependencies = ["fastapi>=0.100", "sqlmodel>=0.0.1", "psycopg>=3"]

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]
""",
    )
    _write(root / "backend/tests/api/test_users.py", "def test_users():\n    assert True\n")
    _write(
        root / "frontend/package.json",
        '{"scripts": {"test": "playwright test"}, "devDependencies": '
        '{"react": "^19", "vite": "^8", "@playwright/test": "^1"}}',
    )
    _write(root / "frontend/playwright.config.ts", "export default {};\n")
    _write(root / "frontend/tests/login.spec.ts", "test('login', () => {});\n")
    return root


def test_nested_manifests_drive_frameworks_and_test_commands(tmp_path: Path) -> None:
    context = build_project_context(_monorepo(tmp_path), {})

    assert "backend/tests/" in context["test_dirs"]
    assert "frontend/tests/" in context["test_dirs"]
    assert {"pytest", "fastapi", "playwright"} <= set(context["framework"])
    assert context["has_frontend"] is True
    assert context["has_database"] is True
    assert context["smallest_test"] == "backend/tests/api/test_users.py"
    assert "python -m pytest backend/tests/api/test_users.py" in context["test_commands"]
    assert context["test_functions"] == {
        "backend/tests/api/test_users.py": ["test_users"]
    }
    assert (
        "python -m pytest backend/tests/api/test_users.py::test_users"
        in context["test_commands"]
    )
    assert any(spec.startswith("pytest") for spec in context["test_dependencies"])


def test_nested_test_directories_are_detected_without_root_tests(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    assert not (root / "tests").exists()
    context = build_project_context(root, {})
    assert context["test_modules"] == ["backend.tests.api.test_users"]
    assert context["test_example"]["path"] == "backend/tests/api/test_users.py"
    assert context["playwright_tests"] == {
        "frontend/tests/login.spec.ts": ["login"]
    }
    assert any("--grep" in command for command in context["test_commands"])


def test_detect_services_for_fastapi_vite_compose_stack(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    _write(
        root / "compose.yml",
        "services:\n  db:\n    image: postgres:18\n",
    )
    _write(root / "backend/app/main.py", "from fastapi import FastAPI\n")
    _write(
        root / "frontend/package.json",
        '{"scripts": {"dev": "vite"}, "devDependencies": {"vite": "^8"}}',
    )
    services = detect_services(root)
    assert list(services) == ["db", "backend", "frontend"]
    assert services["db"]["start"] == "docker compose up -d db"
    assert "uv run fastapi dev" in str(services["backend"]["start"])
    assert "bunx vite" in str(services["frontend"]["start"])


def test_detect_services_includes_override_services_and_flags(tmp_path: Path) -> None:
    root = _monorepo(tmp_path)
    _write(root / "compose.yml", "services:\n  db:\n    image: postgres:18\n")
    _write(
        root / "compose.override.yml",
        "services:\n  mailpit:\n    image: axllent/mailpit\n    ports:\n      - '8025:8025'\n",
    )
    services = detect_services(root)
    assert (
        services["db"]["start"]
        == "docker compose -f compose.yml -f compose.override.yml up -d db"
    )
    assert services["mailpit"]["health"] == "http://127.0.0.1:8025/livez"
    assert "up -d mailpit" in str(services["mailpit"]["start"])


def test_detect_standalone_python_http_service_and_do_not_override_monorepo(
    tmp_path: Path,
) -> None:
    standalone = tmp_path / "standalone"
    _write(
        standalone / "server.py",
        (
            "import os\n"
            "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
            'HOST = "0.0.0.0"\n'
            'PORT = int(os.environ.get("PORT", "8766"))\n'
            "class AppHandler(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            '        if self.path == "/api/items":\n'
            "            self.send_response(200)\n"
            'if __name__ == "__main__":\n'
            "    ThreadingHTTPServer((HOST, PORT), AppHandler).serve_forever()\n"
        ),
    )
    detected = detect_services(standalone)
    assert list(detected) == ["app"]
    assert detected["app"]["start"] == "python server.py"
    assert detected["app"]["health"] == "http://127.0.0.1:8766/"

    mono = _monorepo(tmp_path)
    _write(
        mono / "compose.yml",
        "services:\n  db:\n    image: postgres:18\n",
    )
    _write(mono / "backend/app/main.py", "from fastapi import FastAPI\n")
    _write(
        mono / "server.py",
        (
            "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
            'if __name__ == "__main__":\n'
            '    ThreadingHTTPServer(("127.0.0.1", 9000), BaseHTTPRequestHandler).serve_forever()\n'
        ),
    )
    mono_services = detect_services(mono)
    assert "app" not in mono_services
    assert "db" in mono_services and "backend" in mono_services


def test_http_handler_routes_and_frontend_contracts(tmp_path: Path) -> None:
    root = tmp_path / "web"
    _write(
        root / "server.py",
        (
            "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            '        if self.path == "/api/items": pass\n'
            "    def do_POST(self):\n"
            '        if self.path == "/api/items": pass\n'
            "    def do_PUT(self):\n"
            '        if self.path.startswith("/api/items/"): pass\n'
            "    def do_DELETE(self):\n"
            '        if self.path.startswith("/api/items/"): pass\n'
            'if __name__ == "__main__":\n'
            '    ThreadingHTTPServer(("127.0.0.1", 8123), Handler).serve_forever()\n'
        ),
    )
    _write(
        root / "index.html",
        (
            "<!doctype html><html><head>"
            '<link rel="stylesheet" href="missing.css" />'
            '<link rel="stylesheet" href="https://cdn.example.com/ext.css" />'
            "</head><body>"
            '<script src="app.js"></script>'
            "</body></html>"
        ),
    )
    _write(
        root / "app.js",
        (
            "const API_URL = '/api/items';\n"
            "async function load() { return request(API_URL); }\n"
            "async function add(title) {\n"
            "  return request(API_URL, {\n"
            "    method: 'POST',\n"
            "    body: JSON.stringify({ title: title })\n"
            "  });\n"
            "}\n"
            "async function toggle(id, done) {\n"
            "  return fetch(`/api/items/${id}`, {\n"
            "    method: 'PUT',\n"
            "    body: JSON.stringify({ done })\n"
            "  });\n"
            "}\n"
        ),
    )
    context = build_project_context(root, {})
    assert context["has_frontend"] is True
    assert context["services"]["app"]["health_url"] == "http://127.0.0.1:8123/"
    routes = {(r["method"], r["path"]) for r in context["api_routes"]}
    assert {
        ("GET", "/api/items"),
        ("POST", "/api/items"),
        ("PUT", "/api/items/{id}"),
        ("DELETE", "/api/items/{id}"),
    } <= routes
    calls = {
        (c["method"], c["path"]): c.get("body_keys", [])
        for c in context["frontend_api_calls"]
    }
    assert calls[("GET", "/api/items")] == []
    assert calls[("POST", "/api/items")] == ["title"]
    assert calls[("PUT", "/api/items/{param}")] == ["done"]
    assets = {a["href"]: a["exists_in_repo"] for a in context["frontend_assets"]}
    assert assets == {"missing.css": False, "app.js": True}


def test_task_signal_context_surfaces_sources_without_readme(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    ws_empty = Workspace(tmp_path / "ws_empty")
    proj_empty = Project(project_id="proj_empty", name="empty", root=str(empty_root))
    empty_signal = _task_signal_context(ws_empty, proj_empty)
    assert "项目关键文件" not in empty_signal
    assert "核心源码摘要" not in empty_signal

    app_root = tmp_path / "app"
    _write(
        app_root / "server.py",
        (
            "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
            "class H(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            '        if self.path == "/api/orders": pass\n'
            'if __name__ == "__main__":\n'
            '    ThreadingHTTPServer(("127.0.0.1", 8877), H).serve_forever()\n'
        ),
    )
    _write(app_root / "index.html", '<link rel="stylesheet" href="app.css" />\n')
    ws_app = Workspace(tmp_path / "ws_app")
    proj_app = Project(project_id="proj_app", name="orders", root=str(app_root))
    app_signal = _task_signal_context(ws_app, proj_app)
    assert "项目关键文件" in app_signal
    assert "核心源码摘要" in app_signal
    assert "识别服务" in app_signal
    assert "GET /api/orders" in app_signal
    assert "app.css(exists=False)" in app_signal


def test_workspace_fingerprint_ignores_runtime_data_and_tracks_html_css(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    _write(root / "index.html", "<h1>v1</h1>")
    _write(root / "data" / "todos.json", '{"items": [1]}')
    fp_initial = _workspace_source_fingerprint(root)

    _write(root / "data" / "todos.json", '{"items": [1, 2]}')
    assert _workspace_source_fingerprint(root) == fp_initial

    _write(root / "style.css", "body { color: red; }")
    assert _workspace_source_fingerprint(root) != fp_initial
