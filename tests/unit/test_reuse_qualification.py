"""An inherited verified step alone cannot establish legal whole-case reuse."""

from dataclasses import fields, replace

import pytest

from aitest.domain.execution.cases import aggregate_case_execution
from aitest.domain.execution.reuse import ReuseConditionKind, ReuseConditionState, ReuseIdentity
from aitest.domain.execution.runs import AttemptState
from tests.support.reuse_basis import verified_reuse_basis
from tests.unit.test_case_aggregation_integrity import completed


def test_inherited_verification_without_full_guard_is_history_only():
    result = aggregate_case_execution(
        case_id="case", required_step_ids=("s1",), steps=(completed(current=False),),
        decisive_failure_step_ids=("s1",),
    )
    assert not result.can_count_reuse
    assert not result.can_count_verification
    assert not result.has_decisive_failure
    assert result.pending_step_ids == ("s1",)


def aggregate(basis, steps=None):
    return aggregate_case_execution(
        case_id="case", required_step_ids=("s1",),
        steps=steps or (completed(current=False),), decisive_failure_step_ids=("s1",),
        reuse_basis=basis,
    )


@pytest.mark.parametrize("field", [field.name for field in fields(ReuseIdentity)])
def test_any_changed_source_or_target_identity_denies_reuse(field):
    basis = verified_reuse_basis()
    basis = replace(basis, target=replace(basis.target, **{field: "changed"}))
    assert field + "_changed" in basis.denial_reasons
    result = aggregate(basis)
    assert not result.can_count_reuse and not result.can_count_verification
    assert not result.has_decisive_failure
    assert result.pending_step_ids == ("s1",)


@pytest.mark.parametrize("field", [field.name for field in fields(ReuseIdentity)[2:]])
@pytest.mark.parametrize("side", ["source", "target", "both"])
def test_unknown_identity_does_not_match_even_when_both_missing(field, side):
    basis = verified_reuse_basis()
    for name in ("source", "target") if side == "both" else (side,):
        basis = replace(basis, **{name: replace(getattr(basis, name), **{field: None})})
    assert field + "_unverified" in basis.denial_reasons
    assert not aggregate(basis).can_count_reuse


@pytest.mark.parametrize("kind", list(ReuseConditionKind))
@pytest.mark.parametrize("state", [None, ReuseConditionState.INVALID, ReuseConditionState.UNKNOWN])
def test_each_current_guard_is_required_and_unknown_is_not_success(kind, state):
    basis = verified_reuse_basis()
    conditions = tuple(
        replace(item, state=state) if item.kind is kind else item
        for item in basis.conditions if item.kind is not kind or state is not None
    )
    basis = replace(basis, conditions=conditions)
    assert kind.value + "_unverified" in basis.denial_reasons
    assert not aggregate(basis).can_count_reuse


@pytest.mark.parametrize("mapping", [(), (("s2", "attempt-s1"),), (("s1", "wrong-attempt"),)])
def test_qualification_is_bound_to_exact_required_step_attempt_map(mapping):
    result = aggregate(replace(verified_reuse_basis(), source_attempt_by_step=mapping))
    assert not result.can_count_reuse


@pytest.mark.parametrize("state", list(AttemptState))
@pytest.mark.parametrize("optional", [False, True])
def test_any_new_attempt_denies_reuse_even_with_complete_guard(state, optional):
    inherited = completed(current=False)
    started = replace(completed("optional" if optional else "s1"), state=state)
    result = aggregate(verified_reuse_basis(), (inherited, started) if optional else (started,))
    assert not result.can_count_reuse
    if optional:
        assert not result.has_decisive_failure


@pytest.mark.parametrize(
    "change",
    [
        {"conditions": []}, {"conditions": (True,)},
        {"source_attempt_by_step": [["s1", "attempt-s1"]]},
        {"source_attempt_by_step": (("s1", "a"), ("s1", "b"))},
        {"source_attempt_by_step": (("s1", "a"), ("s2", "a"))},
        {"source_snapshot_digest": "claimed"}, {"source": True},
        {"source_run_id": ""}, {"source_snapshot_id": "\ud800"},
    ],
)
def test_invalid_or_mutable_basis_cannot_enter_the_qualification(change):
    with pytest.raises(ValueError):
        replace(verified_reuse_basis(), **change)


def test_repeated_condition_cannot_hide_an_invalid_observation():
    basis = verified_reuse_basis()
    with pytest.raises(ValueError):
        replace(basis, conditions=(*basis.conditions, basis.conditions[0]))


@pytest.mark.parametrize("value", [True, "verified", {}, 1])
def test_boolean_or_untyped_qualification_is_rejected(value):
    with pytest.raises(ValueError):
        aggregate(value)


def test_complete_legal_failed_reuse_preserves_failure_without_new_execution():
    result = aggregate(verified_reuse_basis())
    assert result.can_count_reuse and result.can_count_verification
    assert result.has_decisive_failure and not result.can_count_execution
    assert result.pending_step_ids == ()
