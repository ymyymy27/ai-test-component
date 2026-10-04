"""Phase-one review decisions and coverage sets.

The authoritative rules are in the phase-one functional contract section 3.
This module derives business outcome, decisive failures, evidence grade, and
coverage sets; it does not persist records or render UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aitest.domain.planning.plans import RunTier


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_positive(value: int, name: str) -> None:
    if value < 1:
        raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class Coverage:
    """S/M coverage sets from phase-one functional contract section 3.

    ``selected`` is S, ``required`` is M, ``executed`` is E, ``reused`` is R,
    ``verified`` is V, and ``passed`` is P. Failed and unverified cases are
    derived and are never stored as independent facts.
    """

    selected: frozenset[str]
    required: frozenset[str]
    executed: frozenset[str]
    reused: frozenset[str]
    verified: frozenset[str]
    passed: frozenset[str]

    def __post_init__(self) -> None:
        if self.executed & self.reused:
            raise ValueError("new execution and reuse must be disjoint")
        if not (self.executed | self.reused) <= self.selected:
            raise ValueError("executed and reused cases must belong to selected scope")
        if not self.verified <= (self.executed | self.reused):
            raise ValueError("verified cases must have effective execution or reuse")
        if not self.passed <= self.verified:
            raise ValueError("unverified cases cannot pass")

    @property
    def failed(self) -> frozenset[str]:
        """Whole-case verified failures, not decisive failures H."""

        return self.verified - self.passed

    @property
    def unverified(self) -> frozenset[str]:
        return self.selected - self.verified


class BusinessOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


class EvidenceGrade(StrEnum):
    """Evidence strength for a full run only.

    This is deliberately separate from ``EvidenceLevel`` on an individual
    EvidenceRef: the grade is a report-level, five-dimensional projection.
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


class SourceIdentityState(StrEnum):
    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNVERIFIED = "unverified"


class ReviewGapCode(StrEnum):
    SOURCE_IDENTITY_MISMATCH = "source_identity_mismatch"
    NO_VALID_EXECUTION_BASIS = "no_valid_execution_basis"
    SOURCE_IDENTITY_UNVERIFIED = "source_identity_unverified"
    TEMPLATE_REQUIRED_BELOW_FLOOR = "template_required_below_floor"
    FULL_REQUIRED_SCOPE_MISSING = "full_required_scope_missing"
    REQUIRED_CASE_UNRESOLVED = "required_case_unresolved"
    REQUIRED_CASE_PENDING_VERIFICATION = "required_case_pending_verification"
    OUTDATED_BASIS = "outdated_basis"
    REQUIRED_EVIDENCE_INVALID = "required_evidence_invalid"
    ENVIRONMENT_EVIDENCE_INCOMPLETE = "environment_evidence_incomplete"
    CRITICAL_PATH_MISSING = "critical_path_missing"
    CRITICAL_UNKNOWN = "critical_unknown"
    CRITICAL_MOCK = "critical_mock"
    FULL_SKIPPED_SCOPE = "full_skipped_scope"
    ASSERTION_BASIS_UNCONFIRMED = "assertion_basis_unconfirmed"
    NONCRITICAL_UNKNOWN = "noncritical_unknown"
    NONCRITICAL_VERIFICATION_GAP = "noncritical_verification_gap"
    UNCLASSIFIED_GAP = "unclassified_gap"


_GAP_GRADES: dict[ReviewGapCode, EvidenceGrade] = {
    ReviewGapCode.SOURCE_IDENTITY_MISMATCH: EvidenceGrade.D,
    ReviewGapCode.NO_VALID_EXECUTION_BASIS: EvidenceGrade.D,
    ReviewGapCode.SOURCE_IDENTITY_UNVERIFIED: EvidenceGrade.C,
    ReviewGapCode.TEMPLATE_REQUIRED_BELOW_FLOOR: EvidenceGrade.C,
    ReviewGapCode.FULL_REQUIRED_SCOPE_MISSING: EvidenceGrade.C,
    ReviewGapCode.REQUIRED_CASE_UNRESOLVED: EvidenceGrade.C,
    ReviewGapCode.REQUIRED_CASE_PENDING_VERIFICATION: EvidenceGrade.C,
    ReviewGapCode.OUTDATED_BASIS: EvidenceGrade.C,
    ReviewGapCode.REQUIRED_EVIDENCE_INVALID: EvidenceGrade.C,
    ReviewGapCode.ENVIRONMENT_EVIDENCE_INCOMPLETE: EvidenceGrade.C,
    ReviewGapCode.CRITICAL_PATH_MISSING: EvidenceGrade.C,
    ReviewGapCode.CRITICAL_UNKNOWN: EvidenceGrade.C,
    ReviewGapCode.CRITICAL_MOCK: EvidenceGrade.C,
    ReviewGapCode.FULL_SKIPPED_SCOPE: EvidenceGrade.C,
    ReviewGapCode.ASSERTION_BASIS_UNCONFIRMED: EvidenceGrade.B,
    ReviewGapCode.NONCRITICAL_UNKNOWN: EvidenceGrade.B,
    ReviewGapCode.NONCRITICAL_VERIFICATION_GAP: EvidenceGrade.B,
    ReviewGapCode.UNCLASSIFIED_GAP: EvidenceGrade.C,
}

