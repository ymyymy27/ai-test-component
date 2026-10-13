"""Consume C ExecutionFacts into the unique D DecisionFacts contract.

Execution completion is not business verification. V/P/F/H can only become
non-empty from explicit D-side AssertionOutcomeFact values bound to the current
Attempt and valid saved evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from aitest.application.review.reuse_consumption import ReuseOutcome
from aitest.contracts.execution_facts import (
    AttemptFact,
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
    VerificationFact,
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
    """来源身份状态：**同一快照内每条来源核验都必须为 verified**（D-10）。

    既有实现用 ``VERIFIED in states`` 判定 matched，于是"同源 verified + unverified"
    会被当成匹配；核验带 gap（``gap_ids`` 非空）同样被忽略。这会把"至少有一条通过"
    冒充"来源已核实"。正确口径是集合级：任一 mismatch 即 MISMATCHED；只要存在
    非 verified（unverified/blocked/unknown）或带缺口的核验，或根本没有任何核验，
    就只能 UNVERIFIED——未核实不得升格为已匹配。
    """

    if not facts.source_verifications:
        return SourceIdentityState.UNVERIFIED
    if any(
        item.state is SourceVerificationStateFact.MISMATCH for item in facts.source_verifications
    ):
        return SourceIdentityState.MISMATCHED
    if any(
        item.state is not SourceVerificationStateFact.VERIFIED or item.gap_ids
        for item in facts.source_verifications
    ):
        return SourceIdentityState.UNVERIFIED
    return SourceIdentityState.MATCHED


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


def _current_attempt(
    facts: ExecutionFacts, step: StepFact, attempts: dict[str, AttemptFact]
) -> AttemptFact | None:
    """取该步骤的**当前** Attempt 事实；不是当前 Attempt 就返回 None（D-10）。

    `current_attempt_by_step` 缺失时会回落到 `step.current_attempt_id`，而两者都可能
    指向一条 `is_current=False` 的历史尝试。执行计数 E 只认当前尝试：历史尝试的事实
    可以保留用于追溯，但不能被当成"本轮整用例执行"。
    """
    attempt_id = facts.current_attempt_by_step.get(step.step_id, step.current_attempt_id)
    attempt = attempts.get(attempt_id) if attempt_id else None
    if attempt is None or not attempt.is_current:
        return None
    return attempt


def _case_executed(facts: ExecutionFacts, case_id: str) -> bool:
    attempts = {item.attempt_id: item for item in facts.attempts}
    steps = _case_steps(facts, case_id)
    if not steps:
        return False
    for step in steps:
        attempt = _current_attempt(facts, step, attempts)
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
        attempt = _current_attempt(facts, step, attempts)
        if attempt is not None and attempt.state is AttemptStateFact.PENDING_VERIFICATION:
            return True
    return False


def _required_evidence_valid(facts: ExecutionFacts, required: frozenset[str]) -> bool:
    """必需证据是否**完整且属于当前 Attempt**（D-10）。

    既有实现只核对"该 Attempt 下存在任意一条证据引用"，于是完整性为 `conflict`/
    `partial`、脱敏或投影被阻塞、带缺口的证据照样让必需证据判有效（进而 passed/A）。
    这里改为逐项核对：当前 Attempt 必须存在且为当前尝试、已完成、采集完整；
    声明了必需证据的步骤，其 Attempt 下**每条**证据都必须通过 `_evidence_valid`，
    且有效证据条数不少于声明的必需项数——只有引用存在不算满足需求。
    """

    attempts = {item.attempt_id: item for item in facts.attempts}
    for case_id in required:
        for step in _case_steps(facts, case_id):
            attempt = _current_attempt(facts, step, attempts)
            if attempt is None or attempt.state is not AttemptStateFact.COMPLETED:
                return False
            if attempt.capture_completeness is not CaptureCompletenessFact.COMPLETE:
                return False
            if any(gap.critical and gap.subject_ref == attempt.attempt_id for gap in facts.gaps):
                return False
            if not step.evidence_requirement_ids:
                continue
            bound = [
                evidence.evidence_id
                for evidence in facts.evidence_refs
                if evidence.attempt_id == attempt.attempt_id
            ]
            valid = [
                evidence_id
                for evidence_id in bound
                if _evidence_valid(facts, (evidence_id,), attempt.attempt_id)
            ]
            if len(valid) != len(bound):
                return False
            if len(valid) < len(set(step.evidence_requirement_ids)):
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


def assertion_outcomes_from_facts(
    facts: ExecutionFacts,
    *,
    basis_revision_for: Callable[[str], int | None],
    basis_confirmed_for: Callable[[str], bool],
) -> tuple[AssertionOutcomeFact, ...]:
    """从**已提交事实**派生断言结果（D-02 的 D 侧生产端，CD-001 §16「D 补断言结果生产者」）。

    期望值来源是既有的冻结形状，本函数**不发明任何期望值**：

    - 比较结果取自已保存的独立核验事实 `VerificationFact.observation`
      （`matched`/`mismatched`，由领域 `compare_business_fields`/`evaluate_http_assertion`
      在同一已提交快照里算出）；`VerificationFact.verification_of` 由核验入口钉在原
      Attempt 上（"resolved query does not verify the original operation"）；
    - 依据修订与依据确认来自冻结准备的 `assertion_bases`（调用方按用例传入）；
    - 断言标识取自步骤自己的 `assertion_refs`：**只有唯一一条时才绑定**，多于一条时
      无法知道这次核验对应哪条断言 → 不产出（不猜），由 `case_assertions_complete`
      如实为假，用例不会因此被判通过。

    产出仍受 `_assertion_valid` 逐项守卫（当前 Attempt、依据确认、核验有效、依赖有效、
    证据有效且属于该 Attempt），因此这里只负责"事实→结果"，不改变判定口径。
    """

    attempts = {attempt.attempt_id: attempt for attempt in facts.attempts}
    steps = {step.step_id: step for step in facts.steps}
    required_steps_by_case: dict[str, list[StepFact]] = {}
    for step in facts.steps:
        required_steps_by_case.setdefault(step.case_id, []).append(step)

    def dependency_valid(step_id: str, attempt_id: str) -> bool:
        return not any(
            invalidation.affected_step_id == step_id
            or invalidation.affected_attempt_id == attempt_id
            for invalidation in facts.dependency_invalidations
        )

    def observation_outcome(item: VerificationFact) -> AssertionOutcome | None:
        if item.gap_ids:
            # 核验带缺口 ⇒ 观察值不可信，不能当作通过/失败。
            return None
        if item.observation is VerificationObservationFact.MATCHED:
            return AssertionOutcome.PASSED
        if item.observation is VerificationObservationFact.MISMATCHED:
            return AssertionOutcome.FAILED
        return None

    drafts: list[tuple[str, str, AssertionOutcomeFact]] = []
    for verification in facts.verifications:
        outcome = observation_outcome(verification)
        if outcome is None:
            continue
        attempt = attempts.get(verification.verification_of)
        if attempt is None or not attempt.is_current:
            continue
        if attempt.state is not AttemptStateFact.COMPLETED:
            continue
        step_fact = steps.get(attempt.step_id)
        if (
            step_fact is None
            or facts.current_attempt_by_step.get(step_fact.step_id) != attempt.attempt_id
        ):
            continue
        if len(step_fact.assertion_refs) != 1 or not verification.evidence_refs:
            continue
        revision = basis_revision_for(step_fact.case_id)
        if revision is None:
            continue
        confirmed = basis_confirmed_for(step_fact.case_id)
        drafts.append(
            (
                step_fact.case_id,
                step_fact.step_id,
                AssertionOutcomeFact(
                    case_id=step_fact.case_id,
                    step_id=step_fact.step_id,
                    attempt_id=attempt.attempt_id,
                    assertion_ref=step_fact.assertion_refs[0],
                    basis_revision=revision,
                    outcome=outcome,
                    evidence_refs=tuple(verification.evidence_refs),
                    case_assertions_complete=False,
                    necessary_assertion=bool(step_fact.required_for_case),
                    basis_confirmed=confirmed,
                    verification_valid=True,
                    dependencies_valid=dependency_valid(step_fact.step_id, attempt.attempt_id),
                ),
            )
        )

    # `case_assertions_complete`：该用例**全部必需步骤**都产出了结果才算"断言齐全"，
    # 否则通过不得成立（功能 §148：V 只含"断言明确通过或失败"者）。
    covered: dict[str, set[str]] = {}
    for case_id, step_id, _ in drafts:
        covered.setdefault(case_id, set()).add(step_id)
    outcomes: list[AssertionOutcomeFact] = []
    for case_id, _step_id, item in drafts:
        complete = all(
            step.step_id in covered.get(case_id, set())
            for step in required_steps_by_case.get(case_id, [])
            if step.required_for_case
        )
        outcomes.append(
            replace(item, case_assertions_complete=complete)
            if complete != item.case_assertions_complete
            else item
        )
    return tuple(outcomes)


def build_decision_facts(
    facts: ExecutionFacts,
    *,
    assertion_outcomes: tuple[AssertionOutcomeFact, ...] = (),
    context: DecisionContextFacts | None = None,
    reuse: ReuseOutcome | None = None,
) -> DecisionFacts:
    """Map one committed snapshot without inferring pass from execution state.

    `reuse` 是**已核对的整用例复用消费结果**（`review.reuse_consumption.ReuseOutcome`）：
    过去这里把 `reused` 写成固定空集，等于把"没有候选"和"消费不了"混成同一个静默结果。
    现在 R 只来自该结果；被拒原因由调用方（报告）随结果一并展示。
    """

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
    reused = frozenset() if reuse is None else reuse.reused & selected - executed
    if reuse is not None:
        verified = verified | (reuse.verified & reused)
        passed = passed | (reuse.passed & reused)
        candidates = candidates + tuple(
            item for item in reuse.failure_candidates if item.case_id in reused
        )
    coverage = Coverage(
        selected=selected,
        required=required,
        executed=executed,
        reused=reused,
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
    if reuse is not None:
        confirmed.update(reuse.verified & reused)
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
        required_unresolved=required - executed - reused - pending,
        required_pending=required & pending,
        assertion_basis_confirmed=frozenset(confirmed),
        source_identity_state=source_state,
        critical_paths_satisfied=_critical_paths_satisfied(
            facts, context.required_critical_chain_item_ids
        ),
        required_evidence_valid=_required_evidence_valid(facts, required - reused),
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
    "assertion_outcomes_from_facts",
    "build_decision_facts",
]
