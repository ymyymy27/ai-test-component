"""Redaction, path checks and generated-script screening."""

from __future__ import annotations

import re
from pathlib import Path

_DANGEROUS = (
    r"\brm\s+-rf\b",
    r"\bDROP\s+TABLE\b",
    r"\bgit\s+push\b",
    r"\bformat\s+[a-z]:",
    r"\bshutdown\b",
    r"\bmkfs\b",
    r"\bRemove-Item\s+-Recurse\b",
    r"\bdel\s+/[sq]\b",
)


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