_GRADE_PRIORITY: dict[EvidenceGrade, int] = {
    EvidenceGrade.D: 0,
    EvidenceGrade.C: 1,
    EvidenceGrade.B: 2,
    EvidenceGrade.A: 3,
}
_GAP_ORDER: dict[ReviewGapCode, int] = {code: index for index, code in enumerate(ReviewGapCode)}


@dataclass(frozen=True, slots=True)
class DecisiveFailure:
    """Facts sufficient to make one case an effective decisive failure H."""

    case_id: str
    step_id: str
    attempt_id: str
    assertion_ref: str
    basis_revision: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.case_id, "case_id")
        _require_text(self.step_id, "step_id")
        _require_text(self.attempt_id, "attempt_id")
        _require_text(self.assertion_ref, "assertion_ref")
        _require_positive(self.basis_revision, "basis_revision")
        if not self.evidence_refs:
            raise ValueError("decisive failure requires evidence references")
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("evidence_refs must not contain empty values")


@dataclass(frozen=True, slots=True)
class FailureCandidate:
    """Current case failure facts used to derive effective decisive failures H."""

    case_id: str
    step_id: str
    attempt_id: str
    assertion_ref: str
    basis_revision: int
    evidence_refs: tuple[str, ...]
    current_effective_attempt: bool
    necessary_assertion_failed: bool
    basis_confirmed: bool
    failure_verification_valid: bool
    source_identity_matched: bool
    dependencies_valid: bool

    def __post_init__(self) -> None:
        _require_text(self.case_id, "case_id")
        _require_text(self.step_id, "step_id")
        _require_text(self.attempt_id, "attempt_id")
        _require_text(self.assertion_ref, "assertion_ref")
        _require_positive(self.basis_revision, "basis_revision")
        if not self.evidence_refs:
            raise ValueError("failure candidate requires evidence references")
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("evidence_refs must not contain empty values")

    @property
    def is_effective(self) -> bool:
        return all(
            (
                self.current_effective_attempt,
                self.necessary_assertion_failed,
                self.basis_confirmed,
                self.failure_verification_valid,
                self.source_identity_matched,
                self.dependencies_valid,
            )
        )


def derive_decisive_failures(
    candidates: tuple[FailureCandidate, ...],
) -> tuple[DecisiveFailure, ...]:
    """Derive H from current fact guards; ineffective candidates stay historical."""

    return tuple(
        DecisiveFailure(
            case_id=candidate.case_id,
            step_id=candidate.step_id,
            attempt_id=candidate.attempt_id,
            assertion_ref=candidate.assertion_ref,
            basis_revision=candidate.basis_revision,
            evidence_refs=candidate.evidence_refs,
        )
        for candidate in candidates
        if candidate.is_effective
    )


@dataclass(frozen=True, slots=True)
class ReviewGap:
    code: ReviewGapCode
    safe_reason: str
    subject_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.safe_reason, "safe_reason")
        if self.subject_id is not None:
            _require_text(self.subject_id, "subject_id")

    @property
    def grade(self) -> EvidenceGrade:
        return _GAP_GRADES[self.code]


