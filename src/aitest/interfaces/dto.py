"""Stable DTO projections for review, reports, and issues.

DTOs only serialize facts already computed by the domain. They must not
recalculate business outcomes, evidence grades, or blocking rules.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.views import CoverageDTO
from aitest.domain.project.context import Delivery
from aitest.domain.review.defects import (
    IssueDisposition,
    IssueRecord,
    IssueSeverity,
    IssueStatus,
)
from aitest.domain.review.issue_queries import (
    IssueBlockingState,
    IssueFilterMask,
    IssueListCursor,
    IssueListPage,
    IssueListProjection,
    IssueListScope,
    IssueReviewState,
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


class IssueListCursorDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commit_id: str
    condition_fingerprint: str
    last_updated_sequence: int
    last_issue_id: str

    @classmethod
    def from_domain(cls, value: IssueListCursor | None) -> "IssueListCursorDTO | None":
        if value is None:
            return None
        return cls(
            commit_id=value.commit_id,
            condition_fingerprint=value.condition_fingerprint,
            last_updated_sequence=value.last_updated_sequence,
            last_issue_id=value.last_issue_id,
        )


class IssueListRowDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str
    content_revision: int
    module_ids: tuple[str, ...]
    layers: tuple[str, ...]
    severity: IssueSeverity | None
    workflow_state: IssueStatus | None
    review_state: IssueReviewState
    disposition: IssueDisposition | None
    blocking: IssueBlockingState
    canonical_issue_id: str | None
    updated_sequence: int
    gap_reason: str | None = None


def issue_list_row_dto(value: IssueListProjection) -> IssueListRowDTO:
    return IssueListRowDTO(
        issue_id=value.issue_id,
        content_revision=value.content_revision,
        module_ids=tuple(sorted(value.module_ids)),
        layers=tuple(sorted(value.layers)),
        severity=value.severity,
        workflow_state=value.workflow_state,
        review_state=value.review_state,
        disposition=value.disposition,
        blocking=value.blocking,
        canonical_issue_id=value.canonical_issue_id,
        updated_sequence=value.updated_sequence,
        gap_reason=value.gap_reason,
    )


class IssueListPageDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_spec_version: str
    scope: IssueListScope
    mask: IssueFilterMask
    items: tuple[IssueListRowDTO, ...]
    next_cursor: IssueListCursorDTO | None


def issue_list_page_dto(value: IssueListPage) -> IssueListPageDTO:
    return IssueListPageDTO(
        query_spec_version=value.query_spec_version,
        scope=value.scope,
        mask=value.mask,
        items=tuple(issue_list_row_dto(item) for item in value.items),
        next_cursor=IssueListCursorDTO.from_domain(value.next_cursor),
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


# --------------------------------------------------------------- 运行视图 DTO

#: 需要人工参与的适配器（人工页面 / 智能体业务）：决定"待人工步骤"与"自动步骤"的划分。
_MANUAL_ADAPTER_KINDS = frozenset({"manual_evidence", "agent"})


class RunStepDTO(BaseModel):
    """运行视图里的单个步骤：状态、当前尝试与失效标记**全部来自已提交事实**。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str
    case_id: str
    state: str
    current_attempt_id: str | None = None
    attempt_state: str | None = None
    adapter_kind: str | None = None
    invalidated: bool = False
    gap_ids: tuple[str, ...] = ()


