from dataclasses import replace

import pytest

from aitest.domain.planning.plans import RunTier
from aitest.domain.review.reports import (
    BusinessOutcome,
    Coverage,
    DecisionFacts,
    DecisiveFailure,
    EvidenceGrade,
    ReviewGap,
    ReviewGapCode,
    SourceIdentityState,
    evaluate_review,
)


def _coverage(
    selected: frozenset[str],
    required: frozenset[str],
    *,
    executed: frozenset[str] | None = None,
    reused: frozenset[str] = frozenset(),
    verified: frozenset[str] | None = None,
    passed: frozenset[str] | None = None,
) -> Coverage:
    executed_values = selected if executed is None else executed
    verified_values = selected if verified is None else verified
    passed_values = verified_values if passed is None else passed
    return Coverage(
        selected=selected,
        required=required,
        executed=executed_values,
        reused=reused,
        verified=verified_values,
        passed=passed_values,
    )


def _complete_facts(
    *,
    tier: RunTier = RunTier.FULL,
    coverage: Coverage | None = None,
) -> DecisionFacts:
    values = coverage or _coverage(frozenset({"case-1"}), frozenset({"case-1"}))
    return DecisionFacts(
        tier=tier,
        coverage=values,
        template_required=values.required,
        assertion_basis_confirmed=values.selected,
        source_identity_state=SourceIdentityState.MATCHED,
        critical_paths_satisfied=True,
        required_evidence_valid=True,
        environment_evidence_complete=True,
    )


def test_complete_full_run_passes_with_grade_a() -> None:
    result = evaluate_review(_complete_facts())

    assert result.business_outcome is BusinessOutcome.PASSED
    assert result.evidence_grade is EvidenceGrade.A
    assert result.primary_gap is None
    assert result.is_full_pass


def test_valid_decisive_failure_wins_over_incomplete_scope() -> None:
    coverage = _coverage(
        frozenset({"case-1", "case-2"}),
        frozenset({"case-1", "case-2"}),
        verified=frozenset({"case-1"}),
        passed=frozenset(),
    )
    failure = DecisiveFailure(
        case_id="case-1",
        step_id="step-1",
        attempt_id="attempt-1",
        assertion_ref="assertion-1",
        basis_revision=1,
        evidence_refs=("evidence-1",),
    )

    result = evaluate_review(
        replace(_complete_facts(coverage=coverage), decisive_failures=(failure,))
    )

    assert result.business_outcome is BusinessOutcome.FAILED
    assert result.evidence_grade is EvidenceGrade.A
    assert result.decisive_failure_case_ids == {"case-1"}


def test_unconfirmed_assertion_basis_is_incomplete_and_grade_b() -> None:
    coverage = _coverage(
        frozenset({"case-1", "case-2"}),
        frozenset({"case-1", "case-2"}),
        verified=frozenset({"case-1"}),
        passed=frozenset({"case-1"}),
    )
    facts = replace(
        _complete_facts(coverage=coverage),
        assertion_basis_confirmed=frozenset({"case-1"}),
    )

    result = evaluate_review(facts)

    assert result.business_outcome is BusinessOutcome.INCOMPLETE
    assert result.evidence_grade is EvidenceGrade.B
    assert result.primary_gap is not None
    assert result.primary_gap.code is ReviewGapCode.ASSERTION_BASIS_UNCONFIRMED


def test_quick_run_never_gets_evidence_grade_or_full_pass() -> None:
    result = evaluate_review(_complete_facts(tier=RunTier.QUICK))

    assert result.business_outcome is BusinessOutcome.INCOMPLETE
    assert result.evidence_grade is None


def test_missing_critical_evidence_is_grade_c() -> None:
    facts = replace(_complete_facts(), critical_paths_satisfied=False)

    result = evaluate_review(facts)

    assert result.business_outcome is BusinessOutcome.INCOMPLETE
    assert result.evidence_grade is EvidenceGrade.C
    assert result.primary_gap is not None
    assert result.primary_gap.code is ReviewGapCode.CRITICAL_PATH_MISSING


def test_source_mismatch_and_no_execution_basis_are_grade_d() -> None:
    coverage = _coverage(frozenset(), frozenset(), executed=frozenset())
    facts = replace(
        _complete_facts(coverage=coverage),
        source_identity_state=SourceIdentityState.MISMATCHED,
    )

    result = evaluate_review(facts)

    assert result.business_outcome is BusinessOutcome.INCOMPLETE
    assert result.evidence_grade is EvidenceGrade.D
    assert result.primary_gap is not None
    assert result.primary_gap.code is ReviewGapCode.SOURCE_IDENTITY_MISMATCH


def test_no_applicable_checks_is_not_applicable() -> None:
    coverage = _coverage(frozenset(), frozenset(), executed=frozenset())
    facts = replace(_complete_facts(coverage=coverage), has_applicable_checks=False)

    result = evaluate_review(facts)

    assert result.business_outcome is BusinessOutcome.NOT_APPLICABLE
    assert result.evidence_grade is EvidenceGrade.A


def test_unknown_gap_is_conservatively_grade_c() -> None:
    gap = ReviewGap(ReviewGapCode.UNCLASSIFIED_GAP, "No matching rule was found.")
    facts = replace(_complete_facts(), noncritical_gaps=(gap,))

    result = evaluate_review(facts)

    assert result.evidence_grade is EvidenceGrade.C


def test_decisive_failure_must_belong_to_selected_scope() -> None:
    failure = DecisiveFailure(
        case_id="outside",
        step_id="step-1",
        attempt_id="attempt-1",
        assertion_ref="assertion-1",
        basis_revision=1,
        evidence_refs=("evidence-1",),
    )

    with pytest.raises(ValueError, match="selected scope"):
        replace(_complete_facts(), decisive_failures=(failure,))
