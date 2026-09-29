"""Finite, read-only QuerySpec execution over a maintained index."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from aitest.contracts.queries import QuerySpec
from . import atomic

@dataclass(frozen=True, slots=True)
class IndexQueryResult:
    status: str
    items: tuple[dict[str, Any], ...] = ()
    next_cursor: str | None = None
    index_version: int | None = None

class IndexMissing(RuntimeError):
    code = "INDEX_REBUILD_REQUIRED"

class FileQueryIndex:
    VERSION = 1
    def __init__(self, root: Path): self.path = root.resolve() / "indexes.json"
    def rebuild(self, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> None:
        atomic.write_json(self.path, {"version": self.VERSION, "rows": [dict(row) for row in rows]})
    def query_spec(self, spec: QuerySpec) -> IndexQueryResult:
        if not self.path.exists(): return IndexQueryResult(status="maintenance_required")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("version") != self.VERSION or not isinstance(raw.get("rows"), list): return IndexQueryResult(status="maintenance_required")
            rows = [row for row in raw["rows"] if isinstance(row, dict)]
        except (OSError, json.JSONDecodeError, TypeError): return IndexQueryResult(status="maintenance_required")
        rows = [row for row in rows if row.get("project_id") == spec.project_id]
        if spec.aggregate_kind is not None: rows = [row for row in rows if row.get("aggregate_kind") == spec.aggregate_kind]
        if spec.record_id is not None: rows = [row for row in rows if row.get("record_id") == spec.record_id]
        if spec.revision is not None: rows = [row for row in rows if row.get("revision") == spec.revision]
        rows.sort(key=lambda row: (row.get(spec.sort), row.get("aggregate_kind"), row.get("record_id"), row.get("revision")), reverse=spec.descending)
        try: offset = int(spec.cursor or "0")
        except ValueError: return IndexQueryResult(status="invalid_cursor", index_version=self.VERSION)
        if offset < 0: return IndexQueryResult(status="invalid_cursor", index_version=self.VERSION)
        page = tuple(rows[offset:offset + spec.limit]); end = offset + len(page)
        return IndexQueryResult(status="ok", items=page, next_cursor=str(end) if end < len(rows) else None, index_version=self.VERSION)
    def query(self, *, project_id: str, kind: str | None = None, limit: int = 50, cursor: int = 0) -> IndexQueryResult:
        spec = QuerySpec(project_id=project_id, aggregate_kind=kind, limit=limit, cursor=str(cursor) if cursor else None)
        return self.query_spec(spec)
