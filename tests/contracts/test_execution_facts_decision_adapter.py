import json
from pathlib import Path

import pytest

from aitest.application.review.execution_decision_adapter import (
    AssertionOutcome,
    AssertionOutcomeFact,
    DecisionContextFacts,
    build_decision_facts,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.review.reports import BusinessOutcome, EvidenceGrade, evaluate_review

FIXTURE_DIR = Path("docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures")
FIXTURE_NAMES = (
    "success.json",
    "failure.json",
    "unknown.json",
    "quick.json",
    "timeout.json",
    "multistream.json",
    "non_utf8.json",
)


def _load(name: str) -> ExecutionFacts:
    return ExecutionFacts.model_validate_json((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_seven_execution_facts_fixtures_are_consumed_conservatively(name: str) -> None:
    facts = _load(name)
    decision_facts = build_decision_facts(facts)
    result = evaluate_review(decision_facts)

    assert decision_facts.coverage.selected == set(facts.run.selected_scope)
    assert decision_facts.coverage.required == set(facts.run.required_scope)
    assert decision_facts.snapshot_commit_id == facts.snapshot_commit_id
    assert decision_facts.snapshot_cursor == facts.snapshot_cursor
    assert decision_facts.coverage.verified == frozenset()
    assert decision_facts.coverage.passed == frozenset()
    assert result.decisive_failure_case_ids == frozenset()
    assert result.business_outcome is not BusinessOutcome.PASSED

    if name in {"success.json", "multistream.json", "non_utf8.json", "quick.json"}:
        assert decision_facts.coverage.executed == frozenset({"case-1"})
    else:
        assert decision_facts.coverage.executed == frozenset()

    if name == "timeout.json":
        assert decision_facts.pending_verification == frozenset({"case-1"})
        assert result.evidence_grade is EvidenceGrade.D
    if name == "unknown.json":
        assert decision_facts.pending_verification == frozenset({"case-1"})
        assert result.evidence_grade is EvidenceGrade.D
    if name == "quick.json":
        assert result.evidence_grade is None


def _passed_outcome() -> AssertionOutcomeFact:
    return AssertionOutcomeFact(
        case_id="case-1",
        step_id="step-1",
        attempt_id="attempt-1",
        assertion_ref="assertion-1",
        basis_revision=1,
        outcome=AssertionOutcome.PASSED,
        evidence_refs=("evidence-1",),
        case_assertions_complete=True,
        necessary_assertion=True,
        basis_confirmed=True,
        verification_valid=True,
        dependencies_valid=True,
    )


def _failed_outcome() -> AssertionOutcomeFact:
    return AssertionOutcomeFact(
        case_id="case-1",
        step_id="step-1",
        attempt_id="attempt-1",
        assertion_ref="assertion-1",
        basis_revision=1,
        outcome=AssertionOutcome.FAILED,
        evidence_refs=("evidence-1",),
        case_assertions_complete=True,
        necessary_assertion=True,
        basis_confirmed=True,
        verification_valid=True,
        dependencies_valid=True,
    )


def _context() -> DecisionContextFacts:
    return DecisionContextFacts(
        template_required_case_ids=frozenset({"case-1"}),
        policy_version="policy-1",
    )


def test_explicit_passed_assertion_allows_full_pass() -> None:
    facts = build_decision_facts(
        _load("success.json"),
        assertion_outcomes=(_passed_outcome(),),
        context=_context(),
    )
    result = evaluate_review(facts)

    assert facts.coverage.executed == frozenset({"case-1"})
    assert facts.coverage.verified == frozenset({"case-1"})
    assert facts.coverage.passed == frozenset({"case-1"})
    assert result.business_outcome is BusinessOutcome.PASSED
    assert result.evidence_grade is EvidenceGrade.A


def test_effective_failure_derives_h_and_preserves_f_subset_h() -> None:
    decision_facts = build_decision_facts(
        _load("success.json"),
        assertion_outcomes=(_failed_outcome(),),
        context=_context(),
    )
    result = evaluate_review(decision_facts)

    assert result.business_outcome is BusinessOutcome.FAILED
    assert result.decisive_failure_case_ids == frozenset({"case-1"})
    assert decision_facts.coverage.failed <= result.decisive_failure_case_ids
    assert decision_facts.coverage.passed == frozenset()


def test_stale_attempt_is_not_an_effective_failure() -> None:
    stale = AssertionOutcomeFact(
        case_id="case-1",
        step_id="step-1",
        attempt_id="old-attempt",
        assertion_ref="assertion-1",
        basis_revision=1,
        outcome=AssertionOutcome.FAILED,
        evidence_refs=("evidence-1",),
        case_assertions_complete=True,
        necessary_assertion=True,
        basis_confirmed=True,
        verification_valid=True,
        dependencies_valid=True,
    )

    result = evaluate_review(
        build_decision_facts(_load("success.json"), assertion_outcomes=(stale,))
    )

    assert result.business_outcome is BusinessOutcome.INCOMPLETE
    assert result.decisive_failure_case_ids == frozenset()


def test_missing_or_invalid_evidence_is_not_an_effective_failure() -> None:
    missing = AssertionOutcomeFact(
        case_id="case-1",
        step_id="step-1",
        attempt_id="attempt-1",
        assertion_ref="assertion-1",
        basis_revision=1,
        outcome=AssertionOutcome.FAILED,
        evidence_refs=("missing-evidence",),
        case_assertions_complete=True,
        necessary_assertion=True,
        basis_confirmed=True,
        verification_valid=True,
        dependencies_valid=True,
    )

    result = evaluate_review(
        build_decision_facts(_load("success.json"), assertion_outcomes=(missing,))
    )

    assert result.decisive_failure_case_ids == frozenset()


def test_execution_evidence_level_does_not_become_run_grade_or_pass() -> None:
    raw = json.loads((FIXTURE_DIR / "success.json").read_text(encoding="utf-8"))
    raw["run"]["evidence_level"] = "full_link"
    facts = ExecutionFacts.model_validate(raw)

    result = evaluate_review(build_decision_facts(facts))

    assert result.business_outcome is BusinessOutcome.INCOMPLETE
    assert result.evidence_grade is not EvidenceGrade.A
