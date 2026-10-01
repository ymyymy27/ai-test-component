"""Finite, read-only QuerySpec execution over a maintained index."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aitest.contracts.queries import QuerySpec

from . import atomic

_CURSOR_SCHEMA = "aitest.index-cursor/1"


@dataclass(frozen=True, slots=True)
class IndexQueryResult:
    status: str
    items: tuple[dict[str, Any], ...] = ()
    next_cursor: str | None = None
    index_version: int | None = None


class IndexMissing(RuntimeError):
    code = "INDEX_REBUILD_REQUIRED"


def encode_cursor(sort_key: tuple[Any, ...]) -> str:
    """Encode the last seen sort key as an opaque, commit-bound cursor token."""
    raw = json.dumps(
        {"schema": _CURSOR_SCHEMA, "key": list(sort_key)},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(token: str) -> tuple[Any, ...]:
    """Decode a cursor; raise ValueError on malformed or foreign tokens."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid index cursor") from error
    if not isinstance(payload, dict) or payload.get("schema") != _CURSOR_SCHEMA:
        raise ValueError("invalid index cursor")
    key = payload.get("key")
    if not isinstance(key, list):
        raise ValueError("invalid index cursor")
    return tuple(key)


class FileQueryIndex:
    VERSION = 1

    def __init__(self, root: Path) -> None:
        self.path = root.resolve() / "indexes.json"

    def rebuild(
        self, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]
    ) -> None:
        atomic.write_json(
            self.path,
            {
                "version": self.VERSION,
                "rows": [dict(row) for row in rows],
            },
        )

    def _sort_key(self, spec: QuerySpec, row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row.get(spec.sort),
            row.get("aggregate_kind"),
            row.get("record_id"),
            row.get("revision"),
        )

    def query_spec(self, spec: QuerySpec) -> IndexQueryResult:
        if not self.path.exists():
            return IndexQueryResult(status="maintenance_required")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                raw.get("version") != self.VERSION
                or not isinstance(raw.get("rows"), list)
            ):
                return IndexQueryResult(status="maintenance_required")
            # 坏行显式报告维护状态，绝不静默忽略后仍返回 ok。
            if any(not isinstance(row, dict) for row in raw["rows"]):
                return IndexQueryResult(status="maintenance_required")
            rows = [dict(row) for row in raw["rows"]]
        except (OSError, json.JSONDecodeError, TypeError):
            return IndexQueryResult(status="maintenance_required")
        rows = [row for row in rows if row.get("project_id") == spec.project_id]
        if spec.aggregate_kind is not None:
            rows = [
                row
                for row in rows
                if row.get("aggregate_kind") == spec.aggregate_kind
            ]
        if spec.record_id is not None:
            rows = [row for row in rows if row.get("record_id") == spec.record_id]
        if spec.revision is not None:
            rows = [row for row in rows if row.get("revision") == spec.revision]

        last_key: tuple[Any, ...] | None = None
        if spec.cursor:
            try:
                last_key = decode_cursor(spec.cursor)
            except ValueError:
                return IndexQueryResult(
                    status="invalid_cursor", index_version=self.VERSION
                )

        try:
            sorted_rows = sorted(
                rows,
                key=lambda row: self._sort_key(spec, row),
                reverse=spec.descending,
            )
            if last_key is not None:
                # 键集续读：只取严格越过上次所见排序键的行。索引在分页间
                # 重建也不会重复返回上一页的 ID。
                if spec.descending:
                    sorted_rows = [
                        row
                        for row in sorted_rows
                        if self._sort_key(spec, row) < last_key
                    ]
                else:
                    sorted_rows = [
                        row
                        for row in sorted_rows
                        if self._sort_key(spec, row) > last_key
                    ]
        except TypeError:
            # 排序键类型不一致说明索引内容已损坏。
            return IndexQueryResult(status="maintenance_required")
        page = tuple(sorted_rows[: spec.limit])
        next_cursor = (
            encode_cursor(self._sort_key(spec, page[-1]))
            if len(page) == spec.limit and len(sorted_rows) > spec.limit
            else None
        )
        return IndexQueryResult(
            status="ok",
            items=page,
            next_cursor=next_cursor,
            index_version=self.VERSION,
        )

    def query(
        self,
        *,
        project_id: str,
        kind: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> IndexQueryResult:
        spec = QuerySpec(
            project_id=project_id,
            aggregate_kind=kind,
            limit=limit,
            cursor=cursor,
        )
        return self.query_spec(spec)
