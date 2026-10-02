from dataclasses import replace

import pytest

from aitest.domain.planning.plans import RunTier
from aitest.domain.review.reports import (
    BusinessOutcome,
    Coverage,
    DecisionFacts,
    DecisiveFailure,
    EvidenceGrade,
    FailureCandidate,
    ReviewGap,
    ReviewGapCode,
    SourceIdentityState,
    derive_decisive_failures,
    evaluate_review,
)
from aitest.interfaces.dto import decision_dto


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
    facts = replace(
        _complete_facts(coverage=_coverage(frozenset({"case-1"}), frozenset({"case-1"}))),
        coverage=coverage,
        decisive_failures=(failure,),
        assertion_basis_confirmed=coverage.selected,
    )

    result = evaluate_review(facts)

    assert result.business_outcome is BusinessOutcome.FAILED
    assert result.evidence_grade is EvidenceGrade.A
    assert result.decisive_failure_case_ids == {"case-1"}


def test_noncritical_grade_b_gap_does_not_change_business_pass() -> None:
    gap = ReviewGap(ReviewGapCode.NONCRITICAL_UNKNOWN, "A local noncritical fact is unknown.")
    result = evaluate_review(replace(_complete_facts(), noncritical_gaps=(gap,)))

    assert result.business_outcome is BusinessOutcome.PASSED
    assert result.evidence_grade is EvidenceGrade.B
    assert result.primary_gap is gap


def test_execution_reuse_may_exist_without_verified_case() -> None:
    coverage = Coverage(
        selected=frozenset({"case-1"}),
        required=frozenset({"case-1"}),
        executed=frozenset(),
        reused=frozenset({"case-1"}),
        verified=frozenset(),
        passed=frozenset(),
    )

    assert coverage.reused == {"case-1"}
    assert coverage.verified == frozenset()


def test_verified_failure_without_effective_h_is_rejected() -> None:
    coverage = _coverage(
        frozenset({"case-1"}),
        frozenset({"case-1"}),
        verified=frozenset({"case-1"}),
        passed=frozenset(),
    )

    with pytest.raises(ValueError, match="decisive failures H"):
        DecisionFacts(
            tier=RunTier.FULL,
            coverage=coverage,
            template_required=frozenset({"case-1"}),
            assertion_basis_confirmed=frozenset({"case-1"}),
            source_identity_state=SourceIdentityState.MATCHED,
            critical_paths_satisfied=True,
            required_evidence_valid=True,
            environment_evidence_complete=True,
        )


def test_failure_candidate_derives_h_only_when_all_guards_hold() -> None:
    effective = FailureCandidate(
        case_id="case-1",
        step_id="step-1",
        attempt_id="attempt-1",
        assertion_ref="assertion-1",
        basis_revision=1,
        evidence_refs=("evidence-1",),
        current_effective_attempt=True,
        necessary_assertion_failed=True,
        basis_confirmed=True,
        failure_verification_valid=True,
        source_identity_matched=True,
        dependencies_valid=True,
    )
    stale = replace(effective, case_id="case-2", current_effective_attempt=False)

    assert derive_decisive_failures((effective, stale)) == (
        DecisiveFailure(
            case_id="case-1",
            step_id="step-1",
            attempt_id="attempt-1",
            assertion_ref="assertion-1",
            basis_revision=1,
            evidence_refs=("evidence-1",),
        ),
    )


def test_dto_has_two_scope_summaries_and_exact_h_references() -> None:
    failing = DecisiveFailure(
        case_id="case-1",
        step_id="step-1",
        attempt_id="attempt-1",
        assertion_ref="assertion-1",
        basis_revision=1,
        evidence_refs=("evidence-1",),
    )
    coverage = _coverage(
        frozenset({"case-1"}),
        frozenset({"case-1"}),
        verified=frozenset({"case-1"}),
        passed=frozenset(),
    )
    facts = replace(
        _complete_facts(coverage=_coverage(frozenset({"case-1"}), frozenset({"case-1"}))),
        coverage=coverage,
        decisive_failures=(failing,),
        case_ids_revision="cases-7",
        source_commit="commit-7",
        snapshot_commit_id="snapshot-7",
        snapshot_cursor=42,
    )
    view = decision_dto(evaluate_review(facts))

    assert view.selected_summary.scope_kind == "selected"
    assert view.required_summary.scope_kind == "required"
    assert view.selected_summary.decisive_failure_count == 1
    assert view.selected_summary.decisive_failures[0].attempt_id == "attempt-1"
    assert view.selected_summary.case_ids_revision == "cases-7"
    assert view.snapshot_cursor == 42


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
