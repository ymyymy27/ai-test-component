"""Infer project test commands, frameworks and capabilities from declared facts.

Only manifests, configuration files and files that actually exist are reported.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from aitest.core.context.contracts import (
    _api_routes,
    _api_routes_with_meta,
    _frontend_api_calls,
    _frontend_assets,
    _normalize_compare_path,
    parse_openapi_spec,
)
from aitest.core.context.manifests import (
    _FRAMEWORK_ORDER,
    _bun_tests,
    _component_commands,
    _component_manifests,
    _configured_smoke_command,
    _existing_commands,
    _frameworks,
    _package_dependencies,
    _playwright_tests,
    _pyproject_dependencies,
    _read_json,
    _read_toml,
    _requirement_names,
    _script_examples,
    _smallest_test_file,
    _specific_test_commands,
    _symbol_map,
    _test_commands,
    _test_dirs,
    _test_example,
    _test_function_map,
    _test_function_summaries,
    _test_modules,
    _unique,
    package_name,
    python_dependencies,
    test_dependency_specs,
)
from aitest.core.context.services import (
    _has_database,
    _has_frontend,
    _health_hosts,
    _services_with_health,
    detect_services,
)
from aitest.core.repo_index import (
    compute_repo_fingerprint,
    read_versioned_cache,
    write_versioned_cache,
)
from aitest.security import redact_tree


def build_project_context(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Return inferred facts used by plan generation."""
    pyproject = _read_toml(root / "pyproject.toml")
    package = _read_json(root / "package.json")
    components = _component_manifests(root)
    dependencies: set[str] = set()
    frameworks_found: list[str] = []
    declared: list[str] = []
    for component, component_pyproject, component_package in components:
        component_dependencies = _pyproject_dependencies(
            component_pyproject
        ) | _package_dependencies(component_package)
        for path in sorted(component.glob("requirements*.txt")):
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            component_dependencies |= _requirement_names(lines)
        dependencies |= component_dependencies
        for framework in _frameworks(
            component, component_dependencies, component_pyproject
        ):
            if framework not in frameworks_found:
                frameworks_found.append(framework)
        declared.extend(test_dependency_specs(component))
    test_dirs = _test_dirs(root)
    frameworks = [
        framework for framework in _FRAMEWORK_ORDER if framework in frameworks_found
    ]
    test_commands = _test_commands(
        config, pyproject, package, frameworks, test_dirs, root
    )
    test_commands = _unique([*test_commands, *_specific_test_commands(root, test_dirs)])
    for component, _component_pyproject, component_package in components:
        test_commands = _unique(
            [*test_commands, *_component_commands(root, component, component_package)]
        )
    services = _services_with_health(config.get("services"))
    if not services:
        services = _services_with_health(detect_services(root))
    api_routes, api_route_meta = _api_routes_with_meta(root)
    return {
        "test_commands": test_commands,
        "smoke_command": _configured_smoke_command(config),
        "smallest_test": _smallest_test_file(root, test_dirs) if test_dirs else "",
        "test_modules": _test_modules(root, test_dirs),
        "test_functions": _test_function_map(root, test_dirs),
        "test_summaries": _test_function_summaries(root, test_dirs),
        "playwright_tests": _playwright_tests(root),
        "bun_tests": _bun_tests(root),
        "test_dependencies": _unique(declared, 40),
        "test_example": _test_example(root, test_dirs),
        "symbols": _symbol_map(root),
        "services": services,
        "health_hosts": sorted(_health_hosts(services)),
        "test_dirs": test_dirs,
        "framework": frameworks,
        "has_frontend": any(
            _has_frontend(
                component,
                _pyproject_dependencies(component_pyproject)
                | _package_dependencies(component_package),
                component_package,
            )
            for component, component_pyproject, component_package in components
        ),
        "has_database": _has_database(root, dependencies, frameworks),
        "existing_commands": _existing_commands(root, package),
        "script_examples": _script_examples(root),
        "api_routes": api_routes,
        "api_route_meta": api_route_meta,
        "frontend_api_calls": _frontend_api_calls(root),
        "frontend_assets": _frontend_assets(root),
    }


_CONTEXT_CACHE_VERSION = 3


