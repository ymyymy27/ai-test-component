"""运行中修订的**消费侧**：把 C 的 `ExecutionFacts` 翻译成领域纯值，再交给领域守卫。

架构文档《01-项目与计划》第 7 节把 `RunPlanRevision` / `StepRevisionRef` 划到本模块
（与 `execution/runner.py` 协作）；`BD-001` 第 2.1 节明确"运行中修订由 C 在执行侧记录，
B 提供守卫规则"。因此本模块只做两件事：

1. **翻译**：`runtime_facts_from_execution_facts()` 把 C 的合同对象
   （`contracts.execution_facts.ExecutionFacts`）变成 `domain.planning.runtime_revision`
   的纯值对象——枚举镜像、当前尝试状态、运行中修订序列计数。事实自相矛盾时**抛错**，
   不猜测、不降级；
2. **编排**：`request_runtime_revision()` 组装领域入参并调用 `evaluate_runtime_revision()`；
   `assert_runtime_revision_accepted()` 把拒绝变成异常。

**产品代码不读文件系统**：事实夹具（`docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures/`）
只在测试侧用 `json.loads` 读取并校验成合同对象，再传进来。产品路径不按路径找夹具，
也不解释文件名——"D 不能直接读取未发布的 spool 文件或以文件名推断证据"是同一条理由。

本模块**不落盘**：`RunPlanRevision` 的持久化属 A 的记录仓储（`AB-001`），B 的转接头
`substrate_adapter.py` 目前只覆盖工作单元与只读仓储，运行中修订的保存尚未接通。
"""

from __future__ import annotations

from collections.abc import Sequence

from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.planning.plans import (
    Case,
    ConfirmationRecord,
    Plan,
    RunDriver,
    RunTier,
)
from aitest.domain.planning.runtime_revision import (
    AttemptRuntimeState,
    RunRuntimeFacts,
    RunRuntimeState,
    RuntimeRevisionDecision,
    RuntimeRevisionRequest,
    StepRuntimeFacts,
    StepRuntimeState,
    assert_runtime_revision_accepted,
    evaluate_runtime_revision,
)

__all__ = [
    "assert_runtime_revision_accepted",
    "request_runtime_revision",
    "runtime_facts_from_execution_facts",
]


