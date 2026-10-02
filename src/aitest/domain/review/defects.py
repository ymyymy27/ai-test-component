"""Unique issue records, lifecycle guards, and effective blocking rules.

Executing a test and recording an issue disposition are different facts.
Disposition never changes the original assertion or evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_evidence(evidence_refs: tuple[str, ...]) -> None:
    if not evidence_refs:
        raise ValueError("evidence_refs must not be empty")
    if any(not ref.strip() for ref in evidence_refs):
        raise ValueError("evidence_refs must not contain empty values")


class IssueStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    READY_FOR_RETEST = "ready_for_retest"
    CLOSED = "closed"


class IssueSeverity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class IssueDisposition(StrEnum):
    ACTIVE = "active"
    FIXED = "fixed"
    NON_DEFECT = "non_defect"
    DUPLICATE = "duplicate"
    DEFERRED = "deferred"


_SEVERITY_PRIORITY: dict[IssueSeverity, int] = {
    IssueSeverity.P0: 0,
    IssueSeverity.P1: 1,
    IssueSeverity.P2: 2,
    IssueSeverity.P3: 3,
}


@dataclass(frozen=True, slots=True)
class IssueRecord:
    issue_id: str
    project_id: str
    revision: int
    title: str
    status: IssueStatus = IssueStatus.DRAFT
    severity: IssueSeverity | None = None
    disposition: IssueDisposition = IssueDisposition.ACTIVE
    owner: str | None = None
    confirmed_by: str | None = None
    disposition_reason: str | None = None
    canonical_issue_id: str | None = None
    fix_version: str | None = None
    regression_method: str | None = None
    closure_criteria: str | None = None
    evidence_refs: tuple[str, ...] = ()
    attempt_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _require_text(self.issue_id, "issue_id")
        _require_text(self.project_id, "project_id")
        _require_text(self.title, "title")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.canonical_issue_id == self.issue_id:
            raise ValueError("an issue cannot be a duplicate of itself")
        if self.disposition is IssueDisposition.DUPLICATE:
            if not self.canonical_issue_id:
                raise ValueError("duplicate disposition requires canonical_issue_id")
        elif self.canonical_issue_id is not None:
            raise ValueError("canonical_issue_id is only valid for duplicate disposition")

        confirmed_status = {
            IssueStatus.CONFIRMED,
            IssueStatus.IN_PROGRESS,
            IssueStatus.READY_FOR_RETEST,
        }
        if self.status in confirmed_status or self.disposition is IssueDisposition.FIXED:
            if self.severity is None:
                raise ValueError("confirmed issue requires severity")
            if not self.owner:
                raise ValueError("confirmed issue requires owner")
            if not self.confirmed_by:
                raise ValueError("confirmed issue requires confirmed_by")

        if self.status is IssueStatus.READY_FOR_RETEST:
            self._require_fix_fields()
        if self.disposition is IssueDisposition.FIXED:
            if self.status is not IssueStatus.CLOSED:
                raise ValueError("fixed disposition requires closed status")
            self._require_fix_fields()
        if self.status is IssueStatus.CLOSED and self.disposition is IssueDisposition.DEFERRED:
            raise ValueError("deferred disposition must remain unresolved")
        if self.disposition in {
            IssueDisposition.NON_DEFECT,
            IssueDisposition.DUPLICATE,
        }:
            if self.status is not IssueStatus.CLOSED:
                raise ValueError("non-defect or duplicate disposition requires closed status")
            if not self.disposition_reason:
                raise ValueError("disposition requires a reason")
            if not self.confirmed_by:
                raise ValueError("disposition requires confirmed_by")

    def _require_fix_fields(self) -> None:
        if not self.fix_version:
            raise ValueError("fix_version is required")
        if not self.regression_method:
            raise ValueError("regression_method is required")
        if not self.closure_criteria:
            raise ValueError("closure_criteria is required")


@dataclass(frozen=True, slots=True)
class IssueClosureEvidence:
    case_id: str
    attempt_id: str
    evidence_refs: tuple[str, ...]
    actual_execution: bool
    current_regression_attempt: bool
    evidence_saved: bool
    satisfies_original_criteria: bool
    basis_confirmed: bool
    source_identity_matched: bool
    dependencies_valid: bool

    def __post_init__(self) -> None:
        _require_text(self.case_id, "case_id")
        _require_text(self.attempt_id, "attempt_id")
        _require_evidence(self.evidence_refs)


def _new_revision(issue: IssueRecord, **changes: Any) -> IssueRecord:
    return replace(issue, revision=issue.revision + 1, **changes)


def _require_status(issue: IssueRecord, expected: IssueStatus) -> None:
    if issue.status is not expected:
        raise ValueError(f"issue must be {expected.value}, got {issue.status.value}")


def mark_awaiting_confirmation(
    issue: IssueRecord,
    *,
    evidence_refs: tuple[str, ...],
) -> IssueRecord:
    _require_status(issue, IssueStatus.DRAFT)
    _require_evidence(evidence_refs)
    return _new_revision(
        issue,
        status=IssueStatus.AWAITING_CONFIRMATION,
        evidence_refs=issue.evidence_refs + evidence_refs,
    )


def confirm_issue(
    issue: IssueRecord,
    *,
    severity: IssueSeverity,
    owner: str,
    confirmed_by: str,
) -> IssueRecord:
    _require_status(issue, IssueStatus.AWAITING_CONFIRMATION)
    _require_text(owner, "owner")
    _require_text(confirmed_by, "confirmed_by")
    return _new_revision(
        issue,
        status=IssueStatus.CONFIRMED,
        severity=severity,
        owner=owner,
        confirmed_by=confirmed_by,
    )


def start_issue(issue: IssueRecord) -> IssueRecord:
    _require_status(issue, IssueStatus.CONFIRMED)
    return _new_revision(issue, status=IssueStatus.IN_PROGRESS)


def record_fix(
    issue: IssueRecord,
    *,
    fix_version: str,
    regression_method: str,
    closure_criteria: str,
) -> IssueRecord:
    _require_status(issue, IssueStatus.IN_PROGRESS)
    for value, name in (
        (fix_version, "fix_version"),
        (regression_method, "regression_method"),
        (closure_criteria, "closure_criteria"),
    ):
        _require_text(value, name)
    return _new_revision(
        issue,
        status=IssueStatus.READY_FOR_RETEST,
        fix_version=fix_version,
        regression_method=regression_method,
        closure_criteria=closure_criteria,
    )


def close_issue(issue: IssueRecord, *, evidence: IssueClosureEvidence) -> IssueRecord:
    _require_status(issue, IssueStatus.READY_FOR_RETEST)
    if evidence.attempt_id in issue.attempt_ids:
        raise ValueError("closure requires a new actual regression attempt")
    if not evidence.actual_execution:
        raise ValueError("closure evidence must come from actual execution")
    if not evidence.current_regression_attempt:
        raise ValueError("closure evidence must reference the current regression attempt")
    if not evidence.evidence_saved:
        raise ValueError("closure evidence must already be saved")
    if not evidence.satisfies_original_criteria:
        raise ValueError("closure evidence does not satisfy the original criteria")
    if not evidence.basis_confirmed:
        raise ValueError("closure evidence requires a confirmed assertion basis")
    if not evidence.source_identity_matched:
        raise ValueError("closure evidence requires matching source identity")
    if not evidence.dependencies_valid:
        raise ValueError("closure evidence requires valid dependencies")
    return _new_revision(
        issue,
        status=IssueStatus.CLOSED,
        disposition=IssueDisposition.FIXED,
        disposition_reason="Original closure criteria satisfied by a new regression attempt.",
        evidence_refs=issue.evidence_refs + evidence.evidence_refs,
        attempt_ids=issue.attempt_ids | frozenset({evidence.attempt_id}),
    )


def record_failed_regression(
    issue: IssueRecord,
    *,
    attempt_id: str,
    evidence_refs: tuple[str, ...],
) -> IssueRecord:
    if issue.status not in {IssueStatus.READY_FOR_RETEST, IssueStatus.CLOSED}:
        raise ValueError("failed regression requires a ready-for-retest or closed issue")
    _require_text(attempt_id, "attempt_id")
    _require_evidence(evidence_refs)
    return _new_revision(
        issue,
        status=IssueStatus.IN_PROGRESS,
        disposition=IssueDisposition.ACTIVE,
        disposition_reason=None,
        canonical_issue_id=None,
        evidence_refs=issue.evidence_refs + evidence_refs,
        attempt_ids=issue.attempt_ids | frozenset({attempt_id}),
    )


def mark_non_defect(
    issue: IssueRecord,
    *,
    reason: str,
    evidence_refs: tuple[str, ...],
    confirmed_by: str,
) -> IssueRecord:
    _require_text(reason, "reason")
    _require_evidence(evidence_refs)
    _require_text(confirmed_by, "confirmed_by")
    return _new_revision(
        issue,
        status=IssueStatus.CLOSED,
        disposition=IssueDisposition.NON_DEFECT,
        disposition_reason=reason,
        confirmed_by=confirmed_by,
        canonical_issue_id=None,
        evidence_refs=issue.evidence_refs + evidence_refs,
    )


def mark_duplicate(
    issue: IssueRecord,
    *,
    canonical_issue: IssueRecord,
    reason: str,
    evidence_refs: tuple[str, ...],
    confirmed_by: str,
    issues: Mapping[str, IssueRecord] | None = None,
) -> IssueRecord:
    if issue.project_id != canonical_issue.project_id:
        raise ValueError("duplicate and canonical issue must belong to the same project")
    if issue.issue_id == canonical_issue.issue_id:
        raise ValueError("an issue cannot be a duplicate of itself")
    if canonical_issue.disposition is IssueDisposition.DUPLICATE and issues is None:
        raise ValueError("duplicate chain validation requires the complete issue graph")
    _require_text(reason, "reason")
    _require_evidence(evidence_refs)
    _require_text(confirmed_by, "confirmed_by")
    updated = _new_revision(
        issue,
        status=IssueStatus.CLOSED,
        disposition=IssueDisposition.DUPLICATE,
        disposition_reason=reason,
        confirmed_by=confirmed_by,
        canonical_issue_id=canonical_issue.issue_id,
        evidence_refs=issue.evidence_refs + evidence_refs,
    )
    if issues is not None:
        graph = dict(issues)
        graph[updated.issue_id] = updated
        resolve_canonical_issue_id(updated.issue_id, graph)
    return updated


def defer_issue(
    issue: IssueRecord,
    *,
    reason: str,
    confirmed_by: str,
) -> IssueRecord:
    _require_text(reason, "reason")
    _require_text(confirmed_by, "confirmed_by")
    return _new_revision(
        issue,
        disposition=IssueDisposition.DEFERRED,
        disposition_reason=reason,
        confirmed_by=confirmed_by,
        canonical_issue_id=None,
    )


def undo_disposition(issue: IssueRecord) -> IssueRecord:
    if issue.disposition is IssueDisposition.ACTIVE:
        raise ValueError("issue has no non-active disposition to undo")
    return _new_revision(
        issue,
        status=IssueStatus.AWAITING_CONFIRMATION,
        disposition=IssueDisposition.ACTIVE,
        confirmed_by=None,
        disposition_reason=None,
        canonical_issue_id=None,
        fix_version=None,
        regression_method=None,
        closure_criteria=None,
    )


def resolve_canonical_issue_id(
    issue_id: str,
    issues: Mapping[str, IssueRecord],
) -> str:
    """Resolve a duplicate chain and reject dangling references or cycles."""

    root = issue_id
    seen: set[str] = set()
    project_id: str | None = None
    while True:
        if root in seen:
            raise ValueError("duplicate issue chain contains a cycle")
        seen.add(root)
        issue = issues.get(root)
        if issue is None:
            raise ValueError(f"duplicate canonical issue is missing: {root}")
        if project_id is None:
            project_id = issue.project_id
        elif issue.project_id != project_id:
            raise ValueError("duplicate issue chain crosses projects")
        if issue.disposition is not IssueDisposition.DUPLICATE:
            return issue.issue_id
        if not issue.canonical_issue_id:
            raise ValueError("duplicate issue has no canonical reference")
        root = issue.canonical_issue_id


def effective_severity(
    issue_id: str,
    issues: Mapping[str, IssueRecord],
) -> IssueSeverity | None:
    """Return the strongest severity along a duplicate chain."""

    root = issue_id
    seen: set[str] = set()
    severities: list[IssueSeverity] = []
    while True:
        if root in seen:
            raise ValueError("duplicate issue chain contains a cycle")
        seen.add(root)
        issue = issues.get(root)
        if issue is None:
            raise ValueError(f"duplicate canonical issue is missing: {root}")
        if issue.severity is not None:
            severities.append(issue.severity)
        if issue.disposition is not IssueDisposition.DUPLICATE:
            break
        if not issue.canonical_issue_id:
            raise ValueError("duplicate issue has no canonical reference")
        root = issue.canonical_issue_id
    if not severities:
        return None
    return min(severities, key=lambda severity: _SEVERITY_PRIORITY[severity])


def effective_blocking_issue_ids(
    issues: Mapping[str, IssueRecord],
) -> frozenset[str]:
    """Return canonical P0/P1 issue IDs that currently block the scope.

    A duplicate follows its final canonical disposition. The strongest
    confirmed severity in the chain is retained so merging cannot silently
    lower an already confirmed issue level.
    """

    blocking: set[str] = set()
    for issue_id in issues:
        root_id = resolve_canonical_issue_id(issue_id, issues)
        root = issues[root_id]
        if root.disposition not in {
            IssueDisposition.ACTIVE,
            IssueDisposition.DEFERRED,
        }:
            continue
        severity = effective_severity(issue_id, issues)
        if severity in {IssueSeverity.P0, IssueSeverity.P1}:
            blocking.add(root_id)
    return frozenset(blocking)


__all__ = [
    "IssueClosureEvidence",
    "IssueDisposition",
    "IssueRecord",
    "IssueSeverity",
    "IssueStatus",
    "close_issue",
    "confirm_issue",
    "defer_issue",
    "effective_blocking_issue_ids",
    "effective_severity",
    "mark_awaiting_confirmation",
    "mark_duplicate",
    "mark_non_defect",
    "record_failed_regression",
    "record_fix",
    "resolve_canonical_issue_id",
    "start_issue",
    "undo_disposition",
]
