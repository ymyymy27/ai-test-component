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
from aitest.domain.review.reports import (
    BusinessOutcome,
    EvidenceGrade,
    SourceIdentityState,
    evaluate_review,
)

FIXTURE_DIR = Path("docs/接口对接/已完成/CD-001-ExecutionFacts/fixtures")
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


# --------------------------------------------------------------- D-10 守卫
# 以下三组用同一 success 夹具的最小变体，把 D-10 已复现的三个错误接受
# 变成真实拒绝；变体只改输入事实，不改断言口径。


def _variant(mutate) -> ExecutionFacts:
    raw = json.loads((FIXTURE_DIR / "success.json").read_text(encoding="utf-8"))
    mutate(raw)
    return ExecutionFacts.model_validate(raw)


def test_conflicting_required_evidence_is_not_valid_and_never_passes() -> None:
    def mutate(raw: dict) -> None:
        raw["steps"][0]["evidence_requirement_ids"] = ["required-stderr"]
        raw["evidence_refs"][1]["integrity"] = "conflict"

    facts = _variant(mutate)
    decision_facts = build_decision_facts(
        facts, assertion_outcomes=(_passed_outcome(),), context=_context()
    )
    result = evaluate_review(decision_facts)

    assert decision_facts.required_evidence_valid is False
    assert result.business_outcome is not BusinessOutcome.PASSED
    assert result.evidence_grade is not EvidenceGrade.A


def test_referenced_but_incomplete_required_evidence_is_rejected() -> None:
    """引用存在但完整性不是 complete：不得算满足必需证据。"""

    def mutate(raw: dict) -> None:
        raw["steps"][0]["evidence_requirement_ids"] = ["evidence-1", "evidence-stderr"]
        raw["evidence_refs"][1]["integrity"] = "partial"

    decision_facts = build_decision_facts(
        _variant(mutate), assertion_outcomes=(_passed_outcome(),), context=_context()
    )

    assert decision_facts.required_evidence_valid is False
    assert evaluate_review(decision_facts).business_outcome is not BusinessOutcome.PASSED


def test_required_evidence_requires_current_completed_attempt() -> None:
    def mutate(raw: dict) -> None:
        raw["steps"][0]["evidence_requirement_ids"] = ["evidence-1"]
        raw["attempts"][0]["state"] = "running"

    decision_facts = build_decision_facts(
        _variant(mutate), assertion_outcomes=(_passed_outcome(),), context=_context()
    )

    assert decision_facts.required_evidence_valid is False


def test_same_source_unverified_never_counts_as_matched() -> None:
    def mutate(raw: dict) -> None:
        extra = json.loads(json.dumps(raw["source_verifications"][0]))
        extra["verification_id"] = "source-verify-unverified"
        extra["state"] = "unverified"
        extra["gap_ids"] = ["import_outside_materialized_source"]
        raw["source_verifications"].append(extra)

    decision_facts = build_decision_facts(
        _variant(mutate), assertion_outcomes=(_passed_outcome(),), context=_context()
    )
    result = evaluate_review(decision_facts)

    assert decision_facts.source_identity_state is SourceIdentityState.UNVERIFIED
    assert decision_facts.source_commit is None
    assert result.business_outcome is not BusinessOutcome.PASSED
    assert result.evidence_grade is not EvidenceGrade.A


def test_verified_source_with_gaps_is_not_matched() -> None:
    def mutate(raw: dict) -> None:
        raw["source_verifications"][0]["gap_ids"] = ["actual_entry_missing"]

    decision_facts = build_decision_facts(
        _variant(mutate), assertion_outcomes=(_passed_outcome(),), context=_context()
    )

    assert decision_facts.source_identity_state is SourceIdentityState.UNVERIFIED


def test_non_current_attempt_is_not_counted_as_executed() -> None:
    def mutate(raw: dict) -> None:
        raw["attempts"][0]["is_current"] = False

    decision_facts = build_decision_facts(
        _variant(mutate), assertion_outcomes=(_passed_outcome(),), context=_context()
    )
    result = evaluate_review(decision_facts)

    assert decision_facts.coverage.executed == frozenset()
    assert decision_facts.coverage.verified == frozenset()
    assert result.business_outcome is BusinessOutcome.INCOMPLETE
    assert result.evidence_grade is not EvidenceGrade.A


def test_mismatched_source_still_wins_over_other_verifications() -> None:
    def mutate(raw: dict) -> None:
        extra = json.loads(json.dumps(raw["source_verifications"][0]))
        extra["verification_id"] = "source-verify-mismatch"
        extra["state"] = "mismatch"
        raw["source_verifications"].append(extra)

    decision_facts = build_decision_facts(_variant(mutate))

    assert decision_facts.source_identity_state is SourceIdentityState.MISMATCHED


@pytest.mark.parametrize("unprovable", [False, True])
def test_new_attempt_can_replace_only_provably_historic_source_gaps(unprovable):
    def mutate(raw):
        current = raw["attempts"][0]
        old = {**current, "attempt_id": "old-attempt", "is_current": False}
        raw["attempts"].append(old)
        evidence = {**raw["evidence_refs"][0], "evidence_id": "old-source-evidence",
                    "attempt_id": "old-attempt", "evidence_kind": "source_check"}
        raw["evidence_refs"].append(evidence)
        historical = {**raw["source_verifications"][0], "verification_id": "old-source",
                      "state": "unverified", "gap_ids": ["actual_source_capture_incomplete"],
                      "evidence_refs": ["missing" if unprovable else "old-source-evidence"]}
        raw["source_verifications"].append(historical)

    facts = _variant(mutate)
    result = build_decision_facts(facts)
    assert result.source_identity_state is (
        SourceIdentityState.UNVERIFIED if unprovable else SourceIdentityState.MATCHED
    )
    assert any(item.verification_id == "old-source" for item in facts.source_verifications)
