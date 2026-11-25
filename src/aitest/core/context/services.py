"""本地技术栈、前后端/数据库能力、健康地址与轻量单文件服务探测。"""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aitest.core.context.manifests import _read_json

_FRONTEND_DEPENDENCIES = {
    "react",
    "react-dom",
    "vue",
    "@angular/core",
    "svelte",
    "next",
    "nuxt",
    "vite",
    "solid-js",
    "preact",
    "ember-source",
}

_FRONTEND_FILES = (
    "index.html",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
    "angular.json",
    "svelte.config.js",
    "nuxt.config.ts",
)

_FRONTEND_SCRIPT_HINTS = (
    "vite",
    "next ",
    "react-scripts",
    "nuxt",
    "ng serve",
    "vue-cli-service",
    "svelte",
)

_DATABASE_DEPENDENCIES = {
    "sqlalchemy",
    "alembic",
    "psycopg2",
    "psycopg2-binary",
    "psycopg",
    "asyncpg",
    "aiosqlite",
    "pymysql",
    "mysqlclient",
    "pymongo",
    "motor",
    "redis",
    "peewee",
    "tortoise-orm",
    "prisma",
    "@prisma/client",
    "sequelize",
    "typeorm",
    "knex",
    "mongoose",
    "pg",
    "mysql2",
    "sqlite3",
    "better-sqlite3",
}

_DATABASE_FILES = ("alembic.ini", "schema.prisma")
_SINGLE_SERVICE_KEYS = frozenset({"start", "health", "stop"})

_PY_SERVER_HINTS = (
    "ThreadingHTTPServer",
    "HTTPServer",
    "BaseHTTPRequestHandler",
    "SimpleHTTPRequestHandler",
    "uvicorn.run(",
    "app.run(",
    "make_server(",
    "WSGIServer",
)
_PY_ENTRY_HINTS = (
    '__name__ == "__main__"',
    "__name__ == '__main__'",
    ".serve_forever(",
    "uvicorn.run(",
    "app.run(",
)
_PY_FRAMEWORK_DEPS = ("fastapi", "flask", "uvicorn", "aiohttp", "tornado", "starlette")
_STANDALONE_PRIORITY_NAMES = ("server", "app", "main", "run", "wsgi", "index")


def _has_frontend(root: Path, dependencies: set[str], package: dict[str, Any]) -> bool:
    if dependencies & _FRONTEND_DEPENDENCIES:
        return True
    scripts = package.get("scripts")
    if isinstance(scripts, dict):
        values = " ".join(
            str(value).lower() for value in scripts.values() if isinstance(value, str)
        )
        if any(hint in values for hint in _FRONTEND_SCRIPT_HINTS):
            return True
    if any((root / name).is_file() for name in _FRONTEND_FILES):
        return True
    return (
        (root / "src" / "components").is_dir()
        or (root / "public").is_dir()
        or (root / "static" / "index.html").is_file()
        or (root / "templates" / "index.html").is_file()
    )


def _has_database(root: Path, dependencies: set[str], frameworks: list[str]) -> bool:
    if dependencies & _DATABASE_DEPENDENCIES:
        return True
    if "django" in frameworks:
        return True
    return any((root / name).is_file() for name in _DATABASE_FILES)


def _is_single_service(value: dict[str, Any]) -> bool:
    return bool(_SINGLE_SERVICE_KEYS & set(value))