def _context_source_fingerprint(root: Path, config: dict[str, Any]) -> str:
    """计算项目上下文缓存指纹（调用统一 repo_index 脏文件验证指纹 + 脱敏配置）。"""
    safe_cfg = redact_tree(config, [])
    fp, _cacheable = compute_repo_fingerprint(
        root,
        cache_version=_CONTEXT_CACHE_VERSION,
        extra_data=safe_cfg,
        include_tests=True,
    )
    return fp


def cached_project_context(
    ws_root: Path,
    project_id: str,
    root: Path,
    config: dict[str, Any],
    *,
    use_cache: bool = True,
) -> dict[str, Any]:
    """带源码指纹磁盘缓存的 build_project_context，超大型/超时不写残缺缓存且淘汰旧槽。"""
    if not use_cache or not root.is_dir():
        return build_project_context(root, config)
    safe_cfg = redact_tree(config, [])
    fp, cacheable = compute_repo_fingerprint(
        root,
        cache_version=_CONTEXT_CACHE_VERSION,
        extra_data=safe_cfg,
        include_tests=True,
    )
    cache_path = ws_root / ".cache" / "context" / f"{project_id}-{fp[:16]}.json"
    if cacheable:
        cached = read_versioned_cache(
            cache_path,
            fingerprint=fp,
            cache_version=_CONTEXT_CACHE_VERSION,
            payload_key="context",
        )
        if cached is not None:
            return cached

    ctx = build_project_context(root, config)
    if cacheable:
        write_versioned_cache(
            cache_path,
            fingerprint=fp,
            cache_version=_CONTEXT_CACHE_VERSION,
            data=ctx,
            payload_key="context",
            evict_prefix=f"{project_id}-",
            keep_slots=2,
        )
    return ctx


_T1_BACKENDS = frozenset(
    {
        "fastapi",
        "django",
        "flask",
        "express",
        "nextjs",
        "nestjs",
        "fastify",
        "spring_boot",
        "go_gin",
    }
)
_T4_PROTOCOLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("graphql", ("graphql", "graphene", "strawberry", "apollo-server", "@apollo/server", "gqlgen")),
    ("grpc", ("grpcio", "@grpc/grpc-js", "google.golang.org/grpc", "protobuf", ".proto")),
    ("websocket", ("socket.io", "websockets", "ws", "gorilla/websocket")),
    ("mqtt", ("paho-mqtt", "mqtt")),
    ("amqp", ("pika", "amqplib", "rabbitmq")),
    ("soap", ("zeep", "soap")),
    ("mobile", ("flutter", "react-native", "expo")),
    ("desktop", ("electron", "@tauri-apps/api", "tauri")),
)


