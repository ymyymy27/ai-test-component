"""仓库级 .aitest.json 共享配置、多环境档案解析与项目错题本（Conventions）模块。

两层配置合并顺序：
1. 仓库根目录下的非敏感共享文件 `.aitest.json`（强制剔除一切凭据与疑似密钥）；
2. 用户数据工作空间下的个人 `project.config.json`（覆盖同名标量字段，深度合并字典，
   错题本去重合并）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from aitest.security import is_secret_field, looks_like_secret_value

SHARED_CONFIG_FILENAME = ".aitest.json"


def _strip_secrets_recursive(value: Any) -> Any:
    """递归过滤共享或环境配置中的所有敏感键名与疑似密钥正文。"""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if is_secret_field(key_str):
                continue
            stripped = _strip_secrets_recursive(item)
            if stripped is None and isinstance(item, str):
                continue
            cleaned[key_str] = stripped
        return cleaned
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            stripped = _strip_secrets_recursive(item)
            if stripped is None and isinstance(item, str):
                continue
            result.append(stripped)
        return result
    if isinstance(value, str) and looks_like_secret_value(value):
        return None
    return value


def _merge_conventions(
    shared: object, local: object
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in (shared, local):
        raw_list = (
            source.get("items")
            if isinstance(source, dict)
            else source
            if isinstance(source, list)
            else []
        )
        if not isinstance(raw_list, list):
            continue
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            pattern = str(entry.get("pattern") or "").strip()
            reason = str(entry.get("reason") or "").strip()
            if not pattern:
                continue
            cid = str(entry.get("id") or "").strip() or (
                "conv_" + hashlib.sha256(pattern.encode("utf-8")).hexdigest()[:10]
            )
            key = f"{cid}::{pattern}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "id": cid,
                    "pattern": pattern,
                    "reason": reason or "项目正常约定",
                    "match_type": (
                        "regex" if entry.get("match_type") == "regex" else "contains"
                    ),
                    "scope": str(entry.get("scope") or "all").strip().lower() or "all",
                }
            )
    return merged


def load_shared_repo_config(root: Path) -> dict[str, Any]:
    """读取并强力脱敏仓库根目录 `.aitest.json`，不存在或格式异常时优雅返回空字典。"""
    path = root / SHARED_CONFIG_FILENAME
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > 1024 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    sanitized = _strip_secrets_recursive(data)
    return sanitized if isinstance(sanitized, dict) else {}


def merge_project_configs(
    shared: dict[str, Any], local: dict[str, Any]
) -> dict[str, Any]:
    """两层合并仓库共享配置与个人项目配置：本地字段优先，字典深度合并，错题本并集去重。"""
    if not shared:
        return dict(local)
    if not local:
        return dict(shared)

    merged: dict[str, Any] = dict(shared)
    for key, value in local.items():
        if key == "conventions":
            continue
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = merge_project_configs(existing, value)
        else:
            merged[key] = value
    if "conventions" in shared or "conventions" in local:
        merged["conventions"] = _merge_conventions(
            shared.get("conventions"), local.get("conventions")
        )
    return merged


def _project_root_from_workspace(ws: Any, project_id: str) -> Path | None:
    path = ws.project_path(project_id)
    if not path.is_file():
        return None
    with contextlib.suppress(OSError, json.JSONDecodeError):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            root_str = str(raw.get("root") or "").strip()
            if root_str:
                return Path(root_str)
    return None


def load_merged_project_config(
    ws: Any, project_id: str, local_config: dict[str, Any]
) -> dict[str, Any]:
    """给定本地配置与项目 ID，在仓库存在 `.aitest.json` 时返回两层合并后的最终配置。"""
    root = _project_root_from_workspace(ws, project_id)
    if root is None:
        return local_config
    shared = load_shared_repo_config(root)
    if not shared:
        return local_config
    return merge_project_configs(shared, local_config)


def resolve_environment_profile(
    config: dict[str, Any], requested_env: str = ""
) -> dict[str, Any]:
    """解析多环境档案（如 local/staging），返回无密钥泄漏的非敏感运行配置。"""
    raw_envs = config.get("environments")
    if not isinstance(raw_envs, dict):
        return {
            "name": requested_env.strip(),
            "found": not requested_env.strip(),
            "base_url": "",
            "env": {},
            "allowed_hosts": [],
            "services": {},
            "roles": {},
            "available": [],
        }

    profiles_raw = raw_envs.get("profiles")
    profiles: dict[str, dict[str, Any]] = {}
    if isinstance(profiles_raw, dict):
        profiles = {
            str(k): v for k, v in profiles_raw.items() if isinstance(v, dict)
        }
    else:
        for k, v in raw_envs.items():
            if k not in {"active", "profiles"} and isinstance(v, dict):
                profiles[str(k)] = v

    active = str(raw_envs.get("active") or "").strip()
    target = requested_env.strip() or active
    if not target:
        return {
            "name": "",
            "found": True,
            "base_url": "",
            "env": {},
            "allowed_hosts": [],
            "services": {},
            "roles": {},
            "available": sorted(profiles.keys()),
        }

    profile = profiles.get(target)
    if profile is None:
        return {
            "name": target,
            "found": False,
            "base_url": "",
            "env": {},
            "allowed_hosts": [],
            "services": {},
            "roles": {},
            "available": sorted(profiles.keys()),
        }

    safe_profile = _strip_secrets_recursive(profile)
    profile_dict = safe_profile if isinstance(safe_profile, dict) else {}
    raw_env = profile_dict.get("env")
    safe_env = (
        {
            str(k): str(v)
            for k, v in raw_env.items()
            if isinstance(k, str) and isinstance(v, (str, int, float)) and not is_secret_field(k)
        }
        if isinstance(raw_env, dict)
        else {}
    )
    allowed_hosts = [
        str(item).strip()
        for item in (profile_dict.get("allowed_hosts") or [])
        if isinstance(item, str) and item.strip()
    ]
    services = (
        dict(profile_dict["services"])
        if isinstance(profile_dict.get("services"), dict)
        else {}
    )
    roles = (
        dict(profile_dict["roles"])
        if isinstance(profile_dict.get("roles"), dict)
        else {}
    )
    return {
        "name": target,
        "found": True,
        "base_url": str(profile_dict.get("base_url") or "").strip().rstrip("/"),
        "env": safe_env,
        "allowed_hosts": allowed_hosts,
        "services": services,
        "roles": roles,
        "available": sorted(profiles.keys()),
    }


def extract_conventions(config: dict[str, Any]) -> list[dict[str, Any]]:
    """从配置中提取标准化的项目错题本规则列表。"""
    return _merge_conventions([], config.get("conventions"))


def match_convention(
    text: str,
    conventions: list[dict[str, Any]] | None,
    *,
    scope: str = "",
) -> dict[str, Any] | None:
    """判断给定告警或失败文本是否命中项目错题本中登记的正常约定。"""
    if not text or not conventions:
        return None
    for entry in conventions:
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("pattern") or "").strip()
        if not pattern:
            continue
        entry_scope = str(entry.get("scope") or "all").strip().lower()
        if scope and entry_scope not in {"all", scope}:
            continue
        if entry.get("match_type") == "regex":
            with contextlib.suppress(re.error):
                if re.search(pattern, text, re.IGNORECASE):
                    return entry
        elif pattern.lower() in text.lower():
            return entry
    return None
