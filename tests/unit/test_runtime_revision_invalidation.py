"""Controlled file checkpoints prove basis invalidation without claiming actual execution."""

from dataclasses import replace
from datetime import UTC, datetime

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import project_attempt_fact
from aitest.application.execution.runtime_revision import SavedRuntimeRevisionReader
from aitest.application.planning.draft import text_digest
from aitest.contracts.execution_facts import StepStateFact
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    ConsumedCondition,
    PlanRevisionRef,
    RecoveryCheckpoint,
    RecoveryRecord,
    StepRevisionRef,
)
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_persisted_runtime_revision import (
    apply,
    initial_basis,
    revision_request,
    saved_next_case,
)
from tests.unit.test_serial_runner import _attempt


def test_basis_revision_saves_history_closure_and_preserves_independent_current(authoritative):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    first_step, fresh_step = tuple(
        step for step in before.steps if step.case_id == cases[0].case_id
    )
    consumer_step, descendant_step = tuple(
        step for step in before.steps if step.case_id == cases[1].case_id
    )
    common = dict(
        run_id=before.run_id,
        expected_plan_revision_ref=PlanRevisionRef(**before.plan_revision.model_dump()),
        state=AttemptState.COMPLETED,
        capture_completeness=CaptureCompleteness.COMPLETE,
    )
    upstream = replace(
        _attempt(),
        **common,
        attempt_id="basis-upstream",
        intent_id="basis-upstream",
        step_id=first_step.step_id,
        step_revision_ref=StepRevisionRef(**first_step.step_revision_ref.model_dump()),
    )
    old_consumer = replace(
        upstream,
        attempt_id="basis-old-consumer",
        intent_id="basis-old-consumer",
        step_id=consumer_step.step_id,
        step_revision_ref=StepRevisionRef(**consumer_step.step_revision_ref.model_dump()),
        consumed_conditions=(ConsumedCondition(upstream.attempt_id, "condition-1", "sha256:one"),),
    )
    current_consumer = replace(
        old_consumer,
        attempt_id="basis-current-independent",
        intent_id="basis-current-independent",
        attempt_index=2,
        consumed_conditions=(),
    )
    descendant = replace(
        upstream,
        attempt_id="basis-descendant",
        intent_id="basis-descendant",
        step_id=descendant_step.step_id,
        step_revision_ref=StepRevisionRef(**descendant_step.step_revision_ref.model_dump()),
        consumed_conditions=(
            ConsumedCondition(old_consumer.attempt_id, "condition-2", "sha256:two"),
        ),
    )
    domain = (upstream, old_consumer, current_consumer, descendant)
    unit = core.unit_of_work
    coordinator = ExecutionCommitCoordinator(unit, records=unit.repo)
    for attempt in domain:
        coordinator.commit_checkpoint(
            project_id=inputs.project_id,
            checkpoint=RecoveryRecord(
                RecoveryCheckpoint(
                    attempt.run_id, attempt.step_id, attempt.attempt_id, "completed"
                ),
                attempt,
                project_id=inputs.project_id,
            ),
        )
    current = dict(before.current_attempt_by_step)
    current.update({attempt.step_id: attempt.attempt_id for attempt in domain})
    controlled = before.model_copy(
        update={
            "committed_at": datetime.now(UTC),
            "run_revision": before.run_revision + 1,
            "run": before.run.model_copy(update={"run_revision": before.run_revision + 1}),
            "attempts": tuple(
                project_attempt_fact(
                    attempt, is_current=current[attempt.step_id] == attempt.attempt_id
                )
                for attempt in domain
            ),
            "current_attempt_by_step": current,
            "steps": tuple(
                step.model_copy(
                    update={
                        "current_attempt_id": current[step.step_id],
                        "state": StepStateFact.COMPLETED,
                    }
                )
                if current[step.step_id] is not None
                else step
                for step in before.steps
            ),
            "coverage": before.coverage.model_copy(
                update={
                    "executed_attempt_ids": (
                        upstream.attempt_id,
                        current_consumer.attempt_id,
                        descendant.attempt_id,
                    )
                }
            ),
        }
    )
    unit.begin(
        "controlled-history-fixture", inputs.project_id, intent_id="controlled-history-fixture"
    )
    _, controlled = coordinator._stage_snapshot(controlled, allow_current_change=True)
    unit.commit("controlled-history-fixture")
    next_text = cases[0].assertion_basis.text + " with a stricter check"
    changed = saved_next_case(
        core,
        inputs,
        replace(
            cases[0],
            revision=2,
            assertion_basis=replace(
                cases[0].assertion_basis,
                revision=2,
                text=next_text,
                text_digest=text_digest(next_text),
            ),
        ),
    )
    after = apply(
        core, plan, controlled, revision_request(controlled, changed), intent="basis-update"
    )
    attempts = {attempt.attempt_id: attempt for attempt in after.attempts}
    assert all(
        attempts[item.attempt_id].state == "invalidated"
        for item in (upstream, old_consumer, descendant)
    )
    assert attempts[current_consumer.attempt_id] == next(
        item for item in controlled.attempts if item.attempt_id == current_consumer.attempt_id
    )
    saved_steps = {step.step_id: step for step in controlled.steps}
    changed_steps = {step.step_id: step for step in after.steps}
    assert changed_steps[first_step.step_id].step_revision_ref == first_step.step_revision_ref
    assert changed_steps[fresh_step.step_id].step_revision_ref != fresh_step.step_revision_ref
    assert changed_steps[consumer_step.step_id] == saved_steps[consumer_step.step_id]
    assert changed_steps[descendant_step.step_id].invalidated
    assert after.coverage.executed_attempt_ids == (current_consumer.attempt_id,)
    assert (
        coordinator.read_runtime_revision_facts(project_id=inputs.project_id, run_id=after.run_id)
        == after
    )
    reader = SavedRuntimeRevisionReader(unit.repo)
    assert reader.read_effective_cases(facts=after, plan=plan, initial_cases=cases) == (
        changed,
        cases[1],
    )
    for old in (upstream, old_consumer, descendant):
        assert (
            unit.repo.read(
                aggregate_kind="execution_checkpoint", record_id=old.attempt_id, revision=1
            ).payload["attempt"]["state"]
            == "completed"
        )
        assert (
            coordinator.read_checkpoint(
                project_id=inputs.project_id, attempt_id=old.attempt_id
            ).attempt.state
            is AttemptState.INVALIDATED
        )
