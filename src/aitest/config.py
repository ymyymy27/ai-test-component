"""User-level configuration and secret resolution. Workspace never stores secrets."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from aitest.security import secret_values_in

MODEL_KEY_ENV = "AITEST_MODEL_KEY"
GITHUB_KEY_ENV = "AITEST_GITHUB_TOKEN"

KeySource = Literal["env", "user_config", "none"]


def user_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "aitest" / "config.json"
    return Path.home() / ".config" / "aitest" / "config.json"


def load_user_config() -> dict[str, Any]:
    path = user_config_path()
    if not path.is_file():
        return {}
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _config_value(section: str, key: str) -> str:
    section_value = load_user_config().get(section)
    if isinstance(section_value, dict):
        value = section_value.get(key)
        if isinstance(value, str):
            return value
    return ""


def model_key() -> tuple[str, KeySource]:
    env_value = os.environ.get(MODEL_KEY_ENV, "").strip()
    if env_value:
        return env_value, "env"
    value = _config_value("model", "api_key").strip()
    if value:
        return value, "user_config"
    return "", "none"


def model_key_source() -> KeySource:
    return model_key()[1]


def github_token() -> tuple[str, KeySource]:
    env_value = os.environ.get(GITHUB_KEY_ENV, "").strip()
    if env_value:
        return env_value, "env"
    value = _config_value("github", "token").strip()
    if value:
        return value, "user_config"
    return "", "none"


def known_secret_values(extra: Iterable[Any] = ()) -> list[str]:
    """Collect credential bodies that must never reach a persisted file.

    Sources: the resolved model key, the optional GitHub token, the user
    configuration, and any extra config trees the caller passes in. The result
    is de-duplicated and never logged.
    """
    values: list[str] = []
    for value, _source in (model_key(), github_token()):
        if value:
            values.append(value)
    values.extend(secret_values_in(load_user_config()))
    for item in extra:
        values.extend(secret_values_in(item))
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result

