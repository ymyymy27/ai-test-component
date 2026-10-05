"""Historical consumers cannot change an independent current execution projection."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aitest.application.execution.current import project_current_update
from aitest.application.execution.facts import project_attempt_fact, project_attempt_update
from aitest.application.execution.runtime_revision import require_runtime_boundary
from aitest.contracts.execution_facts import RedactionSummaryFact, StepStateFact
from aitest.domain.execution.dependencies import AttemptInvalidation, invalidate_downstream_attempts
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    ConsumedOutput,
    OutputBlockRef,
    OutputStreamName,
)
from tests.unit.test_current_execution_snapshot import _batch
from tests.unit.test_serial_runner import FakeExecutionPort, _plan_revision


def test_historical_consumer_invalidates_history_without_harming_independent_current():
    batch = _batch()
    upstream = replace(batch.checkpoint.attempt, capture_completeness=CaptureCompleteness.COMPLETE)
    old_consumer = replace(
        upstream,
        attempt_id="old-consumer",
        step_id="consumer-step",
        intent_id="old-consumer-intent",
        consumed_outputs=(ConsumedOutput(upstream.attempt_id, "sha256:old", "output:old"),),
    )
    current_consumer = replace(
        old_consumer,
        attempt_id="independent-current",
        intent_id="independent-current-intent",
        attempt_index=2,
        consumed_outputs=(),
    )
    facts = batch.facts.model_copy(
        update={
            "steps": (
                batch.facts.steps[0],
                batch.facts.steps[0].model_copy(
                    update={
                        "step_id": old_consumer.step_id,
                        "ordinal": 2,
                        "state": StepStateFact.COMPLETED,
                        "current_attempt_id": current_consumer.attempt_id,
                    }
                ),
            ),
            "attempts": (
                project_attempt_fact(upstream, is_current=True),
                project_attempt_fact(old_consumer, is_current=False),
                project_attempt_fact(current_consumer, is_current=True),
            ),
            "current_attempt_by_step": {
                upstream.step_id: upstream.attempt_id,
                old_consumer.step_id: current_consumer.attempt_id,
            },
            "coverage": batch.facts.coverage.model_copy(
                update={"executed_attempt_ids": (upstream.attempt_id, current_consumer.attempt_id)}
            ),
        }
    )
    invalidations = invalidate_downstream_attempts(
        (upstream, old_consumer, current_consumer),
        previous_plan_revision=_plan_revision(),
        current_plan_revision=_plan_revision(),
        affected_upstream_attempt_ids=(upstream.attempt_id,),
    )
    replacement = replace(
        upstream, attempt_id="upstream-replacement", intent_id="replacement-intent", attempt_index=2
    )
    result = project_current_update(
        facts, replacement, invalidations=invalidations, committed_at=datetime.now(UTC)
    )
    assert next(item for item in result.attempts if item.attempt_id == "old-consumer").state == (
        "invalidated"
    )
    assert result.steps[1] == facts.steps[1]
    assert "consumer-step" not in result.coverage.invalidated_step_ids
    assert current_consumer.attempt_id in result.coverage.executed_attempt_ids
    assert result.dependency_invalidations[-1].affected_attempt_id == old_consumer.attempt_id


def test_invalidation_retains_redaction_provenance_for_the_same_saved_output_bytes():
    batch = _batch()
    old = replace(
        batch.checkpoint.attempt,
        output_block_refs=(
            OutputBlockRef(
                "block-1",
                "attempt-1",
                OutputStreamName.STDOUT,
                0,
                0,
                9,
                "sha256:filtered-bytes",
                True,
                "controlled-fixture",
                "redaction-1",
            ),
        ),
    )
    summary = RedactionSummaryFact(
        policy_version="policy-v2",
        applied_rule_categories=("known-credential",),
        filtered_streams=("stdout",),
        filtered_ranges=("0:9",),
        replacement_count=1,
        completeness="complete",
    )
    original = project_attempt_fact(old, is_current=True)
    original = original.model_copy(
        update={
            "output_blocks": (
                original.output_blocks[0].model_copy(update={"redaction_summary": summary}),
            )
        }
    )
    facts = batch.facts.model_copy(update={"attempts": (original,)})
    invalidated = replace(
        old, state=AttemptState.INVALIDATED, unknown_reason_ref="upstream_dependency_invalidated"
    )
    result = project_current_update(
        facts,
        invalidated,
        invalidations=(
            AttemptInvalidation(invalidated, ("upstream",), "upstream_attempt_replaced"),
        ),
        committed_at=datetime.now(UTC),
    )
    assert result.attempts[0].output_blocks == original.output_blocks
    assert result.attempts[0].state == "invalidated"


@pytest.mark.parametrize(
    "state",
    [
        AttemptState.INTENT_RECORDED,
        AttemptState.STARTING,
        AttemptState.RUNNING,
        AttemptState.STOP_REQUESTED,
        AttemptState.COLLECTING,
        AttemptState.UNKNOWN,
        AttemptState.PENDING_VERIFICATION,
    ],
)
def test_runtime_boundary_rejects_current_or_historical_activity(state):
    batch = _batch()
    fact = project_attempt_fact(replace(batch.checkpoint.attempt, state=state), is_current=False)
    with pytest.raises(ValueError, match="verified non-active"):
        require_runtime_boundary(batch.facts.model_copy(update={"attempts": (fact,)}))


def test_completed_state_with_unverified_handle_does_not_prove_a_runtime_boundary():
    batch = _batch()
    fact = project_attempt_fact(
        replace(batch.checkpoint.attempt, execution_handle_ref=FakeExecutionPort().handle),
        is_current=True,
    )
    with pytest.raises(ValueError, match="verified non-active"):
        require_runtime_boundary(batch.facts.model_copy(update={"attempts": (fact,)}))


def test_changed_output_bytes_cannot_inherit_old_redaction_provenance():
    old = replace(
        _batch().checkpoint.attempt,
        output_block_refs=(
            OutputBlockRef(
                "block-1",
                "attempt-1",
                OutputStreamName.STDOUT,
                0,
                0,
                9,
                "sha256:old-bytes",
                True,
                "controlled-fixture",
                "old-summary",
            ),
        ),
    )
    saved = project_attempt_fact(old, is_current=True)
    new = replace(
        old,
        output_block_refs=(
            replace(
                old.output_block_refs[0], digest="sha256:different-bytes", redaction_summary_id=None
            ),
        ),
    )
    actual = project_attempt_update(new, saved, is_current=True)
    assert actual.output_blocks[0].digest == "sha256:different-bytes"
    assert actual.output_blocks[0].redaction_summary is None