@dataclass(frozen=True, slots=True)
class DecisionFacts:
    """Frozen inputs to the unique phase-one review decision.

    The defaults are deliberately conservative: source and evidence states
    that are not explicitly proven are treated as unverified or incomplete.
    ``noncritical_gaps`` carries B-level facts such as a local unknown; callers
    may append more gaps but cannot lower their grade through this object.
    """

    tier: RunTier
    coverage: Coverage
    decisive_failures: tuple[DecisiveFailure, ...] = ()
    template_required: frozenset[str] = frozenset()
    skipped: frozenset[str] = frozenset()
    pending_verification: frozenset[str] = frozenset()
    outdated_basis: frozenset[str] = frozenset()
    required_unresolved: frozenset[str] = frozenset()
    required_pending: frozenset[str] = frozenset()
    assertion_basis_confirmed: frozenset[str] = frozenset()
    source_identity_state: SourceIdentityState = SourceIdentityState.UNVERIFIED
    critical_paths_satisfied: bool = False
    required_evidence_valid: bool = False
    environment_evidence_complete: bool = False
    critical_unknowns: bool = False
    critical_mock: bool = False
    blocking_issue_ids: frozenset[str] = frozenset()
    noncritical_gaps: tuple[ReviewGap, ...] = ()
    failure_candidates: tuple[FailureCandidate, ...] = ()
    case_ids_revision: str | None = None
    source_commit: str | None = None
    snapshot_commit_id: str | None = None
    snapshot_cursor: int | None = None
    has_applicable_checks: bool = True
    policy_version: str = "1.0"

    def __post_init__(self) -> None:
        _require_text(self.policy_version, "policy_version")
        for value, name in (
            (self.case_ids_revision, "case_ids_revision"),
            (self.source_commit, "source_commit"),
            (self.snapshot_commit_id, "snapshot_commit_id"),
        ):
            if value is not None:
                _require_text(value, name)
        if self.snapshot_cursor is not None and self.snapshot_cursor < 0:
            raise ValueError("snapshot_cursor must be non-negative")
        derived_failures = derive_decisive_failures(self.failure_candidates)
        combined_failures = tuple(
            sorted(
                self.decisive_failures + derived_failures,
                key=lambda failure: (failure.case_id, failure.step_id, failure.attempt_id),
            )
        )
        object.__setattr__(self, "decisive_failures", combined_failures)
        if not self.has_applicable_checks and (self.coverage.selected or self.coverage.required):
            raise ValueError("no-applicable-check facts cannot carry selected or required cases")
        decisive_case_ids = {failure.case_id for failure in self.decisive_failures}
        if not decisive_case_ids <= self.coverage.selected:
            raise ValueError("decisive failures must belong to the selected scope")
        if not self.coverage.failed <= decisive_case_ids:
            raise ValueError("verified whole-case failures must be decisive failures H")


@dataclass(frozen=True, slots=True)
class DecisionResult:
    business_outcome: BusinessOutcome
    evidence_grade: EvidenceGrade | None
    primary_gap: ReviewGap | None
    gaps: tuple[ReviewGap, ...]
    decisive_failures: tuple[DecisiveFailure, ...]
    coverage: Coverage
    policy_version: str
    case_ids_revision: str | None = None
    source_commit: str | None = None
    snapshot_commit_id: str | None = None
    snapshot_cursor: int | None = None

    @property
    def decisive_failure_case_ids(self) -> frozenset[str]:
        return frozenset(failure.case_id for failure in self.decisive_failures)

    @property
    def is_full_pass(self) -> bool:
        return self.business_outcome is BusinessOutcome.PASSED


def _join_ids(values: frozenset[str]) -> str | None:
    return ",".join(sorted(values)) if values else None


