"""Resolve model settings from user and project configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from aitest.adapters.model import ModelClient
from aitest.config import known_secret_values, load_user_config, model_key
from aitest.core.shared_config import load_merged_project_config
from aitest.errors import AiTestError
from aitest.storage.files import check_schema_version
from aitest.storage.workspace import Workspace

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


def project_config_path(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "project.config.json"


def load_project_config(ws: Workspace, project_id: str) -> dict[str, Any]:
    path = project_config_path(ws, project_id)
    local: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AiTestError(
                "CONFIG_INVALID",
                f"项目配置无法读取：{path.name}",
                "修复 project.config.json 的 JSON 后重试",
            ) from exc
        if not isinstance(data, dict):
            raise AiTestError(
                "CONFIG_INVALID",
                f"项目配置不是对象：{path.name}",
                "把 project.config.json 改成 JSON 对象",
            )
        check_schema_version(data, path)
        local = data
    return load_merged_project_config(ws, project_id, local)


def _timeout(value: object) -> float:
    if isinstance(value, bool):
        return 120.0
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 120.0
    return seconds if seconds > 0 else 120.0


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _ai_settings(merged_model: dict[str, Any]) -> dict[str, Any]:
    """解析内部 AI 治理配置（model.ai.mode / model_tier / 缓存 / 单任务预算）。"""
    raw_ai = merged_model.get("ai")
    ai = raw_ai if isinstance(raw_ai, dict) else {}
    env_mode = os.environ.get("AITEST_AI_MODE", "").strip().lower()
    raw_mode = env_mode or str(ai.get("mode") or "assist").strip().lower()
    mode: Literal["assist", "off"] = "off" if raw_mode == "off" else "assist"
    raw_tier = str(ai.get("model_tier") or "auto").strip().lower()
    model_tier = raw_tier if raw_tier in {"auto", "fast", "strong"} else "auto"
    cache_raw = ai.get("cache_enabled", True)
    cache_enabled = bool(cache_raw) if isinstance(cache_raw, bool) else True
    return {
        "mode": mode,
        "model_tier": model_tier,
        "fast_model": str(ai.get("fast_model") or "").strip(),
        "strong_model": str(ai.get("strong_model") or "").strip(),
        "cache_enabled": cache_enabled,
        "task_token_budget": _positive_int(ai.get("task_token_budget")),
    }


def model_settings(ws: Workspace, project_id: str) -> dict[str, Any]:
    project = load_project_config(ws, project_id).get("model")
    user = load_user_config().get("model")
    merged: dict[str, Any] = {}
    for source in (user, project):
        if isinstance(source, dict):
            nested_ai = merged.get("ai")
            merged.update(source)
            if isinstance(nested_ai, dict) and isinstance(source.get("ai"), dict):
                merged["ai"] = {**nested_ai, **source["ai"]}
    ai = _ai_settings(merged)
    return {
        "base_url": str(merged.get("base_url") or DEFAULT_BASE_URL),
        "name": str(merged.get("name") or DEFAULT_MODEL),
        "timeout_seconds": _timeout(merged.get("timeout_seconds", 120)),
        "max_token_budget": _positive_int(merged.get("max_token_budget")),
        "ai": ai,
    }


def build_client(ws: Workspace, project_id: str) -> ModelClient | None:
    key, _source = model_key()
    if not key:
        return None
    config = load_project_config(ws, project_id)
    settings = model_settings(ws, project_id)
    ai = settings.get("ai", {})
    if isinstance(ai, dict) and ai.get("mode") == "off":
        return None
    budget = settings.get("max_token_budget")
    task_budget = ai.get("task_token_budget") if isinstance(ai, dict) else None
    cache_enabled = bool(ai.get("cache_enabled", True)) if isinstance(ai, dict) else True
    cache_dir = (ws.project_dir(project_id) / "cache" / "model") if cache_enabled else None
    return ModelClient(
        base_url=str(settings["base_url"]),
        model=str(settings["name"]),
        api_key=key,
        timeout_seconds=float(settings["timeout_seconds"]),
        secrets=known_secret_values([config]),
        max_token_budget=budget if isinstance(budget, int) else None,
        cache_dir=cache_dir,
        enable_cache=cache_enabled,
        ai_mode="assist",
        model_tier=str(ai.get("model_tier") or "auto") if isinstance(ai, dict) else "auto",
        fast_model=str(ai.get("fast_model") or "") if isinstance(ai, dict) else None,
        strong_model=str(ai.get("strong_model") or "") if isinstance(ai, dict) else None,
        task_token_budget=task_budget if isinstance(task_budget, int) else None,
    )


def build_user_client() -> ModelClient | None:
    """Build a client from user configuration before any project exists."""
    key, _source = model_key()
    if not key:
        return None
    user_config = load_user_config()
    value = user_config.get("model")
    user = value if isinstance(value, dict) else {}
    ai = _ai_settings(user)
    if ai.get("mode") == "off":
        return None
    return ModelClient(
        base_url=str(user.get("base_url") or DEFAULT_BASE_URL),
        model=str(user.get("name") or DEFAULT_MODEL),
        api_key=key,
        timeout_seconds=_timeout(user.get("timeout_seconds", 120)),
        secrets=known_secret_values([user_config]),
        max_token_budget=_positive_int(user.get("max_token_budget")),
        enable_cache=bool(ai.get("cache_enabled", True)),
        ai_mode="assist",
        model_tier=str(ai.get("model_tier") or "auto"),
        fast_model=str(ai.get("fast_model") or "") or None,
        strong_model=str(ai.get("strong_model") or "") or None,
        task_token_budget=ai.get("task_token_budget"),
    )
