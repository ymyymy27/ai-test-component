"""Exact current snapshot references, immutable history and atomic publication failures."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aitest.application.execution.commit import ExecutionCommitBatch, ExecutionCommitCoordinator
from aitest.application.execution.facts import project_attempt_fact
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.execution.runs import (
    AttemptState,
    RecoveryCheckpoint,
    RecoveryRecord,
    StepRevisionRef,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.persistent_evidence_fixture import fixture_facts, save_fixture_bytes
from tests.unit.test_serial_runner import _attempt


@pytest.mark.parametrize(
    "field",
    [
        "capture_completeness",
        "intent_id",
        "intent_digest",
        "attempt_index",
        "attempt_revision",
        "authorization_ref",
        "adapter_kind",
        "adapter_version",
        "timeout_ms",
        "timed_out",
        "consumed_outputs",
        "consumed_conditions",
        "output_blocks",
        "output_cursors",
        "handle",
        "exit_fact",
        "started_at",
        "step_revision_ref",
    ],
)
def test_caller_cannot_publish_completion_or_bytes_absent_from_the_real_checkpoint(tmp_path, field):
    batch = _batch()
    payload = batch.facts.model_dump(mode="json")
    fact = payload["attempts"][0]
    if field == "capture_completeness":
        fact[field] = "complete"
    elif field in {"intent_id", "intent_digest", "authorization_ref", "adapter_version"}:
        fact[field] = "caller-invented-value"
    elif field == "adapter_kind":
        fact[field] = "http"
    elif field == "attempt_index":
        fact[field], fact["retry_count"] = 2, 1
    elif field == "attempt_revision":
        fact[field] += 1
    elif field == "timeout_ms":
        fact[field] = 999
    elif field == "timed_out":
        fact[field] = True
    elif field == "consumed_outputs":
        fact[field] = [
            {
                "upstream_attempt_id": "caller-upstream",
                "output_object_digest": "sha256:x",
                "value_ref": "value:x",
            }
        ]
    elif field == "consumed_conditions":
        fact[field] = [
            {
                "upstream_attempt_id": "caller-upstream",
                "condition_fact_ref": "caller-condition",
                "condition_digest": "sha256:x",
            }
        ]
    elif field == "started_at":
        fact[field] = "2026-10-04T10:00:00+08:00"
    elif field == "step_revision_ref":
        fact[field]["step_revision_id"] = "caller-other-step-with-same-digest"
        payload["steps"][0]["step_revision_ref"] = dict(fact[field])
    else:
        original = ExecutionFacts.model_validate_json(
            (
                Path(__file__).parents[1] / "contracts/fixtures/execution_facts/success.json"
            ).read_text(encoding="utf-8")
        )
        fact[field] = original.attempts[0].model_dump(mode="json")[field]
    forged = replace(batch, facts=ExecutionFacts.model_validate(payload))
    unit = FileUnitOfWork(tmp_path)
    before = unit.current_revision(aggregate_kind="execution_checkpoint", record_id="attempt-1")
    with pytest.raises(ValueError, match="authoritative checkpoint"):
        _publish(unit, forged)
    assert (
        unit.current_revision(aggregate_kind="execution_checkpoint", record_id="attempt-1")
        == before
    )
    assert (
        ExecutionCommitCoordinator(unit).read_current_facts(project_id="project-1", run_id="run-1")
        is None
    )


def test_another_current_step_needs_a_saved_exact_checkpoint(tmp_path):
    batch = _batch()
    original_step, original_attempt = batch.facts.steps[0], batch.facts.attempts[0]
    other_attempt = original_attempt.model_copy(
        update={"attempt_id": "attempt-2", "step_id": "step-2"}
    )
    other_step = original_step.model_copy(
        update={"step_id": "step-2", "current_attempt_id": "attempt-2", "ordinal": 2}
    )
    facts = batch.facts.model_copy(
        update={
            "steps": (original_step, other_step),
            "attempts": (original_attempt, other_attempt),
            "current_attempt_by_step": {"step-1": "attempt-1", "step-2": "attempt-2"},
        }
    )
    unit = FileUnitOfWork(tmp_path)
    with pytest.raises(ValueError, match="unavailable or foreign"):
        _publish(unit, replace(batch, facts=facts))
    assert unit.current_revision(aggregate_kind="execution_checkpoint", record_id="attempt-1") == 0


def _batch():
    facts = ExecutionFacts.model_validate_json(
        (Path(__file__).parents[1] / "contracts/fixtures/execution_facts/success.json").read_text(
            encoding="utf-8"
        )
    )
    facts = fixture_facts(facts)
    step = facts.attempts[0].step_revision_ref
    attempt = replace(
        _attempt(),
        state=AttemptState.COMPLETED,
        step_revision_ref=StepRevisionRef(step.step_revision_id, step.revision_no, step.digest),
    )
    checkpoint = RecoveryRecord(
        RecoveryCheckpoint(attempt.run_id, attempt.step_id, attempt.attempt_id, "completed"),
        attempt,
    )
    facts = facts.model_copy(update={"attempts": (project_attempt_fact(attempt, is_current=True),)})
    return ExecutionCommitBatch(checkpoint=checkpoint, evidence_refs=(), facts=facts)


def _publish(unit, batch, request="publish"):
    save_fixture_bytes(unit.workspace.root, batch.facts.project_id)
    unit.begin(request, batch.facts.project_id)
    return ExecutionCommitCoordinator(unit).stage_and_commit(batch)


def test_current_reference_and_snapshot_share_the_actual_boundary_and_preserve_old_revision(
    tmp_path,
):
    unit = FileUnitOfWork(tmp_path)
    first = _publish(unit, _batch(), "first")
    coordinator = ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == first.facts
    assert first.facts.snapshot_cursor == first.committed["commit_sequence"]
    second = _publish(unit, _batch(), "second")
    assert second.facts.snapshot_cursor == second.committed["commit_sequence"]
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == second.facts
    historical = unit.read(
        aggregate_kind="execution_facts", record_id=first.facts.snapshot_commit_id, revision=1
    )
    assert historical.payload == first.facts.model_dump(mode="json")
    assert first.facts.snapshot_commit_id != second.facts.snapshot_commit_id
    assert coordinator.read_current_facts(project_id="foreign", run_id="run-1") is None


@pytest.mark.parametrize("field", ["digest", "snapshot_commit_id", "project_id", "schema_version"])
def test_unverifiable_current_reference_never_falls_back_to_a_successful_history_item(
    tmp_path, monkeypatch, field
):
    unit = FileUnitOfWork(tmp_path)
    _publish(unit, _batch())
    original = unit.read

    def wrong_reference(**kwargs):
        record = original(**kwargs)
        if kwargs["aggregate_kind"] == "execution_facts_current":
            payload = dict(record.payload)
            payload[field] = "unverifiable"
            return SimpleNamespace(payload=payload)
        return record

    monkeypatch.setattr(unit, "read", wrong_reference)
    with pytest.raises((ValueError, KeyError)):
        ExecutionCommitCoordinator(unit).read_current_facts(project_id="project-1", run_id="run-1")


def test_current_snapshot正文_digest_is_verified_after_exact_revision_read(tmp_path, monkeypatch):
    unit = FileUnitOfWork(tmp_path)
    _publish(unit, _batch())
    original = unit.read

    def wrong_bytes(**kwargs):
        record = original(**kwargs)
        if kwargs["aggregate_kind"] == "execution_facts":
            return SimpleNamespace(payload={**record.payload, "facts_id": "altered"})
        return record

    monkeypatch.setattr(unit, "read", wrong_bytes)
    with pytest.raises(ValueError, match="digest"):
        ExecutionCommitCoordinator(unit).read_current_facts(project_id="project-1", run_id="run-1")


def test_failed_snapshot_staging_cannot_publish_the_preceding_pointer_or_checkpoint(
    tmp_path, monkeypatch
):
    unit = FileUnitOfWork(tmp_path)
    first = _publish(unit, _batch(), "first")
    before = unit.current_commit_sequence()
    original = unit.stage_record

    def fail_snapshot(**kwargs):
        if kwargs["aggregate_kind"] == "execution_facts":
            raise OSError("injected snapshot stage failure after pointer")
        return original(**kwargs)

    monkeypatch.setattr(unit, "stage_record", fail_snapshot)
    with pytest.raises(OSError, match="after pointer"):
        _publish(unit, _batch(), "failed")
    assert unit.current_commit_sequence() == before
    assert unit.current_revision(aggregate_kind="execution_checkpoint", record_id="attempt-1") == 1
    assert (
        ExecutionCommitCoordinator(unit).read_current_facts(project_id="project-1", run_id="run-1")
        == first.facts
    )
    assert unit.pending == [] and unit.project is None


@pytest.mark.parametrize("change", ["map", "current_flag", "step_run", "duplicate", "run_revision"])
def test_inconsistent_current_attempt_facts_are_rejected_before_any_publication(tmp_path, change):
    unit, batch = FileUnitOfWork(tmp_path), _batch()
    facts = batch.facts
    changed = {
        "map": facts.model_copy(update={"current_attempt_by_step": {"step-1": None}}),
        "current_flag": facts.model_copy(
            update={"attempts": (facts.attempts[0].model_copy(update={"is_current": False}),)}
        ),
        "step_run": facts.model_copy(
            update={"steps": (facts.steps[0].model_copy(update={"run_id": "foreign"}),)}
        ),
        "duplicate": facts.model_copy(update={"attempts": facts.attempts * 2}),
        "run_revision": facts.model_copy(update={"run_revision": facts.run_revision + 1}),
    }[change]
    with pytest.raises(ValueError, match="execution snapshot"):
        _publish(unit, replace(batch, facts=changed))
    assert unit.current_commit_sequence() == 0
    assert unit.pending == [] and unit.project is None


def test_current_reference_rejects_rebinding_the_frozen_run_intent(tmp_path):
    unit, batch = FileUnitOfWork(tmp_path), _batch()
    first = _publish(unit, batch, "first")
    changed = batch.facts.model_copy(
        update={"run": batch.facts.run.model_copy(update={"intent_id": "foreign-intent"})}
    )
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="frozen run identity"):
        _publish(unit, replace(batch, facts=changed), "rebind")
    assert unit.current_commit_sequence() == before
    assert (
        ExecutionCommitCoordinator(unit).read_current_facts(project_id="project-1", run_id="run-1")
        == first.facts
    )
