"""Resolve model settings from user and project configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def model_settings(ws: Workspace, project_id: str) -> dict[str, Any]:
    project = load_project_config(ws, project_id).get("model")
    user = load_user_config().get("model")
    merged: dict[str, Any] = {}
    for source in (user, project):
        if isinstance(source, dict):
            merged.update(source)
    return {
        "base_url": str(merged.get("base_url") or DEFAULT_BASE_URL),
        "name": str(merged.get("name") or DEFAULT_MODEL),
        "timeout_seconds": _timeout(merged.get("timeout_seconds", 120)),
        "max_token_budget": _positive_int(merged.get("max_token_budget")),
    }


def build_client(ws: Workspace, project_id: str) -> ModelClient | None:
    key, _source = model_key()
    if not key:
        return None
    config = load_project_config(ws, project_id)
    settings = model_settings(ws, project_id)
    budget = settings.get("max_token_budget")
    return ModelClient(
        base_url=str(settings["base_url"]),
        model=str(settings["name"]),
        api_key=key,
        timeout_seconds=float(settings["timeout_seconds"]),
        secrets=known_secret_values([config]),
        max_token_budget=budget if isinstance(budget, int) else None,
    )


def build_user_client() -> ModelClient | None:
    """Build a client from user configuration before any project exists."""
    key, _source = model_key()
    if not key:
        return None
    user_config = load_user_config()
    value = user_config.get("model")
    user = value if isinstance(value, dict) else {}
    return ModelClient(
        base_url=str(user.get("base_url") or DEFAULT_BASE_URL),
        model=str(user.get("name") or DEFAULT_MODEL),
        api_key=key,
        timeout_seconds=_timeout(user.get("timeout_seconds", 120)),
        secrets=known_secret_values([user_config]),
        max_token_budget=_positive_int(user.get("max_token_budget")),
    )
