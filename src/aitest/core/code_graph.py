"""无上限本地全栈索引与跨层代码依赖图引擎。

移除早期 300 文件扫描限制，按 .gitignore/.aitestignore 与安全规则遍历仓库源码，
构建「前端页面(frontend) -> 接口(api) -> 后端模块(backend) -> 数据存储(storage)」
四层代码图（nodes + edges + 源码坐标）、跨层契约冲突清单、模块分块摘要，
以及基于本地 Git diff 的影响范围反推与增量缓存失效。
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import os
import re
from collections import deque
from pathlib import Path
from typing import Any

from aitest.config import known_secret_values
from aitest.core.context.contracts import (
    _api_routes_with_meta,
    _frontend_api_calls,
    _frontend_assets,
)
from aitest.core.probes import ContractReconciliationProbe, _normalize_route_path
from aitest.security import is_ignored_path, load_ignore_patterns, redact_tree
from aitest.storage.files import read_json, write_json
from aitest.storage.workspace import Workspace, iso_now

_GRAPH_SCHEMA_VERSION = 4

_SOCKET_BUILTIN_EVENTS = frozenset({"connect", "disconnect", "connection", "error", "reconnect"})
_SOCKET_EMIT_RE = re.compile(r"\b(?:io|socket)\.emit\(\s*[\"']([A-Za-z0-9_.:-]+)[\"']")
_SOCKET_ON_RE = re.compile(r"\bsocket\.on\(\s*[\"']([A-Za-z0-9_.:-]+)[\"']")

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "coverage",
        "test-results",
        ".next",
        ".nuxt",
    }
)
_INDEXED_EXTS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".vue",
        ".html",
        ".css",
        ".prisma",
        ".sql",
    }
)
_FRONTEND_VIEW_EXTS = frozenset({".vue", ".html", ".tsx", ".jsx"})
_BACKEND_DIR_NAMES = frozenset({"backend", "server", "api", "app", "routes", "routers", "services"})
_FRONTEND_DIR_NAMES = frozenset({"frontend", "front", "web", "client", "ui", "pages", "views"})

_PY_ROUTE_RE = re.compile(
    r"@(?:\w+_)?router\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_PY_APP_ROUTE_RE = re.compile(
    r"@\w+\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_JS_ROUTE_RE = re.compile(
    r"\b(?:router|app|fastify)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_FE_ROUTE_PATH_RE = re.compile(
    r"(?:path\s*:\s*[\"'](/[^\"']*)[\"']|<Route\b[^>]*?\bpath\s*=\s*[\"']([^\"']*)[\"'])"
)
_JS_EXPORT_RE = re.compile(
    r"\bexport\s+(?:async\s+)?(?:function|class|const|let)\s+([A-Za-z_]\w*)"
)
_JS_IMPORT_RE = re.compile(r"\bfrom\s+[\"']([^\"']+)[\"']")
_SQLALCHEMY_TABLE_RE = re.compile(r"__tablename__\s*=\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']")
_DJANGO_MODEL_RE = re.compile(r"class\s+([A-Za-z_]\w*)\s*\([^)]*models\.Model[^)]*\)\s*:")
_SQLMODEL_CLASS_RE = re.compile(r"class\s+([A-Za-z_]\w*)\s*\([^)]*table\s*=\s*True[^)]*\)\s*:")
_PRISMA_MODEL_RE = re.compile(r"^\s*model\s+([A-Za-z_]\w*)\s*\{", re.MULTILINE)
_ENTITY_RE = re.compile(r"@Entity\(\s*(?:[\"']([^\"']+)[\"'])?\s*\)")
_CLASS_NAME_RE = re.compile(r"class\s+([A-Za-z_]\w*)")
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"']?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _is_test_file(rel_posix: str) -> bool:
    parts = [p.lower() for p in rel_posix.split("/")]
    name = parts[-1] if parts else ""
    if any(p in {"tests", "test", "e2e", "__tests__"} for p in parts[:-1]):
        return True
    return name.startswith("test_") or name.endswith(
        ("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js")
    )


def _module_name_for_path(rel_posix: str) -> str:
    parts = [part for part in rel_posix.split("/") if part]
    if len(parts) <= 1:
        return "root"
    if parts[0] in {"src", "app", "packages"} and len(parts) >= 3:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _line_number_at(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _scan_repository_files(
    root: Path, *, max_files: int = 10000
) -> tuple[list[tuple[str, str, int, int]], str]:
    """全量安全遍历工作区源码文件（尊重忽略规则与高危凭据黑名单），返回正文与总指纹。"""
    ignore_patterns = load_ignore_patterns(root)
    records: list[tuple[str, str, int, int]] = []
    hasher = hashlib.sha256()

    for base, dirs, names in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _SKIP_DIRS
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(names):
            suffix = Path(name).suffix.lower()
            if suffix not in _INDEXED_EXTS:
                continue
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            rel_posix = path.relative_to(root).as_posix()
            if _is_test_file(rel_posix):
                continue
            try:
                stat = path.stat()
                if stat.st_size > 512 * 1024:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            records.append((rel_posix, content, int(stat.st_size), int(stat.st_mtime_ns)))
            hasher.update(f"{rel_posix}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
            if len(records) >= max_files:
                return records, hasher.hexdigest()
    return records, hasher.hexdigest()


def _parse_python_module_facts(content: str) -> tuple[list[str], list[str]]:
    symbols: list[str] = []
    imports: list[str] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return symbols, imports
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                symbols.append(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return symbols[:24], imports[:24]


def _is_frontend_file(rel_posix: str) -> bool:
    suffix = Path(rel_posix).suffix.lower()
    if suffix in _FRONTEND_VIEW_EXTS or suffix == ".css":
        return True
    parts = {part.lower() for part in rel_posix.split("/")[:-1]}
    if parts & _BACKEND_DIR_NAMES:
        return False
    return bool(parts & _FRONTEND_DIR_NAMES)


def _is_boilerplate_init(rel_posix: str, content: str) -> bool:
    """判断 __init__.py 是否仅包含导包、文档字符串或 __all__ 等样板定义。"""
    if Path(rel_posix).name != "__init__.py":
        return False
    if "APIRouter" in content or ".include_router" in content or "@" in content:
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            allowed = {"__all__", "__version__", "__author__"}
            if targets and all(name in allowed for name in targets):
                continue
        return False
    return True


def _sort_nodes_by_priority(graph: dict[str, Any]) -> None:
    """按影响面、冲突、连接度数与样板降权稳定排序节点，提升大图信噪比。"""
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list):
        return
    degrees: dict[str, int] = {}
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            src = str(edge.get("source") or "")
            dst = str(edge.get("target") or "")
            if src:
                degrees[src] = degrees.get(src, 0) + 1
            if dst:
                degrees[dst] = degrees.get(dst, 0) + 1

    def sort_key(node: dict[str, Any]) -> tuple[int, int, int, int, str]:
        impact = str(node.get("impact") or "none")
        has_conflict = bool(node.get("has_conflict"))
        meta = node.get("meta")
        is_boilerplate = bool(isinstance(meta, dict) and meta.get("boilerplate"))
        if impact == "direct":
            bucket = 0
        elif has_conflict:
            bucket = 1
        elif impact == "transitive":
            bucket = 2
        elif not is_boilerplate:
            bucket = 3
        else:
            bucket = 4
        nid = str(node.get("id") or "")
        return (
            bucket,
            0 if has_conflict else 1,
            -degrees.get(nid, 0),
            1 if is_boilerplate else 0,
            nid,
        )

    nodes.sort(key=sort_key)


def _locate_api_routes(
    records: list[tuple[str, str, int, int]],
    declared_routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    located: dict[tuple[str, str], dict[str, Any]] = {}
    located_file_lines: set[tuple[str, int]] = set()
    covered_file_methods: set[tuple[str, str]] = set()
    for route in declared_routes:
        method = str(route.get("method") or "GET").upper()
        raw_path = str(route.get("path") or "").strip()
        if not raw_path:
            continue
        norm = _normalize_route_path(raw_path)
        file_rel = str(route.get("file") or "")
        try:
            line_no = int(route.get("line") or 0)
        except (TypeError, ValueError):
            line_no = 0
        located[(method, norm)] = {
            "method": method,
            "path": raw_path,
            "norm_path": norm,
            "auth": str(route.get("auth") or "public"),
            "fields": str(route.get("fields") or ""),
            "status_code": str(route.get("status_code") or ""),
            "file": file_rel,
            "line": line_no,
        }
        if file_rel:
            covered_file_methods.add((file_rel, method))
        if file_rel and line_no > 0:
            located_file_lines.add((file_rel, line_no))

    for rel_posix, content, _size, _mtime in records:
        suffix = Path(rel_posix).suffix.lower()
        if suffix == ".py":
            for pattern in (_PY_ROUTE_RE, _PY_APP_ROUTE_RE):
                for match in pattern.finditer(content):
                    line = _line_number_at(content, match.start())
                    if (rel_posix, line) in located_file_lines:
                        continue
                    method = match.group(1).upper()
                    raw_path = match.group(2).strip()
                    norm = _normalize_route_path(raw_path)
                    # 若 AST 已解析并展开了该文件同方法路由前缀，避免再新增无前缀裸路由
                    if (rel_posix, method) in covered_file_methods:
                        continue
                    entry = located.get((method, norm))
                    if entry is not None:
                        if not entry["file"]:
                            entry["file"] = rel_posix
                            entry["line"] = line
                    else:
                        # 前缀路由（如挂载到 /api/notes）尝试按后缀精确匹配唯一项
                        candidates = [
                            item
                            for (m, p), item in located.items()
                            if m == method and p.endswith(norm) and not item["file"]
                        ]
                        if len(candidates) == 1:
                            candidates[0]["file"] = rel_posix
                            candidates[0]["line"] = line
                        elif (method, norm) not in located:
                            located[(method, norm)] = {
                                "method": method,
                                "path": raw_path,
                                "norm_path": norm,
                                "auth": "public",
                                "fields": "",
                                "status_code": "",
                                "file": rel_posix,
                                "line": line,
                            }
        elif suffix in {".ts", ".js", ".mjs", ".cjs"} and not _is_frontend_file(rel_posix):
            for match in _JS_ROUTE_RE.finditer(content):
                line = _line_number_at(content, match.start())
                if (rel_posix, line) in located_file_lines:
                    continue
                method = match.group(1).upper()
                raw_path = match.group(2).strip()
                norm = _normalize_route_path(raw_path)
                key = (method, norm)
                if key not in located:
                    located[key] = {
                        "method": method,
                        "path": raw_path,
                        "norm_path": norm,
                        "auth": "public",
                        "fields": "",
                        "status_code": "",
                        "file": rel_posix,
                        "line": line,
                    }
    return list(located.values())


def _extract_storage_entities(
    records: list[tuple[str, str, int, int]],
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_entity(name: str, kind: str, rel_posix: str, line: int) -> None:
        key = name.strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        entities.append(
            {
                "id": f"st:{name}",
                "name": name,
                "kind": kind,
                "file": rel_posix,
                "line": line,
            }
        )

    for rel_posix, content, _size, _mtime in records:
        suffix = Path(rel_posix).suffix.lower()
        if suffix == ".py":
            for match in _SQLALCHEMY_TABLE_RE.finditer(content):
                add_entity(
                    match.group(1),
                    "table",
                    rel_posix,
                    _line_number_at(content, match.start()),
                )
            for match in _SQLMODEL_CLASS_RE.finditer(content):
                add_entity(
                    match.group(1),
                    "sqlmodel",
                    rel_posix,
                    _line_number_at(content, match.start()),
                )
            for match in _DJANGO_MODEL_RE.finditer(content):
                add_entity(
                    match.group(1),
                    "django_model",
                    rel_posix,
                    _line_number_at(content, match.start()),
                )
        elif suffix == ".prisma":
            for match in _PRISMA_MODEL_RE.finditer(content):
                add_entity(
                    match.group(1),
                    "prisma_model",
                    rel_posix,
                    _line_number_at(content, match.start()),
                )
        elif suffix in {".ts", ".js"}:
            for match in _ENTITY_RE.finditer(content):
                explicit = (match.group(1) or "").strip()
                after = content[match.end() : match.end() + 160]
                cls_match = _CLASS_NAME_RE.search(after)
                name = explicit or (cls_match.group(1) if cls_match else "")
                if name:
                    add_entity(
                        name,
                        "typeorm_entity",
                        rel_posix,
                        _line_number_at(content, match.start()),
                    )
        elif suffix == ".sql":
            for match in _CREATE_TABLE_RE.finditer(content):
                add_entity(
                    match.group(1),
                    "sql_table",
                    rel_posix,
                    _line_number_at(content, match.start()),
                )
    return entities


def apply_git_impact(
    graph: dict[str, Any],
    local_git: dict[str, Any] | None,
) -> dict[str, Any]:
    """结合本地 Git 变更文件对项目图做双向受影响传播分析（direct + 2 跳 transitive）。"""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    raw_changed = (
        local_git.get("changed_files")
        if isinstance(local_git, dict)
        else None
    )
    changed_list = raw_changed if isinstance(raw_changed, list) else []
    changed_set = {
        str(p).strip().replace("\\", "/").lstrip("./")
        for p in changed_list
        if isinstance(p, str) and p.strip()
    }

    adj: dict[str, set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        dst = str(edge.get("target") or "")
        if src and dst:
            adj.setdefault(src, set()).add(dst)
            adj.setdefault(dst, set()).add(src)

    direct_ids: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node["impact"] = "none"
        file_path = str(node.get("file") or "").replace("\\", "/").lstrip("./")
        if file_path and file_path in changed_set:
            node["impact"] = "direct"
            direct_ids.append(str(node["id"]))

    affected_ids = set(direct_ids)
    queue: deque[tuple[str, int]] = deque((nid, 0) for nid in direct_ids)
    while queue:
        current, depth = queue.popleft()
        if depth >= 2:
            continue
        for nxt in adj.get(current, ()):
            if nxt not in affected_ids:
                affected_ids.add(nxt)
                queue.append((nxt, depth + 1))

    by_id = {str(n["id"]): n for n in nodes if isinstance(n, dict) and "id" in n}
    for nid in affected_ids:
        node_obj = by_id.get(nid)
        if node_obj is not None and node_obj.get("impact") != "direct":
            node_obj["impact"] = "transitive"

    affected_modules = sorted(
        {
            str(n.get("module") or "")
            for n in nodes
            if isinstance(n, dict) and str(n.get("id")) in affected_ids and n.get("module")
        }
    )
    affected_routes = sorted(
        {
            str(n.get("label") or "")
            for n in nodes
            if isinstance(n, dict)
            and str(n.get("id")) in affected_ids
            and n.get("layer") == "api"
        }
    )
    graph["impact"] = {
        "changed_files": sorted(changed_set),
        "direct_node_ids": sorted(direct_ids),
        "affected_node_ids": sorted(affected_ids),
        "affected_modules": affected_modules,
        "affected_routes": affected_routes,
    }
    return graph


_EXTERNAL_DEP_RULES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "ext:postgres",
        "PostgreSQL",
        "database",
        ("psycopg", "asyncpg", "pg", "postgres", "sqlmodel"),
    ),
    ("ext:mysql", "MySQL", "database", ("pymysql", "mysql2", "mysql-connector")),
    ("ext:sqlite", "SQLite", "database", ("better-sqlite3", "aiosqlite", "sqlite-vec")),
    ("ext:mongodb", "MongoDB", "database", ("pymongo", "motor", "mongoose", "mongodb")),
    ("ext:redis", "Redis", "cache", ("redis", "ioredis", "aioredis")),
    (
        "ext:mq",
        "消息队列 (MQ/Kafka)",
        "broker",
        ("celery", "bullmq", "pika", "kafka-python", "kafkajs", "rabbitmq"),
    ),
    ("ext:s3", "对象存储 (S3/OSS)", "storage", ("boto3", "minio", "@aws-sdk/client-s3")),
    (
        "ext:llm",
        "大模型 API",
        "ai",
        ("openai", "anthropic", "deepseek", "langchain", "google-genai"),
    ),
    ("ext:github", "GitHub 服务", "integration", ("@octokit", "pygithub", "octokit")),
    ("ext:browser", "Playwright 浏览器", "runtime", ("playwright", "@playwright/test")),
    ("ext:mail", "邮件服务 (SMTP)", "notification", ("nodemailer", "aiosmtplib", "resend")),
)


def _detect_external_systems(root: Path, config: dict[str, Any]) -> list[dict[str, str]]:
    """基于通用依赖清单、编排文件与项目配置探测真实外部系统与基础设施依赖。"""
    corpus_parts: list[tuple[str, str]] = []
    manifest_names = {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "go.mod",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    }
    search_dirs = [root]
    with contextlib.suppress(OSError):
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and child.name not in _SKIP_DIRS:
                search_dirs.append(child)
    for folder in search_dirs:
        for fname in sorted(manifest_names):
            fpath = folder / fname
            if not fpath.is_file():
                continue
            with contextlib.suppress(OSError):
                if fpath.stat().st_size <= 256 * 1024:
                    rel = fpath.relative_to(root).as_posix()
                    txt = fpath.read_text(encoding="utf-8", errors="ignore").lower()
                    corpus_parts.append((rel, txt))

    found: dict[str, dict[str, str]] = {}
    for ext_id, title, category, tokens in _EXTERNAL_DEP_RULES:
        for rel, body in corpus_parts:
            hit = next(
                (
                    tok
                    for tok in tokens
                    if re.search(rf"(?<![a-z0-9_-]){re.escape(tok)}(?![a-z0-9_-])", body)
                ),
                None,
            )
            if hit:
                found[ext_id] = {
                    "id": ext_id,
                    "name": title,
                    "category": category,
                    "detail": f"依赖标识 {hit}",
                    "source": rel,
                }
                break

    if isinstance(config.get("github"), dict) and config["github"].get("repo"):
        found.setdefault(
            "ext:github",
            {
                "id": "ext:github",
                "name": "GitHub 服务",
                "category": "integration",
                "detail": str(config["github"].get("repo")),
                "source": "project.config.json",
            },
        )
    if isinstance(config.get("model"), dict) and config["model"].get("model"):
        found.setdefault(
            "ext:llm",
            {
                "id": "ext:llm",
                "name": "大模型 API",
                "category": "ai",
                "detail": str(config["model"].get("model")),
                "source": "project.config.json",
            },
        )
    return list(found.values())[:8]


def build_code_graph(
    root: Path,
    config: dict[str, Any],
    *,
    local_git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """扫描仓库源码并构建 4 层代码链路图、契约冲突、模块分块、外部系统与 Git 影响范围。"""
    if not root.is_dir():
        return {
            "schema_version": _GRAPH_SCHEMA_VERSION,
            "generated_at": iso_now(),
            "source_hash": "",
            "stats": {
                "total_files": 0,
                "frontend": 0,
                "api": 0,
                "backend": 0,
                "storage": 0,
                "edges": 0,
                "conflicts": 0,
            },
            "nodes": [],
            "edges": [],
            "conflicts": [],
            "module_chunks": [],
            "external_systems": [],
            "impact": {
                "changed_files": [],
                "direct_node_ids": [],
                "affected_node_ids": [],
                "affected_modules": [],
                "affected_routes": [],
            },
        }

    records, source_hash = _scan_repository_files(root)
    declared_routes, routes_meta = _api_routes_with_meta(root, max_routes=2000)
    api_calls = _frontend_api_calls(root, max_files=2000, max_calls=1000)
    fe_assets = _frontend_assets(root, max_assets=500, max_html=200)
    located_routes = _locate_api_routes(records, declared_routes)
    route_files = {str(r.get("file") or "") for r in located_routes if r.get("file")}
    git_changed_set = {
        str(p).strip().replace("\\", "/").lstrip("./")
        for p in ((local_git or {}).get("changed_files") or [])
        if isinstance(p, str) and p.strip()
    }

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    file_to_fe_node: dict[str, str] = {}
    file_to_be_node: dict[str, str] = {}
    backend_imports: list[tuple[str, str, list[str]]] = []

    def add_edge(
        source: str,
        target: str,
        kind: str,
        label: str = "",
        conflict: bool = False,
    ) -> None:
        key = (source, target, kind)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append(
            {
                "id": f"e:{source}->{target}:{kind}",
                "source": source,
                "target": target,
                "kind": kind,
                "label": label,
                "conflict": conflict,
            }
        )

    # 1. 前端视图节点与后端源码模块节点（跳过无逻辑无挂载且未改动的空 __init__.py 样板）
    for rel_posix, content, _size, _mtime in records:
        suffix = Path(rel_posix).suffix.lower()
        module = _module_name_for_path(rel_posix)
        if _is_frontend_file(rel_posix):
            if suffix in _FRONTEND_VIEW_EXTS:
                fe_id = f"fe:{rel_posix}"
                file_to_fe_node[rel_posix] = fe_id
                routes_in_file = [
                    m.group(1) or m.group(2) for m in _FE_ROUTE_PATH_RE.finditer(content)
                ]
                nodes.append(
                    {
                        "id": fe_id,
                        "label": Path(rel_posix).name,
                        "layer": "frontend",
                        "kind": suffix.lstrip("."),
                        "file": rel_posix,
                        "line": 1,
                        "module": module,
                        "meta": {"paths": routes_in_file[:10]},
                        "has_conflict": False,
                        "impact": "none",
                    }
                )
        elif suffix in {".py", ".ts", ".js", ".mjs", ".cjs"}:
            is_boiler = _is_boilerplate_init(rel_posix, content)
            if is_boiler and rel_posix not in route_files and rel_posix not in git_changed_set:
                continue
            be_id = f"be:{rel_posix}"
            file_to_be_node[rel_posix] = be_id
            if suffix == ".py":
                symbols, imports = _parse_python_module_facts(content)
            else:
                symbols = _JS_EXPORT_RE.findall(content)[:24]
                imports = _JS_IMPORT_RE.findall(content)[:24]
            backend_imports.append((be_id, rel_posix, imports))
            meta: dict[str, Any] = {"symbols": symbols[:12]}
            if Path(rel_posix).name == "__init__.py":
                meta["boilerplate"] = True
            label = (
                Path(rel_posix).stem
                if Path(rel_posix).name != "__init__.py"
                else f"{Path(rel_posix).parent.name}/__init__"
            )
            nodes.append(
                {
                    "id": be_id,
                    "label": label,
                    "layer": "backend",
                    "kind": suffix.lstrip("."),
                    "file": rel_posix,
                    "line": 1,
                    "module": module,
                    "meta": meta,
                    "has_conflict": False,
                    "impact": "none",
                }
            )

    # 如果前端只有纯 .js/.ts（如原生 fetch api.js）没有独立视图节点，为其挂靠前端节点以保调用链完整
    for call in api_calls:
        src_file = str(call.get("source") or "")
        if src_file and src_file not in file_to_fe_node:
            # 优先归属同目录下已有 .html 节点，否则新建前端脚本节点
            parent = Path(src_file).parent.as_posix()
            sibling_html = next(
                (
                    p
                    for p in file_to_fe_node
                    if Path(p).parent.as_posix() == parent and p.endswith(".html")
                ),
                "",
            )
            if sibling_html:
                file_to_fe_node[src_file] = file_to_fe_node[sibling_html]
            else:
                fe_id = f"fe:{src_file}"
                file_to_fe_node[src_file] = fe_id
                nodes.append(
                    {
                        "id": fe_id,
                        "label": Path(src_file).name,
                        "layer": "frontend",
                        "kind": Path(src_file).suffix.lstrip(".") or "js",
                        "file": src_file,
                        "line": 1,
                        "module": _module_name_for_path(src_file),
                        "meta": {},
                        "has_conflict": False,
                        "impact": "none",
                    }
                )

    # 2. API 路由节点（带精确源码行号）并连向所属 backend 模块
    api_index: dict[tuple[str, str], str] = {}
    api_by_path: dict[str, list[str]] = {}
    for route in located_routes:
        method = str(route["method"])
        norm = str(route["norm_path"])
        api_id = f"api:{method}:{norm}"
        api_index[(method, norm)] = api_id
        api_by_path.setdefault(norm, []).append(api_id)
        route_file = str(route.get("file") or "")
        nodes.append(
            {
                "id": api_id,
                "label": f"{method} {route['path']}",
                "layer": "api",
                "kind": method.lower(),
                "file": route_file,
                "line": int(route.get("line") or 0),
                "module": _module_name_for_path(route_file) if route_file else "api",
                "meta": {
                    "method": method,
                    "path": route["path"],
                    "auth": route["auth"],
                    "fields": route["fields"],
                },
                "has_conflict": False,
                "impact": "none",
            }
        )
        if route_file and route_file in file_to_be_node:
            add_edge(api_id, file_to_be_node[route_file], "handled_by", label="处理")

    # 3. 数据存储模型节点
    storage_entities = _extract_storage_entities(records)
    for ent in storage_entities:
        st_file = str(ent["file"])
        nodes.append(
            {
                "id": ent["id"],
                "label": ent["name"],
                "layer": "storage",
                "kind": ent["kind"],
                "file": st_file,
                "line": int(ent["line"]),
                "module": _module_name_for_path(st_file),
                "meta": {"entity_kind": ent["kind"]},
                "has_conflict": False,
                "impact": "none",
            }
        )

    # 4. 建立 frontend -> api、backend -> backend、backend -> storage 边
    for call in api_calls:
        src_file = str(call.get("source") or "")
        fe_id = file_to_fe_node.get(src_file) or ""
        if not fe_id:
            continue
        method = str(call.get("method") or "GET").upper()
        norm = _normalize_route_path(str(call.get("path") or "/"))
        if method == "ANY":
            targets = list(api_by_path.get(norm) or [])
        else:
            exact = api_index.get((method, norm)) or api_index.get(("ANY", norm))
            targets = [exact] if exact else []
        for api_id in targets:
            add_edge(fe_id, api_id, "calls_api", label=f"{method} {norm}")

    # 后端模块间内部 import 边
    stem_to_be: dict[str, list[str]] = {}
    for rel_posix, be_id in file_to_be_node.items():
        dotted = rel_posix.rsplit(".", 1)[0].replace("/", ".")
        stem_to_be.setdefault(dotted, []).append(be_id)
        stem_to_be.setdefault(Path(rel_posix).stem, []).append(be_id)
    for be_id, _rel_posix, imports in backend_imports:
        for imp in imports:
            candidates = stem_to_be.get(imp) or stem_to_be.get(imp.split(".")[-1]) or []
            for target_be in candidates:
                if target_be != be_id:
                    add_edge(be_id, target_be, "imports", label=imp)

    # 4b. 轻量 Socket.IO 双向事件链路节点（仅当前后端同时匹配事件时生成，不报冲突）
    fe_emits: dict[str, tuple[str, int]] = {}
    be_ons: dict[str, tuple[str, int]] = {}
    for rel_posix, content, _s, _m in records:
        if rel_posix in file_to_fe_node or _is_frontend_file(rel_posix):
            for match in _SOCKET_EMIT_RE.finditer(content):
                ev = match.group(1)
                if ev not in _SOCKET_BUILTIN_EVENTS and ev not in fe_emits:
                    fe_emits[ev] = (rel_posix, _line_number_at(content, match.start()))
        elif rel_posix in file_to_be_node:
            for match in _SOCKET_ON_RE.finditer(content):
                ev = match.group(1)
                if ev not in _SOCKET_BUILTIN_EVENTS and ev not in be_ons:
                    be_ons[ev] = (rel_posix, _line_number_at(content, match.start()))
    for ev in sorted(set(fe_emits) & set(be_ons))[:80]:
        fe_file, _ = fe_emits[ev]
        be_file, be_line = be_ons[ev]
        fe_id = file_to_fe_node.get(fe_file, "")
        be_id = file_to_be_node.get(be_file, "")
        ws_id = f"api:WS:{ev}"
        if ws_id not in {n["id"] for n in nodes}:
            nodes.append(
                {
                    "id": ws_id,
                    "label": f"WS {ev}",
                    "layer": "api",
                    "kind": "socket",
                    "file": be_file,
                    "line": be_line,
                    "module": _module_name_for_path(be_file),
                    "meta": {"event": ev},
                    "has_conflict": False,
                    "impact": "none",
                }
            )
        if fe_id:
            add_edge(fe_id, ws_id, "calls_api", label=f"WS {ev}")
        if be_id:
            add_edge(ws_id, be_id, "handled_by", label="处理")

    # 后端源码对存储模型/表名的引用边
    content_by_file = {rel_posix: body for rel_posix, body, _s, _m in records}
    for rel_posix, be_id in file_to_be_node.items():
        body = content_by_file.get(rel_posix, "")
        for ent in storage_entities:
            name = str(ent["name"])
            if len(name) >= 3 and re.search(rf"\b{re.escape(name)}\b", body):
                add_edge(be_id, str(ent["id"]), "reads_writes", label=name)

    # 5. 跨层契约对账冲突与缺失资源冲突标注
    conflicts: list[dict[str, Any]] = []
    contract_probe = ContractReconciliationProbe().run(
        root=root,
        frontend_api_calls=api_calls,
        api_routes=declared_routes,
        has_dynamic_loader=bool(routes_meta.get("has_dynamic_loader")),
    )
    node_map = {str(n["id"]): n for n in nodes}
    for finding in contract_probe.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        src_file = str(finding.get("source") or "")
        method = str(finding.get("method") or "")
        path = str(finding.get("path") or "")
        affected: list[str] = []
        if src_file in file_to_fe_node:
            fe_id = file_to_fe_node[src_file]
            affected.append(fe_id)
            node_map[fe_id]["has_conflict"] = True
        norm = _normalize_route_path(path) if path else ""
        if method == "ANY":
            for aid in api_by_path.get(norm) or []:
                affected.append(aid)
                node_map[aid]["has_conflict"] = True
        elif (method, norm) in api_index:
            api_id = api_index[(method, norm)]
            affected.append(api_id)
            node_map[api_id]["has_conflict"] = True
        elif ("ANY", norm) in api_index:
            api_id = api_index[("ANY", norm)]
            affected.append(api_id)
            node_map[api_id]["has_conflict"] = True
        conflicts.append(
            {
                "kind": str(finding.get("kind") or "contract_mismatch"),
                "severity": str(finding.get("severity") or "error"),
                "detail": str(finding.get("detail") or ""),
                "file": src_file,
                "node_ids": affected,
            }
        )
    for asset in fe_assets:
        if isinstance(asset, dict) and asset.get("exists_in_repo") is False:
            src_file = str(asset.get("source") or "")
            affected = [file_to_fe_node[src_file]] if src_file in file_to_fe_node else []
            for nid in affected:
                node_map[nid]["has_conflict"] = True
            conflicts.append(
                {
                    "kind": "missing_static_asset",
                    "severity": "warning",
                    "detail": f"{src_file} 引用的本地资源缺失：{asset.get('href')}",
                    "file": src_file,
                    "node_ids": affected,
                }
            )

    # 6. 按业务模块切片（支持大项目按模块增量深读与失效缓存）
    chunks_map: dict[str, dict[str, Any]] = {}
    for node in nodes:
        mod = str(node.get("module") or "root")
        chunk = chunks_map.setdefault(
            mod,
            {
                "chunk_id": f"chunk:{mod}",
                "module": mod,
                "files": [],
                "node_ids": [],
                "layers": {"frontend": 0, "api": 0, "backend": 0, "storage": 0},
            },
        )
        chunk["node_ids"].append(node["id"])
        layer = str(node.get("layer") or "")
        if layer in chunk["layers"]:
            chunk["layers"][layer] += 1
        fpath = str(node.get("file") or "")
        if fpath and fpath not in chunk["files"]:
            chunk["files"].append(fpath)

    module_chunks: list[dict[str, Any]] = []
    for mod in sorted(chunks_map):
        chunk = chunks_map[mod]
        chunk_hasher = hashlib.sha256()
        for fpath in sorted(chunk["files"]):
            chunk_hasher.update(
                f"{fpath}:{len(content_by_file.get(fpath, ''))}\n".encode()
            )
        layers = chunk["layers"]
        chunk["content_hash"] = chunk_hasher.hexdigest()[:16]
        chunk["summary"] = (
            f"模块 {mod}：前端 {layers['frontend']}、接口 {layers['api']}、"
            f"后端 {layers['backend']}、存储 {layers['storage']}"
        )
        module_chunks.append(chunk)

    graph: dict[str, Any] = {
        "schema_version": _GRAPH_SCHEMA_VERSION,
        "generated_at": iso_now(),
        "source_hash": source_hash,
        "stats": {
            "total_files": len(records),
            "frontend": sum(1 for n in nodes if n["layer"] == "frontend"),
            "api": sum(1 for n in nodes if n["layer"] == "api"),
            "backend": sum(1 for n in nodes if n["layer"] == "backend"),
            "storage": sum(1 for n in nodes if n["layer"] == "storage"),
            "edges": len(edges),
            "conflicts": len(conflicts),
        },
        "nodes": nodes,
        "edges": edges,
        "conflicts": conflicts,
        "module_chunks": module_chunks,
        "external_systems": _detect_external_systems(root, config),
    }
    apply_git_impact(graph, local_git)
    _sort_nodes_by_priority(graph)
    secrets = known_secret_values([config])
    safe_graph = redact_tree(graph, secrets)
    return safe_graph if isinstance(safe_graph, dict) else graph


def _fast_source_hash(root: Path, *, max_files: int = 10000) -> str:
    ignore_patterns = load_ignore_patterns(root)
    hasher = hashlib.sha256()
    count = 0
    for base, dirs, names in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _SKIP_DIRS
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(names):
            if Path(name).suffix.lower() not in _INDEXED_EXTS:
                continue
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            rel_posix = path.relative_to(root).as_posix()
            if _is_test_file(rel_posix):
                continue
            with contextlib.suppress(OSError):
                stat = path.stat()
                if stat.st_size <= 512 * 1024:
                    hasher.update(f"{rel_posix}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
                    count += 1
            if count >= max_files:
                return hasher.hexdigest()
    return hasher.hexdigest()


def load_or_build_code_graph(
    ws: Workspace,
    project_id: str,
    root: Path,
    config: dict[str, Any],
    *,
    local_git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """优先从缓存读取代码图并重算当前 Git 影响范围，源码变化时自动增量重建。"""
    cache_path = ws.root / ".cache" / "code_graph" / project_id / "graph.json"
    current_hash = _fast_source_hash(root) if root.is_dir() else ""
    if cache_path.is_file():
        with contextlib.suppress(Exception):
            cached = read_json(cache_path)
            if (
                isinstance(cached, dict)
                and cached.get("schema_version") == _GRAPH_SCHEMA_VERSION
                and cached.get("source_hash") == current_hash
            ):
                apply_git_impact(cached, local_git)
                _sort_nodes_by_priority(cached)
                return cached
    graph = build_code_graph(root, config, local_git=local_git)
    with contextlib.suppress(OSError):
        write_json(cache_path, graph)
    return graph
