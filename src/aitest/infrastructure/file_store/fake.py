"""In-memory A substrate for parallel B/C/D development only."""

from __future__ import annotations

from copy import deepcopy


class MemoryStore:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], list[dict[str, object]]] = {}
        self.intents: dict[str, object] = {}
        self.active: str | None = None

    def begin(self, request_id: str) -> dict[str, str]:
        if self.active and self.active != request_id:
            raise RuntimeError("workspace already has an active transaction")
        self.active = request_id
        return {"request_id": request_id, "state": "active"}

    def append(
        self,
        request_id: str,
        intent_id: str,
        kind: str,
        record_id: str,
        payload: dict[str, object],
        expected_revision: int | None,
    ) -> int:
        if self.active != request_id:
            raise RuntimeError("transaction not active")
        fingerprint = repr(sorted(payload.items()))
        existing = self.intents.get(intent_id)
        if existing is not None:
            assert isinstance(existing, dict)
            if existing["fingerprint"] != fingerprint:
                raise ValueError("intent conflict")
            revision = existing["revision"]
            assert isinstance(revision, int)
            return revision
        rows = self.records.setdefault((kind, record_id), [])
        if expected_revision != len(rows):
            raise ValueError("revision conflict")
        rows.append(deepcopy(payload))
        revision = len(rows)
        self.intents[intent_id] = {
            "fingerprint": fingerprint,
            "revision": revision,
        }
        return revision

    def commit(self, request_id: str) -> dict[str, str]:
        if self.active != request_id:
            raise RuntimeError("transaction not active")
        self.active = None
        return {"request_id": request_id, "state": "committed"}

    def rollback(self, request_id: str) -> dict[str, str]:
        if self.active != request_id:
            raise RuntimeError("transaction not active")
        self.active = None
        return {"request_id": request_id, "state": "rolled_back"}

    def read(
        self, kind: str, record_id: str, revision: int
    ) -> dict[str, object]:
        rows = self.records.get((kind, record_id), [])
        if revision < 1 or revision > len(rows):
            raise KeyError("record not found")
        return deepcopy(rows[revision - 1])

    def query(
        self,
        project_id: str,
        kind: str | None = None,
        limit: int = 50,
        cursor: int = 0,
    ) -> tuple[
        list[tuple[str, str, int, dict[str, object]]], int | None
    ]:
        rows: list[tuple[str, str, int, dict[str, object]]] = []
        for (row_kind, rid), revisions in sorted(self.records.items()):
            if kind and row_kind != kind:
                continue
            for revision, payload in enumerate(revisions, 1):
                if payload.get("project_id") == project_id:
                    rows.append(
                        (row_kind, rid, revision, deepcopy(payload))
                    )
        page = rows[cursor : cursor + limit]
        next_cursor = cursor + limit if cursor + limit < len(rows) else None
        return page, next_cursor
