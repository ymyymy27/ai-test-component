"""Read-only integrity inspection for the committed workspace boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def check_workspace(root: Path) -> dict[str, object]:
    root = root.resolve()
    errors: list[str] = []
    objects = 0
    for path in root.rglob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    directory = root / "objects"
    if directory.exists():
        for path in directory.rglob("*"):
            if path.is_file() and len(path.name) == 64:
                objects += 1
                if hashlib.sha256(path.read_bytes()).hexdigest() != path.name:
                    errors.append(f"digest mismatch: {path}")
    return {"ok": not errors, "objects": objects, "errors": errors}
