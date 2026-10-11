"""Consume C ExecutionFacts into the unique D DecisionFacts contract.

Execution completion is not business verification. V/P/F/H can only become
non-empty from explicit D-side AssertionOutcomeFact values bound to the current
Attempt and valid saved evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aitest.contracts.execution_facts import (
    AttemptStateFact,
    CaptureCompletenessFact,
    EvidenceIntegrityFact,
    ExecutionFacts,
    MockVerificationStateFact,
    ProjectionStateFact,
    RedactionStateFact,
    SourceVerificationStateFact,
    StepFact,
    StepStateFact,
    VerificationObservationFact,
)
from aitest.domain.planning.plans import RunTier
from aitest.domain.review.reports import (
    Coverage,
    DecisionFacts,
    FailureCandidate,
    ReviewGap,
    SourceIdentityState,
)


class AssertionOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AssertionOutcomeFact:
    case_id: str
    step_id: str
    attempt_id: str
    assertion_ref: str
    basis_revision: int
    outcome: AssertionOutcome
    evidence_refs: tuple[str, ...]
    case_assertions_complete: bool
    necessary_assertion: bool
    basis_confirmed: bool
    verification_valid: bool
    dependencies_valid: bool

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (self.case_id, self.step_id, self.attempt_id, self.assertion_ref)
        ):
            raise ValueError("assertion identity must not be empty")
        if self.basis_revision < 1:
            raise ValueError("basis_revision must be positive")
        if not self.evidence_refs or any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("assertion outcome requires valid evidence references")


@dataclass(frozen=True, slots=True)
class DecisionContextFacts:
    template_required_case_ids: frozenset[str] = frozenset()
    assertion_basis_confirmed_case_ids: frozenset[str] = frozenset()
    required_critical_chain_item_ids: frozenset[str] = frozenset()
    environment_evidence_complete: bool = True
    blocking_issue_ids: frozenset[str] = frozenset()
    noncritical_gaps: tuple[ReviewGap, ...] = ()
    policy_version: str = "1.0"
    case_ids_revision: str | None = None

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be empty")


def _source_state(facts: ExecutionFacts) -> SourceIdentityState:
    states = {item.state for item in facts.source_verifications}
    if SourceVerificationStateFact.MISMATCH in states:
        return SourceIdentityState.MISMATCHED
    if SourceVerificationStateFact.VERIFIED in states:
        return SourceIdentityState.MATCHED
    return SourceIdentityState.UNVERIFIED


def _evidence_valid(facts: ExecutionFacts, refs: tuple[str, ...], attempt_id: str) -> bool:
    by_id = {item.evidence_id: item for item in facts.evidence_refs}
    if not refs:
        return False
    for ref in refs:
        item = by_id.get(ref)
        if item is None or item.attempt_id != attempt_id:
            return False
        if (
            item.integrity is not EvidenceIntegrityFact.COMPLETE
            or item.redaction_state is RedactionStateFact.BLOCKED
            or item.projection_state is ProjectionStateFact.BLOCKED
            or item.gap_ids
        ):
            return False
    return True


def _assertion_valid(facts: ExecutionFacts, item: AssertionOutcomeFact) -> bool:
    steps = {step.step_id: step for step in facts.steps}
    attempts = {attempt.attempt_id: attempt for attempt in facts.attempts}
    step = steps.get(item.step_id)
    attempt = attempts.get(item.attempt_id)
    if step is None or attempt is None or step.case_id != item.case_id:
        return False
    if facts.current_attempt_by_step.get(step.step_id, step.current_attempt_id) != item.attempt_id:
        return False
    if not attempt.is_current or attempt.state is not AttemptStateFact.COMPLETED:
        return False
    if not item.basis_confirmed or not item.verification_valid or not item.dependencies_valid:
        return False
    if any(
        invalidation.affected_step_id == step.step_id
        or invalidation.affected_attempt_id == item.attempt_id
        for invalidation in facts.dependency_invalidations
    ):
        return False
    return _evidence_valid(facts, item.evidence_refs, item.attempt_id)


def _assertion_sets(
    facts: ExecutionFacts,
    outcomes: tuple[AssertionOutcomeFact, ...],
) -> tuple[frozenset[str], frozenset[str], tuple[FailureCandidate, ...]]:
    passed: set[str] = set()
    failed_v: set[str] = set()
    candidates: list[FailureCandidate] = []
    for item in outcomes:
        if not _assertion_valid(facts, item):
            continue
        if item.outcome is AssertionOutcome.PASSED and item.case_assertions_complete:
            passed.add(item.case_id)
        if item.outcome is AssertionOutcome.FAILED and item.necessary_assertion:
            candidates.append(
                FailureCandidate(
                    case_id=item.case_id,
                    step_id=item.step_id,
                    attempt_id=item.attempt_id,
                    assertion_ref=item.assertion_ref,
                    basis_revision=item.basis_revision,
                    evidence_refs=item.evidence_refs,
                    current_effective_attempt=True,
                    necessary_assertion_failed=True,
                    basis_confirmed=True,
                    failure_verification_valid=True,
                    source_identity_matched=True,
                    dependencies_valid=True,
                )
            )
            if item.case_assertions_complete:
                failed_v.add(item.case_id)
    decisive = {item.case_id for item in candidates}
    passed -= decisive
    failed_v -= passed
    return frozenset(passed), frozenset(passed | failed_v), tuple(candidates)


def _case_steps(facts: ExecutionFacts, case_id: str) -> tuple[StepFact, ...]:
    return tuple(step for step in facts.steps if step.case_id == case_id and step.required_for_case)


def _case_executed(facts: ExecutionFacts, case_id: str) -> bool:
    attempts = {item.attempt_id: item for item in facts.attempts}
    steps = _case_steps(facts, case_id)
    if not steps:
        return False
    for step in steps:
        attempt_id = facts.current_attempt_by_step.get(step.step_id, step.current_attempt_id)
        attempt = attempts.get(attempt_id) if attempt_id else None
        if attempt is None or attempt.state is not AttemptStateFact.COMPLETED:
            return False
        if attempt.capture_completeness is not CaptureCompletenessFact.COMPLETE:
            return False
        if any(
            invalidation.affected_step_id == step.step_id
            or invalidation.affected_attempt_id == attempt.attempt_id
            for invalidation in facts.dependency_invalidations
        ):
            return False
        if step.evidence_requirement_ids and not any(
            evidence.attempt_id == attempt.attempt_id for evidence in facts.evidence_refs
        ):
            return False
    return True


def _case_pending(facts: ExecutionFacts, case_id: str) -> bool:
    attempts = {item.attempt_id: item for item in facts.attempts}
    for step in _case_steps(facts, case_id):
        if step.state is StepStateFact.PENDING_VERIFICATION:
            return True
        attempt_id = facts.current_attempt_by_step.get(step.step_id, step.current_attempt_id)
        attempt = attempts.get(attempt_id) if attempt_id else None
        if attempt is not None and attempt.state is AttemptStateFact.PENDING_VERIFICATION:
            return True
    return False


def _required_evidence_valid(facts: ExecutionFacts, required: frozenset[str]) -> bool:
    attempts = {item.attempt_id: item for item in facts.attempts}
    for case_id in required:
        for step in _case_steps(facts, case_id):
            attempt_id = facts.current_attempt_by_step.get(step.step_id, step.current_attempt_id)
            attempt = attempts.get(attempt_id) if attempt_id else None
            if attempt is None:
                return False
            if step.evidence_requirement_ids and not any(
                evidence.attempt_id == attempt.attempt_id for evidence in facts.evidence_refs
            ):
                return False
            if any(gap.critical and gap.subject_ref == attempt.attempt_id for gap in facts.gaps):
                return False
    return True


def _critical_paths_satisfied(facts: ExecutionFacts, required_ids: frozenset[str]) -> bool:
    if not required_ids:
        return True
    covered = {
        item_id
        for item in facts.verifications
        if item.observation is VerificationObservationFact.MATCHED and not item.gap_ids
        for item_id in item.covers_critical_chain_item_ids
    }
    return required_ids <= covered


def build_decision_facts(
    facts: ExecutionFacts,
    *,
    assertion_outcomes: tuple[AssertionOutcomeFact, ...] = (),
    context: DecisionContextFacts | None = None,
) -> DecisionFacts:
    """Map one committed snapshot without inferring pass from execution state."""

    context = context or DecisionContextFacts()
    selected = frozenset(facts.run.selected_scope)
    required = frozenset(facts.run.required_scope)
    executed = frozenset(case for case in selected if _case_executed(facts, case))
    pending = frozenset(case for case in selected if _case_pending(facts, case))
    outdated = frozenset(
        case
        for case in selected
        if any(
            invalidation.affected_step_id in {step.step_id for step in _case_steps(facts, case)}
            for invalidation in facts.dependency_invalidations
        )
    )
    passed, verified, candidates = _assertion_sets(facts, assertion_outcomes)
    coverage = Coverage(
        selected=selected,
        required=required,
        executed=executed,
        reused=frozenset(),
        verified=verified,
        passed=passed,
    )
    source_state = _source_state(facts)
    source_commit = None
    if source_state is SourceIdentityState.MATCHED:
        source_commit = next(
            (
                item.observed_source_digest
                for item in facts.source_verifications
                if item.state is SourceVerificationStateFact.VERIFIED
            ),
            None,
        )
    confirmed = set(context.assertion_basis_confirmed_case_ids)
    confirmed.update(
        item.case_id
        for item in assertion_outcomes
        if _assertion_valid(facts, item) and item.basis_confirmed
    )
    return DecisionFacts(
        tier=RunTier(facts.run.tier.value),
        coverage=coverage,
        template_required=context.template_required_case_ids,
        pending_verification=pending,
        outdated_basis=outdated,
        required_unresolved=required - executed - pending,
        required_pending=required & pending,
        assertion_basis_confirmed=frozenset(confirmed),
        source_identity_state=source_state,
        critical_paths_satisfied=_critical_paths_satisfied(
            facts, context.required_critical_chain_item_ids
        ),
        required_evidence_valid=_required_evidence_valid(facts, required),
        environment_evidence_complete=context.environment_evidence_complete,
        critical_unknowns=bool(
            facts.coverage.unknown_step_ids
            or any(item.requires_verification for item in facts.unknowns)
        ),
        critical_mock=any(
            item.verification_state is not MockVerificationStateFact.VERIFIED
            for item in facts.mock_declarations
        ),
        blocking_issue_ids=context.blocking_issue_ids,
        noncritical_gaps=context.noncritical_gaps,
        failure_candidates=candidates,
        case_ids_revision=context.case_ids_revision or facts.plan_revision.digest,
        source_commit=source_commit,
        snapshot_commit_id=facts.snapshot_commit_id,
        snapshot_cursor=facts.snapshot_cursor,
        has_applicable_checks=bool(selected or required),
        policy_version=context.policy_version,
    )


__all__ = [
    "AssertionOutcome",
    "AssertionOutcomeFact",
    "DecisionContextFacts",
    "build_decision_facts",
]
