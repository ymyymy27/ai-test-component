"""实际物化源码的入口、导入与来源身份核验。

本模块只记录执行侧实际观察到的来源事实，不复制 B 包的源码快照规则。
调用方提供 B 冻结的期望来源摘要，以及执行探针观察到的解释器/入口/导入
结果；这里负责形成可持久化的 ``ExecutionSourceVerification`` 和
``SourceCheckResult``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from aitest.domain.execution.runs import FailureClass, PlanRevisionRef
from aitest.domain.execution.sources import (
    ExecutionSourceVerification,
    SourceCheckResult,
    SourceCheckType,
    SourceVerificationState,
)


@dataclass(frozen=True, slots=True)
class SourceCheckRequest:
    """一次源码检查的冻结输入；规则来自 B，实际观察来自执行探针。"""

    verification_id: str
    check_result_id: str
    project_id: str
    attempt_id: str
    plan_revision_ref: PlanRevisionRef
    expected_source_binding_digest: str
    materialized_snapshot_ref: str
    check_type: SourceCheckType
    scope: str
    source_snapshot_ref: str
    environment_ref: str
    rules_revision: str
    adapter_version: str
    observed_source_digest: str
    observed_entry_ref: str | None = None
    observed_import_ref: str | None = None
    evidence_refs: tuple[str, ...] = ()
    raw_output_evidence_ref: str | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "verification_id",
            "check_result_id",
            "project_id",
            "attempt_id",
            "expected_source_binding_digest",
            "materialized_snapshot_ref",
            "scope",
            "source_snapshot_ref",
            "environment_ref",
            "rules_revision",
            "adapter_version",
            "observed_source_digest",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class SourceProbeObservation:
    """执行探针返回的原始观察；不在此层做通过/失败结论。"""

    observed_source_digest: str
    observed_entry_ref: str | None = None
    observed_import_ref: str | None = None
    failure_class: FailureClass | None = None
    evidence_refs: tuple[str, ...] = ()
    raw_output_evidence_ref: str | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SourceCheckOutcome:
    verification: ExecutionSourceVerification
    check_result: SourceCheckResult


class SourceProbePort(Protocol):
    def observe(
        self,
        *,
        materialized_snapshot_ref: str,
        entry_ref: str | None = None,
        import_ref: str | None = None,
    ) -> SourceProbeObservation: ...


class SourceVerificationService:
    """把探针事实与 B 的期望摘要合成为源码核验事实。"""

    def verify(
        self,
        request: SourceCheckRequest,
        observation: SourceProbeObservation,
    ) -> ExecutionSourceVerification:
        state, gaps = _derive_state(request, observation)
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    *observation.evidence_refs,
                    *(
                        (observation.raw_output_evidence_ref,)
                        if observation.raw_output_evidence_ref is not None
                        else ()
                    ),
                )
            )
        )
        return ExecutionSourceVerification(
            verification_id=request.verification_id,
            project_id=request.project_id,
            plan_revision_ref=request.plan_revision_ref,
            expected_source_binding_digest=request.expected_source_binding_digest,
            materialized_snapshot_ref=request.materialized_snapshot_ref,
            observed_source_digest=observation.observed_source_digest,
            state=state,
            observed_entry_ref=observation.observed_entry_ref,
            observed_import_ref=observation.observed_import_ref,
            failure_class=observation.failure_class,
            gap_ids=gaps,
            evidence_refs=evidence_refs,
            verified_at=observation.observed_at or request.observed_at or datetime.now(UTC),
        )

    def check(
        self,
        request: SourceCheckRequest,
        observation: SourceProbeObservation,
    ) -> SourceCheckOutcome:
        verification = self.verify(request, observation)
        failure_class = _check_failure_class(verification, observation)
        return SourceCheckOutcome(
            verification=verification,
            check_result=SourceCheckResult(
                check_result_id=request.check_result_id,
                attempt_id=request.attempt_id,
                check_type=request.check_type,
                scope=request.scope,
                source_snapshot_ref=request.source_snapshot_ref,
                environment_ref=request.environment_ref,
                rules_revision=request.rules_revision,
                adapter_version=request.adapter_version,
                failure_class=failure_class,
                entry_ref=observation.observed_entry_ref,
                argument_refs=(),
                raw_output_evidence_ref=observation.raw_output_evidence_ref,
                evidence_refs=verification.evidence_refs,
            ),
        )


def _derive_state(
    request: SourceCheckRequest,
    observation: SourceProbeObservation,
) -> tuple[SourceVerificationState, tuple[str, ...]]:
    if observation.failure_class in {
        FailureClass.DEPENDENCY_MISSING,
        FailureClass.ENVIRONMENT_UNREACHABLE,
    }:
        return SourceVerificationState.BLOCKED, ("environment_unreachable",)
    if observation.failure_class is FailureClass.SOURCE_ERROR:
        return SourceVerificationState.MISMATCH, ("source_error",)
    if observation.failure_class not in {None, FailureClass.PASSED}:
        return SourceVerificationState.UNKNOWN, ("tool_failure",)
    if not observation.observed_source_digest.strip():
        return SourceVerificationState.UNVERIFIED, ("source_digest_missing",)

    gaps: list[str] = []
    if _outside_materialized(
        observation.observed_entry_ref,
        request.materialized_snapshot_ref,
    ):
        gaps.append("entry_outside_materialized_source")
    if _outside_materialized(
        observation.observed_import_ref,
        request.materialized_snapshot_ref,
    ):
        gaps.append("import_outside_materialized_source")
    if gaps:
        return SourceVerificationState.MISMATCH, tuple(gaps)
    if observation.observed_source_digest != request.expected_source_binding_digest:
        return SourceVerificationState.MISMATCH, ("source_digest_mismatch",)
    return SourceVerificationState.VERIFIED, ()


def _check_failure_class(
    verification: ExecutionSourceVerification,
    observation: SourceProbeObservation,
) -> FailureClass:
    if observation.failure_class is not None:
        return observation.failure_class
    if verification.state is SourceVerificationState.VERIFIED:
        return FailureClass.PASSED
    if verification.state in {
        SourceVerificationState.BLOCKED,
        SourceVerificationState.UNVERIFIED,
    }:
        return FailureClass.ENVIRONMENT_UNREACHABLE
    if verification.state is SourceVerificationState.UNKNOWN:
        return FailureClass.TOOL_FAILURE
    return FailureClass.SOURCE_ERROR


def _outside_materialized(reference: str | None, materialized_ref: str) -> bool:
    if reference is None or not reference.strip():
        return False
    base = Path(materialized_ref)
    if not base.is_absolute() and not base.exists():
        return False
    target = Path(reference)
    if not target.is_absolute():
        return False
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return True
    return False


__all__ = [
    "SourceCheckOutcome",
    "SourceCheckRequest",
    "SourceProbeObservation",
    "SourceProbePort",
    "SourceVerificationService",
]
