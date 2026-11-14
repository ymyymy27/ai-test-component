"""报告/复核的 payload 编解码（从 saved_reports.py 拆出）。

只做形状与摘要，不做业务判定；服务层继续 re-export 这两个公开函数。
"""



from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields
from datetime import datetime
from typing import Any

from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.domain.review.function_coverage import FunctionCoverage
from aitest.domain.review.reports import (
    BusinessOutcome,
    Coverage,
    DecisionResult,
    DecisiveFailure,
    EvidenceGrade,
    LocalReview,
    ReportContext,
    ReportSnapshot,
    ReviewGap,
    ReviewGapCode,
)


class ReportManagementError(ValueError):
    """D 侧报告用例错误；`code` 只复用 `contracts/errors.py` 已登记的错误码。"""

    code = "INVALID_REQUEST"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(message)


REPORT_AGGREGATE = "report"
REPORT_INTENT_AGGREGATE = "report_intent"
LOCAL_REVIEW_AGGREGATE = "local_review"
REPORT_SCHEMA = "aitest.report/1.0"
LOCAL_REVIEW_SCHEMA = "aitest.local-review/1.0"
REPORT_INTENT_SCHEMA = "aitest.report-intent/1.0"

#: 一期尚无带版本的判定策略来源；与 `DecisionContextFacts` 默认值一致。
DEFAULT_POLICY_VERSION = "1.0"

#: 一期动作表里本模块接管的动作。
SUPPORTED_ACTIONS = ("create_report", "record_local_review")

_CONTEXT_FIELDS = tuple(field.name for field in fields(ReportContext))



def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()

def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportManagementError(f"{name} must be nonempty text")
    return value

