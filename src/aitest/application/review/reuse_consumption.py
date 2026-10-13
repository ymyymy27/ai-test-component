"""整用例复用（R）的**消费**规则：要么按完整守卫计入，要么给出具名拒绝原因。

缺口形态
--------

`execution_decision_adapter.build_decision_facts()` 过去把 `reused` 写成固定空集：
仓库里即使存在合法的复用引用记录，报告也无法计入 R，而且**看不出为什么为空**
（"静默空集"比"拒绝"更危险：它让缺失看起来像结论）。

本模块只做**消费**，不做**保存**：候选来自已保存的 `ReuseReference` 记录
（`contracts/reuse_reference.py` 的形状；存储分类里 `reuse_decision` 已预留该类别）。
生产端由已定义的 select_case_reuse 保存准确选择；本模块按保存材料消费。
默认动态环境与目标依赖摘要仍未知，缺完整资格不得授予 R。因此边界是：

- 有候选时：逐条按 BC-001 §986 与功能 §15 的完整守卫核对，**任一缺失/未知/不一致即拒绝**，
  并把原因原样带出（各输入由调用方以**已保存材料**为唯一来源）；
- 无候选时：`reused` 为空是**真的没有候选**，不是"消费不了"。

守卫（每条都必须被显式证明，缺一即拒绝）：

1. `reference_not_for_this_run` —— 引用必须指向本运行；
2. `case_not_in_selected_scope` —— 用例属于本轮选择 S；
3. `target_case_already_started` —— 本轮**未**为该用例建立任何新 Attempt（含可选步骤）；
4. `required_step_mapping_incomplete` —— 引用覆盖该用例**全部必需步骤**，且与本轮步骤集合一致；
5. `source_attempt_unverified` —— 映射到的源 Attempt 属于源运行、已完成、采集完整、未失效；
6. `source_identity_unverified` —— 源内容身份经已保存材料核对一致；
7. `evidence_unreadable` —— 引用到的证据已保存且可读（逐条核对，任一条不可读即拒绝）；
8. `basis_confirmed_unverified` —— 该用例断言依据经受控确认链核对；
9. `verification_valid_unverified` —— 源运行存在有效核验（不把"有结果"当"已核验"）；
10. `dependencies_valid_unverified` —— 本轮没有影响该用例步骤的失效，且消费依赖依据可证明。

只把"十项全过"的用例计入 R；被拒的用例连原因一起返回，供报告与界面展示缺口。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from aitest.application.execution.facts import execution_payload_digest
from aitest.contracts.execution_facts import (
    AttemptStateFact,
    CaptureCompletenessFact,
    EvidenceIntegrityFact,
    ExecutionFacts,
    ProjectionStateFact,
    RedactionStateFact,
    SourceVerificationStateFact,
    VerificationObservationFact,
)
from aitest.contracts.reuse_reference import ReuseReference, parse_reuse_reference
from aitest.domain.execution.reuse import WholeCaseReuseBasis

if TYPE_CHECKING:
    from aitest.domain.review.reports import FailureCandidate

#: 计数 R 需要全部通过的守卫（顺序即拒绝原因的输出顺序）。
REUSE_GUARDS: tuple[str, ...] = (
    "reference_not_for_this_run",
    "case_not_in_selected_scope",
    "target_case_already_started",
    "required_step_mapping_incomplete",
    "source_attempt_unverified",
    "source_identity_unverified",
    "evidence_unreadable",
    "basis_confirmed_unverified",
    "verification_valid_unverified",
    "dependencies_valid_unverified",
    "complete_reuse_basis_unverified",
)


@dataclass(frozen=True, slots=True)
class ReuseDenial:
    """一条被拒的整用例复用候选：用例身份 + **具名**原因。"""

    case_id: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("reuse denial requires a case identity")
        if not self.reasons:
            raise ValueError("a denied reuse candidate must name at least one reason")


@dataclass(frozen=True, slots=True)
class ReuseOutcome:
    """一次消费的结果：计入 R 的用例与被拒候选（含原因）。"""

    reused: frozenset[str]
    denials: tuple[ReuseDenial, ...] = ()
    verified: frozenset[str] = frozenset()
    passed: frozenset[str] = frozenset()
    failure_candidates: tuple[FailureCandidate, ...] = ()

    def __post_init__(self) -> None:
        if not self.passed <= self.verified <= self.reused:
            raise ValueError("reuse result requires P subset V subset R")
        for denial in self.denials:
            if denial.case_id in self.reused:
                raise ValueError("a reused case must not also be denied")
        if len({denial.case_id for denial in self.denials}) != len(self.denials):
            raise ValueError("one case may be denied at most once")

    @property
    def named_reasons(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple((denial.case_id, denial.reasons) for denial in self.denials)

    def reasons_for(self, case_id: str) -> tuple[str, ...]:
        return next((denial.reasons for denial in self.denials if denial.case_id == case_id), ())


def parse_candidate(payload: Mapping[str, object]) -> ReuseReference:
    """从已保存的 `reuse_decision` 载荷严格重建引用；形状不符即拒绝。"""

    return parse_reuse_reference(payload)


def derive_reuse_outcome(
    facts: ExecutionFacts,
    references: tuple[ReuseReference, ...],
    *,
    source_facts: Callable[[str], ExecutionFacts | None],
    evidence_readable: Callable[[str], bool],
    basis_confirmed: Callable[[str], bool],
    complete_basis: Callable[
        [ReuseReference, ExecutionFacts, ExecutionFacts], WholeCaseReuseBasis | None
    ]
    | None = None,
    basis_revision_for: Callable[[ExecutionFacts, str], int | None] | None = None,
) -> ReuseOutcome:
    """按完整守卫决定哪些用例计入 R；每个被拒用例都带具名原因。

    `source_facts`／`evidence_readable`／`basis_confirmed` 必须只读**已保存材料**：
    调用方给出的任何"资格布尔"都不算数，这里只接受可核对的读取函数。
    """

    reused: set[str] = set()
    denials: list[ReuseDenial] = []
    denied_at: dict[str, int] = {}
    seen: set[str] = set()
    verified_cases: set[str] = set()
    passed_cases: set[str] = set()
    failures: list[FailureCandidate] = []
    sources: dict[str, ExecutionFacts | None] = {}

    def read_source(run_id: str) -> ExecutionFacts | None:
        if run_id not in sources:
            sources[run_id] = source_facts(run_id)
        return sources[run_id]

    for reference in references:
        if not isinstance(reference, ReuseReference):
            raise ValueError("reuse consumption requires typed saved references")
        if reference.case_id in seen:
            # 同一用例多条引用：不能"挑一条能过的"，整体按冲突拒绝。
            reused.discard(reference.case_id)
            verified_cases.discard(reference.case_id)
            passed_cases.discard(reference.case_id)
            failures = [item for item in failures if item.case_id != reference.case_id]
            conflict = ReuseDenial(
                case_id=reference.case_id,
                reasons=("conflicting_reuse_references",),
            )
            if reference.case_id in denied_at:
                denials[denied_at[reference.case_id]] = conflict
            else:
                denied_at[reference.case_id] = len(denials)
                denials.append(conflict)
            continue
        seen.add(reference.case_id)
        reasons = _denials_for(
            facts,
            reference,
            source_facts=read_source,
            evidence_readable=evidence_readable,
            basis_confirmed=basis_confirmed,
        )
        source = read_source(reference.source_run_id)
        proof = (
            None
            if complete_basis is None or source is None
            else complete_basis(reference, facts, source)
        )
        if (
            source is None
            or proof is None
            or proof.denial_reasons
            or (
                proof.source_run_id != reference.source_run_id
                or proof.source_snapshot_id != source.snapshot_commit_id
                or proof.source_snapshot_digest
                != execution_payload_digest(source.model_dump(mode="json"))
                or proof.source.project_id != facts.project_id
                or proof.target.project_id != facts.project_id
                or proof.source.case_id != reference.case_id
                or proof.target.case_id != reference.case_id
                or proof.source_attempt_by_step != reference.source_attempt_by_step
                or proof.source.source_content_identity != reference.source_content_identity
                or proof.source.entry_input_digest != reference.entry_input_digest
                or proof.source.rules_digest != reference.rules_revision_digest
                or proof.source.adapter_digest != reference.adapter_digest
            )
        ):
            reasons = (*reasons, "complete_reuse_basis_unverified")
        result = None
        if not reasons and source is not None and basis_revision_for is not None:
            # One assertion producer/decision adapter for new execution and R.
            from aitest.application.review.execution_decision_adapter import (
                assertion_outcomes_from_facts,
                build_decision_facts,
            )

            outcomes = assertion_outcomes_from_facts(
                source,
                basis_revision_for=partial(basis_revision_for, source),
                basis_confirmed_for=reference.case_id.__eq__,
            )
            result = build_decision_facts(source, assertion_outcomes=outcomes)
            if reference.case_id not in result.coverage.verified:
                reasons = ("verification_valid_unverified",)
        elif not reasons:
            reasons = ("verification_valid_unverified",)
        if reasons:
            denied_at[reference.case_id] = len(denials)
            denials.append(ReuseDenial(case_id=reference.case_id, reasons=reasons))
        else:
            reused.add(reference.case_id)
            verified_cases.add(reference.case_id)
            if result is not None:
                passed_cases.update(result.coverage.passed & {reference.case_id})
                failures.extend(
                    item for item in result.failure_candidates if item.case_id == reference.case_id
                )
    return ReuseOutcome(
        reused=frozenset(reused),
        denials=tuple(denials),
        verified=frozenset(verified_cases),
        passed=frozenset(passed_cases),
        failure_candidates=tuple(failures),
    )


def _denials_for(
    facts: ExecutionFacts,
    reference: ReuseReference,
    *,
    source_facts: Callable[[str], ExecutionFacts | None],
    evidence_readable: Callable[[str], bool],
    basis_confirmed: Callable[[str], bool],
) -> tuple[str, ...]:
    reasons: list[str] = []
    case_id = reference.case_id
    if reference.target_run_id != facts.run_id:
        reasons.append("reference_not_for_this_run")
    if case_id not in set(facts.run.selected_scope):
        reasons.append("case_not_in_selected_scope")
    case_steps = tuple(step for step in facts.steps if step.case_id == case_id)
    if not case_steps:
        reasons.append("required_step_mapping_incomplete")
    # 任何新 Attempt（含可选步骤）立即取消整用例复用。
    if any(attempt.step_id in {step.step_id for step in case_steps} for attempt in facts.attempts):
        reasons.append("target_case_already_started")
    required = tuple(step.step_id for step in case_steps if step.required_for_case)
    mapping = dict(reference.source_attempt_by_step)
    if len(mapping) != len(required) or not required:
        reasons.append("required_step_mapping_incomplete")

    source = source_facts(reference.source_run_id)
    if source is None:
        reasons.append("source_identity_unverified")
        reasons.append("source_attempt_unverified")
        reasons.append("verification_valid_unverified")
    else:
        source_required = {
            step.step_id
            for step in source.steps
            if step.case_id == case_id and step.required_for_case
        }
        if set(mapping) != source_required:
            reasons.append("required_step_mapping_incomplete")
        if not _source_identity_matches(reference, source):
            reasons.append("source_identity_unverified")
        if not _source_attempts_verified(source, mapping):
            reasons.append("source_attempt_unverified")
        if not _verification_present(source, mapping):
            reasons.append("verification_valid_unverified")
        selected_attempts = set(mapping.values())
        allowed_evidence = {
            item.evidence_id
            for item in source.evidence_refs
            if item.attempt_id in selected_attempts
        }
        required_evidence = {
            identity
            for item in source.verifications
            if item.verification_of in selected_attempts
            for identity in item.evidence_refs
        }
        if (
            not required_evidence <= set(reference.evidence_refs)
            or not set(reference.evidence_refs) <= allowed_evidence
        ):
            reasons.append("evidence_unreadable")
    if any(not evidence_readable(evidence_id) for evidence_id in reference.evidence_refs):
        reasons.append("evidence_unreadable")
    if not basis_confirmed(case_id):
        reasons.append("basis_confirmed_unverified")
    if any(
        invalidation.affected_step_id in {step.step_id for step in case_steps}
        for invalidation in facts.dependency_invalidations
    ):
        reasons.append("dependencies_valid_unverified")
    return tuple(reason for reason in REUSE_GUARDS if reason in reasons)


def _source_identity_matches(reference: ReuseReference, source: ExecutionFacts) -> bool:
    """源内容身份必须由源运行**已保存的来源核实事实**证明一致。"""

    return bool(source.source_verifications) and all(
        item.state is SourceVerificationStateFact.VERIFIED
        and not item.gap_ids
        and item.project_id == source.project_id
        and item.observed_source_digest == reference.source_content_identity
        and item.expected_source_binding_digest == reference.source_content_identity
        for item in source.source_verifications
    )


def _source_attempts_verified(source: ExecutionFacts, mapping: Mapping[str, str]) -> bool:
    attempts = {item.attempt_id: item for item in source.attempts}
    steps = {item.step_id: item for item in source.steps}
    if not mapping:
        return False
    for step_id, attempt_id in mapping.items():
        attempt = attempts.get(attempt_id)
        if (
            attempt is None
            or step_id not in steps
            or steps[step_id].invalidated
            or bool(steps[step_id].gap_ids)
            or source.current_attempt_by_step.get(step_id, steps[step_id].current_attempt_id)
            != attempt_id
            or not attempt.is_current
            or attempt.run_id != source.run_id
            or attempt.step_id != step_id
            or attempt.state is not AttemptStateFact.COMPLETED
            or attempt.capture_completeness is not CaptureCompletenessFact.COMPLETE
            or attempt.timed_out
            or bool(attempt.unknown_reason_ids)
        ):
            return False
        if any(
            item.affected_step_id == step_id or item.affected_attempt_id == attempt_id
            for item in source.dependency_invalidations
        ):
            return False
        relevant = {source.run_id, steps[step_id].case_id, step_id, attempt_id}
        if any(item.critical and item.subject_ref in relevant for item in source.gaps) or any(
            item.requires_verification and item.subject_id in relevant for item in source.unknowns
        ):
            return False
    return True


def _verification_present(source: ExecutionFacts, mapping: Mapping[str, str]) -> bool:
    """有效核验＝源运行里存在带证据、无缺口的核验事实；"有结果"不算。"""

    evidence = {item.evidence_id: item for item in source.evidence_refs}
    return all(
        any(
            item.verification_of == attempt_id
            and item.evidence_refs
            and not item.gap_ids
            and item.observation
            in {VerificationObservationFact.MATCHED, VerificationObservationFact.MISMATCHED}
            and all(
                ref in evidence
                and evidence[ref].attempt_id == attempt_id
                and evidence[ref].project_id == source.project_id
                and evidence[ref].run_id == source.run_id
                and evidence[ref].integrity is EvidenceIntegrityFact.COMPLETE
                and evidence[ref].redaction_state is not RedactionStateFact.BLOCKED
                and evidence[ref].projection_state is not ProjectionStateFact.BLOCKED
                and not evidence[ref].gap_ids
                for ref in item.evidence_refs
            )
            for item in source.verifications
        )
        for attempt_id in mapping.values()
    )


__all__ = [
    "REUSE_GUARDS",
    "ReuseDenial",
    "ReuseOutcome",
    "derive_reuse_outcome",
    "parse_candidate",
]
