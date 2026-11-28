"""轻量级仓库架构节点图引擎（模块/组件 + 接口路由 + 外部系统 + Git 影响范围）。

基于 ``core.repo_index`` 单遍扫描构建，替代早期千行级多语言正则建表蜘蛛网与分块摘要；
仅展示可核查的文件模块节点、接口路由节点（优先运行态 OpenAPI）、静态资源冲突与外部依赖，
结合只读 Git 变更实现 direct + 2 跳 transitive 影响传播。
"""

from __future__ import annotations

import ast
import contextlib
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
from aitest.core.repo_index import (
    DEFAULT_SKIP_DIRS,
    RepoFile,
    compute_repo_fingerprint,
    discover_monorepo_packages,
    is_test_relpath,
    read_versioned_cache,
    scan_repo,
    write_versioned_cache,
)
from aitest.security import redact_tree
from aitest.storage.workspace import Workspace, iso_now

_GRAPH_SCHEMA_VERSION = 5

_SOCKET_BUILTIN_EVENTS = frozenset({"connect", "disconnect", "connection", "error", "reconnect"})
_SOCKET_EMIT_RE = re.compile(r"\b(?:io|socket)\.emit\(\s*[\"']([A-Za-z0-9_.:-]+)[\"']")
_SOCKET_ON_RE = re.compile(r"\bsocket\.on\(\s*[\"']([A-Za-z0-9_.:-]+)[\"']")

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
        ".json",
        ".yaml",
        ".yml",
        ".sql",
    }
)
_FRONTEND_VIEW_EXTS = frozenset({".vue", ".html", ".tsx", ".jsx"})
_BACKEND_DIR_NAMES = frozenset({"backend", "server", "api", "app", "routes", "routers", "services"})
_FRONTEND_DIR_NAMES = frozenset({"frontend", "front", "web", "client", "ui", "pages", "views"})

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


def _module_name_for_path(rel_posix: str) -> str:
    parts = [part for part in rel_posix.split("/") if part]
    if len(parts) <= 1:
        return Path(rel_posix).stem or "root"
    if parts[0].lower() in _FRONTEND_DIR_NAMES | _BACKEND_DIR_NAMES | {"src", "apps", "packages"}:
        return parts[1] if len(parts) >= 3 else parts[0]
    return parts[0]


def _classify_layer(rel_posix: str) -> str:
    parts = {p.lower() for p in rel_posix.split("/")[:-1]}
    suffix = Path(rel_posix).suffix.lower()
    if parts & _FRONTEND_DIR_NAMES or suffix in _FRONTEND_VIEW_EXTS:
        return "frontend"
    if (parts & _BACKEND_DIR_NAMES) or suffix in {".py", ".sql"}:
        return "backend"
    return "frontend" if suffix in {".js", ".ts", ".mjs", ".cjs"} else "backend"


def _detect_external_systems(root: Path, config: dict[str, Any]) -> list[dict[str, str]]:
    """基于包清单、Compose 与项目配置识别真实外部基础设施依赖。"""
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
    search_dirs = discover_monorepo_packages(root)
    for folder in search_dirs:
        for fname in sorted(manifest_names):
            fpath = folder / fname
            if not fpath.is_file():
                continue
            with contextlib.suppress(OSError, ValueError):
                if fpath.stat().st_size <= 256 * 1024:
                    rel = fpath.relative_to(root.resolve()).as_posix()
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
    return list(found.values())[:10]


def apply_git_impact(
    graph: dict[str, Any],
    local_git: dict[str, Any] | None,
) -> dict[str, Any]:
    """结合本地 Git 变更文件对项目图做双向受影响传播分析（direct + 2 跳 transitive）。"""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    raw_changed = local_git.get("changed_files") if isinstance(local_git, dict) else None
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


