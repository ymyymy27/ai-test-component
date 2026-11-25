"""盲写预期——物理隔离后端实现源码与契约视图。

计划生成第 1 步仅向模型暴露需求、文档、OpenAPI 路由与前端调用契约，
物理剥离后端实现代码，避免模型先看实现后被错误代码“带偏预期”。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_FRONTEND_EXTS = frozenset({".html", ".vue", ".jsx", ".tsx", ".css", ".scss", ".less"})
_FRONTEND_SCRIPT_EXTS = frozenset({".js", ".ts", ".mjs"})
_FRONTEND_DIR_HINTS = frozenset(
    {"frontend", "front", "web", "client", "ui", "static", "public", "src"}
)
_BACKEND_DIR_HINTS = frozenset(
    {"backend", "server", "api", "app", "routes", "routers", "controllers", "services", "models"}
)
_EXCERPT_HEADER_RE = re.compile(r"^---\s+(.+?)\s+---\n", re.MULTILINE)


def _is_frontend_excerpt_path(rel_path: str) -> bool:
    """判断源码片段路径是否属于纯粹的前端/展示契约文件，严防后端实现源码混入。"""
    normalized = rel_path.strip().replace("\\", "/").lstrip("./")
    if not normalized:
        return False
    suffix = Path(normalized).suffix.lower()
    if suffix == ".py":
        return False
    parts = [part.lower() for part in normalized.split("/") if part]
    if any(part in _BACKEND_DIR_HINTS for part in parts[:-1]):
        return suffix == ".html"
    if suffix in _FRONTEND_EXTS:
        return True
    if suffix in _FRONTEND_SCRIPT_EXTS:
        return len(parts) == 1 or any(part in _FRONTEND_DIR_HINTS for part in parts[:-1])
    return False


def _filter_frontend_excerpts(raw_excerpts: object) -> list[str]:
    if not isinstance(raw_excerpts, list):
        return []
    kept: list[str] = []
    for item in raw_excerpts:
        if not isinstance(item, str):
            continue
        match = _EXCERPT_HEADER_RE.match(item)
        if not match:
            continue
        rel_path = match.group(1).strip()
        if _is_frontend_excerpt_path(rel_path):
            kept.append(item)
    return kept


def build_contract_only_context(context: dict[str, Any]) -> dict[str, Any]:
    """从完整项目上下文剥离后端源码实现，构造第 1 步盲写预期专用的契约视图。"""
    contract_view: dict[str, Any] = {
        "files": list(context.get("files") or []),
        "readme": str(context.get("readme") or ""),
        "api_routes": list(context.get("api_routes") or []),
        "frontend_api_calls": list(context.get("frontend_api_calls") or []),
        "frontend_assets": list(context.get("frontend_assets") or []),
        "frontend_excerpts": _filter_frontend_excerpts(context.get("source_excerpts")),
        "services": context.get("services") or {},
        "framework": list(context.get("framework") or []),
        "has_frontend": bool(context.get("has_frontend")),
        "has_database": bool(context.get("has_database")),
    }
    return contract_view


def contract_context_digest(contract_context: dict[str, Any]) -> str:
    """计算契约视图的稳定摘要，供盲写预期与计划缓存失效校验使用。"""
    encoded = json.dumps(contract_context, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