class RunViewDTO(BaseModel):
    """`RunDTO`（架构 06 §1/§5）：运行/步骤/当前尝试 + 用例 S/M 两组 E/R/V/P/F/U 与分母。

    - `selected_summary`/`required_summary` 由领域 `DecisionResult` 派生
      （`_coverage_summary_dto`），界面不得重算；
    - 事实里没有的量（如无当前尝试时的尝试状态）保持 `None`，即"未知"，不用步骤数倒推；
    - 本 DTO 只投影已提交事实与**同一次**领域判定，不写任何记录。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aitest.run-view/1.0"] = "aitest.run-view/1.0"
    project_id: str
    run_id: str
    run_revision: int
    snapshot_commit_id: str
    snapshot_cursor: int
    plan_revision_ref: str
    runtime_revision_refs: tuple[str, ...]
    control_state: str
    driver: str
    tier: str
    conclusion_ceiling: str
    selected_case_ids: tuple[str, ...]
    required_case_ids: tuple[str, ...]
    coverage: CoverageDTO
    selected_summary: CoverageSummaryDTO
    required_summary: CoverageSummaryDTO
    executed_case_ids: tuple[str, ...]
    reused_case_ids: tuple[str, ...]
    verified_case_ids: tuple[str, ...]
    passed_case_ids: tuple[str, ...]
    failed_case_ids: tuple[str, ...]
    unverified_case_ids: tuple[str, ...]
    automatic_step_count: int
    automatic_completed_count: int
    manual_pending_step_ids: tuple[str, ...]
    blocked_step_ids: tuple[str, ...]
    pending_verification_step_ids: tuple[str, ...]
    invalidated_step_ids: tuple[str, ...]
    steps: tuple[RunStepDTO, ...]


def run_view_dto(facts: ExecutionFacts, decision: DecisionResult) -> RunViewDTO:
    """把**已提交事实 + 同一次领域判定**投影成运行视图 DTO（不做任何领域判断）。"""

    coverage = decision.coverage
    attempts_by_step = {attempt.attempt_id: attempt for attempt in facts.attempts}
    steps: list[RunStepDTO] = []
    automatic_total = 0
    automatic_completed = 0
    manual_pending: list[str] = []
    blocked: list[str] = []
    pending_verification: list[str] = []
    invalidated: list[str] = []
    for step in facts.steps:
        current_attempt_id = facts.current_attempt_by_step.get(step.step_id)
        attempt = attempts_by_step.get(current_attempt_id or step.current_attempt_id or "")
        adapter_kind = None if attempt is None else str(attempt.adapter_kind.value)
        state = str(step.state.value)
        if step.invalidated:
            invalidated.append(step.step_id)
        if state == "blocked":
            blocked.append(step.step_id)
        if state == "pending_verification":
            pending_verification.append(step.step_id)
        if adapter_kind is None or adapter_kind not in _MANUAL_ADAPTER_KINDS:
            automatic_total += 1
            if state == "completed":
                automatic_completed += 1
        elif state not in {"completed", "cancelled", "invalidated"}:
            manual_pending.append(step.step_id)
        steps.append(
            RunStepDTO(
                step_id=step.step_id,
                case_id=step.case_id,
                state=state,
                current_attempt_id=current_attempt_id,
                attempt_state=None if attempt is None else str(attempt.state.value),
                adapter_kind=adapter_kind,
                invalidated=bool(step.invalidated),
                gap_ids=tuple(step.gap_ids),
            )
        )
    return RunViewDTO(
        project_id=facts.project_id,
        run_id=facts.run_id,
        run_revision=facts.run_revision,
        snapshot_commit_id=facts.snapshot_commit_id,
        snapshot_cursor=facts.snapshot_cursor,
        plan_revision_ref=(
            f"{facts.plan_revision.revision_id}@{facts.plan_revision.revision_no}"
        ),
        runtime_revision_refs=tuple(facts.runtime_revision_refs),
        control_state=str(facts.run.control_state.value),
        driver=str(facts.run.driver.value),
        tier=str(facts.run.tier.value),
        conclusion_ceiling=str(facts.run.conclusion_ceiling.value),
        selected_case_ids=tuple(sorted(coverage.selected)),
        required_case_ids=tuple(sorted(coverage.required)),
        coverage=coverage_dto(coverage),
        selected_summary=_coverage_summary_dto(
            coverage, scope_kind="selected", result=decision
        ),
        required_summary=_coverage_summary_dto(
            coverage, scope_kind="required", result=decision
        ),
        executed_case_ids=tuple(sorted(coverage.executed)),
        reused_case_ids=tuple(sorted(coverage.reused)),
        verified_case_ids=tuple(sorted(coverage.verified)),
        passed_case_ids=tuple(sorted(coverage.passed)),
        failed_case_ids=tuple(sorted(coverage.failed)),
        unverified_case_ids=tuple(sorted(coverage.unverified)),
        automatic_step_count=automatic_total,
        automatic_completed_count=automatic_completed,
        manual_pending_step_ids=tuple(sorted(manual_pending)),
        blocked_step_ids=tuple(sorted(blocked)),
        pending_verification_step_ids=tuple(sorted(pending_verification)),
        invalidated_step_ids=tuple(sorted(invalidated)),
        steps=tuple(steps),
    )


# --------------------------------------------------------- 面板主屏视图 DTO

#: 三段视图名（架构 06 §1「当前段」；文档三段名：运行前／运行时／运行后）。
_PRE_RUN_STATES = frozenset({"not_started"})
_RUNTIME_STATES = frozenset(
    {
        "running",
        "pause_requested",
        "paused",
        "cancel_requested",
        "cancelling",
        "recovering",
        "pending_verification",
    }
)
_POST_RUN_STATES = frozenset({"completed", "cancelled", "execution_error"})


class PanelViewDTO(BaseModel):
    """面板主屏视图（架构 06 §1 PanelDTO 的可落地部分：**当前段**与信息区事实）。

    - `stage` 只由已提交的 `control_state` 判定（三段名取文档用词）；判不出即拒绝，
      不按"有没有记录"猜段；
    - 主屏要用的"情形"事实全部给**已有词汇**：`decisive_failure_case_ids`（断言失败）、
      `gap_codes`（既有 `ReviewGapCode`，含 `environment_evidence_incomplete`／
      `assertion_basis_unconfirmed` 等）、`manual_pending_step_ids`（待人工操作）、
      `pending_verification_step_ids`（待核实）；界面按设计分册 §3/§8 的表格映射到文案与主动作，
      **核心不重算判定**（判定来自同一次 `DecisionResult`）。
    - `primary_action` 的动作标识未冻结（见待裁定第 8 项），因此本 DTO 不给候选，避免自造动作名。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aitest.panel-view/1.0"] = "aitest.panel-view/1.0"
    project_id: str
    run_id: str
    stage: Literal["pre_run", "runtime", "post_run"]
    control_state: str
    run_revision: int
    snapshot_commit_id: str
    snapshot_cursor: int
    tier: str
    driver: str
    conclusion_ceiling: str
    business_outcome: str
    decisive_failure_case_ids: tuple[str, ...]
    gap_codes: tuple[str, ...]
    manual_pending_step_ids: tuple[str, ...]
    pending_verification_step_ids: tuple[str, ...]


