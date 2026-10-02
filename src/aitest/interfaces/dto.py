"""Stable DTO projections for review, reports, and issues.

DTOs only serialize facts already computed by the domain. They must not
recalculate business outcomes, evidence grades, or blocking rules.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from aitest.contracts.views import CoverageDTO
from aitest.domain.review.defects import (
    IssueDisposition,
    IssueRecord,
    IssueSeverity,
    IssueStatus,
)
from aitest.domain.review.reports import (
    BusinessOutcome,
    Coverage,
    DecisionResult,
    DecisiveFailure,
    EvidenceGrade,
    ReportSnapshot,
    ReviewGap,
    ReviewGapCode,
)


def coverage_dto(value: Coverage) -> CoverageDTO:
    return CoverageDTO(
        selected_applicable_count=len(value.selected),
        required_applicable_count=len(value.required),
        executed_count=len(value.executed),
        reused_count=len(value.reused),
        verified_count=len(value.verified),
        passed_count=len(value.passed),
        failed_count=len(value.failed),
        unverified_count=len(value.unverified),
        required_verified_count=len(value.verified & value.required),
    )


class ReviewGapDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ReviewGapCode
    safe_reason: str
    subject_id: str | None = None

    @classmethod
    def from_domain(cls, value: ReviewGap | None) -> "ReviewGapDTO | None":
        if value is None:
            return None
        return cls(code=value.code, safe_reason=value.safe_reason, subject_id=value.subject_id)


class DecisiveFailureDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    step_id: str
    attempt_id: str
    assertion_ref: str
    basis_revision: int
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_domain(cls, value: DecisiveFailure) -> "DecisiveFailureDTO":
        return cls(
            case_id=value.case_id,
            step_id=value.step_id,
            attempt_id=value.attempt_id,
            assertion_ref=value.assertion_ref,
            basis_revision=value.basis_revision,
            evidence_refs=value.evidence_refs,
        )


class CoverageSummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_kind: Literal["selected", "required"]
    case_ids_revision: str | None = None
    denominator: int
    executed_count: int
    reused_count: int
    verified_count: int
    passed_count: int
    failed_count: int
    unverified_count: int
    decisive_failure_count: int
    decisive_failures: tuple[DecisiveFailureDTO, ...]
    source_commit: str | None = None
    policy_version: str


def _coverage_summary_dto(
    value: Coverage,
    *,
    scope_kind: Literal["selected", "required"],
    result: DecisionResult,
) -> CoverageSummaryDTO:
    scope = value.selected if scope_kind == "selected" else value.required
    failures = tuple(
        DecisiveFailureDTO.from_domain(failure)
        for failure in result.decisive_failures
        if failure.case_id in scope
    )
    return CoverageSummaryDTO(
        scope_kind=scope_kind,
        case_ids_revision=result.case_ids_revision,
        denominator=len(scope),
        executed_count=len(value.executed & scope),
        reused_count=len(value.reused & scope),
        verified_count=len(value.verified & scope),
        passed_count=len(value.passed & scope),
        failed_count=len(value.failed & scope),
        unverified_count=len(scope - value.verified),
        decisive_failure_count=len({failure.case_id for failure in failures}),
        decisive_failures=failures,
        source_commit=result.source_commit,
        policy_version=result.policy_version,
    )


class DecisionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aitest.decision/1.0"] = "aitest.decision/1.0"
    business_outcome: BusinessOutcome
    evidence_grade: EvidenceGrade | None
    primary_gap: ReviewGapDTO | None
    gaps: tuple[ReviewGapDTO, ...]
    decisive_failure_case_ids: tuple[str, ...]
    selected_summary: CoverageSummaryDTO
    required_summary: CoverageSummaryDTO
    snapshot_commit_id: str | None = None
    snapshot_cursor: int | None = None
    coverage: CoverageDTO
    policy_version: str


def decision_dto(value: DecisionResult) -> DecisionDTO:
    primary_gap = ReviewGapDTO.from_domain(value.primary_gap)
    gap_dtos: list[ReviewGapDTO] = []
    for gap in value.gaps:
        gap_dto = ReviewGapDTO.from_domain(gap)
        if gap_dto is not None:
            gap_dtos.append(gap_dto)
    return DecisionDTO(
        business_outcome=value.business_outcome,
        evidence_grade=value.evidence_grade,
        primary_gap=primary_gap,
        gaps=tuple(gap_dtos),
        decisive_failure_case_ids=tuple(sorted(value.decisive_failure_case_ids)),
        selected_summary=_coverage_summary_dto(value.coverage, scope_kind="selected", result=value),
        required_summary=_coverage_summary_dto(value.coverage, scope_kind="required", result=value),
        snapshot_commit_id=value.snapshot_commit_id,
        snapshot_cursor=value.snapshot_cursor,
        coverage=coverage_dto(value.coverage),
        policy_version=value.policy_version,
    )


class ReportSummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    content_revision: int
    project_id: str
    run_id: str
    run_revision: int
    scope_name: str
    business_outcome: BusinessOutcome
    evidence_grade: EvidenceGrade | None
    primary_gap: ReviewGapDTO | None
    created_at: datetime | None = None


def report_summary_dto(value: ReportSnapshot) -> ReportSummaryDTO:
    return ReportSummaryDTO(
        report_id=value.report_id,
        content_revision=value.content_revision,
        project_id=value.context.project_id,
        run_id=value.context.run_id,
        run_revision=value.context.run_revision,
        scope_name=value.context.scope_name,
        business_outcome=value.business_outcome,
        evidence_grade=value.evidence_grade,
        primary_gap=ReviewGapDTO.from_domain(value.primary_gap),
        created_at=value.created_at,
    )


class IssueSummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str
    project_id: str
    revision: int
    title: str
    status: IssueStatus
    severity: IssueSeverity | None
    effective_severity: IssueSeverity | None
    disposition: IssueDisposition
    effectively_blocking: bool


def issue_summary_dto(
    value: IssueRecord,
    *,
    effective_severity: IssueSeverity | None,
    effectively_blocking: bool,
) -> IssueSummaryDTO:
    return IssueSummaryDTO(
        issue_id=value.issue_id,
        project_id=value.project_id,
        revision=value.revision,
        title=value.title,
        status=value.status,
        severity=value.severity,
        effective_severity=effective_severity,
        disposition=value.disposition,
        effectively_blocking=effectively_blocking,
    )
