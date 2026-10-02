"""Application facade for issue lifecycle and disposition commands.

The functions validate optimistic revision and then call pure domain rules.
Persistence remains A's responsibility and is intentionally not simulated here.
"""

from __future__ import annotations

from collections.abc import Mapping

from aitest.domain.review.defects import (
    IssueClosureEvidence,
    IssueDisposition,
    IssueRecord,
    IssueSeverity,
)
from aitest.domain.review.defects import (
    close_issue as domain_close_issue,
)
from aitest.domain.review.defects import (
    confirm_issue as domain_confirm_issue,
)
from aitest.domain.review.defects import (
    defer_issue as domain_defer_issue,
)
from aitest.domain.review.defects import (
    mark_awaiting_confirmation as domain_mark_awaiting_confirmation,
)
from aitest.domain.review.defects import (
    mark_duplicate as domain_mark_duplicate,
)
from aitest.domain.review.defects import (
    mark_non_defect as domain_mark_non_defect,
)
from aitest.domain.review.defects import (
    record_failed_regression as domain_record_failed_regression,
)
from aitest.domain.review.defects import (
    record_fix as domain_record_fix,
)
from aitest.domain.review.defects import (
    start_issue as domain_start_issue,
)
from aitest.domain.review.defects import (
    undo_disposition as domain_undo_disposition,
)


def _require_expected_revision(issue: IssueRecord, expected_revision: int) -> None:
    if expected_revision < 1:
        raise ValueError("expected_revision must be positive")
    if issue.revision != expected_revision:
        raise ValueError(
            f"revision conflict: expected {expected_revision}, current {issue.revision}"
        )


def mark_awaiting_confirmation(
    issue: IssueRecord,
    *,
    expected_revision: int,
    evidence_refs: tuple[str, ...],
) -> IssueRecord:
    _require_expected_revision(issue, expected_revision)
    return domain_mark_awaiting_confirmation(issue, evidence_refs=evidence_refs)


def confirm_issue(
    issue: IssueRecord,
    *,
    expected_revision: int,
    severity: IssueSeverity,
    owner: str,
    confirmed_by: str,
) -> IssueRecord:
    _require_expected_revision(issue, expected_revision)
    return domain_confirm_issue(
        issue,
        severity=severity,
        owner=owner,
        confirmed_by=confirmed_by,
    )


def start_issue(issue: IssueRecord, *, expected_revision: int) -> IssueRecord:
    _require_expected_revision(issue, expected_revision)
    return domain_start_issue(issue)


def record_fix(
    issue: IssueRecord,
    *,
    expected_revision: int,
    fix_version: str,
    regression_method: str,
    closure_criteria: str,
) -> IssueRecord:
    _require_expected_revision(issue, expected_revision)
    return domain_record_fix(
        issue,
        fix_version=fix_version,
        regression_method=regression_method,
        closure_criteria=closure_criteria,
    )


def close_issue(
    issue: IssueRecord,
    *,
    expected_revision: int,
    evidence: IssueClosureEvidence,
) -> IssueRecord:
    _require_expected_revision(issue, expected_revision)
    return domain_close_issue(issue, evidence=evidence)


def record_failed_regression(
    issue: IssueRecord,
    *,
    expected_revision: int,
    attempt_id: str,
    evidence_refs: tuple[str, ...],
) -> IssueRecord:
    _require_expected_revision(issue, expected_revision)
    return domain_record_failed_regression(
        issue,
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def update_issue_disposition(
    issue: IssueRecord,
    *,
    expected_revision: int,
    disposition: IssueDisposition,
    reason: str,
    evidence_refs: tuple[str, ...],
    confirmed_by: str,
    canonical_issue: IssueRecord | None = None,
    issues: Mapping[str, IssueRecord] | None = None,
) -> IssueRecord:
    """Apply a non-fix disposition; fixed closure must use ``close_issue``."""

    _require_expected_revision(issue, expected_revision)
    if disposition is IssueDisposition.ACTIVE:
        return domain_undo_disposition(issue)
    if disposition is IssueDisposition.NON_DEFECT:
        return domain_mark_non_defect(
            issue,
            reason=reason,
            evidence_refs=evidence_refs,
            confirmed_by=confirmed_by,
        )
    if disposition is IssueDisposition.DUPLICATE:
        if canonical_issue is None:
            raise ValueError("duplicate disposition requires canonical_issue")
        return domain_mark_duplicate(
            issue,
            canonical_issue=canonical_issue,
            reason=reason,
            evidence_refs=evidence_refs,
            confirmed_by=confirmed_by,
            issues=issues,
        )
    if disposition is IssueDisposition.DEFERRED:
        return domain_defer_issue(
            issue,
            reason=reason,
            confirmed_by=confirmed_by,
        )
    if disposition is IssueDisposition.FIXED:
        raise ValueError("fixed disposition requires close_issue with new regression evidence")
    raise ValueError(f"unsupported disposition: {disposition}")


__all__ = [
    "close_issue",
    "confirm_issue",
    "mark_awaiting_confirmation",
    "record_failed_regression",
    "record_fix",
    "start_issue",
    "update_issue_disposition",
]
