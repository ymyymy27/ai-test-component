"""Small assertion helpers shared by HTTP and command verification."""

from __future__ import annotations

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


def evaluate(value: object, operator: str, expected: object) -> bool:
    """Evaluate one assertion against an already resolved value."""
    if value is MISSING:
        return False
    if operator == "equals":
        return value == expected
    if operator == "contains":
        return _contains(value, expected)
    if operator == "exists":
        return value is not None
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
