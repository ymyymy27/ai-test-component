import pytest

from aitest.application.connectivity import retry_delay
from aitest.application.planning.regression import affected_modules
from aitest.domain.planning.plans import (
    AcceptanceScope,
    ConclusionCeiling,
    RunDriver,
    RunTier,
    conclusion_ceiling_for,
    narrow_driver,
)
from aitest.domain.review.reports import Coverage
from aitest.interfaces.dto import coverage_dto


def test_new_execution_is_not_inflated_by_reuse() -> None:
    selected = frozenset(str(i) for i in range(12))
    reused = frozenset({"10", "11"})
    dto = coverage_dto(Coverage(selected, selected, selected - reused, reused, selected, selected))
    assert dto.executed_count == 10
    assert dto.reused_count == 2
    assert dto.verified_count == 12
    assert dto.required_verified_count == 12


def test_supplemental_failure_and_unselected_required_cases() -> None:
    selected = frozenset({"extra-1", "extra-2", "extra-3"})
    value = Coverage(
        selected,
        frozenset({"required"}),
        selected,
        frozenset(),
        selected,
        frozenset({"extra-1", "extra-2"}),
    )
    assert value.failed == {"extra-3"}
    assert coverage_dto(value).required_verified_count == 0


def test_unconfirmed_basis_is_executed_but_not_verified() -> None:
    value = Coverage(
        frozenset({"a", "b"}),
        frozenset({"a", "b"}),
        frozenset({"a", "b"}),
        frozenset(),
        frozenset({"a"}),
        frozenset({"a"}),
    )
    assert value.unverified == {"b"}
    assert coverage_dto(value).executed_count == 2


def test_reuse_cannot_overlap_new_attempt() -> None:
    with pytest.raises(ValueError, match="disjoint"):
        Coverage(*[frozenset({"a"})] * 6)


def test_full_and_driver_guards() -> None:
    scope = AcceptanceScope("scope", 1, "library", frozenset({"a", "b"}), frozenset({"a"}))
    with pytest.raises(ValueError, match="all required"):
        scope.validate_selection(RunTier.FULL, frozenset({"a"}))
    with pytest.raises(ValueError, match="empty"):
        scope.validate_selection(RunTier.ON_DEMAND, frozenset())
    scope.validate_selection(RunTier.QUICK, frozenset({"a"}))
    with pytest.raises(ValueError, match="expand"):
        narrow_driver(RunDriver.STEPWISE, RunDriver.PLANNED)


def test_conclusion_ceiling_is_derived_only_from_tier() -> None:
    assert conclusion_ceiling_for(RunTier.QUICK) is ConclusionCeiling.PARTIAL
    assert conclusion_ceiling_for(RunTier.ON_DEMAND) is ConclusionCeiling.PARTIAL
    assert conclusion_ceiling_for(RunTier.FULL) is ConclusionCeiling.PASSABLE


def test_dependency_cycle_terminates_and_keeps_changed_module() -> None:
    assert affected_modules(frozenset({"a"}), {"a": ("b",), "b": ("a",), "c": ("b",)}) == {
        "a",
        "b",
        "c",
    }
    assert affected_modules(frozenset({"solo"}), {}) == {"solo"}


def test_transport_retry_never_replays_unproven_side_effects() -> None:
    assert [retry_delay(i, read_only=True, idempotency_proven=False) for i in range(4)] == [
        2,
        5,
        10,
        None,
    ]
    assert retry_delay(0, read_only=False, idempotency_proven=False) is None
