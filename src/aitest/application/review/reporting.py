"""Application use cases for immutable reports, local review, and export.

Persistence is intentionally not implemented here. A later integration step
will call these pure operations inside A's workspace unit of work.
"""

from __future__ import annotations

from datetime import datetime

from aitest.domain.review.reports import (
    DecisionResult,
    LocalReview,
    ReportContext,
    ReportDraft,
    ReportExport,
    ReportExportKind,
    ReportSnapshot,
    create_report_export,
    create_report_revision,
)


def create_report(
    previous: ReportSnapshot | None,
    *,
    report_id: str,
    context: ReportContext,
    decision: DecisionResult,
    evidence_refs: tuple[str, ...] = (),
    issue_revisions: tuple[tuple[str, int], ...] = (),
    created_at: datetime | None = None,
) -> ReportSnapshot:
    """Freeze a new report revision and leave earlier revisions untouched."""

    if decision.policy_version != context.policy_version:
        raise ValueError("decision and report context policy versions must match")
    draft = ReportDraft(
        report_id=report_id,
        context=context,
        decision=decision,
        evidence_refs=evidence_refs,
        issue_revisions=issue_revisions,
    )
    return create_report_revision(previous, draft, created_at=created_at)


def record_local_review(
    report: ReportSnapshot,
    *,
    review_id: str,
    reviewer: str,
    conclusion: str,
    explanation: str,
    report_revision: int,
    recorded_at: datetime | None = None,
) -> LocalReview:
    """Record a review of an exact report revision without changing its body."""

    if report_revision != report.content_revision:
        raise ValueError("review must bind the exact report revision")
    return LocalReview(
        review_id=review_id,
        project_id=report.context.project_id,
        report_id=report.report_id,
        report_revision=report_revision,
        reviewer=reviewer,
        conclusion=conclusion,
        explanation=explanation,
        recorded_at=recorded_at,
    )


def register_report_export(
    report: ReportSnapshot,
    reviews: tuple[LocalReview, ...],
    existing_exports: tuple[ReportExport, ...],
    *,
    export_id: str,
    report_revision: int,
    review_ids: tuple[str, ...],
    kind: ReportExportKind,
    redaction_policy_version: str,
    artifact_ref: str,
    artifact_digest: str,
    attachment_refs: tuple[str, ...] = (),
    created_at: datetime | None = None,
) -> ReportExport:
    """Freeze an export request and idempotently reuse an identical artifact."""

    return create_report_export(
        report,
        reviews,
        existing_exports,
        export_id=export_id,
        report_revision=report_revision,
        review_ids=review_ids,
        kind=kind,
        redaction_policy_version=redaction_policy_version,
        artifact_ref=artifact_ref,
        artifact_digest=artifact_digest,
        attachment_refs=attachment_refs,
        created_at=created_at,
    )


__all__ = [
    "create_report",
    "record_local_review",
    "register_report_export",
]
