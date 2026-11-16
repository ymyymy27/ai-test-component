from datetime import UTC, datetime

import pytest

from aitest.application.review.reporting import (
    create_report,
    record_local_review,
    register_report_export,
)
from aitest.domain.planning.plans import RunTier
from aitest.domain.review.reports import (
    Coverage,
    DecisionFacts,
    DecisionResult,
    ReportContext,
    ReportExportKind,
    SourceIdentityState,
    evaluate_review,
)
from aitest.interfaces.dto import decision_dto, report_summary_dto


def _decision(policy_version: str = "policy-1") -> DecisionResult:
    coverage = Coverage(
        selected=frozenset({"case-1"}),
        required=frozenset({"case-1"}),
        executed=frozenset({"case-1"}),
        reused=frozenset(),
        verified=frozenset({"case-1"}),
        passed=frozenset({"case-1"}),
    )
    return evaluate_review(
        DecisionFacts(
            tier=RunTier.FULL,
            coverage=coverage,
            template_required=frozenset({"case-1"}),
            assertion_basis_confirmed=frozenset({"case-1"}),
            source_identity_state=SourceIdentityState.MATCHED,
            critical_paths_satisfied=True,
            required_evidence_valid=True,
            environment_evidence_complete=True,
            policy_version=policy_version,
        )
    )


def _context(policy_version: str = "policy-1") -> ReportContext:
    return ReportContext(
        project_id="project-1",
        run_id="run-1",
        run_revision=1,
        plan_revision_refs=("plan-1",),
        scope_revision="scope-1",
        scope_name="Checkout",
        source_identity="sha256:source-1",
        environment_ref="env-1",
        rules_revision="rules-1",
        acceptance_revision="acceptance-1",
        template_revision="template-1",
        policy_version=policy_version,
    )


def test_report_revision_is_immutable_and_increments_content_revision() -> None:
    first = create_report(
        None,
        report_id="report-1",
        context=_context(),
        decision=_decision(),
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    second = create_report(
        first,
        report_id="report-1",
        context=_context(),
        decision=_decision(),
        created_at=datetime(2026, 9, 30, 1, tzinfo=UTC),
    )

    assert first.content_revision == 1
    assert second.content_revision == 2
    assert first.created_at == datetime(2026, 9, 30, tzinfo=UTC)
    assert second.created_at == datetime(2026, 9, 30, 1, tzinfo=UTC)
    assert first is not second


def test_local_review_binds_exact_report_revision_without_changing_report() -> None:
    report = create_report(
        None,
        report_id="report-1",
        context=_context(),
        decision=_decision(),
    )
    review = record_local_review(
        report,
        review_id="review-1",
        reviewer="reviewer-1",
        conclusion="acknowledged",
        explanation="Checked against the frozen evidence.",
        report_revision=report.content_revision,
    )

    assert review.report_revision == report.content_revision
    assert report.content_revision == 1

    with pytest.raises(ValueError, match="exact report revision"):
        record_local_review(
            report,
            review_id="review-2",
            reviewer="reviewer-1",
            conclusion="acknowledged",
            explanation="Wrong revision.",
            report_revision=2,
        )


def test_identical_export_inputs_reuse_the_same_artifact() -> None:
    report = create_report(None, report_id="report-1", context=_context(), decision=_decision())
    review = record_local_review(
        report,
        review_id="review-1",
        reviewer="reviewer-1",
        conclusion="acknowledged",
        explanation="Reviewed.",
        report_revision=1,
    )
    first = register_report_export(
        report,
        (review,),
        (),
        export_id="export-1",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.MARKDOWN_SUMMARY,
        redaction_policy_version="redaction-1",
        artifact_ref="exports/report.md",
        artifact_digest="sha256:artifact-1",
    )
    reused = register_report_export(
        report,
        (review,),
        (first,),
        export_id="export-2",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.MARKDOWN_SUMMARY,
        redaction_policy_version="redaction-1",
        artifact_ref="exports/report.md",
        artifact_digest="sha256:artifact-1",
    )

    assert reused is first


def test_changed_redaction_policy_creates_new_export() -> None:
    report = create_report(None, report_id="report-1", context=_context(), decision=_decision())
    review = record_local_review(
        report,
        review_id="review-1",
        reviewer="reviewer-1",
        conclusion="acknowledged",
        explanation="Reviewed.",
        report_revision=1,
    )
    first = register_report_export(
        report,
        (review,),
        (),
        export_id="export-1",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.MARKDOWN_SUMMARY,
        redaction_policy_version="redaction-1",
        artifact_ref="exports/report.md",
        artifact_digest="sha256:artifact-1",
    )
    second = register_report_export(
        report,
        (review,),
        (first,),
        export_id="export-2",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.MARKDOWN_SUMMARY,
        redaction_policy_version="redaction-2",
        artifact_ref="exports/report-v2.md",
        artifact_digest="sha256:artifact-2",
    )

    assert second is not first
    assert second.key.redaction_policy_version == "redaction-2"


def test_changed_attachment_set_creates_new_export() -> None:
    report = create_report(None, report_id="report-1", context=_context(), decision=_decision())
    review = record_local_review(
        report,
        review_id="review-1",
        reviewer="reviewer-1",
        conclusion="acknowledged",
        explanation="Reviewed.",
        report_revision=1,
    )
    first = register_report_export(
        report,
        (review,),
        (),
        export_id="export-1",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.EVIDENCE_BUNDLE,
        redaction_policy_version="redaction-1",
        artifact_ref="exports/bundle.zip",
        artifact_digest="sha256:artifact-1",
        attachment_refs=(),
    )
    second = register_report_export(
        report,
        (review,),
        (first,),
        export_id="export-2",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.EVIDENCE_BUNDLE,
        redaction_policy_version="redaction-1",
        artifact_ref="exports/bundle-v2.zip",
        artifact_digest="sha256:artifact-2",
        attachment_refs=("evidence-1",),
    )

    assert second is not first
    assert second.key.attachment_refs == ("evidence-1",)


def test_same_export_key_cannot_silently_replace_artifact() -> None:
    report = create_report(None, report_id="report-1", context=_context(), decision=_decision())
    review = record_local_review(
        report,
        review_id="review-1",
        reviewer="reviewer-1",
        conclusion="acknowledged",
        explanation="Reviewed.",
        report_revision=1,
    )
    existing = register_report_export(
        report,
        (review,),
        (),
        export_id="export-1",
        report_revision=1,
        review_ids=("review-1",),
        kind=ReportExportKind.MARKDOWN_SUMMARY,
        redaction_policy_version="redaction-1",
        artifact_ref="exports/report.md",
        artifact_digest="sha256:artifact-1",
    )

    with pytest.raises(ValueError, match="different artifact"):
        register_report_export(
            report,
            (review,),
            (existing,),
            export_id="export-2",
            report_revision=1,
            review_ids=("review-1",),
            kind=ReportExportKind.MARKDOWN_SUMMARY,
            redaction_policy_version="redaction-1",
            artifact_ref="exports/other.md",
            artifact_digest="sha256:artifact-2",
        )


def test_report_and_decision_dto_reuse_domain_results() -> None:
    decision = _decision()
    report = create_report(None, report_id="report-1", context=_context(), decision=decision)

    decision_view = decision_dto(decision)
    report_view = report_summary_dto(report)

    assert decision_view.business_outcome == decision.business_outcome
    assert decision_view.coverage.passed_count == len(decision.coverage.passed)
    assert report_view.report_id == report.report_id
    assert report_view.content_revision == report.content_revision
