"""Every historical fact consumed by runtime guards needs saved authority."""

from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from aitest.application.execution.facts import project_attempt_fact
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    PlanRevisionRef,
    RecoveryCheckpoint,
    RecoveryRecord,
    SideEffectClass,
    StepRevisionRef,
)
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_saved_runtime_revision import runtime as runtime


def save_history(runtime, *, damage=None):
    core, service, _, _, facts, _ = runtime
    step = facts.steps[0]
    attempt = Attempt(
        attempt_id="saved-historical-attempt",
        run_id=facts.run_id,
        step_id=step.step_id,
        attempt_index=1,
        resolved_input_digest="sha256:historical-input",
        # Historical execution keeps its own basis, rather than following current Step.
        step_revision_ref=StepRevisionRef("historical-content", 9, "sha256:historical"),
        expected_plan_revision_ref=PlanRevisionRef(**facts.plan_revision.model_dump()),
        source_binding_digest="sha256:historical-source",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="controlled-history-test",
        state=AttemptState.COMPLETED,
    )
    checkpoint = RecoveryRecord(
        checkpoint=RecoveryCheckpoint(
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            last_committed_stage="completed",
        ),
        attempt=attempt,
        project_id=facts.project_id,
    )
    if damage == "state":
        checkpoint = replace(checkpoint, attempt=replace(attempt, state=AttemptState.RUNNING))
    elif damage == "input":
        checkpoint = replace(
            checkpoint, attempt=replace(attempt, resolved_input_digest="sha256:different-input")
        )
    elif damage == "plan":
        checkpoint = replace(
            checkpoint,
            attempt=replace(
                attempt, expected_plan_revision_ref=PlanRevisionRef("other-plan", 1, "sha256:other")
            ),
        )
    snapshot = facts.model_copy(
        update={"attempts": (project_attempt_fact(attempt, is_current=False),)}
    )
    unit = core.unit_of_work
    unit.begin("controlled-history", facts.project_id, intent_id="controlled-history-intent")
    try:
        if damage != "missing":
            unit.stage_record(
                aggregate_kind="execution_checkpoint",
                record_id=attempt.attempt_id,
                expected_revision=0,
                payload=TypeAdapter(RecoveryRecord).dump_python(checkpoint, mode="json"),
            )
        _, saved = service.execution._stage_snapshot(snapshot)
        unit.commit("controlled-history")
    except BaseException:
        unit.rollback()
        raise
    return saved


@pytest.mark.parametrize("damage", ["missing", "state", "input", "plan", "owner"])
def test_runtime_read_rejects_unproven_history_even_with_no_current_attempt(
    runtime, damage, monkeypatch
):
    saved = save_history(runtime, damage=damage)
    core, service, *_ = runtime
    if damage == "owner":
        original = service.execution._read_payload

        def read(kind, identity):
            payload = original(kind, identity)
            if kind == "execution_checkpoint":
                return {**payload, "project_id": "foreign-project"}
            return payload

        monkeypatch.setattr(service.execution, "_read_payload", read)
    assert (
        service.execution.read_current_facts(project_id=saved.project_id, run_id=saved.run_id)
        == saved
    )
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError):
        service.execution.read_runtime_revision_facts(
            project_id=saved.project_id, run_id=saved.run_id
        )
    assert core.unit_of_work.current_commit_sequence() == sequence


def test_exact_history_keeps_original_step_revision_and_never_becomes_current(runtime):
    saved = save_history(runtime)
    core, service, *_ = runtime
    sequence = core.unit_of_work.current_commit_sequence()
    assert (
        service.execution.read_runtime_revision_facts(
            project_id=saved.project_id, run_id=saved.run_id
        )
        == saved
    )
    assert saved.attempts[0].step_revision_ref != saved.steps[0].step_revision_ref
    assert not saved.attempts[0].is_current
    assert all(identity is None for identity in saved.current_attempt_by_step.values())
    assert core.unit_of_work.current_commit_sequence() == sequence