def _sort_nodes_by_priority(graph: dict[str, Any]) -> None:
    """保持 api/冲突节点优先排列，便于前端裁剪展示高信号节点。"""
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return

    def _key(node: Any) -> tuple[int, int, str]:
        if not isinstance(node, dict):
            return (9, 9, "")
        conflict_rank = 0 if node.get("has_conflict") else 1
        layer = str(node.get("layer") or "")
        layer_rank = {"api": 0, "backend": 1, "frontend": 2}.get(layer, 3)
        return (conflict_rank, layer_rank, str(node.get("id") or ""))

    nodes.sort(key=_key)


def _empty_graph() -> dict[str, Any]:
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


def _read_text_safe(entry: RepoFile, max_bytes: int = 256 * 1024) -> str:
    if entry.size > max_bytes:
        return ""
    try:
        return entry.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def build_code_graph(
    root: Path,
    config: dict[str, Any],
    *,
    local_git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建轻量诚实项目架构图（模块/组件 + 运行态或静态路由 + 外部系统 + Git 影响）。"""
    if not root.is_dir():
        return _empty_graph()

    scan = scan_repo(root, extensions=_INDEXED_EXTS, include_tests=False, max_files=6000)
    source_hash, _ = compute_repo_fingerprint(
        root,
        cache_version=_GRAPH_SCHEMA_VERSION,
        extra_data=redact_tree(config, []),
        include_tests=False,
        extensions=_INDEXED_EXTS,
    )
    declared_routes, routes_meta = _api_routes_with_meta(root, max_routes=2000)
    api_calls = _frontend_api_calls(root, max_files=2000, max_calls=1000)
    fe_assets = _frontend_assets(root, max_assets=500, max_html=200)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    file_to_node: dict[str, str] = {}
    layer_by_file: dict[str, str] = {}

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

    route_files = {
        str(r.get("file") or "")
        for r in declared_routes
        if isinstance(r, dict) and r.get("file")
    }
    call_files = {
        str(c.get("source") or "")
        for c in api_calls
        if isinstance(c, dict) and c.get("source")
    }
    asset_files = {
        str(a.get("source") or "")
        for a in fe_assets
        if isinstance(a, dict) and a.get("source")
    }

    # 1. 注册轻量源码/组件文件节点（过滤空 __init__.py 与无业务符号占位文件）
    file_texts: dict[str, str] = {}
    for entry in scan.files[:800]:
        rel = entry.rel_posix
        is_empty_init = False
        text = ""
        if Path(rel).name == "__init__.py" or rel.endswith((".js", ".ts", ".tsx", ".jsx")):
            text = _read_text_safe(entry)
            if text:
                file_texts[rel] = text
        if Path(rel).name == "__init__.py" and not text.strip():
            is_empty_init = True
        if is_empty_init and rel not in route_files:
            continue
        layer = _classify_layer(rel)
        node_id = f"mod:{rel}"
        is_fe_view = (
            rel in call_files
            or rel in asset_files
            or Path(rel).suffix.lower() in _FRONTEND_VIEW_EXTS
        )
        kind = (
            "component"
            if Path(rel).name in {"package.json", "pyproject.toml"}
            else "frontend_page"
            if layer == "frontend" and is_fe_view
            else "module"
        )
        nodes.append(
            {
                "id": node_id,
                "layer": layer,
                "kind": kind,
                "label": Path(rel).name,
                "module": _module_name_for_path(rel),
                "file": rel,
                "line": 1,
                "meta": {},
                "has_conflict": False,
                "impact": "none",
            }
        )
        file_to_node[rel] = node_id
        layer_by_file[rel] = layer

    # 2. 注册接口路由节点（优先运行态 OpenAPI 元数据，静态解析标 heuristic）
    api_node_by_norm: dict[tuple[str, str], list[str]] = {}
    api_nodes_by_path: dict[str, list[str]] = {}
    for route in declared_routes:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "GET").upper()
        path_str = str(route.get("path") or "/")
        src_file = str(route.get("file") or "")
        try:
            line_no = max(1, int(str(route.get("line") or "1")))
        except ValueError:
            line_no = 1
        norm = _normalize_route_path(path_str)
        api_id = f"api:{method}:{norm}"
        if not any(n["id"] == api_id for n in nodes):
            nodes.append(
                {
                    "id": api_id,
                    "layer": "api",
                    "kind": "api_route",
                    "label": f"{method} {path_str}",
                    "module": _module_name_for_path(src_file) if src_file else "api",
                    "file": src_file,
                    "line": line_no,
                    "meta": {
                        "method": method,
                        "path": path_str,
                        "auth": str(route.get("auth") or "public"),
                        "source_kind": str(route.get("source_kind") or "static"),
                        "confidence": str(route.get("confidence") or "heuristic"),
                    },
                    "has_conflict": False,
                    "impact": "none",
                }
            )
        api_node_by_norm.setdefault((method, norm), []).append(api_id)
        api_nodes_by_path.setdefault(norm, []).append(api_id)
        if src_file and src_file in file_to_node:
            add_edge(api_id, file_to_node[src_file], "handled_by", label="处理")

    # 3. 连接前端调用边与轻量级 Python 模块内部依赖边
    for call in api_calls:
        if not isinstance(call, dict):
            continue
        src_file = str(call.get("source") or "")
        method = str(call.get("method") or "GET").upper()
        norm = _normalize_route_path(str(call.get("path") or "/"))
        fe_id = file_to_node.get(src_file)
        if not fe_id:
            continue
        targets = api_node_by_norm.get((method, norm)) or api_nodes_by_path.get(norm) or []
        for tgt in targets:
            add_edge(fe_id, tgt, "calls_api", label=f"{method} {norm}")

    # 基于真实 Python import 语句建立同仓模块依赖边（不靠正则猜 DB 表）
    py_module_to_file: dict[str, str] = {}
    for rel in file_to_node:
        if rel.endswith(".py"):
            mod = rel[:-3].replace("/", ".")
            py_module_to_file[mod] = rel
            if mod.endswith(".__init__"):
                py_module_to_file[mod[: -len(".__init__")]] = rel
    for entry in scan.files[:400]:
        if not entry.rel_posix.endswith(".py") or entry.rel_posix not in file_to_node:
            continue
        src_text = file_texts.get(entry.rel_posix) or _read_text_safe(entry)
        if not src_text:
            continue
        src_id = file_to_node[entry.rel_posix]
        with contextlib.suppress(SyntaxError, ValueError):
            tree = ast.parse(src_text)
            for py_node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(py_node, ast.Import):
                    imported.extend(alias.name for alias in py_node.names)
                elif isinstance(py_node, ast.ImportFrom) and py_node.module:
                    imported.append(py_node.module)
                    for alias in py_node.names:
                        imported.append(f"{py_node.module}.{alias.name}")
                for cand in imported:
                    tgt_file = py_module_to_file.get(cand)
                    if tgt_file and tgt_file != entry.rel_posix and tgt_file in file_to_node:
                        add_edge(src_id, file_to_node[tgt_file], "depends_on", label="import")

    # 4. 保留 Socket 事件轻节点
    for rel, txt in file_texts.items():
        if not rel.endswith((".js", ".ts", ".tsx", ".jsx")):
            continue
        sock_src_id = file_to_node.get(rel)
        for evt_m in list(_SOCKET_ON_RE.finditer(txt)) + list(_SOCKET_EMIT_RE.finditer(txt)):
            evt = evt_m.group(1)
            if evt in _SOCKET_BUILTIN_EVENTS:
                continue
            sock_id = f"sock:{evt}"
            if not any(n["id"] == sock_id for n in nodes):
                line_no = txt.count("\n", 0, evt_m.start()) + 1
                nodes.append(
                    {
                        "id": sock_id,
                        "layer": "api",
                        "kind": "socket",
                        "label": f"ws:{evt}",
                        "module": _module_name_for_path(rel),
                        "file": rel,
                        "line": line_no,
                        "meta": {"event": evt},
                        "has_conflict": False,
                        "impact": "none",
                    }
                )
            if sock_src_id is not None:
                add_edge(sock_src_id, sock_id, "socket", label=evt)

    # 5. 跨层契约与静态资源断链冲突（客观对比，绝不加虚构节点）
    conflicts: list[dict[str, Any]] = []
    if api_calls and declared_routes:
        probe_res = ContractReconciliationProbe().run(
            root=root,
            api_routes=declared_routes,
            frontend_api_calls=api_calls,
            has_dynamic_loader=bool(routes_meta.get("has_dynamic_loader")),
        )
        for finding in probe_res.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            kind = str(finding.get("kind") or "contract_mismatch")
            severity = (
                "warning"
                if routes_meta.get("has_dynamic_loader")
                else str(finding.get("severity") or "warning")
            )
            src_file = str(finding.get("source") or finding.get("file") or "")
            node_ids = [file_to_node[src_file]] if src_file in file_to_node else []
            detail = str(
                finding.get("detail")
                or f"{finding.get('method', '')} {finding.get('path', '')}"
            ).strip()
            conflicts.append(
                {
                    "kind": kind,
                    "severity": severity,
                    "detail": detail,
                    "file": src_file,
                    "node_ids": node_ids,
                }
            )
    for asset in fe_assets:
        if isinstance(asset, dict) and asset.get("exists_in_repo") is False:
            src_file = str(asset.get("source") or "")
            href = str(asset.get("href") or "")
            node_ids = [file_to_node[src_file]] if src_file in file_to_node else []
            conflicts.append(
                {
                    "kind": "missing_static_asset",
                    "severity": "warning",
                    "detail": f"{src_file} 引用缺失静态资源 {href}",
                    "file": src_file,
                    "node_ids": node_ids,
                }
            )

    conflict_node_ids = {nid for c in conflicts for nid in (c.get("node_ids") or [])}
    for node in nodes:
        if node["id"] in conflict_node_ids:
            node["has_conflict"] = True

    graph: dict[str, Any] = {
        "schema_version": _GRAPH_SCHEMA_VERSION,
        "generated_at": iso_now(),
        "source_hash": source_hash,
        "stats": {
            "total_files": len(scan.files),
            "frontend": sum(1 for n in nodes if n["layer"] == "frontend"),
            "api": sum(1 for n in nodes if n["layer"] == "api"),
            "backend": sum(1 for n in nodes if n["layer"] == "backend"),
            "storage": 0,
            "edges": len(edges),
            "conflicts": len(conflicts),
        },
        "nodes": nodes,
        "edges": edges,
        "conflicts": conflicts,
        "module_chunks": [],
        "external_systems": _detect_external_systems(root, config),
    }
    apply_git_impact(graph, local_git)
    _sort_nodes_by_priority(graph)
    secrets = known_secret_values([config])
    safe_graph = redact_tree(graph, secrets)
    return safe_graph if isinstance(safe_graph, dict) else graph


def _fast_source_hash(root: Path, *, max_files: int = 10000) -> str:
    """兼容旧调用点的快速仓源码指纹封装，直接委托 repo_index 计算。"""
    fp, _cacheable = compute_repo_fingerprint(
        root,
        cache_version=_GRAPH_SCHEMA_VERSION,
        include_tests=False,
        extensions=_INDEXED_EXTS,
        max_non_git_files=max_files,
    )
    return fp


def load_or_build_code_graph(
    ws: Workspace,
    project_id: str,
    root: Path,
    config: dict[str, Any],
    *,
    local_git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """优先从版本化缓存读取轻量架构图并重算当前 Git 影响范围，源码变更时自动失效重建。"""
    cache_path = ws.root / ".cache" / "code_graph" / project_id / "graph.json"
    current_hash, cacheable = (
        compute_repo_fingerprint(
            root,
            cache_version=_GRAPH_SCHEMA_VERSION,
            extra_data=redact_tree(config, []),
            include_tests=False,
            extensions=_INDEXED_EXTS,
        )
        if root.is_dir()
        else ("", False)
    )
    cached = read_versioned_cache(
        cache_path,
        fingerprint=current_hash,
        cache_version=_GRAPH_SCHEMA_VERSION,
        payload_key="graph",
    )
    # 兼容直接将 graph 存于 cache 根对象的读取形式
    if cached is None and cache_path.is_file():
        with contextlib.suppress(Exception):
            from aitest.storage.files import read_json

            raw = read_json(cache_path)
            if (
                isinstance(raw, dict)
                and raw.get("schema_version") == _GRAPH_SCHEMA_VERSION
                and raw.get("source_hash") == current_hash
            ):
                cached = raw
    if cached is not None:
        apply_git_impact(cached, local_git)
        _sort_nodes_by_priority(cached)
        return cached

    graph = build_code_graph(root, config, local_git=local_git)
    if cacheable:
        from aitest.storage.files import write_json

        with contextlib.suppress(OSError):
            write_json(cache_path, graph)
            write_versioned_cache(
                cache_path.parent / f"slot-{current_hash[:12]}.json",
                fingerprint=current_hash,
                cache_version=_GRAPH_SCHEMA_VERSION,
                data=graph,
                payload_key="graph",
                evict_prefix="slot-",
                keep_slots=2,
            )
    return graph


def _dependency_search_tokens(dependencies: list[str]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in dependencies:
        dep = raw.strip()
        if not dep:
            continue
        candidates = [dep]
        if "/" in dep:
            tail = dep.rsplit("/", 1)[-1]
            head = dep.split("/", 1)[0].lstrip("@")
            candidates.extend([head, tail])
        if "." in dep:
            candidates.append(dep.split(".", 1)[0])
        if "-" in dep:
            candidates.append(dep.replace("-", "_"))
        for token in candidates:
            cleaned = token.strip().lower()
            if len(cleaned) >= 2 and cleaned not in seen:
                seen.add(cleaned)
                tokens.append(cleaned)
    return tokens


def _file_mentions_dependency(path: Path, tokens: list[str]) -> bool:
    if not tokens:
        return False
    try:
        if path.stat().st_size > 256 * 1024:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lower = text.lower()
    if path.suffix.lower() == ".py":
        with contextlib.suppress(SyntaxError, ValueError):
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0].lower()
                        full = alias.name.lower()
                        if any(tok in (top, full) for tok in tokens):
                            return True
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0].lower()
                    full = node.module.lower()
                    if any(tok in (top, full) for tok in tokens):
                        return True
    return any(
        re.search(rf"(?<![a-z0-9_]){re.escape(tok)}(?![a-z0-9_])", lower)
        for tok in tokens
    )


def related_files_for_dependencies(
    project_root: Path,
    dependencies: list[str],
    *,
    max_depth: int = 3,
    skip_dirs: frozenset[str] | None = None,
    candidate_suffixes: frozenset[str] | None = None,
    config_names: frozenset[str] | None = None,
    component_manifests: frozenset[str] | None = None,
    max_direct_hits: int = 240,
) -> list[Path]:
    """按单个依赖筛出直接引用文件；超上限时短路仅返回直接命中文件，不附加清单。"""
    del max_depth
    grouped = related_files_by_dependency(
        project_root,
        dependencies,
        skip_dirs=skip_dirs,
        candidate_suffixes=candidate_suffixes,
        config_names=config_names,
        component_manifests=component_manifests,
        per_dep_cap=max(40, max_direct_hits * 2),
    )
    selected: dict[str, Path] = {}
    manifest_set = component_manifests or frozenset(
        {
            "pyproject.toml",
            "package.json",
            "requirements.txt",
            "go.mod",
            "Cargo.toml",
            "pom.xml",
            "build.gradle",
            "composer.json",
        }
    )
    for dep in dependencies:
        hits = grouped.get(dep, [])
        direct_only = [p for p in hits if p.name not in manifest_set]
        chosen = direct_only if len(direct_only) > max_direct_hits else hits
        for path in chosen:
            selected[path.relative_to(project_root).as_posix()] = path
    return [selected[key] for key in sorted(selected)]


def related_files_by_dependency(
    project_root: Path,
    dependencies: list[str],
    *,
    max_depth: int = 3,
    skip_dirs: frozenset[str] | None = None,
    candidate_suffixes: frozenset[str] | None = None,
    config_names: frozenset[str] | None = None,
    component_manifests: frozenset[str] | None = None,
    per_dep_cap: int = 40,
) -> dict[str, list[Path]]:
    """单遍扫描仓库按依赖分组收集直接引用文件，避免重复遍历与重建架构图。"""
    del max_depth
    result: dict[str, list[Path]] = {dep: [] for dep in dependencies}
    if not dependencies or not project_root.is_dir():
        return result

    skip = {s.lower() for s in (skip_dirs if skip_dirs is not None else DEFAULT_SKIP_DIRS)}
    suffixes = candidate_suffixes if candidate_suffixes is not None else _INDEXED_EXTS
    cfg_names = {c.lower() for c in (config_names or frozenset())}
    manifests = component_manifests or frozenset(
        {
            "pyproject.toml",
            "package.json",
            "requirements.txt",
            "go.mod",
            "Cargo.toml",
            "pom.xml",
            "build.gradle",
            "composer.json",
        }
    )

    dep_tokens: dict[str, list[str]] = {}
    dep_patterns: dict[str, re.Pattern[str]] = {}
    for dep in dependencies:
        if dep in dep_tokens:
            continue
        toks = _dependency_search_tokens([dep])
        if not toks:
            continue
        dep_tokens[dep] = toks
        ordered = sorted(toks, key=len, reverse=True)
        dep_patterns[dep] = re.compile(
            "|".join(rf"(?<![a-z0-9_]){re.escape(tok)}(?![a-z0-9_])" for tok in ordered)
        )

    active_deps = list(dep_patterns.keys())
    if not active_deps:
        return result

    def _compile_coarse(deps_list: list[str]) -> re.Pattern[str] | None:
        all_toks = sorted(
            {tok for dep in deps_list for tok in dep_tokens[dep]},
            key=len,
            reverse=True,
        )
        if not all_toks:
            return None
        return re.compile(
            "|".join(rf"(?<![a-z0-9_]){re.escape(tok)}(?![a-z0-9_])" for tok in all_toks)
        )

    coarse_pattern = _compile_coarse(active_deps)
    hits: dict[str, dict[str, Path]] = {dep: {} for dep in active_deps}
    scan = scan_repo(project_root, extra_skip_dirs=skip - DEFAULT_SKIP_DIRS, include_tests=True)
    manifest_files = {
        rel: path for rel, path in scan.manifests.items() if Path(rel).name in manifests
    }

    for entry in scan.files:
        lname = entry.path.name.lower()
        if entry.path.suffix.lower() not in suffixes and lname not in cfg_names:
            continue
        if not active_deps or coarse_pattern is None or entry.size > 256 * 1024:
            continue
        try:
            lower = entry.path.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if coarse_pattern.search(lower) is None:
            continue
        saturated: list[str] = []
        for dep in active_deps:
            if dep_patterns[dep].search(lower) is not None:
                hits[dep][entry.rel_posix] = entry.path
                if len(hits[dep]) >= per_dep_cap:
                    saturated.append(dep)
        if saturated:
            saturated_set = set(saturated)
            active_deps = [dep for dep in active_deps if dep not in saturated_set]
            coarse_pattern = _compile_coarse(active_deps)

    for dep in dependencies:
        dep_hits = hits.get(dep)
        if not dep_hits:
            result[dep] = []
            continue
        if len(dep_hits) >= per_dep_cap:
            result[dep] = [dep_hits[key] for key in sorted(dep_hits)]
            continue
        merged = dict(dep_hits)
        for mrel in sorted(manifest_files):
            if len(merged) >= max(len(dep_hits), per_dep_cap - 1):
                break
            merged.setdefault(mrel, manifest_files[mrel])
        result[dep] = [merged[key] for key in sorted(merged)]
    return result


__all__ = [
    "apply_git_impact",
    "build_code_graph",
    "is_test_relpath",
    "load_or_build_code_graph",
    "related_files_by_dependency",
    "related_files_for_dependencies",
]
