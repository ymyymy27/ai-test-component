"""Project context detection for multi-language monorepos."""

from __future__ import annotations

from pathlib import Path

from aitest.core.project_context import build_project_context, detect_services


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
    assert services["db"]["start"] == "docker compose -f compose.yml -f compose.override.yml up -d db"
    assert services["mailpit"]["health"] == "http://127.0.0.1:8025/livez"
    assert "up -d mailpit" in str(services["mailpit"]["start"])