def _scan_project_text_signals(
    root: Path, *, max_files: int = 120
) -> tuple[set[str], list[str], list[str]]:
    """只读扫描项目清单、Compose 与少量核心代码文件，提取协议/依赖/认证信号（不启进程）。"""
    signals: set[str] = set()
    evidence: list[str] = []
    compose_deps: list[str] = []
    if not root.is_dir():
        return signals, evidence, compose_deps

    # 1. 常见顶层/子目录清单与编排文件
    manifest_names = (
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Cargo.toml",
        "composer.json",
        "Gemfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "openapi.json",
        "swagger.json",
        ".env.example",
    )
    candidate_paths: list[Path] = []
    for name in manifest_names:
        p = root / name
        if p.is_file():
            candidate_paths.append(p)
    ignored_children = {"node_modules", ".venv", "venv", "dist", "build"}
    try:
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name in ignored_children:
                continue
            for name in manifest_names:
                p = child / name
                if p.is_file():
                    candidate_paths.append(p)
    except OSError:
        pass

    compose_dep_tokens = (
        "redis",
        "postgres",
        "postgresql",
        "mysql",
        "mariadb",
        "mongo",
        "mongodb",
        "minio",
        "mailpit",
        "mailhog",
    )
    for path in candidate_paths:
        rel = path.relative_to(root).as_posix()
        lname = path.name.lower()
        if lname in {"openapi.json", "swagger.json"}:
            signals.add("openapi")
            evidence.append(f"OPENAPI {rel}")
        elif lname == "go.mod":
            signals.add("go_mod")
        elif lname in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            signals.add("java_build")
        elif lname == "cargo.toml":
            signals.add("rust_cargo")
        elif lname == "composer.json":
            signals.add("php_composer")
        elif lname == "gemfile":
            signals.add("ruby_bundler")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:64 * 1024].lower()
        except OSError:
            continue
        if "compose" in lname:
            for dep in compose_dep_tokens:
                if dep in text:
                    compose_deps.append(dep)
                    evidence.append(f"{rel}:{dep}")
        for tok in (
            "fastapi",
            "django",
            "djangorestframework",
            "rest_framework",
            "flask",
            "express",
            "next",
            "@nestjs/core",
            "fastify",
            "spring-boot",
            "spring-web",
            "gin-gonic/gin",
            "labstack/echo",
            "redis",
            "ioredis",
            "sqlite",
            "aiosqlite",
            "sql.js",
            "better-sqlite3",
            "psycopg",
            "asyncpg",
            "pymysql",
            "mysql2",
            "pymongo",
            "mongoose",
            "minio",
            "boto3",
            "smtp",
            "nodemailer",
            "stripe",
            "jsonwebtoken",
            "pyjwt",
            "python-jose",
            "simplejwt",
            "flask-jwt",
            "oauth2",
            "cookie-parser",
            "axios",
            "graphql",
            "graphene",
            "strawberry",
            "apollo-server",
            "grpcio",
            "@grpc/grpc-js",
            "socket.io",
            "websockets",
            "paho-mqtt",
            "pika",
            "amqplib",
            "electron",
            "tauri",
            "react-native",
            "flutter",
            "uvicorn",
            "gunicorn",
            "pytest",
            "vitest",
            "playwright",
        ):
            if tok in text:
                signals.add(tok)

    # 2. 轻扫少量源码文件以发现框架/认证/自定义封装信号
    scanned = 0
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "coverage",
    }
    code_exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".kt", ".proto"}
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not d.startswith("."))
        if scanned >= max_files:
            break
        base_path = Path(base)
        rel_parts = base_path.relative_to(root).parts
        for name in sorted(files):
            if scanned >= max_files:
                break
            path = base_path / name
            suffix = path.suffix.lower()
            if suffix not in code_exts:
                continue
            if suffix == ".proto":
                signals.add(".proto")
                evidence.append(path.relative_to(root).as_posix())
                continue
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:32 * 1024]
            except OSError:
                continue
            low = text.lower()
            if "jwt" in low or "bearer" in low or "authorization" in low:
                signals.add("auth_jwt_hint")
            if "x-api-key" in low or "api_key" in low:
                signals.add("auth_api_key_hint")
            if "session" in low and "cookie" in low:
                signals.add("auth_session_cookie_hint")
            if "basic " in low and "authorization" in low:
                signals.add("auth_basic_hint")
            if "oauth2" in low or "client_credentials" in low:
                signals.add("auth_oauth2_hint")
            if "axios.create(" in text or "axios." in text:
                signals.add("axios_call")
            if "`${" in text and ("axios" in low or "base_url" in low or "baseurl" in low):
                signals.add("axios_template")
            stem_low = path.stem.lower()
            if (
                "fetch(" in text
                and ("function " in text or "const " in text)
                and any(k in stem_low for k in ("wrapper", "client", "request", "api"))
            ):
                signals.add("fetch_wrapper")
            if "import redis" in low or "from redis" in low or "ioredis" in low:
                signals.add("redis")
            if "sqlite3" in low or "sqlite:///" in low or "sql.js" in low:
                signals.add("sqlite")
            if "@restcontroller" in low or "@requestmapping" in low:
                signals.add("spring-boot")
            if "gin.default(" in low or "gin.new(" in low or "github.com/gin-gonic/gin" in low:
                signals.add("gin-gonic/gin")
            if "@controller(" in low:
                signals.add("@nestjs/core")
            if "defaultrouter(" in low or "simplerouter(" in low or "rest_framework" in low:
                signals.add("rest_framework")
                if "@action(" in low:
                    signals.add("drf_custom_action")
            if "flask(" in low or "blueprint(" in low:
                signals.add("flask")
            if "fastapi(" in low or "apirouter(" in low:
                signals.add("fastapi")
            if "express()" in low or "express.router(" in low:
                signals.add("express")
            if "nextresponse" in low or (
                path.name.startswith("route.") and "app" in rel_parts and "api" in rel_parts
            ):
                signals.add("nextjs_app")

    return signals, evidence, compose_deps


