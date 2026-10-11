"""Outdated dependency facts propagate through every intermediate attempt state."""

from dataclasses import replace

import pytest

from aitest.domain.execution.dependencies import (
    CaseReuseBasis,
    invalidate_downstream_attempts,
    invalidate_reuse_bases,
)
from aitest.domain.execution.runs import AttemptState, ConsumedCondition, ConsumedOutput
from tests.unit.test_serial_runner import _attempt, _plan_revision


@pytest.mark.parametrize("middle_state", list(AttemptState))
@pytest.mark.parametrize("condition", [False, True])
def test_terminal_and_previously_outdated_nodes_do_not_cut_the_actual_dependency_chain(
    middle_state, condition
):
    output = (ConsumedOutput("upstream", "sha256:same", "value"),)
    conditions = (ConsumedCondition("upstream", "condition", "sha256:same"),)
    middle = replace(
        _attempt(),
        attempt_id="middle",
        step_id="middle-step",
        state=middle_state,
        consumed_outputs=() if condition else output,
        consumed_conditions=conditions if condition else (),
    )
    downstream = replace(
        _attempt(),
        attempt_id="downstream",
        step_id="downstream-step",
        state=AttemptState.COMPLETED,
        consumed_outputs=(ConsumedOutput("middle", "sha256:same", "value"),),
    )
    independent = replace(
        _attempt(),
        attempt_id="independent",
        step_id="independent-step",
        state=AttemptState.COMPLETED,
        consumed_outputs=(ConsumedOutput("other", "sha256:same", "value"),),
    )
    facts = (downstream, independent, middle)
    invalidations = invalidate_downstream_attempts(
        facts,
        previous_plan_revision=_plan_revision(),
        current_plan_revision=_plan_revision(),
        affected_upstream_attempt_ids=("upstream",),
    )
    assert [item.attempt.attempt_id for item in invalidations] == ["downstream", "middle"]
    assert all(item.attempt.state is AttemptState.INVALIDATED for item in invalidations)
    assert invalidations[0].upstream_attempt_ids == ("middle",)
    assert invalidations[1].upstream_attempt_ids == ("upstream",)
    assert all(item.reason == "upstream_attempt_replaced" for item in invalidations)
    assert independent.state is AttemptState.COMPLETED
    assert downstream.state is AttemptState.COMPLETED
    assert middle.state is middle_state


def test_duplicate_attempt_facts_cannot_hide_a_dependency_by_last_value_wins():
    original = _attempt()
    changed = replace(original, consumed_outputs=(ConsumedOutput("upstream", "sha256:a", "value"),))
    with pytest.raises(ValueError, match="unique identities"):
        invalidate_downstream_attempts(
            (changed, original),
            previous_plan_revision=_plan_revision(),
            current_plan_revision=_plan_revision(),
            affected_upstream_attempt_ids=("upstream",),
        )


def test_self_consumption_is_rejected_instead_of_becoming_a_usable_dependency_fact():
    attempt = replace(
        _attempt(), consumed_conditions=(ConsumedCondition("attempt-1", "condition", "sha256:a"),)
    )
    with pytest.raises(ValueError, match="own result"):
        invalidate_downstream_attempts(
            (attempt,),
            previous_plan_revision=_plan_revision(),
            current_plan_revision=_plan_revision(),
            affected_upstream_attempt_ids=("attempt-1",),
        )


def test_duplicate_reuse_case_identity_cannot_silently_choose_one_basis():
    with pytest.raises(ValueError, match="unique case identities"):
        invalidate_reuse_bases(
            (CaseReuseBasis("case", ("old",)), CaseReuseBasis("case", ("unrelated",))),
            affected_upstream_attempt_ids=("old",),
        )