def _port(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value < 65536 else None
    if isinstance(value, str) and value.isdigit():
        number = int(value)
        return number if 0 < number < 65536 else None
    return None


def _health_url(entry: dict[str, Any]) -> str:
    health = entry.get("health")
    if isinstance(health, str) and health.strip():
        text = health.strip()
        if "://" in text:
            return text
        if text.startswith("/"):
            port = _port(entry.get("port"))
            if port is not None:
                host = str(entry.get("host") or "127.0.0.1")
                return f"http://{host}:{port}{text}"
        elif ":" in text or "/" in text:
            return f"http://{text}"
        return text
    port = _port(entry.get("port"))
    if port is None:
        return ""
    host = str(entry.get("host") or "127.0.0.1")
    return f"http://{host}:{port}/health"


def _services_with_health(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if _is_single_service(value):
        entry = dict(value)
        entry["health_url"] = _health_url(entry)
        return entry
    result: dict[str, Any] = {}
    for name, item in value.items():
        if isinstance(item, dict):
            entry = dict(item)
            entry["health_url"] = _health_url(entry)
            result[str(name)] = entry
        else:
            result[str(name)] = item
    return result


def _health_hosts(services: dict[str, Any]) -> set[str]:
    """Loopback plus every host declared by a service health URL."""
    hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    entries = services.values() if not _is_single_service(services) else [services]
    for item in entries:
        if not isinstance(item, dict):
            continue
        for key in ("health_url", "health"):
            raw = item.get(key)
            if not isinstance(raw, str) or "://" not in raw:
                continue
            host = urlsplit(raw).hostname
            if host:
                hosts.add(host.lower())
    return hosts


def _extract_port_and_host(text: str) -> tuple[int | None, str]:
    """从轻量服务端源码静态提取监听端口与可访问主机名。"""
    port_patterns = (
        r"os\.(?:environ\.get|getenv)\(\s*[\"'][A-Z_]*PORT[\"']\s*,\s*[\"']?(\d{2,5})[\"']?\s*\)",
        r"^\s*[A-Z_]*PORT\s*=\s*int\([^\n]*?,\s*[\"']?(\d{2,5})[\"']?\s*\)",
        r"^\s*PORT\s*=\s*(\d{2,5})\b",
        r"\bport\s*[:=]\s*(\d{2,5})\b",
        r"\(\s*(?:HOST|[\"'][^\"']*[\"'])\s*,\s*(\d{2,5})\s*\)",
    )
    port: int | None = None
    for idx, pattern in enumerate(port_patterns):
        flags = re.MULTILINE | (re.IGNORECASE if idx == 3 else 0)
        match = re.search(pattern, text, flags)
        if match:
            candidate = _port(match.group(1))
            if candidate is not None:
                port = candidate
                break
    if port is None:
        lowered = text.lower()
        if "flask" in lowered:
            port = 5000
        elif "uvicorn" in lowered or "fastapi" in lowered or "httpserver" in lowered:
            port = 8000

    raw_host = ""
    host_match = re.search(r"^\s*HOST\s*=\s*[\"']([^\"']*)[\"']", text, re.MULTILINE)
    if host_match:
        raw_host = host_match.group(1).strip()
    else:
        bind_match = re.search(
            r"\(\s*[\"']([^\"']*)[\"']\s*,\s*\d{2,5}\s*\)", text
        )
        if bind_match:
            raw_host = bind_match.group(1).strip()
    health_host = (
        "127.0.0.1"
        if not raw_host or raw_host in {"0.0.0.0", "::", "*"}
        else raw_host
    )
    return port, health_host


def _detect_standalone_service(root: Path) -> dict[str, dict[str, object]]:
    """无 Compose/全栈脚手架时，探测单文件 Python 或 Node 本地服务入口。"""
    py_candidates: list[tuple[int, int, str, Path]] = []
    search_dirs = ("", "app", "src", "backend", "server", "api")
    for depth_idx, rel_dir in enumerate(search_dirs):
        target_dir = root / rel_dir if rel_dir else root
        if not target_dir.is_dir():
            continue
        with suppress(OSError):
            for path in sorted(target_dir.iterdir()):
                if not path.is_file() or path.suffix.lower() != ".py":
                    continue
                if path.name.startswith("test_") or path.name.endswith("_test.py"):
                    continue
                if path.name in {"conftest.py", "setup.py"}:
                    continue
                stem = path.stem.lower()
                name_rank = (
                    0
                    if any(hint in stem for hint in _STANDALONE_PRIORITY_NAMES)
                    else 1
                )
                py_candidates.append(
                    (0 if depth_idx == 0 else 1, name_rank, path.as_posix(), path)
                )
    py_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    for _depth, _rank, _posix, path in py_candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:65536]
        except OSError:
            continue
        if not any(hint in text for hint in _PY_SERVER_HINTS):
            continue
        if not any(hint in text for hint in _PY_ENTRY_HINTS):
            continue
        port, health_host = _extract_port_and_host(text)
        if port is None:
            continue
        rel_posix = path.relative_to(root).as_posix()
        lowered = text.lower()
        needs_uv = (
            (root / "pyproject.toml").is_file()
            and (root / "uv.lock").is_file()
            and any(dep in lowered for dep in _PY_FRAMEWORK_DEPS)
        )
        start_cmd = f"uv run python {rel_posix}" if needs_uv else f"python {rel_posix}"
        return {
            "app": {
                "start": start_cmd,
                "health": f"http://{health_host}:{port}/",
                "timeout_seconds": 30,
            }
        }

    for js_name in ("server.js", "app.js", "index.js"):
        js_path = root / js_name
        if not js_path.is_file():
            continue
        try:
            text = js_path.read_text(encoding="utf-8", errors="replace")[:65536]
        except OSError:
            continue
        if "createServer(" not in text and "express()" not in text:
            continue
        listen_match = re.search(r"\.listen\(\s*(\d{2,5})", text)
        port_match = listen_match or re.search(r"\bPORT\s*[:=]\s*(\d{2,5})", text)
        port = _port(port_match.group(1)) if port_match else None
        if port is None:
            continue
        return {
            "app": {
                "start": f"node {js_name}",
                "health": f"http://127.0.0.1:{port}/",
                "timeout_seconds": 30,
            }
        }
    return {}


def detect_services(root: Path) -> dict[str, dict[str, object]]:
    """Best-effort local stack for a FastAPI + Vite + Compose repository."""
    services: dict[str, dict[str, object]] = {}
    compose_names = (
        "compose.yml",
        "compose.yaml",
        "docker-compose.yml",
        "compose.override.yml",
        "compose.override.yaml",
        "docker-compose.override.yml",
    )
    compose_files = [root / name for name in compose_names if (root / name).is_file()]
    compose_flags = (
        " ".join(f"-f {path.name}" for path in compose_files)
        if len(compose_files) > 1
        else ""
    )
    compose_text = ""
    for path in compose_files:
        try:
            compose_text += path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    db_name = ""
    if compose_text and "postgres" in compose_text.lower() and "db:" in compose_text:
        db_name = "db"
    if db_name:
        up = f"docker compose {compose_flags} up -d db".replace("  ", " ").strip()
        services[db_name] = {
            "start": up,
            "stop": up.replace(" up -d ", " stop "),
            "timeout_seconds": 120,
        }
    if "mailpit" in compose_text.lower():
        up = (
            f"docker compose {compose_flags} up -d mailpit".replace("  ", " ").strip()
        )
        services["mailpit"] = {
            "start": up,
            "stop": up.replace(" up -d ", " stop "),
            "health": "http://127.0.0.1:8025/livez",
            "timeout_seconds": 120,
        }
    backend = root / "backend"
    if (backend / "app" / "main.py").is_file() and "fastapi" in (
        (backend / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        if (backend / "pyproject.toml").is_file()
        else ""
    ).lower():
        prestart = (
            "uv run bash scripts/prestart.sh && "
            if (backend / "scripts" / "prestart.sh").is_file()
            else ""
        )
        services["backend"] = {
            "start": (
                "cd backend && "
                + prestart
                + "uv run fastapi dev --host 127.0.0.1 --port 18000"
            ),
            "health": "http://127.0.0.1:18000/api/v1/utils/health-check/",
            "timeout_seconds": 180,
        }
    frontend = root / "frontend"
    package = _read_json(frontend / "package.json")
    scripts = package.get("scripts")
    dev_script = scripts.get("dev") if isinstance(scripts, dict) else ""
    if (
        isinstance(dev_script, str)
        and "vite" in dev_script.lower()
        and (frontend / "playwright.config.ts").is_file()
    ):
        services["frontend"] = {
            "start": "cd frontend && bunx vite --host 127.0.0.1 --port 15173",
            "health": "http://127.0.0.1:15173",
            "timeout_seconds": 180,
        }
    if not services:
        services.update(_detect_standalone_service(root))
    return services
