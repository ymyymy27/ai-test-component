from dataclasses import replace

import pytest

from aitest.domain.execution.cases import StepExecutionBasis, aggregate_case_execution
from aitest.domain.execution.runs import AttemptState, CaptureCompleteness
from tests.support.reuse_basis import verified_reuse_basis


def completed(step_id="s1", current=True):
    return StepExecutionBasis(
        step_id=step_id, attempt_id="attempt-" + step_id, from_current_run=current,
        state=AttemptState.COMPLETED, capture_completeness=CaptureCompleteness.COMPLETE,
        verification_valid=True,
    )


@pytest.mark.parametrize("current", [True, False])
def test_valid_failed_case_retains_execution_or_reuse_and_verification(current) -> None:
    result = aggregate_case_execution(
        case_id="case", required_step_ids=("s1",), steps=(completed(current=current),),
        decisive_failure_step_ids=("s1",),
        reuse_basis=verified_reuse_basis() if not current else None,
    )
    assert result.has_decisive_failure
    assert result.can_count_execution == current
    assert result.can_count_reuse == (not current)
    assert result.can_count_verification
    assert result.pending_step_ids == ()


def test_empty_required_steps_cannot_be_a_completed_or_verified_case() -> None:
    result = aggregate_case_execution(case_id="case", required_step_ids=(), steps=())
    assert not result.can_count_execution
    assert not result.can_count_reuse
    assert not result.can_count_verification


@pytest.mark.parametrize("identity", [None, ""])
def test_missing_attempt_identity_cannot_count_execution(identity) -> None:
    result = aggregate_case_execution(
        case_id="case", required_step_ids=("s1",),
        steps=(replace(completed(), attempt_id=identity),),
    )
    assert not result.can_count_execution
    assert not result.can_count_verification


@pytest.mark.parametrize("current", [True, False])
def test_capture_gap_cannot_count_execution_reuse_or_verification(current) -> None:
    result = aggregate_case_execution(
        case_id="case", required_step_ids=("s1",),
        steps=(replace(completed(current=current), capture_completeness=CaptureCompleteness.GAP),),
    )
    assert not result.can_count_execution
    assert not result.can_count_reuse
    assert not result.can_count_verification


def test_new_optional_attempt_cancels_whole_case_reuse() -> None:
    result = aggregate_case_execution(
        case_id="case", required_step_ids=("s1",),
        steps=(
            completed(current=False),
            replace(completed("optional"), state=AttemptState.CANCELLED),
        ),
    )
    assert not result.can_count_reuse
    assert not result.can_count_execution
    assert not result.can_count_verification
    assert result.pending_step_ids == ("s1",)


def test_ambiguous_current_step_facts_are_rejected_instead_of_last_wins() -> None:
    with pytest.raises(ValueError, match="current step facts must be unique"):
        aggregate_case_execution(
            case_id="case", required_step_ids=("s1",),
            steps=(completed(), replace(completed(), state=AttemptState.INVALIDATED)),
        )


def test_valid_upstream_failure_remains_when_downstream_is_unverified() -> None:
    result = aggregate_case_execution(
        case_id="case", required_step_ids=("s1", "s2"),
        steps=(completed(), replace(completed("s2"), state=AttemptState.PENDING_VERIFICATION)),
        decisive_failure_step_ids=("s1",),
    )
    assert result.has_decisive_failure
    assert not result.can_count_execution
    assert not result.can_count_reuse
    assert not result.can_count_verification
    assert result.pending_step_ids == ("s2",)
