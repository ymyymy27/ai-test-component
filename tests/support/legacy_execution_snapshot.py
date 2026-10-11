"""Seed old saved material for reader counterexamples, bypassing today's publisher.

This fixture deliberately has no checkpoint map. It proves neither admission nor
reuse; the actual low-level A transaction and old DTO remain readable.
"""

from aitest.application.execution.commit import _run_pointer_id
from aitest.application.execution.facts import execution_payload_digest


def stage_legacy_snapshot(unit, facts, previous):
    sequence = int(unit.next_commit_seq()) + 1
    saved = facts.model_copy(update={
        "snapshot_commit_id": f"commit-{sequence}", "snapshot_cursor": sequence,
        "snapshot_revision": 1,
    })
    pointer_id = _run_pointer_id(facts.project_id, facts.run_id)
    unit.stage_record(
        aggregate_kind="execution_facts_current", record_id=pointer_id,
        expected_revision=unit.current_revision(
            aggregate_kind="execution_facts_current", record_id=pointer_id,
        ),
        payload={
            "schema_version": "aitest.execution-facts-reference/1.0",
            "project_id": facts.project_id, "run_id": facts.run_id,
            "snapshot_commit_id": saved.snapshot_commit_id, "snapshot_revision": 1,
            "digest": execution_payload_digest(saved.model_dump(mode="json")),
            "previous_snapshot_commit_id": previous.snapshot_commit_id,
        },
    )
    unit.stage_record(
        aggregate_kind="execution_facts", record_id=saved.snapshot_commit_id,
        expected_revision=0, payload=saved.model_dump(mode="json"),
    )
    return saved