def runtime_facts_from_execution_facts(facts: ExecutionFacts) -> RunRuntimeFacts:
    """把 C 的 `ExecutionFacts` 快照翻译成领域事实视图。

    一致性核对（全部来自 C 自己的字段，不引入调用者输入）：

    1. 顶层与 Run 内的运行标识和修订必须一致；
    2. 顶层 `runtime_revision_refs` 与 `run.runtime_revision_refs` 必须逐项同序一致——
       运行中修订序列是 C 的记录，两处不一致时不能任选一处当"修订序号"；
    3. 步骤与尝试各自标识唯一且属于本运行，尝试必须指向已知步骤；
    4. 当前映射准确覆盖全部步骤，被引用尝试属于该步骤且确为当前；
       历史尝试可以保留，但不能与映射矛盾地宣称当前。

    任一条不成立即抛 `ValueError`：事实自相矛盾时，"拒绝决策"也是一种猜测。
    """
    if facts.run.run_id != facts.run_id:
        raise ValueError(
            "execution facts disagree on the run identity: "
            f"top level {facts.run_id!r}, run {facts.run.run_id!r}"
        )
    if facts.run.run_revision != facts.run_revision:
        raise ValueError("execution facts disagree on the run revision")

    revision_refs = facts.run.runtime_revision_refs
    if revision_refs != facts.runtime_revision_refs:
        raise ValueError("execution facts disagree on the exact runtime revision sequence")
    if any(not ref.strip() for ref in revision_refs) or len(set(revision_refs)) != len(
        revision_refs
    ):
        raise ValueError("runtime revision references must be nonempty and unique")

    step_ids = {step.step_id for step in facts.steps}
    if len(step_ids) != len(facts.steps):
        raise ValueError("execution facts contain duplicate step ids")
    if set(facts.current_attempt_by_step) != step_ids:
        raise ValueError("current attempt mapping must cover exactly the known steps")
    if any(step.run_id != facts.run_id for step in facts.steps):
        raise ValueError("execution step belongs to another run")

    attempts = {attempt.attempt_id: attempt for attempt in facts.attempts}
    if len(attempts) != len(facts.attempts):
        raise ValueError("execution facts contain duplicate attempt ids")

    history_by_step: dict[str, list[AttemptRuntimeState]] = {}
    for historical in facts.attempts:
        if not historical.is_current:
            history_by_step.setdefault(historical.step_id, []).append(
                AttemptRuntimeState(historical.state.value)
            )

    steps: list[StepRuntimeFacts] = []
    for step in facts.steps:
        mapped_attempt_id = facts.current_attempt_by_step.get(step.step_id)
        if mapped_attempt_id != step.current_attempt_id:
            raise ValueError(
                "execution facts disagree on the current attempt of "
                f"{step.step_id}: step {step.current_attempt_id!r}, "
                f"map {mapped_attempt_id!r}"
            )
        attempt_state: AttemptRuntimeState | None = None
        if step.current_attempt_id is not None:
            attempt = attempts.get(step.current_attempt_id)
            if attempt is None:
                raise ValueError(
                    f"{step.step_id} references an unknown attempt "
                    f"{step.current_attempt_id!r}"
                )
            if attempt.step_id != step.step_id:
                raise ValueError("current execution attempt belongs to another step")
            if attempt.step_revision_ref != step.step_revision_ref:
                raise ValueError("current attempt does not use the exact current step revision")
            attempt_state = AttemptRuntimeState(attempt.state.value)
        steps.append(
            StepRuntimeFacts(
                step_id=step.step_id,
                case_id=step.case_id,
                ordinal=step.ordinal,
                state=StepRuntimeState(step.state.value),
                required_for_case=step.required_for_case,
                current_attempt_state=attempt_state,
                invalidated=step.invalidated,
                step_revision_no=step.step_revision,
                historical_attempt_states=tuple(history_by_step.get(step.step_id, ())),
            )
        )

    for attempt in facts.attempts:
        if attempt.run_id != facts.run_id or attempt.step_id not in step_ids:
            raise ValueError("execution attempt run/step ownership differs")
        mapped_current = facts.current_attempt_by_step[attempt.step_id] == attempt.attempt_id
        if attempt.is_current != mapped_current:
            raise ValueError("execution current attempt identity disagrees with the mapping")

    return RunRuntimeFacts(
        run_id=facts.run.run_id,
        run_revision=facts.run.run_revision,
        plan_revision_id=facts.plan_revision.revision_id,
        plan_revision_no=facts.plan_revision.revision_no,
        plan_revision_digest=facts.plan_revision.digest,
        control_state=RunRuntimeState(facts.run.control_state.value),
        driver=RunDriver(facts.run.driver.value),
        tier=RunTier(facts.run.tier.value),
        snapshot_commit_id=facts.snapshot_commit_id,
        snapshot_cursor=facts.snapshot_cursor,
        runtime_revision_count=len(revision_refs),
        frozen_required_case_ids=frozenset(facts.run.required_scope),
        mandatory_case_ids=frozenset(facts.coverage.mandatory_case_ids),
        steps=tuple(steps),
    )


def request_runtime_revision(
    *,
    plan: Plan,
    cases: Sequence[Case],
    confirmations: Sequence[ConfirmationRecord],
    request: RuntimeRevisionRequest,
    facts: ExecutionFacts,
) -> RuntimeRevisionDecision:
    """消费一次运行中修订请求；返回决策（含拒绝原因与交接清单）。

    `cases` 只需要包含被改动的用例的**冻结修订**对象；它们的 `revision` 会与
    `Plan.case_revisions` 的冻结引用逐项核对。
    """
    return evaluate_runtime_revision(
        plan=plan,
        cases=cases,
        confirmations=confirmations,
        request=request,
        facts=runtime_facts_from_execution_facts(facts),
    )
