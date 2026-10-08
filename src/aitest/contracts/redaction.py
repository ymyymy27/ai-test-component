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
from collections.abc import Callable, Mapping
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


def _is_redaction_marker(value: str) -> bool:
    """值位是否已经是脱敏标记；JSON/带引号的值（``"[REDACTED]"``）同样幂等。"""
    return value.strip("\"'") == _REDACTED


def scrub_secret_text(text: str) -> tuple[str, bool]:
    """替换字符串内已知凭据形态；返回（脱敏文本，是否发生替换）。

    对已经脱敏的文本幂等：值位为 ``[REDACTED]``（含带引号的 JSON 值
    ``"[REDACTED]"``）时不再计为命中，避免重复过滤把安全材料误判为不洁，
    也避免把结构化材料二次改写成非法 JSON（A-09 落盘底线复用本原语）。
    """
    redacted = text
    changed = False
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.groups >= 1:

            def _replace(match: re.Match[str]) -> str:
                nonlocal changed
                if _is_redaction_marker(match.group(2)):
                    return match.group(0)
                changed = True
                return match.group(1) + _REDACTED

            redacted = pattern.sub(_replace, redacted)
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


def safeguard_projector(
    projector: Callable[[Mapping[str, object]], Mapping[str, object]],
) -> Callable[[Mapping[str, object]], Mapping[str, object]]:
    """给任意自定义投影器套上不可绕过的统一安全底线（A-09）。

    自定义投影器只允许做**再组织/裁剪**，无权决定凭据是否出站：其输出
    （或异常时的原始输入）必须再经 :func:`redact_structure` 递归脱敏。
    投影器返回非 Mapping（无法承载结构化结果）或直接抛错时，回退为对
    原始值脱敏，绝不把异常或非结构对象透传给协议出口。
    """

    def _safeguarded(value: Mapping[str, object]) -> Mapping[str, object]:
        try:
            projected: Any = projector(value)
        except Exception:
            projected = value
        if not isinstance(projected, Mapping):
            projected = value
        redacted, _changed = redact_structure(projected)
        if isinstance(redacted, Mapping):
            return redacted
        # 极端情况下脱敏结果退化为标量（自定义投影器返回了奇怪的 Mapping
        # 子类），包一层固定键，保证协议出口结构仍为对象且无原文泄漏。
        return {"value": redacted}

    return _safeguarded


__all__ = [
    "SENSITIVE_KEYS",
    "redact_json_text",
    "redact_structure",
    "safeguard_projector",
    "scrub_secret_text",
]
