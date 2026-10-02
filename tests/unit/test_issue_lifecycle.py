from dataclasses import replace

import pytest

from aitest.domain.review.defects import (
    IssueClosureEvidence,
    IssueDisposition,
    IssueRecord,
    IssueSeverity,
    IssueStatus,
    close_issue,
    confirm_issue,
    defer_issue,
    effective_blocking_issue_ids,
    mark_awaiting_confirmation,
    mark_duplicate,
    mark_non_defect,
    record_failed_regression,
    record_fix,
    resolve_canonical_issue_id,
    start_issue,
    undo_disposition,
)


def _draft(issue_id: str = "issue-1") -> IssueRecord:
    return IssueRecord(
        issue_id=issue_id,
        project_id="project-1",
        revision=1,
        title="Checkout fails to persist the order",
    )


def _confirmed(
    issue_id: str = "issue-1",
    severity: IssueSeverity = IssueSeverity.P1,
) -> IssueRecord:
    draft = _draft(issue_id)
    awaiting = mark_awaiting_confirmation(draft, evidence_refs=("failure-evidence",))
    return confirm_issue(
        awaiting,
        severity=severity,
        owner="developer-1",
        confirmed_by="reviewer-1",
    )


def _ready(issue_id: str = "issue-1") -> IssueRecord:
    return record_fix(
        start_issue(_confirmed(issue_id)),
        fix_version="1.2.3",
        regression_method="Rerun the original end-to-end checkout path.",
        closure_criteria="Order row exists and payment status is completed.",
    )


def _closure(
    *,
    attempt_id: str,
    evidence_refs: tuple[str, ...] = ("regression-evidence",),
    actual_execution: bool = True,
    current_regression_attempt: bool = True,
    evidence_saved: bool = True,
    satisfies_original_criteria: bool = True,
    basis_confirmed: bool = True,
    source_identity_matched: bool = True,
    dependencies_valid: bool = True,
) -> IssueClosureEvidence:
    return IssueClosureEvidence(
        case_id="case-checkout",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
        actual_execution=actual_execution,
        current_regression_attempt=current_regression_attempt,
        evidence_saved=evidence_saved,
        satisfies_original_criteria=satisfies_original_criteria,
        basis_confirmed=basis_confirmed,
        source_identity_matched=source_identity_matched,
        dependencies_valid=dependencies_valid,
    )


def test_fixed_issue_closes_only_with_new_actual_evidence() -> None:
    ready = _ready()
    closed = close_issue(
        ready,
        evidence=_closure(attempt_id="attempt-regression"),
    )

    assert closed.status is IssueStatus.CLOSED
    assert closed.disposition is IssueDisposition.FIXED
    assert closed.revision == ready.revision + 1
    assert closed.attempt_ids == {"attempt-regression"}


def test_close_rejects_reused_old_attempt() -> None:
    ready = replace(_ready(), attempt_ids=frozenset({"attempt-old"}))

    with pytest.raises(ValueError, match="new actual regression attempt"):
        close_issue(
            ready,
            evidence=_closure(attempt_id="attempt-old"),
        )


def test_close_rejects_non_actual_or_unconfirmed_evidence() -> None:
    with pytest.raises(ValueError, match="actual execution"):
        close_issue(
            _ready(),
            evidence=_closure(
                attempt_id="attempt-1",
                evidence_refs=("evidence-1",),
                actual_execution=False,
            ),
        )

    with pytest.raises(ValueError, match="confirmed assertion basis"):
        close_issue(
            _ready(),
            evidence=_closure(
                attempt_id="attempt-1",
                evidence_refs=("evidence-1",),
                basis_confirmed=False,
            ),
        )


def test_duplicate_change_rejects_cycle_before_returning_updated_record() -> None:
    root = _confirmed("root")
    leaf = mark_duplicate(
        _confirmed("leaf"),
        canonical_issue=root,
        reason="Leaf follows root.",
        evidence_refs=("duplicate-proof",),
        confirmed_by="reviewer-1",
    )

    with pytest.raises(ValueError, match="cycle"):
        mark_duplicate(
            root,
            canonical_issue=leaf,
            reason="This would close the loop.",
            evidence_refs=("duplicate-proof",),
            confirmed_by="reviewer-1",
            issues={"root": root, "leaf": leaf},
        )


