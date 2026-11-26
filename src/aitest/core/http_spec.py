"""HTTP 验证载荷的确定性归一（extract 形态与路径前缀）。"""

from __future__ import annotations

from typing import Any

_BODY_PREFIX = "body."


def strip_body_prefix(path: str) -> str:
    """extract 的目标是响应体本身，``body.`` 前缀需要去掉。"""
    text = path.strip()
    if text.startswith(_BODY_PREFIX):
        return text[len(_BODY_PREFIX) :]
    if text == "body":
        return ""
    return text


def normalize_extract(raw: Any) -> dict[str, str]:
    """把模型常见的 extract 写法统一成 ``{变量名: 响应体路径}``。

    兼容：
    - 标准映射 ``{"token": "token"}``；
    - 单变量对象 ``{"var": "token", "path": "body.token"}`` / ``{"name": ..., "path": ...}``；
    - 路径带 ``body.`` 前缀。
    """
    if not isinstance(raw, dict):
        return {}
    keys = set(raw)
    if keys in ({"var", "path"}, {"name", "path"}):
        name = raw.get("var", raw.get("name"))
        path = raw.get("path")
        if isinstance(name, str) and isinstance(path, str):
            variable = name.strip()
            target = strip_body_prefix(path)
            if variable and target:
                return {variable: target}
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        variable = key.strip()
        target = strip_body_prefix(value)
        if variable and target:
            result[variable] = target
    return result
