"""运行中修订的纯守卫规则：吃 C 的**执行事实视图**，不吃调用者自报的结论。

依据：

- 架构文档《01-项目与计划》第 4 节（意图与运行修订）、第 7 节（`RunPlanRevision` /
  `StepRevisionRef` 落在 `application/planning/run_mode.py` 与 `execution/runner.py`）；
- 需求 P1-FR06／FR07、P1-AC20："修改形成运行中修订只影响尚未执行的步骤，已完成步骤
  绑定原修订、不回改不重跑；正在执行的步骤拒绝修改；删必测项或弱化断言的修改被拒绝"；
- 功能文档第 10.6 节："只在步骤边界修改未执行内容；正在执行的步骤拒绝修改"；
- `BD-001` 第 2.1 节：`RunPlanRevision` **由 C 在执行侧记录**，B 提供守卫规则。

三条边界（本模块存在的理由）：

1. **不 import `contracts`／`pydantic`**：C 的事实由应用层
   （`application/planning/run_mode.py`）翻译成这里的纯值对象。本模块镜像 C 的状态
   枚举取值（`StepRuntimeState` / `AttemptRuntimeState` / `RunRuntimeState`），
   与 C 合同的一致性由 `tests/contracts/test_runtime_vocabulary.py` 锁定——
   与 `plans.CaseLayer` 的双层声明同一做法。
2. **只判断，不落盘、不回写**：`Plan`／`Case` 是不可变对象，本模块从不修改入参；
   "补确认不回写冻结 Case/Plan"在这里是**结构性保证**，不是纪律要求。
3. **不下结论**：依据变化只登记"交给 C 失效""交给 D 新判定"的交接清单，
   本模块不产生通过／失败／已验证／证据等级。`RuntimeRevisionDecision` 上没有这类字段，
   `tests/unit/test_runtime_revision.py` 用字段名断言把这条钉住。

活动与已完成步骤、当前依据、修订序列**全部来自 C 的事实**：

| 输入 | 唯一来源 |
| --- | --- |
| 步骤是否正在执行／已产生事实／尚未执行 | C 的 `StepFact.state` + 当前 `AttemptFact.state` |
| 下一个运行中修订序号 | C 的 `run.runtime_revision_refs` 计数 + 1 |
| 决策依据的是哪一份快照 | C 的 `snapshot_commit_id` + `snapshot_cursor`（不匹配即拒绝） |
| 冻结必测集合 | B 计划必测 ∪ C 的 `required_scope` ∪ `coverage.mandatory_case_ids` |
| 依据是否已确认 | `effective_assertion_basis_state()` 按准确修订与确认记录派生 |
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from aitest.domain.planning.plans import (
    AssertionBasis,
    AssertionBasisState,
    Case,
    ConfirmationRecord,
    Plan,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
    effective_assertion_basis_state,
    narrow_driver,
)


class StepRuntimeState(StrEnum):
    """镜像 `contracts.execution_facts.StepStateFact`（C 的 `StepState`）。"""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    PENDING_VERIFICATION = "pending_verification"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"
    EXECUTION_ERROR = "execution_error"


class AttemptRuntimeState(StrEnum):
    """镜像 `contracts.execution_facts.AttemptStateFact`（C 的 `AttemptState`）。"""

    INTENT_RECORDED = "intent_recorded"
    STARTING = "starting"
    RUNNING = "running"
    STOP_REQUESTED = "stop_requested"
    COLLECTING = "collecting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PENDING_VERIFICATION = "pending_verification"
    EXECUTION_ERROR = "execution_error"
    INVALIDATED = "invalidated"
    UNKNOWN = "unknown"


class RunRuntimeState(StrEnum):
    """镜像 `contracts.execution_facts.RunControlStateFact`（C 的 `RunControlState`）。"""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLING = "cancelling"
    RECOVERING = "recovering"
    PENDING_VERIFICATION = "pending_verification"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXECUTION_ERROR = "execution_error"


#: 尝试仍在推进：此时修改其步骤就是"修改正在执行的项"。
ACTIVE_ATTEMPT_STATES: frozenset[AttemptRuntimeState] = frozenset(
    {
        AttemptRuntimeState.INTENT_RECORDED,
        AttemptRuntimeState.STARTING,
        AttemptRuntimeState.RUNNING,
        AttemptRuntimeState.STOP_REQUESTED,
        AttemptRuntimeState.COLLECTING,
    }
)

#: 步骤状态表示"尚未执行"，运行中修订可以作用在它上面。
_UNSTARTED_STEP_STATES: frozenset[StepRuntimeState] = frozenset(
    {
        StepRuntimeState.PENDING,
        StepRuntimeState.READY,
        StepRuntimeState.BLOCKED,
        StepRuntimeState.INVALIDATED,
    }
)

#: 允许评估运行中修订的运行状态。
#:
#: `pending_verification` 在列内：超时／未知的尝试已停在待核实，用户正需要处置它；
#: 但**待核实的步骤本身已产生事实**，仍按"不回改"处理（两者的边界不同）。
#: `not_started`、`cancel_requested`、`cancelling` 与三个终态都不接受运行中修订。
_REVISABLE_RUN_STATES: frozenset[RunRuntimeState] = frozenset(
    {
        RunRuntimeState.RUNNING,
        RunRuntimeState.PAUSE_REQUESTED,
        RunRuntimeState.PAUSED,
        RunRuntimeState.RECOVERING,
        RunRuntimeState.PENDING_VERIFICATION,
    }
)


class StepRuntimeProgress(StrEnum):
    """步骤在"运行中修订"这个用途下的三态分类。"""

    NOT_STARTED = "not_started"
    EXECUTING = "executing"
    FACTS_RECORDED = "facts_recorded"


def step_runtime_progress(
    state: StepRuntimeState, *, current_attempt_state: AttemptRuntimeState | None
) -> StepRuntimeProgress:
    """按 C 的事实派生步骤进度。

    | 事实 | 分类 | 对运行中修订的含义 |
    | --- | --- | --- |
    | 当前尝试在 `intent_recorded`…`collecting`，或步骤 `running` | `EXECUTING` | **拒绝修改** |
    | 步骤 `pending` / `ready` / `blocked` / `invalidated` | `NOT_STARTED` | 修订可以作用 |
    | 步骤已停在待核实／完成／取消／执行错误 | `FACTS_RECORDED` | 绑定原修订，不回改 |

    `invalidated` 归入"尚未执行"：C 已判定该步骤需要重跑，它不再是历史事实。
    尝试状态优先于步骤状态——步骤状态滞后时不能让一个仍在跑的尝试被改写。
    """
    if current_attempt_state is not None and current_attempt_state in ACTIVE_ATTEMPT_STATES:
        return StepRuntimeProgress.EXECUTING
    if state is StepRuntimeState.RUNNING:
        return StepRuntimeProgress.EXECUTING
    if state in _UNSTARTED_STEP_STATES:
        return StepRuntimeProgress.NOT_STARTED
    return StepRuntimeProgress.FACTS_RECORDED


@dataclass(frozen=True, slots=True)
class StepRuntimeFacts:
    """单个步骤的运行事实视图（C 的 `StepFact` 去掉 B 不用的字段）。"""

    step_id: str
    case_id: str
    ordinal: int
    state: StepRuntimeState
    required_for_case: bool = True
    current_attempt_state: AttemptRuntimeState | None = None
    invalidated: bool = False
    step_revision_no: int = 1

    def __post_init__(self) -> None:
        _require_text(self.step_id, "step_id")
        _require_text(self.case_id, "case_id")
        if self.ordinal < 1:
            raise ValueError("step ordinal must be >= 1")
        if self.step_revision_no < 1:
            raise ValueError("step revision must be >= 1")

    def progress(self) -> StepRuntimeProgress:
        return step_runtime_progress(
            self.state, current_attempt_state=self.current_attempt_state
        )


@dataclass(frozen=True, slots=True)
class RunRuntimeFacts:
    """一次运行的一致事实视图；由应用层从 C 的 `ExecutionFacts` 翻译而来。

    `runtime_revision_count` 是**已经记录在 C 事实里的运行中修订条数**；
    下一个修订序号由它派生，不接受调用者自报（检查项 B-05 的"修订序列仍依赖调用者输入"）。
    """

    run_id: str
    run_revision: int
    plan_revision_id: str
    plan_revision_no: int
    plan_revision_digest: str
    control_state: RunRuntimeState
    driver: RunDriver
    tier: RunTier
    snapshot_commit_id: str
    snapshot_cursor: int
    runtime_revision_count: int
    frozen_required_case_ids: frozenset[str] = frozenset()
    mandatory_case_ids: frozenset[str] = frozenset()
    steps: tuple[StepRuntimeFacts, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.run_id, "run_id")
        _require_text(self.plan_revision_id, "plan_revision_id")
        _require_text(self.plan_revision_digest, "plan_revision_digest")
        _require_text(self.snapshot_commit_id, "snapshot_commit_id")
        if self.run_revision < 0:
            raise ValueError("run_revision must be >= 0")
        if self.plan_revision_no < 1:
            raise ValueError("plan_revision_no must be >= 1")
        if self.snapshot_cursor < 0:
            raise ValueError("snapshot_cursor must be >= 0")
        if self.runtime_revision_count < 0:
            raise ValueError("runtime_revision_count must be >= 0")
        _require_unique([step.step_id for step in self.steps], "step_id")

    def steps_of(self, case_id: str) -> tuple[StepRuntimeFacts, ...]:
        return tuple(step for step in self.steps if step.case_id == case_id)


class RuntimeRevisionRefusalCode(StrEnum):
    """运行中修订被拒绝的结构化原因；界面与报告按**代码**分支，不解析文案。"""

    PLAN_NOT_PUBLISHED = "plan_not_published"
    PLAN_REVISION_MISMATCH = "plan_revision_mismatch"
    STALE_SNAPSHOT = "stale_snapshot"
    RUN_NOT_ACTIVE = "run_not_active"
    DRIVER_EXPANSION = "driver_expansion"
    FROZEN_CASE_NOT_PROVIDED = "frozen_case_not_provided"
    FROZEN_CASE_REVISION_MISMATCH = "frozen_case_revision_mismatch"
    CASE_REVISION_NOT_ADVANCED = "case_revision_not_advanced"
    MANDATORY_CASE_REMOVAL = "mandatory_case_removal"
    APPLICABILITY_WEAKENED = "applicability_weakened"
    ASSERTION_WEAKENED = "assertion_weakened"
    INDEPENDENT_VERIFICATION_REMOVED = "independent_verification_removed"
    STEP_IS_EXECUTING = "step_is_executing"
    STEP_NOT_IN_CASE = "step_not_in_case"
    STEP_FACTS_ALREADY_RECORDED = "step_facts_already_recorded"
    NO_UNEXECUTED_STEP = "no_unexecuted_step"


@dataclass(frozen=True, slots=True)
class RuntimeRevisionRefusal:
    """一条拒绝原因；`code` 是机器可读的，`detail` 只用于展示。"""

    code: RuntimeRevisionRefusalCode
    detail: str

    def __post_init__(self) -> None:
        _require_text(self.detail, "detail")


@dataclass(frozen=True, slots=True)
class CaseRuntimeChange:
    """对某个用例的**内容**请求，不是标签。

    `next_case` 是提议的**新用例修订对象**。本模块从不把它写回冻结 `Case`／`Plan`：
    `Plan` 与 `Case` 都是 `frozen=True`，回写在类型上就不可能发生。
    """

    next_case: Case
    target_step_ids: tuple[str, ...] = ()
    remove_from_required: bool = False

    def __post_init__(self) -> None:
        _require_items(self.target_step_ids, "target_step_ids")
        _require_unique(list(self.target_step_ids), "target_step_ids")


@dataclass(frozen=True, slots=True)
class RuntimeRevisionRequest:
    """一次运行中修订请求。

    - `base_plan_revision_id` / `base_plan_revision_digest`：本次修订所依据的**冻结计划身份**
      （ID + 修订 + 摘要）。只比修订号不够——检查项 B-15 的反例正是"同修订号、不同
      `plan_id`"的计划被当成冻结依据接受。摘要为可选是为了兼容只做修订号核对的旧调用方，
      但**应用层一律给出**：`plan_revision_digest` 在 C 的事实里就有，不给就等于放弃这项核对；
    - `base_plan_revision_no`：冻结计划的仓储读取修订，须与 Plan.record_revision 和 C 的事实一致；
    - `observed_snapshot_cursor`：决策时读到的 C 快照游标；不一致即 `stale_snapshot`，
      保证"按同一 commit 读取整个快照"（`CD-001` 第 3 节）而不是在混合快照上做决策；
    - `requested_driver`：只允许 `planned → stepwise` 收窄，反向由 `narrow_driver()` 拒绝；
    - `operator_ref`：操作者／授权引用，由受控交互入口提供；本模块只如实登记，不校验角色。
    """

    base_plan_revision_id: str
    base_plan_revision_no: int
    observed_snapshot_cursor: int
    case_changes: tuple[CaseRuntimeChange, ...]
    reason: str
    operator_ref: str
    base_plan_revision_digest: str | None = None
    requested_driver: RunDriver | None = None

    def __post_init__(self) -> None:
        _require_text(self.base_plan_revision_id, "base_plan_revision_id")
        if self.base_plan_revision_no < 1:
            raise ValueError("base_plan_revision_no must be >= 1")
        if self.base_plan_revision_digest is not None:
            _require_text(self.base_plan_revision_digest, "base_plan_revision_digest")
        if self.observed_snapshot_cursor < 0:
            raise ValueError("observed_snapshot_cursor must be >= 0")
        _require_text(self.reason, "reason")
        _require_text(self.operator_ref, "operator_ref")
        if not self.case_changes:
            raise ValueError("a runtime revision must change at least one case")
        _require_unique(
            [change.next_case.case_id for change in self.case_changes], "case_id"
        )


@dataclass(frozen=True, slots=True)
class RuntimeRevisionDecision:
    """评估结果。

    **没有**结论类字段：依据变化的后果只以交接清单出现——`invalidated_basis_step_ids`
    交给 C 失效（含它自己的传递失效），`rejudge_case_ids` 交给 D 重新判定。
    `pause_required` 是"交 C 在步骤边界暂停"，不是 B 自己暂停运行。
    """

    accepted: bool
    revision_no: int
    effective_driver: RunDriver
    snapshot_commit_id: str
    snapshot_cursor: int
    refusals: tuple[RuntimeRevisionRefusal, ...] = ()
    affected_step_ids: tuple[str, ...] = ()
    preserved_step_ids: tuple[str, ...] = ()
    invalidated_basis_step_ids: tuple[str, ...] = ()
    rejudge_case_ids: tuple[str, ...] = ()
    confirmation_required_case_ids: tuple[str, ...] = ()
    pause_required: bool = False
    new_run_required: bool = False


class RuntimeRevisionRefused(ValueError):
    """`assert_runtime_revision_accepted()` 在拒绝时抛出的异常，携带完整决策。"""

    def __init__(self, decision: RuntimeRevisionDecision) -> None:
        codes = ", ".join(refusal.code.value for refusal in decision.refusals)
        super().__init__(f"runtime revision refused: {codes}")
        self.decision = decision


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_items(values: Sequence[str], name: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must not contain empty values")


def _require_unique(values: Sequence[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


_BASIS_RANK: dict[AssertionBasisState, int] = {
    AssertionBasisState.MISSING: 0,
    AssertionBasisState.PRESENT_UNCONFIRMED: 1,
    AssertionBasisState.CONFIRMED: 2,
}


def assertion_basis_weakened(
    *, current: AssertionBasis, requested: AssertionBasis
) -> bool:
    """按**内容**判定断言是否被弱化，不看调用者怎么称呼这次改动。

    - 把依据改回 `missing`（删掉断言）→ 弱化；
    - 依据三态降级（`confirmed` → `present_unconfirmed` / `missing`，
      `present_unconfirmed` → `missing`）→ 弱化；
    - 同级换文本（例如"响应 200"改成"响应码 200 且订单落库"）**不算弱化**：
      领域层无法判定新文本更强还是更弱，强行判成弱化会拦住正常的依据修订。
      这类改动按"依据变化"处理：只作用于未执行步骤，并交出失效与重判清单。
    """
    if requested.state is AssertionBasisState.MISSING:
        return True
    return _BASIS_RANK[requested.state] < _BASIS_RANK[current.state]


def evaluate_runtime_revision(
    *,
    plan: Plan,
    cases: Sequence[Case],
    confirmations: Sequence[ConfirmationRecord],
    request: RuntimeRevisionRequest,
    facts: RunRuntimeFacts,
) -> RuntimeRevisionDecision:
    """评估一次运行中修订；**收集全部拒绝原因**后整体拒绝，不做部分接受。

    运行中修订是一条不可分割的记录（`RunPlanRevision`）：任何一条门禁不过，
    整次修订都不产生，避免"一半生效"的序列无法解释。
    """
    refusals: list[RuntimeRevisionRefusal] = []

    def refuse(code: RuntimeRevisionRefusalCode, detail: str) -> None:
        refusals.append(RuntimeRevisionRefusal(code=code, detail=detail))

    if plan.status is not PlanPublicationStatus.PUBLISHED:
        refuse(
            RuntimeRevisionRefusalCode.PLAN_NOT_PUBLISHED,
            "an unpublished plan has no frozen run to revise",
        )

    plan_record_revision = plan.record_revision or plan.revision
    if (
        request.base_plan_revision_id != plan.plan_id
        or plan.plan_id != facts.plan_revision_id
        or plan_record_revision != facts.plan_revision_no
        or request.base_plan_revision_no != plan_record_revision
    ):
        refuse(
            RuntimeRevisionRefusalCode.PLAN_REVISION_MISMATCH,
            "frozen plan "
            f"{plan.plan_id}@{plan_record_revision} (body version {plan.revision}), "
            "request base plan "
            f"{request.base_plan_revision_id}@{request.base_plan_revision_no}, "
            f"facts plan {facts.plan_revision_id}@{facts.plan_revision_no}",
        )
    elif (
        request.base_plan_revision_digest is not None
        and request.base_plan_revision_digest != facts.plan_revision_digest
    ):
        # 摘要只在调用方给出时核对：没给就是没核对，不拿计划自己的内容**猜**一个摘要来比
        # （那样等于自己跟自己比，看着通过、其实什么也没验）。
        refuse(
            RuntimeRevisionRefusalCode.PLAN_REVISION_MISMATCH,
            "request base plan digest "
            f"{request.base_plan_revision_digest} does not match the frozen plan digest "
            f"{facts.plan_revision_digest}",
        )

    if request.observed_snapshot_cursor != facts.snapshot_cursor:
        refuse(
            RuntimeRevisionRefusalCode.STALE_SNAPSHOT,
            "decision was taken on snapshot cursor "
            f"{request.observed_snapshot_cursor} but the committed facts are at "
            f"{facts.snapshot_cursor}",
        )

    if facts.control_state not in _REVISABLE_RUN_STATES:
        refuse(
            RuntimeRevisionRefusalCode.RUN_NOT_ACTIVE,
            f"run control state {facts.control_state.value} accepts no runtime revision",
        )

    effective_driver = facts.driver
    new_run_required = False
    try:
        effective_driver = narrow_driver(
            facts.driver, request.requested_driver or facts.driver
        )
    except ValueError:
        refuse(
            RuntimeRevisionRefusalCode.DRIVER_EXPANSION,
            "driver can only narrow planned -> stepwise; "
            "expanding authorization requires a new run",
        )
        new_run_required = True
        effective_driver = facts.driver

    frozen_refs = {ref.case_id: ref for ref in plan.case_revisions}
    provided = {case.case_id: case for case in cases}
    mandatory = (
        plan.scope.required_case_ids
        | facts.frozen_required_case_ids
        | facts.mandatory_case_ids
    )

    affected: list[str] = []
    preserved: list[str] = []
    invalidated_basis: list[str] = []
    rejudge: list[str] = []
    confirmation_required: list[str] = []
    pause_required = False

    for change in request.case_changes:
        next_case = change.next_case
        case_id = next_case.case_id
        frozen_ref = frozen_refs.get(case_id)
        current = provided.get(case_id)

        if frozen_ref is None:
            refuse(
                RuntimeRevisionRefusalCode.FROZEN_CASE_NOT_PROVIDED,
                f"{case_id} is not frozen in the published plan",
            )
            continue
        if current is None or current.revision != frozen_ref.revision:
            refuse(
                RuntimeRevisionRefusalCode.FROZEN_CASE_REVISION_MISMATCH,
                f"{case_id} needs the frozen case revision {frozen_ref.revision}",
            )
            continue
        if next_case.revision <= frozen_ref.revision:
            refuse(
                RuntimeRevisionRefusalCode.CASE_REVISION_NOT_ADVANCED,
                f"{case_id} next revision {next_case.revision} must advance past "
                f"{frozen_ref.revision}",
            )

        if change.remove_from_required and case_id in mandatory:
            refuse(
                RuntimeRevisionRefusalCode.MANDATORY_CASE_REMOVAL,
                f"{case_id} is mandatory (M / required scope) and cannot be removed "
                "from the required set mid-run",
            )

        if case_id in mandatory:
            # 必测用例的适用性**只能保留或增加**：`required ⊆ next`。
            # 检查项 B-15：过去用真子集比较（`next < current`），两个互不包含的集合比不出
            # 真子集关系，于是"把 {AC-A,AC-B} 换成 {AC-A,AC-C}"这种**替换**被静默接受——
            # 等于在运行中删掉了一条必测验收依据。
            dropped_acceptance = (
                current.links.acceptance_item_ids - next_case.links.acceptance_item_ids
            )
            dropped_critical_paths = (
                current.links.critical_path_ids - next_case.links.critical_path_ids
            )
            if dropped_acceptance or dropped_critical_paths:
                refuse(
                    RuntimeRevisionRefusalCode.APPLICABILITY_WEAKENED,
                    f"{case_id} drops acceptance items or critical paths while remaining "
                    f"mandatory (acceptance {sorted(dropped_acceptance)}, "
                    f"critical paths {sorted(dropped_critical_paths)})",
                )

        basis_changed = next_case.assertion_basis != current.assertion_basis
        weakened = basis_changed and assertion_basis_weakened(
            current=current.assertion_basis, requested=next_case.assertion_basis
        )
        if weakened:
            refuse(
                RuntimeRevisionRefusalCode.ASSERTION_WEAKENED,
                f"{case_id} weakens the assertion basis "
                f"({current.assertion_basis.state.value} -> "
                f"{next_case.assertion_basis.state.value})",
            )

        if current.independent_verification and not next_case.independent_verification:
            refuse(
                RuntimeRevisionRefusalCode.INDEPENDENT_VERIFICATION_REMOVED,
                f"{case_id} removes the independent verification method",
            )

        if basis_changed and not weakened:
            # 状态一律派生：请求里写的 confirmed 不算数，只有绑定了**新**依据修订与
            # 文本摘要的 ConfirmationRecord 才算（"补确认不回写冻结 Case/Plan"）。
            # 依据文本已变，旧确认按定义失效，所以新依据在拿到新确认前一律记为待确认。
            derived = effective_assertion_basis_state(
                next_case.assertion_basis, confirmations, case_id=case_id
            )
            if derived is not AssertionBasisState.CONFIRMED:
                confirmation_required.append(case_id)

        case_steps = facts.steps_of(case_id)
        if not case_steps:
            refuse(
                RuntimeRevisionRefusalCode.NO_UNEXECUTED_STEP,
                f"{case_id} has no step in the run facts, so no step boundary exists",
            )
            continue

        executing = [
            step.step_id
            for step in case_steps
            if step.progress() is StepRuntimeProgress.EXECUTING
        ]
        if executing:
            refuse(
                RuntimeRevisionRefusalCode.STEP_IS_EXECUTING,
                f"{case_id} has a step being executed: {sorted(executing)}",
            )

        recorded = [
            step.step_id
            for step in case_steps
            if step.progress() is StepRuntimeProgress.FACTS_RECORDED
        ]

        if change.target_step_ids:
            known = {step.step_id for step in case_steps}
            unknown = sorted(set(change.target_step_ids) - known)
            if unknown:
                refuse(
                    RuntimeRevisionRefusalCode.STEP_NOT_IN_CASE,
                    f"{case_id} has no such step: {unknown}",
                )
            named_recorded = sorted(set(change.target_step_ids) & set(recorded))
            if named_recorded:
                refuse(
                    RuntimeRevisionRefusalCode.STEP_FACTS_ALREADY_RECORDED,
                    "steps that already recorded facts cannot be rewritten: "
                    f"{named_recorded}",
                )
            targets = [
                step for step in case_steps if step.step_id in set(change.target_step_ids)
            ]
        else:
            targets = list(case_steps)

        fresh = sorted(
            step.step_id
            for step in targets
            if step.progress() is StepRuntimeProgress.NOT_STARTED
        )
        if not fresh:
            refuse(
                RuntimeRevisionRefusalCode.NO_UNEXECUTED_STEP,
                f"{case_id} has no step left to execute; a runtime revision cannot "
                "rewrite recorded facts",
            )

        affected.extend(fresh)
        preserved.extend(recorded)
        if basis_changed and recorded:
            invalidated_basis.extend(recorded)
            rejudge.append(case_id)
            pause_required = True

    if refusals:
        # 整体拒绝：不产生部分生效的修订，也不留下"哪些步骤被改过"的歧义。
        return RuntimeRevisionDecision(
            accepted=False,
            revision_no=facts.runtime_revision_count + 1,
            effective_driver=facts.driver,
            snapshot_commit_id=facts.snapshot_commit_id,
            snapshot_cursor=facts.snapshot_cursor,
            refusals=tuple(refusals),
            new_run_required=new_run_required,
        )

    return RuntimeRevisionDecision(
        accepted=True,
        revision_no=facts.runtime_revision_count + 1,
        effective_driver=effective_driver,
        snapshot_commit_id=facts.snapshot_commit_id,
        snapshot_cursor=facts.snapshot_cursor,
        affected_step_ids=tuple(sorted(set(affected))),
        preserved_step_ids=tuple(sorted(set(preserved))),
        invalidated_basis_step_ids=tuple(sorted(set(invalidated_basis))),
        rejudge_case_ids=tuple(sorted(set(rejudge))),
        confirmation_required_case_ids=tuple(sorted(set(confirmation_required))),
        pause_required=pause_required,
        new_run_required=False,
    )


def assert_runtime_revision_accepted(
    decision: RuntimeRevisionDecision,
) -> RuntimeRevisionDecision:
    """把拒绝变成异常，供"必须通过"的调用点使用。"""
    if not decision.accepted:
        raise RuntimeRevisionRefused(decision)
    return decision


__all__ = [
    "ACTIVE_ATTEMPT_STATES",
    "AttemptRuntimeState",
    "CaseRuntimeChange",
    "RunRuntimeFacts",
    "RunRuntimeState",
    "RuntimeRevisionDecision",
    "RuntimeRevisionRefusal",
    "RuntimeRevisionRefusalCode",
    "RuntimeRevisionRefused",
    "RuntimeRevisionRequest",
    "StepRuntimeFacts",
    "StepRuntimeProgress",
    "StepRuntimeState",
    "assert_runtime_revision_accepted",
    "assertion_basis_weakened",
    "evaluate_runtime_revision",
    "step_runtime_progress",
]