def _derive_gaps(facts: DecisionFacts) -> tuple[ReviewGap, ...]:
    if (
        not facts.has_applicable_checks
        and not facts.coverage.selected
        and not facts.coverage.required
    ):
        return ()

    gaps: list[ReviewGap] = []

    def add(code: ReviewGapCode, reason: str, subject: frozenset[str] = frozenset()) -> None:
        gaps.append(ReviewGap(code=code, safe_reason=reason, subject_id=_join_ids(subject)))

    coverage = facts.coverage
    valid_execution_basis = coverage.executed | coverage.reused

    if facts.source_identity_state is SourceIdentityState.MISMATCHED:
        add(
            ReviewGapCode.SOURCE_IDENTITY_MISMATCH,
            "Source identity does not match the executed facts.",
        )
    if not valid_execution_basis:
        add(
            ReviewGapCode.NO_VALID_EXECUTION_BASIS,
            "The run has no valid new execution or reuse basis.",
        )
    if facts.source_identity_state is SourceIdentityState.UNVERIFIED:
        add(
            ReviewGapCode.SOURCE_IDENTITY_UNVERIFIED,
            "Source identity is not verified.",
        )

    template_missing = facts.template_required - coverage.required
    if template_missing:
        add(
            ReviewGapCode.TEMPLATE_REQUIRED_BELOW_FLOOR,
            "Frozen required cases are below the template lower bound.",
            template_missing,
        )

    required_not_selected = coverage.required - coverage.selected
    if facts.tier is RunTier.FULL and required_not_selected:
        add(
            ReviewGapCode.FULL_REQUIRED_SCOPE_MISSING,
            "A full run does not select all frozen required cases.",
            required_not_selected,
        )

    if facts.tier is RunTier.FULL and facts.skipped:
        add(
            ReviewGapCode.FULL_SKIPPED_SCOPE,
            "A full run must not skip applicable cases.",
            facts.skipped,
        )
    if facts.pending_verification & coverage.selected:
        add(
            ReviewGapCode.REQUIRED_CASE_PENDING_VERIFICATION,
            "Selected cases still have pending verification.",
            facts.pending_verification & coverage.selected,
        )
    if facts.outdated_basis & coverage.selected:
        add(
            ReviewGapCode.OUTDATED_BASIS,
            "Selected cases depend on an outdated basis.",
            facts.outdated_basis & coverage.selected,
        )
    if facts.required_unresolved:
        add(
            ReviewGapCode.REQUIRED_CASE_UNRESOLVED,
            "Required cases are unresolved or lack valid evidence.",
            facts.required_unresolved & coverage.required,
        )
    if facts.required_pending:
        add(
            ReviewGapCode.REQUIRED_CASE_PENDING_VERIFICATION,
            "Required cases are pending verification.",
            facts.required_pending & coverage.required,
        )
    if not facts.required_evidence_valid:
        add(
            ReviewGapCode.REQUIRED_EVIDENCE_INVALID,
            "Required evidence is missing, corrupted, or invalid.",
        )
    if not facts.environment_evidence_complete:
        add(
            ReviewGapCode.ENVIRONMENT_EVIDENCE_INCOMPLETE,
            "Environment facts needed for required verification are incomplete.",
        )
    if not facts.critical_paths_satisfied:
        add(
            ReviewGapCode.CRITICAL_PATH_MISSING,
            "At least one required critical path lacks valid evidence.",
        )
    if facts.critical_unknowns:
        add(
            ReviewGapCode.CRITICAL_UNKNOWN,
            "A critical path or required verification remains unknown.",
        )
    if facts.critical_mock:
        add(
            ReviewGapCode.CRITICAL_MOCK,
            "A critical path has an unresolved Mock.",
        )

    unconfirmed = facts.coverage.selected - facts.assertion_basis_confirmed
    if unconfirmed:
        add(
            ReviewGapCode.ASSERTION_BASIS_UNCONFIRMED,
            "Selected cases have an unconfirmed assertion basis.",
            unconfirmed,
        )

    gaps.extend(facts.noncritical_gaps)
    return tuple(gaps)


def _derive_evidence_grade(
    facts: DecisionFacts,
    gaps: tuple[ReviewGap, ...],
) -> EvidenceGrade | None:
    if facts.tier is not RunTier.FULL:
        return None
    if not gaps:
        return EvidenceGrade.A
    return min(gaps, key=lambda gap: _GRADE_PRIORITY[gap.grade]).grade


def _full_pass_conditions_met(
    facts: DecisionFacts,
    decisive_failures: tuple[DecisiveFailure, ...],
) -> bool:
    coverage = facts.coverage
    return (
        facts.tier is RunTier.FULL
        and bool(coverage.selected)
        and bool(coverage.required)
        and not decisive_failures
        and facts.source_identity_state is SourceIdentityState.MATCHED
        and facts.critical_paths_satisfied
        and facts.required_evidence_valid
        and facts.environment_evidence_complete
        and not facts.critical_unknowns
        and not facts.critical_mock
        and not facts.blocking_issue_ids
        and not facts.skipped
        and not (facts.pending_verification & coverage.selected)
        and not (facts.outdated_basis & coverage.selected)
        and not facts.required_unresolved
        and not facts.required_pending
        and facts.template_required <= coverage.required
        and coverage.required <= coverage.selected
        and coverage.required <= facts.assertion_basis_confirmed
        and coverage.passed == coverage.selected
    )