def _compute_capabilities_fingerprint(cap: dict[str, Any]) -> str:
    """仅对稳定能力事实字段计算 sha256 指纹（排除时间戳与自身）。"""
    stable = {
        "backend": sorted(cap.get("backend") or []),
        "router": sorted(cap.get("router") or []),
        "auth": sorted(cap.get("auth") or []),
        "frontend_calls": sorted(cap.get("frontend_calls") or []),
        "services": sorted(cap.get("services") or []),
        "start_adapters": sorted(cap.get("start_adapters") or []),
        "dependencies": sorted(
            (
                str(d.get("type") or ""),
                str(d.get("level") or ""),
                str(d.get("source") or ""),
            )
            for d in (cap.get("dependencies") or [])
            if isinstance(d, dict)
        ),
        "external_systems": sorted(
            (
                str(d.get("type") or ""),
                str(d.get("level") or ""),
                str(d.get("source") or ""),
            )
            for d in (cap.get("external_systems") or [])
            if isinstance(d, dict)
        ),
        "hosts": sorted(cap.get("hosts") or []),
        "level": str(cap.get("level") or ""),
        "unknowns": sorted(cap.get("unknowns") or []),
        "unsupported": sorted(cap.get("unsupported") or []),
        "source": str(cap.get("source") or "static"),
    }
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _extract_runtime_openapi_routes(
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """委托完整 OAS 2.0/3.x 解析器提取运行态契约，并保持向后兼容字段格式。"""
    parsed = parse_openapi_spec(spec, source="runtime_openapi", confidence="runtime")
    routes: list[dict[str, Any]] = []
    for entry in parsed:
        raw_fields = str(entry.get("fields") or "")
        field_list = [f for f in raw_fields.split(",") if f] if raw_fields else []
        formats = entry.get("field_formats")
        ftypes = dict(formats) if isinstance(formats, dict) else {}
        auth_kind = "authenticated" if entry.get("auth") != "public" else "public"
        item = {
            **entry,
            "auth": auth_kind,
            "fields": field_list,
            "field_types": ftypes,
            "source": "runtime_openapi",
            "runtime_verified": True,
        }
        routes.append(item)
    return routes


def _ordered_backend_urls(raw_services: object, *, local_only: bool = True) -> list[str]:
    """按 role=backend/api 优先顺序提取全部配置服务 URL，避免前端服务遮蔽后端 OpenAPI。"""
    if not isinstance(raw_services, dict):
        return []
    svc_items: list[tuple[str, dict[str, Any]]] = (
        [("", raw_services)]
        if {"start", "health", "stop", "base_url", "url"} & set(raw_services)
        else [(str(k), v) for k, v in raw_services.items() if isinstance(v, dict)]
    )
    candidates: list[tuple[int, str]] = []
    for sname, svc in svc_items:
        role = str(svc.get("role") or "").strip().lower()
        low_name = sname.strip().lower()
        prio = (
            0
            if (
                role in {"backend", "api"}
                or any(tok in low_name for tok in ("back", "api", "server"))
            )
            else 1
        )
        for key in ("base_url", "health", "health_url", "url"):
            raw = str(svc.get(key) or "").strip()
            m = re.match(r"^(https?://[^/]+)", raw)
            if not m:
                continue
            host = (m.group(1).split("://", 1)[1].split(":", 1)[0]).strip("[]").lower()
            if local_only and host not in {"127.0.0.1", "localhost", "::1"}:
                continue
            candidates.append((prio, m.group(1)))
            break
    candidates.sort(key=lambda item: item[0])
    return list(dict.fromkeys(url for _prio, url in candidates))


def fetch_runtime_openapi_routes(
    config: dict[str, Any],
    *,
    timeout: float = 1.5,
) -> tuple[list[dict[str, Any]], str]:
    """严格只读按 backend/api 优先遍历全部服务 URL 探测运行态 OpenAPI（绝不启进程）。"""
    raw_services = config.get("services") if isinstance(config, dict) else None
    urls = _ordered_backend_urls(raw_services, local_only=True)
    if not urls:
        return [], ""

    for base_url in urls:
        for probe_path in ("/openapi.json", "/openapi", "/v3/api-docs", "/swagger.json"):
            try:
                req = urllib.request.Request(f"{base_url}{probe_path}", method="GET")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if int(resp.status) != 200:
                        continue
                    raw_body = resp.read(1024 * 1024).decode("utf-8", errors="replace")
                    body = json.loads(raw_body)
                    if isinstance(body, dict) and isinstance(body.get("paths"), dict):
                        routes = _extract_runtime_openapi_routes(body)
                        if routes:
                            return routes, probe_path
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                json.JSONDecodeError,
                ValueError,
            ):
                continue
    return [], ""


