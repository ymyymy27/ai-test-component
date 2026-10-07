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


def decode_json(raw: bytes | str) -> Any:
    try:
        return json.loads(
            raw, object_pairs_hook=_unique, parse_constant=_nonfinite, parse_float=_finite_decimal
        )
    except (RecursionError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("immutable material is not readable JSON") from error
