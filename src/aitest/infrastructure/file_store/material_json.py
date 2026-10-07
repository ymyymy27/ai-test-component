"""Strict JSON and bounded bytes for immutable tree materials."""

import json
import math
from pathlib import Path
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


def decode_material(raw: bytes) -> Any:
    try:
        return json.loads(
            raw, object_pairs_hook=_unique, parse_constant=_nonfinite, parse_float=_finite_decimal
        )
    except (RecursionError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("immutable material is not readable JSON") from error


def read_material_bytes(path: Path, maximum: int) -> bytes:
    # Callers verify paths before opening. Bound the actual read, including a
    # file that grew after a size probe; the extra byte only detects overflow.
    if type(maximum) is not int or maximum < 1:
        raise ValueError("immutable material byte limit must be a positive integer")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("immutable material exceeds its byte limit")
    return raw