def build_capabilities_snapshot(
    root: Path,
    config: dict[str, Any],
    *,
    project_context: dict[str, Any] | None = None,
    runtime_introspect: bool = False,
    secrets: list[str] | None = None,
) -> dict[str, Any]:
    """构造严格只读的项目能力快照与稳定指纹（覆盖 T1-T4 / D1-D4）。

    严守 D3：默认 ``runtime_introspect=False`` 绝不启动任何子进程或服务；
    仅当显式确认升级 ``runtime_introspect=True`` 时才对已声明健康地址尝试只读内省，
    失败自动回退静态并标 ``degraded=True``。
    """
    ctx = project_context if project_context is not None else build_project_context(root, config)
    if project_context is not None and isinstance(ctx.get("api_routes"), list):
        routes = list(ctx.get("api_routes") or [])
        raw_meta = ctx.get("api_route_meta")
        route_meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    else:
        routes, route_meta = _api_routes_with_meta(root)
    fe_calls = ctx.get("frontend_api_calls") or []
    signals, scan_evidence, compose_deps = _scan_project_text_signals(root)

    backends: list[str] = []
    routers: list[str] = []
    evidence: list[str] = list(scan_evidence[:20])

    def _add(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)

    route_kinds = {str(r.get("source_kind") or "") for r in routes if isinstance(r, dict)}
    for r in routes[:10]:
        if isinstance(r, dict) and r.get("file"):
            ev = f"{r['file']}:{r.get('line') or 1}"
            if ev not in evidence:
                evidence.append(ev)

    frameworks = set(str(x).lower() for x in (ctx.get("framework") or []))
    if "fastapi" in frameworks or "fastapi" in signals:
        _add(backends, "fastapi")
        _add(routers, "fastapi")
    if "django" in frameworks or "django" in signals or "rest_framework" in signals:
        _add(backends, "django")
        if "djangorestframework" in signals or "rest_framework" in signals or "drf" in route_kinds:
            _add(routers, "drf")
        else:
            _add(routers, "django")
    if "flask" in frameworks or "flask" in signals:
        _add(backends, "flask")
        _add(routers, "flask")
    if "express" in frameworks or "express" in signals or "node_express" in route_kinds:
        _add(backends, "express")
        _add(routers, "express")
    if (
        "nextjs" in frameworks
        or "nextjs_app" in signals
        or "nextjs_app" in route_kinds
        or "nextjs_pages" in route_kinds
    ):
        _add(backends, "nextjs")
        _add(
            routers,
            "nextjs-app-router"
            if ("nextjs_app" in route_kinds or "nextjs_app" in signals)
            else "nextjs-pages-router",
        )
    if "@nestjs/core" in signals or "nestjs" in route_kinds:
        _add(backends, "nestjs")
        _add(routers, "nestjs")
    if "fastify" in signals:
        _add(backends, "fastify")
        _add(routers, "fastify")
    if "spring-boot" in signals or "spring-web" in signals or "spring_boot" in route_kinds:
        _add(backends, "spring_boot")
        _add(routers, "spring-mvc")
    if "gin-gonic/gin" in signals or "go_web" in route_kinds:
        _add(backends, "go_gin")
        _add(routers, "gin")
    if "labstack/echo" in signals:
        _add(backends, "go_echo")
        _add(routers, "echo")
    if "openapi" in signals or "openapi" in route_kinds:
        _add(routers, "openapi")

    # 认证识别
    auth_list: list[str] = []
    if any(
        s in signals
        for s in ("jsonwebtoken", "pyjwt", "python-jose", "simplejwt", "flask-jwt", "auth_jwt_hint")
    ) or any(str(r.get("auth") or "") in {"user", "superuser"} for r in routes):
        _add(auth_list, "jwt")
    if "cookie-parser" in signals or "auth_session_cookie_hint" in signals:
        _add(auth_list, "session_cookie")
    if "auth_api_key_hint" in signals:
        _add(auth_list, "api_key")
    if "auth_basic_hint" in signals:
        _add(auth_list, "basic")
    if "oauth2" in signals or "auth_oauth2_hint" in signals:
        _add(auth_list, "oauth2")
    if not auth_list and routes:
        _add(auth_list, "public")

    # 前端调用封装特征
    frontend_calls_kinds: list[str] = []
    if "axios_template" in signals:
        _add(frontend_calls_kinds, "axios-template")
    elif "axios" in signals or "axios_call" in signals:
        _add(frontend_calls_kinds, "axios")
    if "fetch_wrapper" in signals:
        _add(frontend_calls_kinds, "fetch-wrapper")
    elif fe_calls and not frontend_calls_kinds:
        _add(frontend_calls_kinds, "fetch")

    # 服务角色
    services_roles: list[str] = []
    raw_services = ctx.get("services")
    if isinstance(raw_services, dict) and raw_services:
        entries = (
            [raw_services]
            if {"start", "health", "stop"} & set(raw_services)
            else [v for v in raw_services.values() if isinstance(v, dict)]
        )
        for idx, entry in enumerate(entries):
            role = str(entry.get("role") or "").strip().lower()
            if not role:
                role = "frontend" if idx > 0 and ctx.get("has_frontend") else "backend"
            _add(services_roles, role)
    if backends and "backend" not in services_roles:
        _add(services_roles, "backend")
    if ctx.get("has_frontend") and "frontend" not in services_roles:
        _add(services_roles, "frontend")

    # 启动适配器识别
    start_adapters: list[str] = []
    if "uvicorn" in signals or "fastapi" in backends:
        _add(start_adapters, "python/uvicorn")
    if "flask" in backends:
        _add(start_adapters, "python/flask")
    if "django" in backends:
        _add(start_adapters, "python/manage.py")
    if (root / "package.json").is_file() or any(
        b in backends for b in ("express", "nextjs", "nestjs", "fastify")
    ):
        _add(start_adapters, "node/npm")
    if "java_build" in signals or "spring_boot" in backends:
        _add(start_adapters, "java/mvnw-gradlew")
    if "go_mod" in signals or "go_gin" in backends:
        _add(start_adapters, "go/run")
    if "rust_cargo" in signals:
        _add(start_adapters, "rust/cargo")
    if "php_composer" in signals:
        _add(start_adapters, "php/artisan")
    if "ruby_bundler" in signals:
        _add(start_adapters, "ruby/rails")

    # D1–D4 依赖与外部系统分级
    dependencies: list[dict[str, str]] = []
    seen_deps: set[str] = set()

    def _add_dep(dep_type: str, level: str, source: str, status: str = "detected") -> None:
        if dep_type not in seen_deps:
            seen_deps.add(dep_type)
            dependencies.append(
                {"type": dep_type, "level": level, "source": source, "status": status}
            )

    if any(s in signals for s in ("sqlite", "aiosqlite", "sql.js", "better-sqlite3")) or any(
        root.glob("*.sqlite*")
    ) or any(root.glob("*.db")):
        _add_dep("sqlite", "D1", "manifest/source")
    if "redis" in signals or "ioredis" in signals or "redis" in compose_deps:
        _add_dep("redis", "D1", "docker-compose" if "redis" in compose_deps else "manifest/source")
    pg_compose = any(d in compose_deps for d in ("postgres", "postgresql"))
    if any(s in signals for s in ("psycopg", "asyncpg")) or pg_compose:
        _add_dep("postgresql", "D1", "docker-compose" if pg_compose else "manifest")
    mysql_compose = any(d in compose_deps for d in ("mysql", "mariadb"))
    if any(s in signals for s in ("pymysql", "mysql2")) or mysql_compose:
        _add_dep("mysql", "D1", "docker-compose" if mysql_compose else "manifest")
    mongo_compose = any(d in compose_deps for d in ("mongo", "mongodb"))
    if any(s in signals for s in ("pymongo", "mongoose")) or mongo_compose:
        _add_dep("mongodb", "D2", "docker-compose" if mongo_compose else "manifest")
    if any(s in signals for s in ("minio", "boto3")) or "minio" in compose_deps:
        _add_dep("s3_minio", "D2", "docker-compose" if "minio" in compose_deps else "manifest")
    if any(d in compose_deps for d in ("mailpit", "mailhog")):
        _add_dep("mailpit", "D2", "docker-compose")

    external_systems: list[dict[str, str]] = []
    if "smtp" in signals or "nodemailer" in signals:
        external_systems.append(
            {"type": "smtp", "level": "D3", "source": "config/manifest", "status": "unknown"}
        )
    if "stripe" in signals:
        external_systems.append(
            {"type": "stripe", "level": "D3", "source": "manifest", "status": "unknown"}
        )

    # unknowns 与 unsupported（T4 协议 / D4 设施）
    unknowns: list[str] = []
    unsupported: list[str] = []
    if "fetch_wrapper" in signals:
        _add(unknowns, "frontend_wrapper")
    if "drf_custom_action" in signals:
        _add(unsupported, "drf_custom_action")
    if route_meta.get("dynamic_plugins"):
        _add(unknowns, "dynamic_plugin_routes")
    for proto_name, markers in _T4_PROTOCOLS:
        if any(m in signals for m in markers):
            _add(unsupported, proto_name)

    # T1–T4 级别判定
    if any(b in _T1_BACKENDS for b in backends):
        level = "T1"
    elif "openapi" in routers:
        level = "T2"
    elif routes or fe_calls:
        level = "T3"
    elif unsupported:
        level = "T4"
    else:
        level = "T3"
        if not routes and not fe_calls:
            _add(unknowns, "no_static_routes_detected")

    # D3 显式升级运行时内省（按 role=backend/api 优先遍历全部配置服务 URL，严守不自启进程）
    source_kind = "static"
    degraded = False
    runtime_routes: list[dict[str, Any]] = []
    if runtime_introspect:
        introspected = False
        health_hosts = ctx.get("health_hosts") or []
        candidate_urls: list[tuple[int, str]] = []
        raw_svcs = raw_services if isinstance(raw_services, dict) else {}
        svc_items: list[tuple[str, dict[str, Any]]] = (
            [("", raw_svcs)]
            if {"start", "health", "stop"} & set(raw_svcs)
            else [(str(k), v) for k, v in raw_svcs.items() if isinstance(v, dict)]
        )
        for sname, svc in svc_items:
            role = str(svc.get("role") or "").strip().lower()
            low_name = sname.strip().lower()
            is_backend = role in {"backend", "api"} or any(
                tok in low_name for tok in ("back", "api", "server")
            )
            prio = 0 if is_backend else 1
            for url_key in ("base_url", "health", "health_url", "url"):
                raw_u = str(svc.get(url_key) or "").strip()
                m = re.match(r"^(https?://[^/]+)", raw_u)
                if m:
                    candidate_urls.append((prio, m.group(1)))
                    break
        candidate_urls.sort(key=lambda item: item[0])
        ordered_urls = list(dict.fromkeys(u for _p, u in candidate_urls))
        if ordered_urls and health_hosts:
            for base_url in ordered_urls:
                for probe_path in ("/openapi.json", "/openapi", "/v3/api-docs", "/swagger.json"):
                    try:
                        req = urllib.request.Request(f"{base_url}{probe_path}", method="GET")
                        with urllib.request.urlopen(req, timeout=2.5) as resp:
                            if int(resp.status) == 200:
                                raw_body = resp.read(1024 * 1024).decode("utf-8", errors="replace")
                                body = json.loads(raw_body)
                                if isinstance(body, dict) and isinstance(body.get("paths"), dict):
                                    introspected = True
                                    source_kind = "runtime"
                                    _add(evidence, f"GET {probe_path}")
                                    if "openapi" not in routers:
                                        _add(routers, "openapi")
                                    runtime_routes.extend(
                                        parse_openapi_spec(
                                            body,
                                            source=f"{base_url}{probe_path}",
                                            confidence="runtime",
                                        )
                                    )
                                    break
                    except (
                        urllib.error.URLError,
                        TimeoutError,
                        OSError,
                        json.JSONDecodeError,
                        ValueError,
                    ):
                        continue
                if introspected:
                    break
        if not introspected:
            degraded = True
            source_kind = "static"
            _add(unknowns, "runtime_introspect_failed")

    snapshot: dict[str, Any] = {
        "backend": backends,
        "router": routers,
        "auth": auth_list,
        "frontend_calls": frontend_calls_kinds,
        "services": services_roles,
        "start_adapters": start_adapters,
        "dependencies": dependencies,
        "external_systems": external_systems,
        "hosts": ["local"],
        "level": level,
        "unknowns": unknowns,
        "unsupported": unsupported,
        "source": source_kind,
        "degraded": degraded,
        "evidence": evidence[:24],
    }
    if runtime_routes:
        snapshot["runtime_routes"] = runtime_routes[:300]
    snapshot["fingerprint"] = _compute_capabilities_fingerprint(snapshot)
    safe = redact_tree(snapshot, secrets or [])
    return safe if isinstance(safe, dict) else snapshot