def test_undo_disposition_preserves_confirmed_severity_and_blocking() -> None:
    issue = _confirmed(severity=IssueSeverity.P1)
    non_defect = mark_non_defect(
        issue,
        reason="Initially closed as non-defect.",
        evidence_refs=("evidence-1",),
        confirmed_by="reviewer-1",
    )

    restored = undo_disposition(non_defect)

    assert restored.status is IssueStatus.AWAITING_CONFIRMATION
    assert restored.severity is IssueSeverity.P1
    assert restored.disposition is IssueDisposition.ACTIVE
    assert effective_blocking_issue_ids({restored.issue_id: restored}) == {restored.issue_id}


def test_failed_regression_returns_to_in_progress_and_keeps_attempts() -> None:
    reopened = record_failed_regression(
        _ready(),
        attempt_id="attempt-failed-regression",
        evidence_refs=("new-failure-evidence",),
    )

    assert reopened.status is IssueStatus.IN_PROGRESS
    assert reopened.disposition is IssueDisposition.ACTIVE
    assert reopened.attempt_ids == {"attempt-failed-regression"}


def test_active_and_deferred_p0_p1_are_blocking() -> None:
    p0 = _confirmed("issue-p0", IssueSeverity.P0)
    p1 = _confirmed("issue-p1", IssueSeverity.P1)
    deferred = defer_issue(p1, reason="Waiting for vendor patch.", confirmed_by="reviewer-1")
    issues = {issue.issue_id: issue for issue in (p0, deferred)}

    assert effective_blocking_issue_ids(issues) == {"issue-p0", "issue-p1"}


def test_non_defect_disposition_does_not_block_but_preserves_issue() -> None:
    issue = _confirmed(severity=IssueSeverity.P0)
    non_defect = mark_non_defect(
        issue,
        reason="Confirmed a stale test fixture rather than a product defect.",
        evidence_refs=("fixture-forensics",),
        confirmed_by="reviewer-1",
    )

    assert non_defect.status is IssueStatus.CLOSED
    assert non_defect.disposition is IssueDisposition.NON_DEFECT
    assert non_defect.severity is IssueSeverity.P0
    assert effective_blocking_issue_ids({non_defect.issue_id: non_defect}) == frozenset()


def test_duplicate_follows_root_but_keeps_strongest_confirmed_severity() -> None:
    root = _confirmed("root", IssueSeverity.P2)
    duplicate = mark_duplicate(
        _confirmed("duplicate", IssueSeverity.P1),
        canonical_issue=root,
        reason="Same missing persistence path.",
        evidence_refs=("duplicate-proof",),
        confirmed_by="reviewer-1",
    )
    issues = {root.issue_id: root, duplicate.issue_id: duplicate}

    assert resolve_canonical_issue_id("duplicate", issues) == "root"
    assert effective_blocking_issue_ids(issues) == {"root"}


def test_fixed_root_removes_duplicate_blocking() -> None:
    root = _confirmed("root", IssueSeverity.P1)
    duplicate = mark_duplicate(
        _confirmed("duplicate", IssueSeverity.P1),
        canonical_issue=root,
        reason="Same missing persistence path.",
        evidence_refs=("duplicate-proof",),
        confirmed_by="reviewer-1",
    )
    fixed_root = replace(
        root,
        status=IssueStatus.CLOSED,
        disposition=IssueDisposition.FIXED,
        disposition_reason="Regression passed.",
        fix_version="1.2.3",
        regression_method="Rerun the path.",
        closure_criteria="Data persisted.",
    )
    issues = {fixed_root.issue_id: fixed_root, duplicate.issue_id: duplicate}

    assert effective_blocking_issue_ids(issues) == frozenset()


def test_duplicate_cycle_is_rejected() -> None:
    first = IssueRecord(
        issue_id="issue-a",
        project_id="project-1",
        revision=1,
        title="Issue A",
        status=IssueStatus.CLOSED,
        disposition=IssueDisposition.DUPLICATE,
        confirmed_by="reviewer-1",
        disposition_reason="Duplicate B.",
        canonical_issue_id="issue-b",
    )
    second = IssueRecord(
        issue_id="issue-b",
        project_id="project-1",
        revision=1,
        title="Issue B",
        status=IssueStatus.CLOSED,
        disposition=IssueDisposition.DUPLICATE,
        confirmed_by="reviewer-1",
        disposition_reason="Duplicate A.",
        canonical_issue_id="issue-a",
    )

    with pytest.raises(ValueError, match="cycle"):
        resolve_canonical_issue_id("issue-a", {"issue-a": first, "issue-b": second})
