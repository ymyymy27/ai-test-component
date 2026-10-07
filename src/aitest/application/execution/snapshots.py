"""Exact immutable execution snapshots shared by current and historic consumers."""

import json
from collections.abc import Mapping

from aitest.application.execution.facts import execution_payload_digest, validate_execution_facts
from aitest.application.ports import RecordRepository
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.json_material import require_json_text


def read_execution_snapshot(
    records: RecordRepository,
    *,
    project_id: str,
    run_id: str,
    snapshot_id: str,
    digest: str,
    snapshot_cursor: int | None = None,
) -> ExecutionFacts:
    """Read @1 only; a valid historic snapshot does not grant reuse eligibility."""
    for identity in (project_id, run_id, snapshot_id, digest):
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("execution snapshot reference requires nonempty text identities")
        require_json_text(identity)
    if snapshot_cursor is not None and (
        type(snapshot_cursor) is not int or snapshot_cursor < 0
    ):
        raise ValueError("execution snapshot cursor requires an exact nonnegative integer")
    revision = records.current_revision(aggregate_kind="execution_facts", record_id=snapshot_id)
    if type(revision) is not int or revision != 1:
        raise ValueError("execution snapshot must remain immutable at warehouse revision 1")
    record = records.read(aggregate_kind="execution_facts", record_id=snapshot_id, revision=1)
    if (
        getattr(record, "aggregate_kind", None),
        getattr(record, "record_id", None),
        getattr(record, "revision", None),
    ) != ("execution_facts", snapshot_id, 1) or type(getattr(record, "revision", None)) is not int:
        raise ValueError("execution snapshot envelope cannot be verified")
    payload = getattr(record, "payload", None)
    if not isinstance(payload, Mapping) or execution_payload_digest(payload) != digest:
        raise ValueError("execution snapshot digest cannot be verified")
    facts = ExecutionFacts.model_validate_json(
        json.dumps(dict(payload), ensure_ascii=False, allow_nan=False), strict=True
    )
    if (facts.project_id, facts.run_id, facts.snapshot_commit_id, facts.snapshot_revision) != (
        project_id, run_id, snapshot_id, 1,
    ) or (snapshot_cursor is not None and facts.snapshot_cursor != snapshot_cursor):
        raise ValueError("execution snapshot identity cannot be verified")
    validate_execution_facts(facts)
    return facts
