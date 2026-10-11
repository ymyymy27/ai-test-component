"""Whole-case counts cannot be obtained from coercion, duplicate attempts or missing facts."""

from dataclasses import replace

import pytest

from aitest.domain.execution.cases import aggregate_case_execution
from aitest.domain.execution.runs import AttemptState, CaptureCompleteness
from tests.unit.test_case_aggregation_integrity import completed


@pytest.mark.parametrize("current", [True, False])
def test_one_attempt_cannot_fill_two_steps_of_the_same_case(current):
    first = completed("s1", current)
    second = replace(completed("s2", current), attempt_id=first.attempt_id)
    with pytest.raises(ValueError):
        aggregate_case_execution(
            case_id="case", required_step_ids=("s1", "s2"), steps=(first, second)
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("from_current_run", "false"),
        ("from_current_run", 1),
        ("verification_valid", "false"),
        ("verification_valid", 1),
        ("state", "completed"),
        ("capture_completeness", "complete"),
        ("step_id", 1),
        ("attempt_id", 1),
    ],
)
def test_invalid_fact_types_are_rejected_before_any_aggregate(field, value):
    with pytest.raises(ValueError):
        aggregate_case_execution(
            case_id="case",
            required_step_ids=("s1",),
            steps=(replace(completed(), **{field: value}),),
        )


@pytest.mark.parametrize(
    "case,required", [("", ("s1",)), ("case", ("",)), ("case", (" ",)), ("case", (1,))]
)
def test_invalid_case_or_required_identity_is_not_a_countable_scope(case, required):
    with pytest.raises(ValueError):
        aggregate_case_execution(case_id=case, required_step_ids=required, steps=(completed(),))


@pytest.mark.parametrize(
    "change",
    [
        {"attempt_id": None},
        {"attempt_id": ""},
        {"attempt_id": " "},
        {"capture_completeness": CaptureCompleteness.PARTIAL},
        {"capture_completeness": CaptureCompleteness.GAP},
    ],
)
def test_noncountable_execution_still_lists_the_required_step(change):
    result = aggregate_case_execution(
        case_id="case", required_step_ids=("s1",), steps=(replace(completed(), **change),)
    )
    assert not result.can_count_execution and not result.can_count_verification
    assert result.pending_step_ids == ("s1",)


@pytest.mark.parametrize(
    "facts",
    [
        (),
        (replace(completed(), attempt_id=None),),
        (replace(completed(), state=AttemptState.INVALIDATED),),
        (replace(completed(), verification_valid=False),),
    ],
)
def test_failure_selector_cannot_create_a_failure_without_valid_actual_step(facts):
    result = aggregate_case_execution(
        case_id="case", required_step_ids=("s1",), steps=facts, decisive_failure_step_ids=("s1",)
    )
    assert not result.has_decisive_failure


def test_valid_failed_upstream_is_kept_while_other_required_step_is_pending():
    result = aggregate_case_execution(
        case_id="case",
        required_step_ids=("s1", "s2"),
        steps=(completed(),),
        decisive_failure_step_ids=("s1",),
    )
    assert result.has_decisive_failure and not result.can_count_execution
    assert result.pending_step_ids == ("s2",)


def test_revoked_inherited_failure_is_history_when_optional_attempt_has_started():
    result = aggregate_case_execution(
        case_id="case",
        required_step_ids=("s1",),
        steps=(
            completed(current=False),
            replace(completed("optional"), state=AttemptState.CANCELLED),
        ),
        decisive_failure_step_ids=("s1",),
    )
    assert not result.can_count_reuse and not result.has_decisive_failure
    assert result.pending_step_ids == ("s1",)


def test_partial_capture_does_not_hide_separately_valid_decisive_failure():
    result = aggregate_case_execution(
        case_id="case",
        required_step_ids=("s1",),
        steps=(replace(completed(), capture_completeness=CaptureCompleteness.PARTIAL),),
        decisive_failure_step_ids=("s1",),
    )
    assert result.has_decisive_failure and not result.can_count_execution
    assert result.pending_step_ids == ("s1",)