def panel_view_dto(
    facts: ExecutionFacts, decision: DecisionResult, run_view: RunViewDTO
) -> PanelViewDTO:
    """当前段 + 信息区事实（判定与运行视图都取自**同一次**已提交读数）。"""

    control_state = str(facts.run.control_state.value)
    if control_state in _PRE_RUN_STATES:
        stage: str = "pre_run"
    elif control_state in _RUNTIME_STATES:
        stage = "runtime"
    elif control_state in _POST_RUN_STATES:
        stage = "post_run"
    else:
        raise ValueError(f"panel view cannot derive a stage from control_state {control_state!r}")
    return PanelViewDTO(
        project_id=facts.project_id,
        run_id=facts.run_id,
        stage=cast(Any, stage),
        control_state=control_state,
        run_revision=facts.run_revision,
        snapshot_commit_id=facts.snapshot_commit_id,
        snapshot_cursor=facts.snapshot_cursor,
        tier=run_view.tier,
        driver=run_view.driver,
        conclusion_ceiling=run_view.conclusion_ceiling,
        business_outcome=str(decision.business_outcome.value),
        decisive_failure_case_ids=tuple(sorted(decision.decisive_failure_case_ids)),
        gap_codes=tuple(sorted({gap.code.value for gap in decision.gaps})),
        manual_pending_step_ids=run_view.manual_pending_step_ids,
        pending_verification_step_ids=run_view.pending_verification_step_ids,
    )


# --------------------------------------------------------- 交付说明视图 DTO


class DeliverySubmissionViewDTO(BaseModel):
    """正式提测记录视图：冻结的验收项集合与内容身份（只读投影）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    submission_id: str
    revision: int
    version: str
    content_identity: str
    acceptance_item_ids: tuple[str, ...]


class DeliveryViewDTO(BaseModel):
    """交付说明视图（BD-001 §7.2、功能 §4）：**自述与验证分列**，核验状态取冻结三态。

    - `self_report` 与 `verified_in_scope`/`unverified_scope` 各自成块，界面不得合并；
    - `verified_in_scope` 为空而 `self_report.completed` 非空 ⇒ `verification_state` 为
      「必要核验未完成」（显示**待验证**），不得显示为已完成；
    - 所有块都来自已保存记录，视图层不重算判定、不写任何记录。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aitest.delivery-view/1.0"] = "aitest.delivery-view/1.0"
    project_id: str
    delivery_id: str
    revision: int
    version: str
    task_id: str
    run_method: str
    self_report_completed: tuple[str, ...]
    self_report_incomplete: tuple[str, ...]
    verified_in_scope: tuple[str, ...]
    unverified_scope: tuple[str, ...]
    verification_state: str
    submitted: bool
    submission: DeliverySubmissionViewDTO | None = None


def delivery_view_dto(
    delivery: Delivery,
    *,
    project_id: str,
    submission: Mapping[str, object] | None = None,
) -> DeliveryViewDTO:
    """把已保存的交付说明（可选正式提测记录）投影成交付视图。"""

    parsed: DeliverySubmissionViewDTO | None = None
    if submission is not None:
        parsed = DeliverySubmissionViewDTO(
            submission_id=_view_text(submission, "submission_id"),
            revision=_view_revision(submission, "revision"),
            version=_view_text(submission, "version"),
            content_identity=_view_text(submission, "content_identity"),
            acceptance_item_ids=tuple(
                _view_text_list(submission.get("acceptance_item_ids"), "acceptance_item_ids")
            ),
        )
    return DeliveryViewDTO(
        project_id=project_id,
        delivery_id=delivery.delivery_id,
        revision=delivery.revision,
        version=delivery.version,
        task_id=delivery.task_id,
        run_method=delivery.run_method,
        self_report_completed=tuple(delivery.self_report.completed),
        self_report_incomplete=tuple(delivery.self_report.incomplete),
        verified_in_scope=tuple(delivery.verified_in_scope),
        unverified_scope=tuple(delivery.unverified_scope),
        verification_state=delivery.verification_state.value,
        submitted=parsed is not None,
        submission=parsed,
    )


def _view_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"saved delivery view requires {key}")
    return value


def _view_revision(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 1:
        raise ValueError(f"saved delivery view requires {key}")
    return value


def _view_text_list(value: object, name: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"saved delivery view requires {name}")
    return list(value)