def _text_list(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ReportManagementError(f"{name} must be a list of strings")
    items = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ReportManagementError(f"{name} must be a list of nonempty strings")
    if len(set(items)) != len(items):
        raise ReportManagementError(f"{name} must not repeat values")
    return items

def _sequence_number(payload: Mapping[str, object]) -> int:
    value = payload.get("published_sequence", 0)
    return value if type(value) is int else 0

def _sequence_len(value: object) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0

def _revision_mapping(
    value: object, evidence_refs: tuple[str, ...], name: str
) -> dict[str, int]:
    """Parse the optional evidence-id → revision map; old records return {}."""

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ReportManagementError(f"{name} must be an object")
    revisions: dict[str, int] = {}
    for identity, revision in value.items():
        if not isinstance(identity, str) or not identity.strip():
            raise ReportManagementError(f"{name} keys must be nonempty strings")
        if identity not in evidence_refs:
            raise ReportManagementError(f"{name} references undeclared evidence")
        if type(revision) is not int or revision < 1:
            raise ReportManagementError(f"{name} values must be positive integers")
        revisions[identity] = revision
    return revisions

def _gap_to_payload(gap: ReviewGap) -> dict[str, object]:
    return {"code": gap.code.value, "safe_reason": gap.safe_reason, "subject_id": gap.subject_id}

def _gap_from_payload(raw: object) -> ReviewGap:
    if not isinstance(raw, Mapping):
        raise ReportManagementError("saved report gap is unreadable")
    subject = raw.get("subject_id")
    return ReviewGap(
        code=ReviewGapCode(_text(raw.get("code"), "gap.code")),
        safe_reason=_text(raw.get("safe_reason"), "gap.safe_reason"),
        subject_id=None if subject is None else _text(subject, "gap.subject_id"),
    )

def _failure_to_payload(failure: DecisiveFailure) -> dict[str, object]:
    return {
        "case_id": failure.case_id,
        "step_id": failure.step_id,
        "attempt_id": failure.attempt_id,
        "assertion_ref": failure.assertion_ref,
        "basis_revision": failure.basis_revision,
        "evidence_refs": list(failure.evidence_refs),
    }

def _failure_from_payload(raw: object) -> DecisiveFailure:
    if not isinstance(raw, Mapping):
        raise ReportManagementError("saved decisive failure is unreadable")
    revision = raw.get("basis_revision")
    if type(revision) is not int or revision < 1:
        raise ReportManagementError("saved decisive failure basis revision is unreadable")
    return DecisiveFailure(
        case_id=_text(raw.get("case_id"), "failure.case_id"),
        step_id=_text(raw.get("step_id"), "failure.step_id"),
        attempt_id=_text(raw.get("attempt_id"), "failure.attempt_id"),
        assertion_ref=_text(raw.get("assertion_ref"), "failure.assertion_ref"),
        basis_revision=revision,
        evidence_refs=_text_list(raw.get("evidence_refs"), "failure.evidence_refs"),
    )

def _coverage_to_payload(coverage: Coverage) -> dict[str, object]:
    return {
        "selected": sorted(coverage.selected),
        "required": sorted(coverage.required),
        "executed": sorted(coverage.executed),
        "reused": sorted(coverage.reused),
        "verified": sorted(coverage.verified),
        "passed": sorted(coverage.passed),
    }

def _coverage_from_payload(raw: object) -> Coverage:
    if not isinstance(raw, Mapping):
        raise ReportManagementError("saved report coverage is unreadable")
    return Coverage(
        selected=frozenset(_text_list(raw.get("selected"), "coverage.selected")),
        required=frozenset(_text_list(raw.get("required"), "coverage.required")),
        executed=frozenset(_text_list(raw.get("executed"), "coverage.executed")),
        reused=frozenset(_text_list(raw.get("reused"), "coverage.reused")),
        verified=frozenset(_text_list(raw.get("verified"), "coverage.verified")),
        passed=frozenset(_text_list(raw.get("passed"), "coverage.passed")),
    )

def _function_coverage_to_payload(value: FunctionCoverage) -> dict[str, object]:
    """报告中的功能×维度收据；只序列化领域已投影的状态。"""

    return {
        "function_id": value.function_id,
        "dimensions": {
            dimension: status.value
            for dimension, status in value.coverage_by_dimension.items()
        },
        "overall_status": value.overall_coverage.value,
        "gap_reason": value.gap_reason,
        "case_ids": sorted(value.case_ids),
        "evidence_refs": list(value.evidence_refs),
        "verification_refs": list(value.verification_refs),
    }

def _report_confidence(
    decision: DecisionResult, function_coverage: tuple[FunctionCoverage, ...]
) -> str:
    """High/medium/low is a summary of evidence, never an AI score."""

    if decision.business_outcome.value == "incomplete":
        return "low"
    statuses = {item.overall_coverage.value for item in function_coverage}
    if statuses & {"unsupported", "unknown", "not_proven"}:
        return "low"
    grade = decision.evidence_grade.value if decision.evidence_grade is not None else None
    if grade == "A" and statuses <= {"proven", "not_applicable"}:
        return "high"
    return "medium"

def _decision_to_payload(
    decision: DecisionResult,
    function_coverage: tuple[FunctionCoverage, ...] = (),
) -> dict[str, object]:
    grade = decision.evidence_grade
    coverage = _coverage_to_payload(decision.coverage)
    if function_coverage:
        coverage["function_coverage"] = [
            _function_coverage_to_payload(item) for item in function_coverage
        ]
    return {
        "business_outcome": decision.business_outcome.value,
        "confidence": _report_confidence(decision, function_coverage),
        "evidence_grade": None if grade is None else grade.value,
        "primary_gap": (
            None if decision.primary_gap is None else _gap_to_payload(decision.primary_gap)
        ),
        "gaps": [_gap_to_payload(gap) for gap in decision.gaps],
        "decisive_failures": [_failure_to_payload(item) for item in decision.decisive_failures],
        "coverage": coverage,
        "policy_version": decision.policy_version,
        "case_ids_revision": decision.case_ids_revision,
        "source_commit": decision.source_commit,
        "snapshot_commit_id": decision.snapshot_commit_id,
        "snapshot_cursor": decision.snapshot_cursor,
    }

def _decision_from_payload(raw: object) -> DecisionResult:
    if not isinstance(raw, Mapping):
        raise ReportManagementError("saved report decision is unreadable")
    gaps = raw.get("gaps")
    failures = raw.get("decisive_failures")
    if not isinstance(gaps, (list, tuple)) or not isinstance(failures, (list, tuple)):
        raise ReportManagementError("saved report gap/failure lists are unreadable")
    grade = raw.get("evidence_grade")
    gaps_tuple = tuple(_gap_from_payload(item) for item in gaps)
    primary = raw.get("primary_gap")
    primary_gap = None if primary is None else _gap_from_payload(primary)
    if primary_gap is not None and primary_gap not in gaps_tuple:
        raise ReportManagementError("saved primary gap is not part of the saved gaps")
    revision = raw.get("case_ids_revision")
    commit = raw.get("source_commit")
    snapshot_commit = raw.get("snapshot_commit_id")
    cursor = raw.get("snapshot_cursor")
    if cursor is not None and (type(cursor) is not int or cursor < 0):
        raise ReportManagementError("saved report snapshot cursor is unreadable")
    return DecisionResult(
        business_outcome=BusinessOutcome(_text(raw.get("business_outcome"), "outcome")),
        evidence_grade=None if grade is None else EvidenceGrade(_text(grade, "evidence_grade")),
        primary_gap=primary_gap,
        gaps=gaps_tuple,
        decisive_failures=tuple(_failure_from_payload(item) for item in failures),
        coverage=_coverage_from_payload(raw.get("coverage")),
        policy_version=_text(raw.get("policy_version"), "policy_version"),
        case_ids_revision=None if revision is None else _text(revision, "case_ids_revision"),
        source_commit=None if commit is None else _text(commit, "source_commit"),
        snapshot_commit_id=(
            None if snapshot_commit is None else _text(snapshot_commit, "snapshot_commit_id")
        ),
        snapshot_cursor=cursor,
    )

def report_to_payload(
    snapshot: ReportSnapshot,
    *,
    published_sequence: int,
    reuse: Mapping[str, object] | None = None,
    execution_snapshot_ref: SnapshotContentRef | None = None,
    blocking_issue_ids: frozenset[str] | None = None,
    function_coverage: tuple[FunctionCoverage, ...] = (),
    pytest_failures: Sequence[Mapping[str, object]] = (),
    pytest_baseline: Mapping[str, object] | None = None,
    layers: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """报告修订 payload；同时给出 `reports.list` / `reports.by_id` 的最小摘要键。

    `reuse` 记录整用例复用（R）的消费情况：候选条数、计入的用例与**具名拒绝原因**。
    候选来源未接时必须是 `{"candidates": None, ...}`——"没接"与"核过但没有候选"
    不是同一个事实，不能都写 0。
    """

    if type(published_sequence) is not int or published_sequence < 0:
        raise ReportManagementError("published_sequence must be a nonnegative integer")
    context = {name: getattr(snapshot.context, name) for name in _CONTEXT_FIELDS}
    context["plan_revision_refs"] = list(snapshot.context.plan_revision_refs)
    payload: dict[str, object] = {
        "schema_version": REPORT_SCHEMA,
        "report_id": snapshot.report_id,
        "project_id": snapshot.context.project_id,
        "content_revision": snapshot.content_revision,
        "context": context,
        "decision": _decision_to_payload(snapshot.decision, function_coverage),
        "evidence_refs": list(snapshot.evidence_refs),
        "evidence_revisions": dict(snapshot.evidence_revisions),
        "issue_revisions": [list(item) for item in snapshot.issue_revisions],
        "reuse": dict(reuse)
        if reuse is not None
        else {"candidates": None, "reason": "reuse_candidate_source_not_configured"},
        "created_at": None if snapshot.created_at is None else snapshot.created_at.isoformat(),
        # 存储侧最小摘要白名单使用的事实：
        "run_id": snapshot.context.run_id,
        "business_outcome": snapshot.decision.business_outcome.value,
        "published_sequence": published_sequence,
    }
    if execution_snapshot_ref is not None:
        payload["execution_snapshot_ref"] = execution_snapshot_ref.model_dump(mode="json")
    if blocking_issue_ids is not None:
        payload["blocking_issue_ids"] = sorted(blocking_issue_ids)
    if pytest_failures:
        payload["pytest_failures"] = [dict(item) for item in pytest_failures]
    if pytest_baseline is not None:
        payload["pytest_baseline"] = dict(pytest_baseline)
    if layers:
        payload["layers"] = [dict(item) for item in layers]
    return payload

def report_from_payload(payload: Mapping[str, object]) -> ReportSnapshot:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != REPORT_SCHEMA:
        raise ReportManagementError("saved report schema cannot be verified")
    raw_context = payload.get("context")
    if not isinstance(raw_context, Mapping) or set(raw_context) != set(_CONTEXT_FIELDS):
        raise ReportManagementError("saved report context is unreadable")
    values: dict[str, Any] = {}
    for name in _CONTEXT_FIELDS:
        if name == "plan_revision_refs":
            values[name] = _text_list(raw_context.get(name), "plan_revision_refs")
        elif name == "run_revision":
            revision = raw_context.get(name)
            if type(revision) is not int or revision < 1:
                raise ReportManagementError("saved report run revision is unreadable")
            values[name] = revision
        else:
            values[name] = _text(raw_context.get(name), name)
    context = ReportContext(**values)
    revisions = payload.get("issue_revisions")
    if not isinstance(revisions, (list, tuple)):
        raise ReportManagementError("saved report issue revisions are unreadable")
    issue_revisions: list[tuple[str, int]] = []
    for item in revisions:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ReportManagementError("saved report issue revision is unreadable")
        issue_id = _text(item[0], "issue_id")
        revision = item[1]
        if type(revision) is not int or revision < 1:
            raise ReportManagementError("saved report issue revision is unreadable")
        issue_revisions.append((issue_id, revision))
    content_revision = payload.get("content_revision")
    if type(content_revision) is not int or content_revision < 1:
        raise ReportManagementError("saved report content revision is unreadable")
    created_at = payload.get("created_at")
    if created_at is not None:
        created_at = _text(created_at, "created_at")
    evidence_refs = _text_list(payload.get("evidence_refs"), "evidence_refs")
    return ReportSnapshot(
        report_id=_text(payload.get("report_id"), "report_id"),
        content_revision=content_revision,
        context=context,
        decision=_decision_from_payload(payload.get("decision")),
        evidence_refs=evidence_refs,
        evidence_revisions=_revision_mapping(
            payload.get("evidence_revisions"), evidence_refs, "evidence_revisions"
        ),
        issue_revisions=tuple(issue_revisions),
        created_at=None if created_at is None else datetime.fromisoformat(created_at),
    )

def local_review_to_payload(review: LocalReview) -> dict[str, object]:
    return {
        "schema_version": LOCAL_REVIEW_SCHEMA,
        "review_id": review.review_id,
        "project_id": review.project_id,
        "report_id": review.report_id,
        "report_revision": review.report_revision,
        "reviewer": review.reviewer,
        "conclusion": review.conclusion,
        "explanation": review.explanation,
        "recorded_at": None if review.recorded_at is None else review.recorded_at.isoformat(),
    }

def local_review_from_payload(payload: Mapping[str, object]) -> LocalReview:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != LOCAL_REVIEW_SCHEMA:
        raise ReportManagementError("saved local review schema cannot be verified")
    revision = payload.get("report_revision")
    if type(revision) is not int or revision < 1:
        raise ReportManagementError("saved local review report revision is unreadable")
    recorded_at = payload.get("recorded_at")
    return LocalReview(
        review_id=_text(payload.get("review_id"), "review_id"),
        project_id=_text(payload.get("project_id"), "project_id"),
        report_id=_text(payload.get("report_id"), "report_id"),
        report_revision=revision,
        reviewer=_text(payload.get("reviewer"), "reviewer"),
        conclusion=_text(payload.get("conclusion"), "conclusion"),
        explanation=_text(payload.get("explanation"), "explanation"),
        recorded_at=(
            None
            if recorded_at is None
            else datetime.fromisoformat(_text(recorded_at, "recorded_at"))
        ),
    )

def _command_identity(command: object) -> tuple[str, str, str, int]:
    project_id = _text(getattr(command, "project_id", None), "project_id")
    intent_id = _text(getattr(command, "intent_id", None), "intent_id")
    request_id = _text(getattr(command, "request_id", None), "request_id")
    expected = getattr(command, "expected_revision", None)
    if type(expected) is not int or expected < 0:
        raise ReportManagementError("expected_revision must be a nonnegative integer")
    return project_id, intent_id, request_id, expected

def _command_parameters(command: object) -> Mapping[str, object]:
    parameters = getattr(command, "parameters", None)
    if not isinstance(parameters, Mapping):
        raise ReportManagementError("parameters must be an object")
    return parameters
