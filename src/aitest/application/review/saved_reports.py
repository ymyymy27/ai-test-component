"""Persisted report revisions and local reviews (P1-FR14).

Contracts: phase-one architecture 03 (frozen report/export inputs and blocking
issues), architecture 04 (finite indexes and exact revisions), and CORE-001
(callers do not supply decision or eligibility booleans).

The shared assertion producer and decision rules consume saved execution,
effective controlled basis confirmation and fully proved whole-case reuse.
Finite issue queries collect all scope-related blockers and their canonical
chains; issue_ids cannot remove known blockers. Read/publication sequence
checks reject mixed boundaries. Reports retain exact execution and issue
references for exports; old records without exact sources retain gaps.

Dynamic reuse environment/dependency proof and default business verification
still require trustworthy inputs. policy_version uses the current 1.0 policy;
case_ids_revision uses the frozen scope identity. A local review pins the exact
saved report revision and never rewrites that report.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Protocol, cast

from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.planning.preparation import preparation_identity_digest
from aitest.application.planning.preparation_origin import (
    load_saved_preparation,
    validate_preparation_origin,
)
from aitest.application.planning.serialization import acceptance_scope_from_payload
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import AggregateKind, RecordQuery
from aitest.application.review import reporting as facade
from aitest.application.review.authoritative_basis import SavedCaseBasisCheck
from aitest.application.review.execution_decision_adapter import (
    AssertionOutcomeFact,
    assertion_outcomes_from_facts,
    build_decision_facts,
)
from aitest.application.review.reuse_consumption import ReuseOutcome, derive_reuse_outcome
from aitest.application.review.saved_defects import (
    ISSUE_AGGREGATE,
    blocking_state,
    issue_from_payload,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.reuse_reference import ReuseReference
from aitest.domain.execution.reuse import WholeCaseReuseBasis
from aitest.domain.review.defects import IssueRecord, resolve_canonical_issue_id
from aitest.domain.review.reports import (
    BusinessOutcome,
    Coverage,
    DecisionResult,
    DecisiveFailure,
    EvidenceGrade,
    LocalReview,
    ReportContext,
    ReportDraft,
    ReportSnapshot,
    ReviewGap,
    ReviewGapCode,
    create_report_revision,
)

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


class ReportManagementError(ValueError):
    """D 侧报告用例错误；`code` 只复用 `contracts/errors.py` 已登记的错误码。"""

    code = "INVALID_REQUEST"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(message)


class ReportRevisionConflict(ReportManagementError):
    code = "RECORD_REVISION_CONFLICT"

    def __init__(self, *, expected: int, current: int) -> None:
        self.expected, self.current = expected, current
        super().__init__(f"report revision conflict: expected {expected}, current {current}")


class ReportNotSaved(ReportManagementError):
    code = "RECORD_NOT_FOUND"


class ReportIntentConflict(ReportManagementError):
    code = "INTENT_CONFLICT"


class ReportGuardRefused(ReportManagementError):
    code = "INVALID_REQUEST"


class ReportActorRequired(ReportManagementError):
    code = "AWAITING_USER_CONFIRMATION"


class ReportUnitOfWork(Protocol):
    def begin(self, request_id: str, project_id: str, *, intent_id: str | None = None) -> Any: ...
    def next_commit_seq(self) -> str: ...
    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> Any: ...
    def commit(self, request_id: str | None = None) -> Any: ...
    def rollback(self, request_id: str | None = None) -> Any: ...


class ReportRecordRepository(Protocol):
    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int: ...
    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> Any: ...
    def query(self, query: RecordQuery) -> Any: ...


class ReportRuntimeFactsReader(Protocol):
    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> ExecutionFacts: ...


class ReportActorReader(Protocol):
    def current(self) -> Any: ...


class ReportClock(Protocol):
    def now(self) -> datetime: ...


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


# ------------------------------------------------------------------ 编解码


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


def _decision_to_payload(decision: DecisionResult) -> dict[str, object]:
    grade = decision.evidence_grade
    return {
        "business_outcome": decision.business_outcome.value,
        "evidence_grade": None if grade is None else grade.value,
        "primary_gap": (
            None if decision.primary_gap is None else _gap_to_payload(decision.primary_gap)
        ),
        "gaps": [_gap_to_payload(gap) for gap in decision.gaps],
        "decisive_failures": [_failure_to_payload(item) for item in decision.decisive_failures],
        "coverage": _coverage_to_payload(decision.coverage),
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
        "decision": _decision_to_payload(snapshot.decision),
        "evidence_refs": list(snapshot.evidence_refs),
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
    return ReportSnapshot(
        report_id=_text(payload.get("report_id"), "report_id"),
        content_revision=content_revision,
        context=context,
        decision=_decision_from_payload(payload.get("decision")),
        evidence_refs=_text_list(payload.get("evidence_refs"), "evidence_refs"),
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


@dataclass(frozen=True, slots=True)
class _ReuseReading:
    """一次 R 消费的读数：`candidates is None` ＝ 候选来源未接（不是"0 条"）。"""

    outcome: ReuseOutcome | None
    candidates: int | None

    def payload(self) -> dict[str, object]:
        if self.outcome is None:
            return {"candidates": None, "reason": "reuse_candidate_source_not_configured"}
        return {
            "candidates": self.candidates,
            "reused": sorted(self.outcome.reused),
            "denials": [
                {"case_id": case_id, "reasons": list(reasons)}
                for case_id, reasons in self.outcome.named_reasons
            ],
        }


@dataclass(frozen=True, slots=True)
class _LiveDecision:
    """一次实时判定的全部输入与结果（报告与面板 DTO 共用）。"""

    facts: ExecutionFacts
    prepared: Any
    scope: Any
    issues: tuple[Any, ...]
    reuse: _ReuseReading
    decision: DecisionResult
    source_sequence: int
    blocking_issue_ids: frozenset[str]


class SavedReportService:
    """报告修订与本地复核的生产用例：权威读取、领域冻结、同一工作单元持久化。"""

    def __init__(
        self,
        *,
        unit: ReportUnitOfWork,
        reader: RecordReader,
        records: ReportRecordRepository,
        clock: ReportClock,
        actors: ReportActorReader,
        runtime: ReportRuntimeFactsReader,
        workspace_id: str,
        basis_proof: object | None = None,
        case_basis: Any | None = None,
        reuse_references: Callable[[str, str], tuple[ReuseReference, ...]] | None = None,
        reuse_basis: Callable[
            [ReuseReference, ExecutionFacts, ExecutionFacts], WholeCaseReuseBasis | None
        ]
        | None = None,
    ) -> None:
        for name, value in (
            ("unit", unit),
            ("reader", reader),
            ("records", records),
            ("clock", clock),
            ("actors", actors),
            ("runtime", runtime),
        ):
            if value is None:
                raise ReportManagementError(
                    f"report use cases require the {name} capability",
                    code="CAPABILITY_UNAVAILABLE",
                )
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ReportManagementError("report use cases require a workspace identity")
        self._unit, self._reader, self._records = unit, reader, records
        self._clock, self._actors, self._runtime = clock, actors, runtime
        self._workspace_id = workspace_id
        self._bases = SavedCaseBasisCheck(
            reader=reader,
            records=records,
            workspace_id=workspace_id,
            basis_proof=basis_proof,
            override=case_basis,
        )
        #: 整用例复用候选来源（`(project_id, run_id) -> 已保存引用`）。生产端保存动作
        #: 仍缺合同冻结名称，因此默认 `None`：报告把 R 记为"候选来源未接"而不是空结论。
        self._reuse_references = reuse_references
        self._reuse_basis = reuse_basis

    # ------------------------------------------------------------ 动作入口

    def create_report(self, command: object) -> Mapping[str, object]:
        return self._create_report(command)

    def record_local_review(self, command: object) -> Mapping[str, object]:
        return self._record_local_review(command)

    def live_decision(
        self, project_id: str, run_id: str, *, issue_ids: tuple[str, ...] = ()
    ) -> tuple[ExecutionFacts, DecisionResult]:
        """实时判定（只读）：**唯一**装配点，报告与面板 DTO 共用同一次领域判定。

        架构 06 §1/§5 要求运行 DTO 与报告用同一领域判定（等级、覆盖、缺口只能在领域算一次），
        因此把 `create_report` 里的判定装配提成公开方法：它读已提交事实 + 冻结准备 + 范围 +
        问题阻塞 + 依据确认 + R 消费，再调 `evaluate_review`，**不写任何记录**。
        """

        live = self._live(project_id, run_id, issue_ids)
        return live.facts, live.decision

    def _live(self, project_id: str, run_id: str, issue_ids: tuple[str, ...]) -> _LiveDecision:
        source_sequence = self._next_sequence()
        facts = self._facts(project_id, run_id)
        prepared = self._prepared(project_id, facts.run.intent_id)
        scope = self._scope(project_id, prepared)
        issues = self._issues(
            project_id,
            issue_ids,
            selected_cases=frozenset(facts.run.selected_scope),
            selected_modules=frozenset(
                module
                for case in prepared.frozen_cases
                if case.case_id in facts.run.selected_scope
                for module in case.links.module_ids
            ),
        )
        graph = {issue.issue_id: issue for issue in issues}
        blocking_ids: set[str] = set()
        for issue in issues:
            root_id = resolve_canonical_issue_id(issue.issue_id, graph)
            if blocking_state(issue, root=graph[root_id], issues=graph) != "non_blocking":
                blocking_ids.add(root_id)
        blocking = frozenset(blocking_ids)
        confirmed_cases = frozenset(
            case.case_id
            for case in prepared.frozen_cases
            if self._bases.for_facts(facts, case.case_id)
        )
        critical_chain = frozenset(
            item for case in prepared.frozen_cases for item in case.links.critical_path_ids
        )
        reuse = self._reuse_outcome(facts)
        outcomes = assertion_outcomes_from_facts(
            facts,
            basis_revision_for=lambda case_id: self._bases.revision(
                project_id, facts.run.intent_id, case_id, facts=facts
            ),
            basis_confirmed_for=lambda case_id: case_id in confirmed_cases,
        )
        decision = self._decision(
            facts,
            scope_template_cases=frozenset(scope.template_case_ids),
            confirmed_cases=confirmed_cases,
            blocking_issue_ids=blocking,
            critical_chain=critical_chain,
            environment_complete=prepared.environment.resolution is not None,
            reuse=reuse.outcome,
            assertion_outcomes=outcomes,
        )
        if self._next_sequence() != source_sequence:
            raise ReportGuardRefused("decision inputs changed while reading the committed boundary")
        return _LiveDecision(
            facts=facts,
            prepared=prepared,
            scope=scope,
            issues=issues,
            reuse=reuse,
            decision=decision,
            source_sequence=source_sequence,
            blocking_issue_ids=blocking,
        )

    # ------------------------------------------------------------ 报告修订

    def _create_report(self, command: object) -> Mapping[str, object]:
        project_id, intent_id, request_id, expected = _command_identity(command)
        parameters = _command_parameters(command)
        unknown = set(parameters) - {"report_id", "run_id", "issue_ids"}
        if unknown:
            raise ReportManagementError(
                f"create_report received unknown parameters: {sorted(unknown)}"
            )
        report_id = _text(parameters.get("report_id"), "report_id")
        run_id = _text(parameters.get("run_id"), "run_id")
        issue_ids = _text_list(parameters.get("issue_ids"), "issue_ids")

        fingerprint = _digest(["create_report", report_id, expected, run_id, list(issue_ids)])
        intent_record_id = "report-intent-" + _digest([project_id, intent_id])[7:]
        recalled = self._recall(
            project_id, intent_record_id, "create_report", report_id, fingerprint
        )
        if recalled is not None:
            return recalled

        current = self._current_report(project_id, report_id)
        current_revision = 0 if current is None else cast(int, current["content_revision"])
        if expected != current_revision:
            raise ReportRevisionConflict(expected=expected, current=current_revision)

        live = self._live(project_id, run_id, issue_ids)
        facts, decision, prepared, scope = live.facts, live.decision, live.prepared, live.scope
        issues, reuse = live.issues, live.reuse
        if decision.policy_version != DEFAULT_POLICY_VERSION:
            raise ReportGuardRefused("saved decision policy version cannot be verified")
        context = ReportContext(
            project_id=project_id,
            run_id=run_id,
            run_revision=facts.run_revision,
            plan_revision_refs=self._plan_refs(facts),
            scope_revision=f"scope:{scope.scope_id}@{scope.revision}",
            scope_name=scope.name,
            source_identity=prepared.snapshot.content_identity,
            environment_ref=facts.run.environment_ref,
            rules_revision=facts.run.rules_revision,
            acceptance_revision=str(prepared.acceptance_scope_revision),
            template_revision=self._template_revision(prepared),
            policy_version=DEFAULT_POLICY_VERSION,
        )
        previous = None if current is None else report_from_payload(current["payload"])
        snapshot = create_report_revision(
            previous,
            ReportDraft(
                report_id=report_id,
                context=context,
                decision=decision,
                evidence_refs=tuple(sorted({item.evidence_id for item in facts.evidence_refs})),
                issue_revisions=tuple(sorted((issue.issue_id, issue.revision) for issue in issues)),
            ),
            created_at=self._clock.now(),
        )
        sequence = self._next_sequence()
        payload = report_to_payload(
            snapshot,
            published_sequence=sequence,
            reuse=reuse.payload(),
            execution_snapshot_ref=SnapshotContentRef.of(facts),
            blocking_issue_ids=live.blocking_issue_ids,
        )
        return self._commit(
            action="create_report",
            project_id=project_id,
            record_id=report_id,
            intent_id=intent_id,
            request_id=request_id,
            intent_record_id=intent_record_id,
            fingerprint=fingerprint,
            aggregate_kind=REPORT_AGGREGATE,
            payload=payload,
            expected_revision=current_revision,
            revision=snapshot.content_revision,
            source_sequence=live.source_sequence,
        )

    def _decision(
        self,
        facts: ExecutionFacts,
        *,
        scope_template_cases: frozenset[str],
        confirmed_cases: frozenset[str],
        blocking_issue_ids: frozenset[str],
        critical_chain: frozenset[str],
        environment_complete: bool,
        reuse: ReuseOutcome | None,
        assertion_outcomes: tuple[AssertionOutcomeFact, ...] = (),
    ) -> DecisionResult:
        """只用已提交事实求判定；断言结果由 `assertion_outcomes_from_facts` 从事实派生。"""

        from aitest.application.review.execution_decision_adapter import DecisionContextFacts
        from aitest.domain.review.reports import evaluate_review

        context = DecisionContextFacts(
            template_required_case_ids=scope_template_cases,
            assertion_basis_confirmed_case_ids=confirmed_cases,
            required_critical_chain_item_ids=critical_chain,
            environment_evidence_complete=environment_complete,
            blocking_issue_ids=blocking_issue_ids,
            policy_version=DEFAULT_POLICY_VERSION,
        )
        return evaluate_review(
            build_decision_facts(
                facts,
                assertion_outcomes=assertion_outcomes,
                context=context,
                reuse=reuse,
            )
        )

    def _reuse_outcome(self, facts: ExecutionFacts) -> _ReuseReading:
        """整用例复用（R）的消费：候选来源未接时显式记为"未接"。

        候选由 select_case_reuse 保存；来源未装配时显式记为未接。
        缺完整依据时拒绝 R，并给出具名原因；动态资格和来源材料不得猜测。
        """

        if self._reuse_references is None:
            return _ReuseReading(outcome=None, candidates=None)
        references = self._reuse_references(facts.project_id, facts.run_id)
        if not references:
            return _ReuseReading(outcome=ReuseOutcome(reused=frozenset()), candidates=0)
        return _ReuseReading(
            outcome=derive_reuse_outcome(
                facts,
                references,
                source_facts=lambda run_id: self._source_facts(facts.project_id, run_id),
                evidence_readable=self._evidence_saved,
                basis_confirmed=lambda case_id: self._bases.for_facts(facts, case_id),
                complete_basis=self._reuse_basis,
                basis_revision_for=lambda source, case: self._bases.revision(
                    source.project_id,
                    source.run.intent_id,
                    case,
                    facts=source,
                ),
            ),
            candidates=len(references),
        )

    def _source_facts(self, project_id: str, run_id: str) -> ExecutionFacts | None:
        try:
            return self._runtime.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def _evidence_saved(self, evidence_id: str) -> bool:
        revision = self._records.current_revision(
            aggregate_kind="evidence_ref", record_id=evidence_id
        )
        return type(revision) is int and revision >= 1

    def _plan_refs(self, facts: ExecutionFacts) -> tuple[str, ...]:
        refs = [
            f"{facts.plan_revision.revision_id}@{facts.plan_revision.revision_no}",
            *facts.runtime_revision_refs,
        ]
        if not refs:
            raise ReportGuardRefused("the saved run has no plan revision reference")
        return tuple(dict.fromkeys(refs))

    def _template_revision(self, prepared: Any) -> str:
        versions = [f"{item.template_id}@{item.version}" for item in prepared.template_versions]
        if not versions:
            raise ReportGuardRefused("the saved preparation has no frozen template version")
        return versions[0]

    # ------------------------------------------------------------ 本地复核

    def _record_local_review(self, command: object) -> Mapping[str, object]:
        project_id, intent_id, request_id, expected = _command_identity(command)
        parameters = _command_parameters(command)
        unknown = set(parameters) - {
            "review_id",
            "report_id",
            "conclusion",
            "explanation",
            "report_revision",
        }
        if unknown:
            raise ReportManagementError(
                f"record_local_review received unknown parameters: {sorted(unknown)}"
            )
        review_id = _text(parameters.get("review_id"), "review_id")
        report_id = _text(parameters.get("report_id"), "report_id")
        conclusion = _text(parameters.get("conclusion"), "conclusion")
        explanation = _text(parameters.get("explanation"), "explanation")
        if expected != 0:
            raise ReportRevisionConflict(expected=expected, current=0)

        fingerprint = _digest(
            ["record_local_review", review_id, report_id, conclusion, explanation]
        )
        intent_record_id = "report-intent-" + _digest([project_id, intent_id])[7:]
        recalled = self._recall(
            project_id, intent_record_id, "record_local_review", review_id, fingerprint
        )
        if recalled is not None:
            return recalled

        current = self._current_report(project_id, report_id)
        if current is None:
            raise ReportNotSaved(f"report {report_id} is not saved")
        snapshot = report_from_payload(current["payload"])
        pinned = parameters.get("report_revision")
        if pinned is not None and pinned != snapshot.content_revision:
            raise ReportRevisionConflict(
                expected=cast(int, pinned), current=snapshot.content_revision
            )

        review = facade.record_local_review(
            snapshot,
            review_id=review_id,
            reviewer=self._actor(),
            conclusion=conclusion,
            explanation=explanation,
            report_revision=snapshot.content_revision,
            recorded_at=self._clock.now(),
        )
        sequence = self._next_sequence()
        payload = local_review_to_payload(review)
        payload["published_sequence"] = sequence
        return self._commit(
            action="record_local_review",
            project_id=project_id,
            record_id=review_id,
            intent_id=intent_id,
            request_id=request_id,
            intent_record_id=intent_record_id,
            fingerprint=fingerprint,
            aggregate_kind=LOCAL_REVIEW_AGGREGATE,
            payload=payload,
            expected_revision=0,
            revision=1,
        )

    # ------------------------------------------------------------ 权威读取

    def _facts(self, project_id: str, run_id: str) -> ExecutionFacts:
        try:
            facts = self._runtime.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ReportGuardRefused(f"saved run facts are unavailable: {error}") from error
        if not isinstance(facts, ExecutionFacts):
            raise ReportGuardRefused("saved run facts are unreadable")
        if facts.project_id != project_id:
            raise ReportGuardRefused("saved run facts belong to another project")
        if facts.run_revision < 1:
            raise ReportGuardRefused("saved run revision cannot be verified")
        return facts

    def _prepared(self, project_id: str, run_intent_id: str) -> Any:
        receipt = self._reader.find_preparation_by_intent(intent_id=run_intent_id)
        if receipt is None or getattr(receipt, "cancelled", False):
            raise ReportGuardRefused("the saved preparation receipt is unavailable")
        request = getattr(receipt, "request", None)
        if request is None:
            raise ReportGuardRefused("the saved preparation receipt is unreadable")
        prepared_run_id = "prepared-" + preparation_identity_digest(
            project_id=project_id,
            client_id=request.client_id,
            prepare_request_id=request.prepare_request_id,
        )
        try:
            prepared, _digest_value = load_saved_preparation(
                reader=self._reader,
                project_id=project_id,
                workspace_id=self._workspace_id,
                prepared_run_id=prepared_run_id,
            )
            validate_preparation_origin(prepared, reader=self._reader)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ReportGuardRefused(f"saved preparation cannot be verified: {error}") from error
        return prepared

    def _scope(self, project_id: str, prepared: Any) -> Any:
        if not prepared.scope_id:
            raise ReportGuardRefused("the saved preparation has no acceptance scope reference")
        try:
            record = self._reader.read(
                aggregate_kind="acceptance_scope",
                record_id=prepared.scope_id,
                revision=prepared.acceptance_scope_revision,
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ReportGuardRefused(
                f"the frozen acceptance scope revision is unavailable: {error}"
            ) from error
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping) or payload.get("project_id") != project_id:
            raise ReportGuardRefused("the frozen acceptance scope owner cannot be verified")
        try:
            scope = acceptance_scope_from_payload(payload)
        except ValueError as error:
            raise ReportGuardRefused(
                f"the frozen acceptance scope is unreadable: {error}"
            ) from error
        if (
            scope.scope_id != prepared.scope_id
            or scope.revision != prepared.acceptance_scope_revision
        ):
            raise ReportGuardRefused("the frozen acceptance scope identity differs")
        return scope

    def _issues(
        self,
        project_id: str,
        issue_ids: tuple[str, ...],
        *,
        selected_cases: frozenset[str] | None = None,
        selected_modules: frozenset[str] = frozenset(),
    ) -> tuple[IssueRecord, ...]:
        # The caller may request additional issues, but cannot subtract saved
        # blockers. Pages use the finite ALL issue index and its stable cursor.
        payloads: dict[str, Mapping[str, object]] = {}
        cursor = None
        seen: set[str] = set()
        while True:
            page = self._records.query(
                RecordQuery(
                    project_id=project_id,
                    aggregate_kind=cast(AggregateKind, ISSUE_AGGREGATE),
                    view="ALL",
                    sort="updated_sequence",
                    limit=100,
                    cursor=cursor,
                )
            )
            if getattr(page, "status", None) != "ok":
                if cursor is None and self._next_sequence() == 1:
                    break  # Authoritative empty workspace, before its first commit.
                raise ReportGuardRefused("saved issue scope cannot be read completely")
            for record in page.items:
                payloads[record.record_id] = record.payload
            cursor = page.next_cursor
            if cursor is None:
                break
            if cursor in seen:
                raise ReportGuardRefused("saved issue query repeated its cursor")
            seen.add(cursor)
        for issue_id in issue_ids:
            current = payloads.get(issue_id)
            if current is None:
                raise ReportGuardRefused(f"issue {issue_id} is not saved")
        records: dict[str, IssueRecord] = {}
        for issue_id, current in payloads.items():
            issue = issue_from_payload(current)
            if issue.project_id != project_id:
                raise ReportGuardRefused(f"issue {issue_id} belongs to another project")
            links = current.get("links")
            case_id = links.get("case_id") if isinstance(links, Mapping) else None
            modules = (
                set(links.get("module_ids", ())) - {"unknown"}
                if isinstance(links, Mapping)
                else set()
            )
            # Unknown scope is retained, never interpreted as unrelated.
            if (
                selected_cases is None
                or case_id in selected_cases
                or issue_id in issue_ids
                or not modules
                or not selected_modules
                or bool(modules & selected_modules)
            ):
                records[issue_id] = issue
        # Include exact canonical dependencies even when their own scope differs.
        for issue in tuple(records.values()):
            current_issue = issue
            chain: set[str] = set()
            while current_issue.canonical_issue_id is not None:
                root_id = current_issue.canonical_issue_id
                if root_id in chain or root_id not in payloads:
                    raise ReportGuardRefused("issue canonical dependency is missing or cyclic")
                chain.add(root_id)
                current_issue = issue_from_payload(payloads[root_id])
                records[root_id] = current_issue
        return tuple(records[key] for key in sorted(records))

    def _current_report(self, project_id: str, report_id: str) -> dict[str, Any] | None:
        payload = self._current(REPORT_AGGREGATE, report_id)
        if payload is None:
            return None
        if payload.get("project_id") != project_id:
            raise ReportNotSaved(f"report {report_id} belongs to another project")
        revision = payload.get("content_revision")
        if type(revision) is not int or revision < 1:
            raise ReportManagementError("saved report content revision cannot be verified")
        return {"payload": payload, "content_revision": revision}

    def _current(self, aggregate_kind: str, record_id: str) -> dict[str, Any] | None:
        revision = self._records.current_revision(
            aggregate_kind=aggregate_kind, record_id=record_id
        )
        if type(revision) is not int or revision < 0:
            raise ReportManagementError("warehouse revision cannot be verified")
        if revision == 0:
            return None
        record = self._records.read(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
        )
        if (
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (aggregate_kind, record_id, revision):
            raise ReportManagementError("saved record envelope cannot be verified")
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping):
            raise ReportManagementError("saved record payload is unreadable")
        return dict(payload)

    def _actor(self) -> str:
        try:
            actor = self._actors.current()
        except (LookupError, RuntimeError, ValueError) as error:
            raise ReportActorRequired(
                "local review requires a controlled interactive session"
            ) from error
        session_id = getattr(actor, "session_id", None)
        entry_kind = getattr(actor, "entry_kind", None)
        if getattr(entry_kind, "value", None) == "agent_relay" or not getattr(
            actor, "interactive", False
        ):
            raise ReportActorRequired("local review cannot come from an AI relay session")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ReportActorRequired("local review requires a session identity")
        return f"session:{session_id}"

    # ------------------------------------------------------------ 提交与幂等

    def _next_sequence(self) -> int:
        try:
            return int(self._unit.next_commit_seq())
        except (TypeError, ValueError) as error:
            raise ReportManagementError("commit sequence cannot be verified") from error

    def _recall(
        self,
        project_id: str,
        intent_record_id: str,
        action: str,
        record_id: str,
        fingerprint: str,
    ) -> Mapping[str, object] | None:
        payload = self._current(REPORT_INTENT_AGGREGATE, intent_record_id)
        if payload is None:
            return None
        if (
            payload.get("schema_version") != REPORT_INTENT_SCHEMA
            or payload.get("project_id") != project_id
            or payload.get("action") != action
            or payload.get("record_id") != record_id
        ):
            raise ReportIntentConflict("report intent belongs to different inputs")
        if payload.get("input_digest") != fingerprint:
            raise ReportIntentConflict("report intent has different frozen input")
        result = payload.get("result")
        if not isinstance(result, Mapping) or set(result) != {
            "aggregate_kind",
            "record_id",
            "revision",
        }:
            raise ReportManagementError("saved report intent result is unreadable")
        return dict(result)

    def _commit(
        self,
        *,
        action: str,
        project_id: str,
        record_id: str,
        intent_id: str,
        request_id: str,
        intent_record_id: str,
        fingerprint: str,
        aggregate_kind: str,
        payload: dict[str, object],
        expected_revision: int,
        revision: int,
        source_sequence: int | None = None,
    ) -> Mapping[str, object]:
        result = {
            "aggregate_kind": aggregate_kind,
            "record_id": record_id,
            "revision": revision,
        }
        self._unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            if source_sequence is not None and self._next_sequence() != source_sequence:
                raise ReportGuardRefused("decision inputs changed before report publication")
            staged = self._unit.stage_record(
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                expected_revision=expected_revision,
                payload=payload,
            )
            staged_revision = getattr(staged, "revision", staged)
            if type(staged_revision) is not int or staged_revision != revision:
                raise ReportManagementError("staged report revision cannot be verified")
            self._unit.stage_record(
                aggregate_kind=REPORT_INTENT_AGGREGATE,
                record_id=intent_record_id,
                expected_revision=0,
                payload={
                    "schema_version": REPORT_INTENT_SCHEMA,
                    "project_id": project_id,
                    "intent_id": intent_id,
                    "action": action,
                    "record_id": record_id,
                    "input_digest": fingerprint,
                    "result": dict(result),
                },
            )
            self._unit.commit(request_id)
        except BaseException:
            self._unit.rollback(request_id)
            raise
        return dict(result)


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


__all__ = [
    "DEFAULT_POLICY_VERSION",
    "LOCAL_REVIEW_AGGREGATE",
    "LOCAL_REVIEW_SCHEMA",
    "REPORT_AGGREGATE",
    "REPORT_INTENT_AGGREGATE",
    "REPORT_SCHEMA",
    "ReportActorRequired",
    "ReportGuardRefused",
    "ReportIntentConflict",
    "ReportManagementError",
    "ReportNotSaved",
    "ReportRevisionConflict",
    "SUPPORTED_ACTIONS",
    "SavedReportService",
    "local_review_from_payload",
    "local_review_to_payload",
    "report_from_payload",
    "report_to_payload",
]
