"""纯凭据脱敏原语：无业务依赖，供投影、协议出口与出站适配共用。

放在 ``contracts`` 层是因为：

- :mod:`aitest.interfaces` 不得导入 ``aitest.infrastructure``，但协议出口
  的成功结果与异常消息必须无条件脱敏；
- 基础设施侧的投影器与 HTTP 适配同样调用这一份实现，保证全链路口径一致。

本模块只做文本/结构替换，不读取凭据存储、不判断业务状态。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_REDACTED = "[REDACTED]"

#: 结构化数据中键名命中即整体替换其值的敏感键（大小写/中横线/下划线不敏感）。
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "access_token",
        "access-token",
        "refreshtoken",
        "refresh_token",
        "refresh-token",
        "secret",
        "client_secret",
        "clientsecret",
        "api_key",
        "apikey",
        "api-key",
        "authorization",
    }
)

#: 字符串值内已知凭据形态：键值、Bearer/Basic、GitHub/OpenAI 形态、JWT。
_SECRET_VALUE_PATTERNS = (
    re.compile(
        r"(?i)(\b(?:password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|"
        r"secret|client[_-]?secret|api[_-]?key|apikey)\b\s*[:=]\s*)"
        r'(\"[^\"]*\"|\'[^\']*\'|[^\s,;]+)'
    ),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9\-._~+/]+=*)"),
    re.compile(r"(?i)(authorization\s*:\s*basic\s+)([A-Za-z0-9+/]+=*)"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)


def scrub_secret_text(text: str) -> tuple[str, bool]:
    """替换字符串内已知凭据形态；返回（脱敏文本，是否发生替换）。"""
    redacted = text
    changed = False
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.groups >= 1:
            redacted, count = pattern.subn(
                lambda match: match.group(1) + _REDACTED, redacted
            )
        else:
            redacted, count = pattern.subn(_REDACTED, redacted)
        changed = changed or count > 0
    return redacted, changed


def redact_structure(value: Any) -> tuple[Any, bool]:
    """递归脱敏嵌套 Mapping/dict/list/tuple/str；返回（脱敏值，是否命中）。

    键名命中敏感集合时整体替换值；其余字符串值按已知凭据形态替换。
    """
    if isinstance(value, Mapping):
        changed_any = False
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            key_aliases = {str(key).lower(), normalized}
            if key_aliases & SENSITIVE_KEYS:
                result[str(key)] = _REDACTED
                changed_any = True
                continue
            redacted, changed = redact_structure(item)
            result[str(key)] = redacted
            changed_any = changed_any or changed
        return result, changed_any
    if isinstance(value, dict):
        return redact_structure(dict(value))
    if isinstance(value, (list, tuple)):
        changed_any = False
        items: list[Any] = []
        for item in value:
            redacted, changed = redact_structure(item)
            items.append(redacted)
            changed_any = changed_any or changed
        return items, changed_any
    if isinstance(value, str):
        return scrub_secret_text(value)
    return value, False


def redact_json_text(text: str) -> tuple[str, bool] | None:
    """对 JSON 对象/数组文本做嵌套脱敏；非结构化文本返回 None。

    可解析但未命中凭据时返回 ``(原文, False)``。
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    redacted, changed = redact_structure(parsed)
    if not changed:
        return text, False
    return json.dumps(redacted, ensure_ascii=False), True


__all__ = [
    "SENSITIVE_KEYS",
    "redact_json_text",
    "redact_structure",
    "scrub_secret_text",
]
