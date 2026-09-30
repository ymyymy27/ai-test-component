import pytest

from aitest.application.review.defect_management import (
    close_issue as app_close_issue,
)
from aitest.application.review.defect_management import (
    confirm_issue as app_confirm_issue,
)
from aitest.application.review.defect_management import (
    mark_awaiting_confirmation as app_mark_awaiting_confirmation,
)
from aitest.application.review.defect_management import (
    record_fix as app_record_fix,
)
from aitest.application.review.defect_management import (
    start_issue as app_start_issue,
)
from aitest.application.review.defect_management import (
    update_issue_disposition,
)
from aitest.domain.review.defects import (
    IssueClosureEvidence,
    IssueDisposition,
    IssueRecord,
    IssueSeverity,
    IssueStatus,
)
from aitest.interfaces.dto import issue_summary_dto


def _draft() -> IssueRecord:
    return IssueRecord(
        issue_id="issue-1",
        project_id="project-1",
        revision=1,
        title="Order is not persisted",
    )


def _confirmed() -> IssueRecord:
    awaiting = app_mark_awaiting_confirmation(
        _draft(),
        expected_revision=1,
        evidence_refs=("failure-evidence",),
    )
    return app_confirm_issue(
        awaiting,
        expected_revision=awaiting.revision,
        severity=IssueSeverity.P1,
        owner="developer-1",
        confirmed_by="reviewer-1",
    )


def test_application_rejects_stale_expected_revision() -> None:
    with pytest.raises(ValueError, match="revision conflict"):
        app_start_issue(_confirmed(), expected_revision=1)


def test_application_runs_fix_and_retest_flow() -> None:
    confirmed = _confirmed()
    in_progress = app_start_issue(confirmed, expected_revision=confirmed.revision)
    ready = app_record_fix(
        in_progress,
        expected_revision=in_progress.revision,
        fix_version="1.2.3",
        regression_method="Run original checkout path.",
        closure_criteria="Order and payment are both persisted.",
    )
    closed = app_close_issue(
        ready,
        expected_revision=ready.revision,
        evidence=IssueClosureEvidence(
            case_id="case-checkout",
            attempt_id="attempt-regression",
            evidence_refs=("regression-evidence",),
        ),
    )

    assert closed.status is IssueStatus.CLOSED
    assert closed.disposition is IssueDisposition.FIXED
    assert closed.attempt_ids == {"attempt-regression"}


def test_non_defect_disposition_goes_through_application_guard() -> None:
    issue = _confirmed()
    disposed = update_issue_disposition(
        issue,
        expected_revision=issue.revision,
        disposition=IssueDisposition.NON_DEFECT,
        reason="Confirmed the failure came from a stale fixture.",
        evidence_refs=("fixture-analysis",),
        confirmed_by="reviewer-1",
    )

    assert disposed.status is IssueStatus.CLOSED
    assert disposed.disposition is IssueDisposition.NON_DEFECT


def test_fixed_disposition_requires_close_issue_command() -> None:
    issue = _confirmed()
    with pytest.raises(ValueError, match="close_issue"):
        update_issue_disposition(
            issue,
            expected_revision=issue.revision,
            disposition=IssueDisposition.FIXED,
            reason="Fixed.",
            evidence_refs=("evidence",),
            confirmed_by="reviewer-1",
        )


def test_issue_summary_dto_uses_supplied_effective_blocking_projection() -> None:
    issue = _confirmed()
    view = issue_summary_dto(issue, effective_severity=IssueSeverity.P0, effectively_blocking=True)

    assert view.issue_id == issue.issue_id
    assert view.severity is IssueSeverity.P1
    assert view.effective_severity is IssueSeverity.P0
    assert view.effectively_blocking
