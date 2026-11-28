"""Small assertion helpers shared by HTTP and command verification."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

MISSING: object = object()


def resolve_path(value: object, path: str) -> object:
    """Resolve a dotted path (``data.id`` or ``items.0.name``) inside JSON-like data."""
    if path == "":
        return value
    current: object = value
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return MISSING
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            try:
                index = int(part)
            except ValueError:
                return MISSING
            try:
                current = current[index]
            except IndexError:
                return MISSING
        else:
            return MISSING
    return current


def _numeric_pair(value: object, expected: object) -> tuple[float, float] | None:
    if isinstance(value, bool) or isinstance(expected, bool):
        return None
    if isinstance(value, (int, float)) and isinstance(expected, (int, float)):
        return float(value), float(expected)
    return None


def _matches_type(value: object, expected: object) -> bool:
    if not isinstance(expected, str):
        return False
    kind = expected.strip().lower()
    if kind in {"null", "none"}:
        return value is None
    if kind in {"str", "string"}:
        return isinstance(value, str)
    if kind in {"bool", "boolean"}:
        return isinstance(value, bool)
    if kind in {"int", "integer"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind in {"float", "double"}:
        return isinstance(value, float) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind in {"list", "array"}:
        return isinstance(value, list)
    if kind in {"dict", "object", "mapping"}:
        return isinstance(value, Mapping)
    raise ValueError(f"unsupported type_is target: {expected}")


def evaluate(value: object, operator: str, expected: object) -> bool:
    """评估单条断言结果，支持等值、比较、集合成员、正则、长度及类型校验。"""
    if operator == "not_exists":
        return value is MISSING or value is None
    if operator == "exists":
        return value is not MISSING and value is not None
    if value is MISSING:
        return False
    if operator == "equals":
        return value == expected
    if operator == "not_equals":
        return value != expected
    if operator == "contains":
        return _contains(value, expected)
    if operator == "not_contains":
        return not _contains(value, expected)
    if operator in {"gt", "gte", "lt", "lte"}:
        pair = _numeric_pair(value, expected)
        if pair is None:
            return False
        left, right = pair
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "lt":
            return left < right
        return left <= right
    if operator in {"in", "not_in"}:
        if isinstance(expected, str):
            matched = isinstance(value, str) and value in expected
        elif (
            isinstance(expected, Sequence) and not isinstance(expected, (bytes, bytearray))
        ) or isinstance(expected, Mapping):
            try:
                matched = value in expected
            except TypeError:
                matched = False
        else:
            return False
        return matched if operator == "in" else not matched
    if operator == "regex":
        if not isinstance(expected, str) or not isinstance(value, str):
            return False
        try:
            return bool(re.search(expected, value))
        except re.error as exc:
            raise ValueError(f"invalid regex pattern: {exc}") from exc
    if operator in {"length_equals", "length_gt", "length_gte", "length_lt", "length_lte"}:
        if not isinstance(value, (str, bytes, bytearray, Sequence, Mapping)):
            return False
        if isinstance(expected, bool) or not isinstance(expected, int):
            return False
        actual_len = len(value)
        if operator == "length_equals":
            return actual_len == expected
        if operator == "length_gt":
            return actual_len > expected
        if operator == "length_gte":
            return actual_len >= expected
        if operator == "length_lt":
            return actual_len < expected
        return actual_len <= expected
    if operator == "type_is":
        return _matches_type(value, expected)
    raise ValueError(f"unsupported assertion operator: {operator}")


def _contains(value: object, expected: object) -> bool:
    if isinstance(value, str):
        return isinstance(expected, str) and expected in value
    if isinstance(value, (bytes, bytearray)):
        if isinstance(expected, bytes):
            return expected in value
        return isinstance(expected, str) and expected.encode() in value
    if isinstance(value, Mapping):
        try:
            return expected in value
        except TypeError:
            return False
    if isinstance(value, (list, tuple, set, frozenset)):
        try:
            return expected in value
        except TypeError:
            return False
    return False