def evaluate_review(facts: DecisionFacts) -> DecisionResult:
    """Derive the only business outcome and, for full runs, evidence grade."""

    decisive_failures = facts.decisive_failures
    gaps = _derive_gaps(facts)
    primary_gap = (
        min(
            gaps,
            key=lambda gap: (
                _GRADE_PRIORITY[gap.grade],
                _GAP_ORDER[gap.code],
                gap.subject_id or "",
            ),
        )
        if gaps
        else None
    )
    evidence_grade = _derive_evidence_grade(facts, gaps)

    if (
        not facts.has_applicable_checks
        and not facts.coverage.selected
        and not facts.coverage.required
    ):
        business_outcome = BusinessOutcome.NOT_APPLICABLE
    elif decisive_failures:
        business_outcome = BusinessOutcome.FAILED
    elif _full_pass_conditions_met(facts, decisive_failures):
        business_outcome = BusinessOutcome.PASSED
    else:
        business_outcome = BusinessOutcome.INCOMPLETE

    return DecisionResult(
        business_outcome=business_outcome,
        evidence_grade=evidence_grade,
        primary_gap=primary_gap,
        gaps=gaps,
        decisive_failures=decisive_failures,
        coverage=facts.coverage,
        policy_version=facts.policy_version,
        case_ids_revision=facts.case_ids_revision,
        source_commit=facts.source_commit,
        snapshot_commit_id=facts.snapshot_commit_id,
        snapshot_cursor=facts.snapshot_cursor,
    )


class ReportExportKind(StrEnum):
    MARKDOWN_SUMMARY = "markdown_summary"
    EVIDENCE_BUNDLE = "evidence_bundle"


@dataclass(frozen=True, slots=True)
class ReportContext:
    """Inputs frozen into a report revision."""

    project_id: str
    run_id: str
    run_revision: int
    plan_revision_refs: tuple[str, ...]
    scope_revision: str
    scope_name: str
    source_identity: str
    environment_ref: str
    rules_revision: str
    acceptance_revision: str
    template_revision: str
    policy_version: str

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "run_id",
            "scope_revision",
            "scope_name",
            "source_identity",
            "environment_ref",
            "rules_revision",
            "acceptance_revision",
            "template_revision",
            "policy_version",
        ):
            _require_text(getattr(self, name), name)
        _require_positive(self.run_revision, "run_revision")
        if not self.plan_revision_refs:
            raise ValueError("plan_revision_refs must not be empty")
        if any(not ref.strip() for ref in self.plan_revision_refs):
            raise ValueError("plan_revision_refs must not contain empty values")


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    """Immutable report revision.

    A changed decision, evidence set, or issue revision creates another
    ReportSnapshot revision; it never mutates an earlier snapshot.
    """

    report_id: str
    content_revision: int
    context: ReportContext
    decision: DecisionResult
    evidence_refs: tuple[str, ...] = ()
    issue_revisions: tuple[tuple[str, int], ...] = ()
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.report_id, "report_id")
        _require_positive(self.content_revision, "content_revision")
        if self.decision.policy_version != self.context.policy_version:
            raise ValueError("report and decision policy versions must match")
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("evidence_refs must not contain empty values")
        issue_ids = [issue_id for issue_id, _ in self.issue_revisions]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("issue revisions must be unique per report")
        for issue_id, revision in self.issue_revisions:
            _require_text(issue_id, "issue_id")
            _require_positive(revision, "issue_revision")

    @property
    def business_outcome(self) -> BusinessOutcome:
        return self.decision.business_outcome

    @property
    def evidence_grade(self) -> EvidenceGrade | None:
        return self.decision.evidence_grade

    @property
    def primary_gap(self) -> ReviewGap | None:
        return self.decision.primary_gap


@dataclass(frozen=True, slots=True)
class ReportDraft:
    report_id: str
    context: ReportContext
    decision: DecisionResult
    evidence_refs: tuple[str, ...] = ()
    issue_revisions: tuple[tuple[str, int], ...] = ()


