"""Pure strict JSON decoding shared by transport and immutable material readers."""

import json
import math
from typing import Any


def _unique(fields: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in fields:
        if name in result:
            raise ValueError("immutable material has duplicate JSON fields")
        result[name] = value
    return result


def _nonfinite(value: str) -> Any:
    raise ValueError("immutable material contains a non-JSON number")


def _finite_decimal(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("material JSON number exceeds the finite representation")
    return parsed


def require_json_text(value: str) -> None:
    """Require Unicode characters that preserve their exact strict UTF-8 value."""
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValueError("JSON string is not representable as strict UTF-8") from error


def decode_json(raw: bytes | str) -> Any:
    try:
        # json.loads(bytes) otherwise auto-detects UTF-16/32, violating our wire/storage contract.
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        result = json.loads(
            text, object_pairs_hook=_unique, parse_constant=_nonfinite, parse_float=_finite_decimal
        )
        pending = [result]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                require_json_text(value)
            elif isinstance(value, dict):
                for key, item in value.items():
                    require_json_text(key)
                    pending.append(item)
            elif isinstance(value, list):
                pending.extend(value)
        return result
    except (RecursionError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("immutable material is not readable UTF-8 JSON") from error
