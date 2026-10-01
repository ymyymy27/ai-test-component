"""Read-only integrity inspection for the committed workspace boundary."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")


def _iter_digest_refs(value: Any) -> list[str]:
    """递归收集 ``sha256:<hex>`` 形式的永久对象引用。"""
    found: list[str] = []
    if isinstance(value, str):
        match = _DIGEST_RE.match(value)
        if match is not None:
            found.append(match.group(1))
    elif isinstance(value, dict):
        for nested in value.values():
            found.extend(_iter_digest_refs(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_iter_digest_refs(nested))
    return found


def check_workspace(root: Path) -> dict[str, object]:
    root = root.resolve()
    errors: list[str] = []
    for path in root.rglob("*.json"):
        if path.is_symlink():
            errors.append(f"unexpected symlink: {path}")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")

    objects = 0
    object_digests: set[str] = set()
    directory = root / "objects"
    if directory.exists():
        for path in directory.rglob("*"):
            if path.is_file() and len(path.name) == 64:
                objects += 1
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                object_digests.add(path.name)
                if actual != path.name:
                    errors.append(f"digest mismatch: {path}")

    # 永久引用闭包：records.json 中声明的每个 sha256 引用都必须可达。
    records_path = root / "records.json"
    if records_path.exists():
        try:
            records = json.loads(records_path.read_text(encoding="utf-8"))
            referenced = set(_iter_digest_refs(records))
        except json.JSONDecodeError:
            referenced = set()
        for digest in sorted(referenced - object_digests):
            errors.append(f"unreachable object reference: sha256:{digest}")
    return {"ok": not errors, "objects": objects, "errors": errors}
