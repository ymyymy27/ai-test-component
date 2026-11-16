"""唯一的规范 JSON 与摘要实现。

规则固定：键排序、紧凑分隔符、`ensure_ascii=False`、`allow_nan=False`，拒绝
非 JSON 类型。所有“内容身份/摘要”都应调用这里；字节摘要、存储分片键与流式
哈希不属于 JSON 族，继续各自实现。
"""

from __future__ import annotations

import hashlib
import json


def canonical_json_bytes(value: object) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"value is not canonical JSON: {error}") from error
    return text.encode("utf-8")


def canonical_json_text(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def canonical_hexdigest(value: object) -> str:
    """不带前缀的摘要，供自身带命名空间的标识（如 ``wizard-<hex>``）使用。"""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonical_digest(value: object) -> str:
    return "sha256:" + canonical_hexdigest(value)


__all__ = [
    "canonical_digest",
    "canonical_hexdigest",
    "canonical_json_bytes",
    "canonical_json_text",
]
