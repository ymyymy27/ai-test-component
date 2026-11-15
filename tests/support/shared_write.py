"""Shared test helper: write records through the real commit unit."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

Record = tuple[str, str, int, dict[str, object]]


def write_records(
    root: Path,
    records: Iterable[Record],
    *,
    project_id: str = "project",
    request_id: str = "fixture-write",
    intent_id: str | None = None,
) -> dict[str, object]:
    pending = list(records)
    if not pending:
        return {"state": "empty", "created": [], "commit_sequence": 0}
    unit = FileUnitOfWork(root)
    unit.begin(request_id, project_id, intent_id=intent_id)
    for kind, record_id, expected_revision, payload in pending:
        unit.stage_record(
            aggregate_kind=kind,
            record_id=record_id,
            expected_revision=expected_revision,
            payload=payload,
        )
    return unit.commit(request_id)