def create_report_revision(
    previous: ReportSnapshot | None,
    draft: ReportDraft,
    *,
    created_at: datetime | None = None,
) -> ReportSnapshot:
    """Create the next immutable report revision."""

    if previous is not None:
        if previous.report_id != draft.report_id:
            raise ValueError("report revision cannot change report_id")
        if previous.context.project_id != draft.context.project_id:
            raise ValueError("report revision cannot change project")
        content_revision = previous.content_revision + 1
    else:
        content_revision = 1
    return ReportSnapshot(
        report_id=draft.report_id,
        content_revision=content_revision,
        context=draft.context,
        decision=draft.decision,
        evidence_refs=draft.evidence_refs,
        issue_revisions=draft.issue_revisions,
        created_at=created_at,
    )


@dataclass(frozen=True, slots=True)
class LocalReview:
    review_id: str
    project_id: str
    report_id: str
    report_revision: int
    reviewer: str
    conclusion: str
    explanation: str
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "review_id",
            "project_id",
            "report_id",
            "reviewer",
            "conclusion",
            "explanation",
        ):
            _require_text(getattr(self, name), name)
        _require_positive(self.report_revision, "report_revision")


@dataclass(frozen=True, slots=True)
class ReportExportKey:
    report_id: str
    report_revision: int
    review_ids: tuple[str, ...]
    kind: ReportExportKind
    redaction_policy_version: str
    attachment_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.report_id, "report_id")
        _require_positive(self.report_revision, "report_revision")
        _require_text(self.redaction_policy_version, "redaction_policy_version")
        if len(self.review_ids) != len(set(self.review_ids)):
            raise ValueError("review_ids must be unique")
        if any(not review_id.strip() for review_id in self.review_ids):
            raise ValueError("review_ids must not contain empty values")
        if len(self.attachment_refs) != len(set(self.attachment_refs)):
            raise ValueError("attachment_refs must be unique")
        if any(not ref.strip() for ref in self.attachment_refs):
            raise ValueError("attachment_refs must not contain empty values")
        object.__setattr__(self, "review_ids", tuple(sorted(self.review_ids)))
        object.__setattr__(self, "attachment_refs", tuple(sorted(self.attachment_refs)))


@dataclass(frozen=True, slots=True)
class ReportExport:
    export_id: str
    key: ReportExportKey
    artifact_ref: str
    artifact_digest: str
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("export_id", "artifact_ref", "artifact_digest"):
            _require_text(getattr(self, name), name)


def validate_reviews_for_report(
    report: ReportSnapshot,
    reviews: tuple[LocalReview, ...],
) -> None:
    review_ids = [review.review_id for review in reviews]
    if len(review_ids) != len(set(review_ids)):
        raise ValueError("reviews must be unique")
    for review in reviews:
        if review.project_id != report.context.project_id:
            raise ValueError("review belongs to a different project")
        if review.report_id != report.report_id:
            raise ValueError("review belongs to a different report")
        if review.report_revision != report.content_revision:
            raise ValueError("review must bind the exact report revision")


def create_report_export(
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
    """Create or idempotently reuse an export for one frozen report input."""

    if report_revision != report.content_revision:
        raise ValueError("export must freeze the exact report revision")
    validate_reviews_for_report(report, reviews)
    if {review.review_id for review in reviews} != set(review_ids):
        raise ValueError("export review_ids must match the supplied reviews")
    key = ReportExportKey(
        report_id=report.report_id,
        report_revision=report_revision,
        review_ids=review_ids,
        kind=kind,
        redaction_policy_version=redaction_policy_version,
        attachment_refs=attachment_refs,
    )
    for existing in existing_exports:
        if existing.key != key:
            continue
        if existing.artifact_ref != artifact_ref or existing.artifact_digest != artifact_digest:
            raise ValueError("same export key produced a different artifact")
        return existing
    return ReportExport(
        export_id=export_id,
        key=key,
        artifact_ref=artifact_ref,
        artifact_digest=artifact_digest,
        created_at=created_at,
    )


__all__ = [
    "BusinessOutcome",
    "Coverage",
    "DecisionFacts",
    "DecisionResult",
    "DecisiveFailure",
    "EvidenceGrade",
    "FailureCandidate",
    "LocalReview",
    "ReportContext",
    "ReportDraft",
    "ReportExport",
    "ReportExportKey",
    "ReportExportKind",
    "ReportSnapshot",
    "ReviewGap",
    "ReviewGapCode",
    "SourceIdentityState",
    "create_report_export",
    "create_report_revision",
    "derive_decisive_failures",
    "evaluate_review",
    "validate_reviews_for_report",
]