def build_project_health_summary(
    capabilities: dict[str, Any],
    project_context: dict[str, Any],
    code_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造概览页顶部只读体检条所需的精简统计事实（不启动任何进程）。"""
    routes = project_context.get("api_routes") or []
    fe_calls = project_context.get("frontend_api_calls") or []
    services = project_context.get("services") or {}
    if isinstance(services, dict) and ({"start", "health", "stop"} & set(services)):
        service_count = 1
    elif isinstance(services, dict):
        service_count = len([v for v in services.values() if isinstance(v, dict)])
    else:
        service_count = 0
    fe_paths = {
        _normalize_compare_path(str(c.get("path") or ""))
        for c in fe_calls
        if isinstance(c, dict) and str(c.get("path") or "").strip()
    }
    uncovered_routes = [
        f"{r.get('method', 'GET')} {r.get('path', '')}"
        for r in routes
        if isinstance(r, dict)
        and _normalize_compare_path(str(r.get("path") or "")) not in fe_paths
    ]
    conflicts_count = (
        len(code_graph.get("conflicts") or [])
        if isinstance(code_graph, dict)
        else 0
    )
    return {
        "level": str(capabilities.get("level") or "T3"),
        "source": str(capabilities.get("source") or "static"),
        "degraded": bool(capabilities.get("degraded", False)),
        "service_count": service_count,
        "dependencies": list(capabilities.get("dependencies") or []),
        "route_count": len(routes),
        "uncovered_route_count": len(uncovered_routes),
        "uncovered_routes_preview": uncovered_routes[:6],
        "unknowns_count": len(capabilities.get("unknowns") or []),
        "unsupported_count": len(capabilities.get("unsupported") or []),
        "unknowns": list(capabilities.get("unknowns") or []),
        "unsupported": list(capabilities.get("unsupported") or []),
        "conflicts_count": conflicts_count,
        "fingerprint": str(capabilities.get("fingerprint") or ""),
    }


__all__ = [
    "_api_routes",
    "_bun_tests",
    "_component_commands",
    "_component_manifests",
    "_configured_smoke_command",
    "_existing_commands",
    "_frontend_api_calls",
    "_frontend_assets",
    "_has_database",
    "_has_frontend",
    "_health_hosts",
    "_playwright_tests",
    "_read_json",
    "_read_toml",
    "_script_examples",
    "_services_with_health",
    "_smallest_test_file",
    "_specific_test_commands",
    "_symbol_map",
    "_test_commands",
    "_test_dirs",
    "_test_example",
    "_test_function_map",
    "_test_function_summaries",
    "_test_modules",
    "build_capabilities_snapshot",
    "build_project_context",
    "build_project_health_summary",
    "cached_project_context",
    "detect_services",
    "package_name",
    "python_dependencies",
    "test_dependency_specs",
]
