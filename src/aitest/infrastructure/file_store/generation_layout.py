"""Per-generation layout (P1 architecture 04 §13).

Every committed generation exposes an inspectable
``generations/<generation>/`` directory with the record, manifest, index and
event facts it published. Entries hold roots and references, never a second
copy of payload bytes, so the layout adds no data duplication.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import atomic

_GENERATION_SCHEMA = "aitest.generation/1.0"


def _safe_generation_id(value: object) -> str:
    text = str(value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", text)
    return safe or "generation"


def record_generation(root: Path, manifest: Mapping[str, Any]) -> Path:
    """Write the four per-generation reference files for one commit."""

    generation_id = manifest.get("generation_id")
    base = root / "generations" / _safe_generation_id(generation_id)
    base.mkdir(parents=True, exist_ok=True)
    common = {"schema": _GENERATION_SCHEMA, "generation_id": generation_id}
    atomic.write_json(
        base / "records.json",
        {
            **common,
            "commit_sequence": manifest.get("commit_sequence"),
            "record_header": manifest.get("record_header"),
            "created": manifest.get("created", []),
        },
    )
    atomic.write_json(
        base / "manifests.json",
        {
            **common,
            "commit_sequence": manifest.get("commit_sequence"),
            "parent_manifest": manifest.get("parent_manifest"),
            "manifest": dict(manifest),
        },
    )
    atomic.write_json(base / "indexes.json", {**common, "index_root": manifest.get("index_root")})
    atomic.write_json(base / "events.json", {**common, "event_root": manifest.get("event_root")})
    return base


__all__ = ["record_generation"]
