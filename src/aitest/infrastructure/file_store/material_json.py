"""Strict JSON and bounded bytes for immutable tree materials."""

from pathlib import Path
from typing import Any

from aitest.domain.json_material import decode_json


def decode_material(raw: bytes | str) -> Any:
    return decode_json(raw)


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
