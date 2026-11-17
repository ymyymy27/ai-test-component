"""Redaction, path checks and generated-script screening."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_DANGEROUS = (
    r"\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)\b",
    r"\brm\s+-[rR]\b[^\n]*\s+-[fF]\b",
    r"\brm\s+-[fF]\b[^\n]*\s+-[rR]\b",
    r"\brm\s+--recursive\b",
    r"\brm\s+--force\b",
    r"\bDROP\s+DATABASE\b",
    r"\bDROP\s+TABLE\b",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bgit\s+push\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bformat\s+[a-z]:",
    r"\bshutdown\b",
    r"\bmkfs\b",
    r"\bRemove-Item\b[^\n]*-(?:Recurse|Force)\b",
    r"\bdel\s+/[sq]\b",
    r"\brd\s+/s\b",
    r"\bchmod\s+777\b",
    r"\bsudo\b",
    r"\b(?:system32|Windows\\System32)\b",
    r"\bC:\\Windows\b",
)

_SECRET_SEGMENTS = frozenset(
    {
        "key",
        "token",
        "secret",
        "password",
        "passwd",
        "passphrase",
        "credential",
        "credentials",
        "authorization",
        "cookie",
    }
)

_JOINED_SECRET_NAMES = frozenset(
    {
        "apikey",
        "accesskey",
        "privatekey",
        "secretkey",
        "clientsecret",
        "accesstoken",
        "authtoken",
        "refreshtoken",
        "dbpassword",
        "dbpasswd",
    }
)

_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:postgres|postgresql|mysql|mongodb|redis|amqp)://[^\s:@/]+:[^\s@/]+@"),
)


def _name_segments(name: str) -> list[str]:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    segments: list[str] = []
    for part in parts:
        if not part:
            continue
        segments.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part))
    return [segment.lower() for segment in segments]


def is_secret_field(name: str) -> bool:
    """Return True for field names that may carry a credential body."""
    segments = _name_segments(name)
    if any(segment in _SECRET_SEGMENTS for segment in segments):
        return True
    return "".join(segments) in _JOINED_SECRET_NAMES


def looks_like_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def secret_values_in(value: Any) -> list[str]:
    """Collect credential-looking string values from a config-like tree."""
    found: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(child, str) and is_secret_field(str(key)):
                    if child.strip():
                        found.append(child)
                else:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str) and looks_like_secret_value(item):
            found.append(item)

    walk(value)
    return found


def scan_script(text: str) -> list[str]:
    """Return matched dangerous patterns; empty list means allowed."""
    matches: list[str] = []
    for pattern in _DANGEROUS:
        if re.search(pattern, text, re.IGNORECASE):
            matches.append(pattern)
    return matches


def ensure_within(root: Path, target: Path) -> Path:
    root_resolved = root.resolve()
    target_resolved = target.resolve()
    if target_resolved != root_resolved and root_resolved not in target_resolved.parents:
        raise ValueError(f"路径越界：{target}")
    return target_resolved


def redact(text: str, secrets: list[str]) -> str:
    result = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            result = result.replace(secret, "***")
    return result


def redact_bytes(data: bytes, secrets: list[str]) -> bytes:
    """Redact UTF-8 text payloads; binary media is returned unchanged."""
    if not secrets:
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if "\x00" in text:
        return data
    return redact(text, secrets).encode("utf-8")


def redact_tree(value: Any, secrets: list[str]) -> Any:
    """Recursively redact string leaves before a JSON record is written."""
    if isinstance(value, dict):
        return {key: redact_tree(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_tree(item, secrets) for item in value]
    if isinstance(value, str):
        return redact(value, secrets)
    return value
