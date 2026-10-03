"""运行中修订的消费侧（检查文档 B-05）：用 C 的固定夹具驱动 B 的门禁。

夹具来源：`docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures/*.json`（C 交付；
形状已由 `tests/contracts/test_public_execution_facts_fixtures.py` 锁定）。
测试侧负责读文件与校验，产品代码只接受已解析的 `ExecutionFacts` 对象。

**夹具覆盖缺口（如实登记）**：交付夹具的 `run.control_state` 只有 `completed` 与
`pending_verification`，且没有任何一步是 `running`。"运行中快照"与"正在执行的步骤"
两个取值在 C 的合同里合法、在夹具里没有样本。因此：

- 拒绝路径尽量用**交付夹具原样**驱动（已完成／执行错误／阻塞／超时／未知）；
- "运行中"与"正在执行"两个取值由 `failure.json` 的载荷**派生**（只改
  `control_state` 与步骤／尝试状态），派生只用于让门禁两侧都跑到，
  不冒充交付夹具，也不写进产品代码。

测试用临时目录的写法遵循本仓库受限环境的约定：不用 `tmp_path`。本文件不需要临时目录，
也没有产品代码的文件系统访问。
"""

from __future__ import annotations

import copy
import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from aitest.application.planning.run_mode import (
    request_runtime_revision,
    runtime_facts_from_execution_facts,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseLayer,
    CaseLink,
    CaseRevisionRef,
    ConfirmationRecord,
    Plan,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
    TemplateVersionRef,
)
from aitest.domain.planning.rules import RuleRevisionRef
from aitest.domain.planning.runtime_revision import (
    AttemptRuntimeState,
    CaseRuntimeChange,
    RunRuntimeState,
    RuntimeRevisionDecision,
    RuntimeRevisionRefusalCode,
    RuntimeRevisionRefused,
    RuntimeRevisionRequest,
    StepRuntimeProgress,
    StepRuntimeState,
    assert_runtime_revision_accepted,
)

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures"
)

#: 交付夹具全集；每个都至少被本文件用到一次。
_DELIVERED = ("success", "failure", "timeout", "unknown", "quick", "multistream", "non_utf8")

_BASIS_TEXT = "订单创建后状态为已支付"
_BASIS_DIGEST = "sha256:basis-1"


# ------------------------------------------------------------------ 夹具装载


def _payload(name: str) -> dict[str, Any]:
    """读一份交付夹具；`json.loads` 只用在测试侧。"""
    return cast(
        dict[str, Any],
        json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")),
    )


def _with_run_control(payload: dict[str, Any], state: str) -> dict[str, Any]:
    """派生：只改运行控制状态（交付夹具里没有运行中快照）。"""
    payload["run"]["control_state"] = state
    return payload


def _with_running_step(payload: dict[str, Any], step_id: str) -> dict[str, Any]:
    """派生：把某一步及其当前尝试改成 `running`（交付夹具里没有正在执行的步骤）。"""
    step = next(item for item in payload["steps"] if item["step_id"] == step_id)
    step["state"] = "running"
    attempt_id = step["current_attempt_id"]
    if attempt_id is not None:
        attempt = next(
            item for item in payload["attempts"] if item["attempt_id"] == attempt_id
        )
        attempt["state"] = "running"
    return payload


def _with_runtime_revisions(payload: dict[str, Any], count: int) -> dict[str, Any]:
    """派生：给 C 的事实加上已记录的运行中修订条数（顶层与 run 内一致）。"""
    refs = [f"runtime-revision-{index}" for index in range(1, count + 1)]
    payload["run"]["runtime_revision_refs"] = refs
    payload["runtime_revision_refs"] = refs
    return payload


def _failure_in_progress() -> dict[str, Any]:
    """`failure.json` 派生出的"运行中"快照：step-1 已产生事实，step-2 仍未执行。"""
    return _with_run_control(_payload("failure"), "running")


# ------------------------------------------------------------------ 领域夹具


def _case(
    *,
    case_id: str = "case-1",
    revision: int = 1,
    basis_state: AssertionBasisState = AssertionBasisState.PRESENT_UNCONFIRMED,
    basis_text: str = _BASIS_TEXT,
    basis_digest: str = _BASIS_DIGEST,
    critical_paths: frozenset[str] = frozenset({"path-1"}),
    acceptance_item_ids: frozenset[str] = frozenset({"AC-01"}),
    independent_verification: str | None = "查询订单库核对状态",
) -> Case:
    basis = (
        AssertionBasis(revision=revision, state=AssertionBasisState.MISSING)
        if basis_state is AssertionBasisState.MISSING
        else AssertionBasis(
            revision=revision,
            state=basis_state,
            text=basis_text,
            text_digest=basis_digest,
        )
    )
    return Case(
        case_id=case_id,
        revision=revision,
        layer=CaseLayer.L2,
        objective="确认订单创建链路可用",
        preconditions=("订单服务已启动",),
        inputs=("下单请求",),
        steps=("创建订单", "查询订单"),
        expected="订单状态为已支付",
        verification_method="命令输出比对",
        independent_verification=independent_verification,
        links=CaseLink(
            acceptance_item_ids=acceptance_item_ids,
            critical_path_ids=critical_paths,
        ),
        assertion_basis=basis,
    )


def _plan(
    *,
    revision: int = 1,
    status: PlanPublicationStatus = PlanPublicationStatus.PUBLISHED,
    case_revision: int = 1,
    plan_id: str = "plan-1",
) -> Plan:
    return Plan(
        plan_id=plan_id,
        revision=revision,
        scope=AcceptanceScope(
            scope_id="scope-1",
            revision=1,
            name="订单模块回归",
            required_case_ids=frozenset({"case-1"}),
            template_case_ids=frozenset({"case-1"}),
        ),
        case_revisions=(
            CaseRevisionRef(case_id="case-1", revision=case_revision, digest="sha256:c1"),
        ),
        rule_revisions=(RuleRevisionRef(rule_id="rule-1", revision=1, digest="sha256:r1"),),
        template_versions=(
            TemplateVersionRef(
                template_id="http-workflow", version="1.0.0", digest="sha256:t"
            ),
        ),
        run_tier=RunTier.FULL,
        initial_driver=RunDriver.PLANNED,
        status=status,
        confirmation_id="confirm-1" if status is PlanPublicationStatus.PUBLISHED else None,
    )


def _change(
    case: Case,
    *,
    revision: int = 2,
    basis: AssertionBasis | None = None,
    targets: tuple[str, ...] = (),
    remove_from_required: bool = False,
) -> CaseRuntimeChange:
    """提出一个新的用例修订；`basis=None` 表示本次不改断言依据。"""
    updates: dict[str, Any] = {
        "revision": revision,
        "steps": (*case.steps, f"补充步骤-{revision}"),
    }
    if basis is not None:
        updates["assertion_basis"] = basis
    return CaseRuntimeChange(
        next_case=dataclasses.replace(case, **updates),
        target_step_ids=targets,
        remove_from_required=remove_from_required,
    )


def _decide(
    payload: Mapping[str, Any],
    *,
    change: CaseRuntimeChange | None = None,
    plan: Plan | None = None,
    frozen_case: Case | None = None,
    confirmations: Sequence[ConfirmationRecord] = (),
    cursor: int | None = None,
    driver: RunDriver | None = None,
    base_plan_revision_no: int = 1,
    base_plan_revision_id: str = "plan-1",
    base_plan_revision_digest: str | None = "sha256:plan-1",
) -> RuntimeRevisionDecision:
    """驱动一次运行中修订。

    默认写入的是夹具里的**冻结计划身份**（`plan-1@1`，摘要 `sha256:plan-1`）：
    检查项 B-15 要求核对完整身份，因此正向用例也必须给出这三项，
    否则测不到"身份对了才放行"这一半。
    """
    case = frozen_case if frozen_case is not None else _case()
    return request_runtime_revision(
        plan=plan if plan is not None else _plan(),
        cases=(case,),
        confirmations=tuple(confirmations),
        request=RuntimeRevisionRequest(
            base_plan_revision_id=base_plan_revision_id,
            base_plan_revision_no=base_plan_revision_no,
            base_plan_revision_digest=base_plan_revision_digest,
            observed_snapshot_cursor=(
                int(payload["snapshot_cursor"]) if cursor is None else cursor
            ),
            case_changes=(change if change is not None else _change(case),),
            reason="上游变更，需要调整未执行步骤",
            operator_ref="operator-1",
            requested_driver=driver,
        ),
        facts=ExecutionFacts.model_validate(dict(payload)),
    )


def _codes(decision: RuntimeRevisionDecision) -> set[str]:
    return {refusal.code.value for refusal in decision.refusals}


# ------------------------------------------------------------------ 1 事实翻译


def test_every_delivered_fixture_becomes_a_runtime_view() -> None:
    for name in _DELIVERED:
        view = runtime_facts_from_execution_facts(ExecutionFacts.model_validate(_payload(name)))
        assert view.run_id == "run-1"
        assert view.plan_revision_no == 1
        assert view.run_revision >= 0
        assert view.runtime_revision_count == 0
        assert view.frozen_required_case_ids == {"case-1"}
        assert view.steps


def test_the_delivered_failure_snapshot_separates_recorded_and_unstarted_steps() -> None:
    """`failure.json` 是唯一同时给出"已产生事实"和"尚未执行"的交付夹具。"""
    view = runtime_facts_from_execution_facts(
        ExecutionFacts.model_validate(_payload("failure"))
    )
    by_id = {step.step_id: step for step in view.steps}
    assert by_id["step-1"].state is StepRuntimeState.EXECUTION_ERROR
    assert by_id["step-1"].current_attempt_state is AttemptRuntimeState.EXECUTION_ERROR
    assert by_id["step-1"].progress() is StepRuntimeProgress.FACTS_RECORDED
    assert by_id["step-2"].state is StepRuntimeState.BLOCKED
    assert by_id["step-2"].current_attempt_state is None
    assert by_id["step-2"].progress() is StepRuntimeProgress.NOT_STARTED
    assert set(by_id) == {"step-1", "step-2"}


def test_the_delivered_timeout_snapshot_keeps_the_step_pending_verification() -> None:
    """超时 Attempt 停在待核实：既不是"正在执行"，也不因此可以被改写。"""
    view = runtime_facts_from_execution_facts(
        ExecutionFacts.model_validate(_payload("timeout"))
    )
    step = view.steps[0]
    assert view.control_state is RunRuntimeState.PENDING_VERIFICATION
    assert step.state is StepRuntimeState.PENDING_VERIFICATION
    assert step.current_attempt_state is AttemptRuntimeState.PENDING_VERIFICATION
    assert step.progress() is StepRuntimeProgress.FACTS_RECORDED


def test_the_delivered_unknown_snapshot_is_a_recorded_fact_not_a_pass() -> None:
    """未知结果按"已产生事实"处理：B 不把它当通过，也不允许回改。"""
    view = runtime_facts_from_execution_facts(
        ExecutionFacts.model_validate(_payload("unknown"))
    )
    assert view.control_state is RunRuntimeState.PENDING_VERIFICATION
    assert view.steps[0].progress() is StepRuntimeProgress.FACTS_RECORDED


def test_the_translation_refuses_inconsistent_facts() -> None:
    """C 的事实自相矛盾时抛错：不一致的快照不能拿来当决策依据。"""
    mismatched_run = _payload("success")
    mismatched_run["run_id"] = "run-other"
    with pytest.raises(ValueError, match="run identity"):
        runtime_facts_from_execution_facts(
            ExecutionFacts.model_validate(mismatched_run)
        )

    mismatched_revisions = _payload("success")
    mismatched_revisions["runtime_revision_refs"] = ["runtime-revision-1"]
    with pytest.raises(ValueError, match="runtime revision sequence"):
        runtime_facts_from_execution_facts(
            ExecutionFacts.model_validate(mismatched_revisions)
        )

    mismatched_attempt = _payload("success")
    mismatched_attempt["current_attempt_by_step"]["step-1"] = None
    with pytest.raises(ValueError, match="current attempt"):
        runtime_facts_from_execution_facts(
            ExecutionFacts.model_validate(mismatched_attempt)
        )

    unknown_attempt = _payload("success")
    unknown_attempt["current_attempt_by_step"]["step-1"] = "attempt-404"
    unknown_attempt["steps"][0]["current_attempt_id"] = "attempt-404"
    with pytest.raises(ValueError, match="unknown attempt"):
        runtime_facts_from_execution_facts(
            ExecutionFacts.model_validate(unknown_attempt)
        )


# ------------------------------------------------------------------ 2 只影响未执行步骤


def test_a_revision_applies_only_to_steps_without_facts() -> None:
    """`failure.json`（派生为运行中）：修订作用 step-2，step-1 绑定原修订。"""
    decision = _decide(_failure_in_progress())
    assert decision.accepted is True
    assert decision.refusals == ()
    assert decision.affected_step_ids == ("step-2",)
    assert decision.preserved_step_ids == ("step-1",)
    assert decision.revision_no == 1


def test_the_next_revision_number_is_derived_from_the_facts() -> None:
    """修订序列来自 C 的记录条数，不是调用者报的号。"""
    payload = _with_runtime_revisions(_failure_in_progress(), 2)
    decision = _decide(payload)
    assert decision.accepted is True
    assert decision.revision_no == 3


def test_a_completed_run_accepts_no_runtime_revision() -> None:
    """`success.json` 原样：运行已结束，且每一步都已产生事实。"""
    decision = _decide(_payload("success"))
    assert decision.accepted is False
    assert _codes(decision) == {
        RuntimeRevisionRefusalCode.RUN_NOT_ACTIVE.value,
        RuntimeRevisionRefusalCode.NO_UNEXECUTED_STEP.value,
    }
    assert decision.affected_step_ids == ()


def test_naming_a_step_that_already_recorded_facts_is_refused() -> None:
    decision = _decide(
        _failure_in_progress(),
        change=_change(_case(), targets=("step-1",)),
    )
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.STEP_FACTS_ALREADY_RECORDED.value in _codes(decision)


def test_a_delivered_timeout_step_cannot_be_rewritten() -> None:
    """`timeout.json` 原样：没有未执行步骤，且拒绝原因不是"正在执行"。"""
    decision = _decide(_payload("timeout"))
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.NO_UNEXECUTED_STEP.value in _codes(decision)
    assert RuntimeRevisionRefusalCode.STEP_IS_EXECUTING.value not in _codes(decision)


# ------------------------------------------------------------------ 3 拒绝正在执行的项


def test_modifying_a_running_step_is_refused() -> None:
    """派生载荷：step-1 正在执行（夹具自身没有 running 样本）。"""
    payload = _with_running_step(_failure_in_progress(), "step-1")
    view = runtime_facts_from_execution_facts(ExecutionFacts.model_validate(payload))
    assert view.steps[0].progress() is StepRuntimeProgress.EXECUTING
    decision = _decide(payload)
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.STEP_IS_EXECUTING.value in _codes(decision)


def test_a_step_whose_attempt_is_still_collecting_is_refused() -> None:
    """尝试状态优于步骤状态：步骤写 `blocked`、尝试仍在采集时也必须拒绝。"""
    payload = _failure_in_progress()
    payload["steps"][1]["state"] = "blocked"
    payload["steps"][1]["current_attempt_id"] = "attempt-2"
    payload["current_attempt_by_step"]["step-2"] = "attempt-2"
    payload["attempts"].append(
        {
            "attempt_id": "attempt-2",
            "run_id": "run-1",
            "step_id": "step-2",
            "attempt_revision": 1,
            "attempt_index": 1,
            "retry_count": 0,
            "state": "collecting",
            "is_current": True,
            "resolved_input_digest": "sha256:input-2",
            "step_revision_ref": {
                "step_revision_id": "step-rev-2",
                "revision_no": 1,
                "digest": "sha256:step-2",
                "inherited": False,
            },
            "source_binding_digest": "sha256:source-1",
            "side_effect_class": "read_only",
            "adapter_kind": "command",
            "adapter_version": "1.0",
            "capture_completeness": "partial",
            "output_cursors": [],
        }
    )
    decision = _decide(payload)
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.STEP_IS_EXECUTING.value in _codes(decision)


# ------------------------------------------------------------------ 4 必测、适用性与断言


def test_removing_a_mandatory_case_is_refused() -> None:
    decision = _decide(
        _failure_in_progress(),
        change=_change(_case(), remove_from_required=True),
    )
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.MANDATORY_CASE_REMOVAL.value in _codes(decision)


def test_narrowing_a_mandatory_case_link_is_refused() -> None:
    """必测用例丢掉关键链路／验收项关联属于"变更适用性"，同样拒绝。"""
    change = CaseRuntimeChange(
        next_case=dataclasses.replace(
            _case(),
            revision=2,
            links=CaseLink(
                acceptance_item_ids=frozenset({"AC-01"}),
                critical_path_ids=frozenset(),
                no_critical_path_reason="本次不跑关键链路",
            ),
        )
    )
    decision = _decide(_failure_in_progress(), change=change)
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.APPLICABILITY_WEAKENED.value in _codes(decision)


def test_weakening_the_assertion_basis_is_refused() -> None:
    without_basis = _change(
        _case(),
        basis=AssertionBasis(revision=2, state=AssertionBasisState.MISSING),
    )
    decision = _decide(_failure_in_progress(), change=without_basis)
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.ASSERTION_WEAKENED.value in _codes(decision)

    downgraded = _change(
        _case(basis_state=AssertionBasisState.CONFIRMED),
        basis=AssertionBasis(
            revision=2,
            state=AssertionBasisState.PRESENT_UNCONFIRMED,
            text=_BASIS_TEXT,
            text_digest=_BASIS_DIGEST,
        ),
    )
    downgraded_decision = _decide(
        _failure_in_progress(),
        change=downgraded,
        frozen_case=_case(basis_state=AssertionBasisState.CONFIRMED),
    )
    assert downgraded_decision.accepted is False
    assert (
        RuntimeRevisionRefusalCode.ASSERTION_WEAKENED.value
        in _codes(downgraded_decision)
    )


def test_removing_independent_verification_is_refused() -> None:
    kept = _decide(_failure_in_progress(), change=_change(_case()))
    assert kept.accepted is True  # 保留核验方式时正常通过

    stripped = CaseRuntimeChange(
        next_case=dataclasses.replace(
            _case(), revision=2, independent_verification=None
        )
    )
    refused = _decide(_failure_in_progress(), change=stripped)
    assert refused.accepted is False
    assert (
        RuntimeRevisionRefusalCode.INDEPENDENT_VERIFICATION_REMOVED.value
        in _codes(refused)
    )


def test_an_unchanged_basis_is_not_reported_as_weakening() -> None:
    """依据没变就不是弱化：`missing` 的历史用例沿用原依据时不该被误拦。"""
    frozen = _case(basis_state=AssertionBasisState.MISSING)
    decision = _decide(
        _failure_in_progress(),
        frozen_case=frozen,
        change=_change(frozen),
    )
    assert decision.accepted is True
    assert RuntimeRevisionRefusalCode.ASSERTION_WEAKENED.value not in _codes(decision)


def test_a_new_basis_text_is_not_weakening_but_must_be_confirmed_again() -> None:
    """同级换文本按"依据变化"处理：不判弱化，但要交出失效与重判清单。"""
    changed = _change(
        _case(),
        basis=AssertionBasis(
            revision=2,
            state=AssertionBasisState.PRESENT_UNCONFIRMED,
            text="响应码 200 且订单落库",
            text_digest="sha256:basis-2",
        ),
    )
    decision = _decide(_failure_in_progress(), change=changed)
    assert decision.accepted is True
    assert decision.confirmation_required_case_ids == ("case-1",)
    assert decision.invalidated_basis_step_ids == ("step-1",)
    assert decision.rejudge_case_ids == ("case-1",)
    assert decision.pause_required is True


def test_a_basis_change_on_an_unexecuted_case_needs_no_invalidation() -> None:
    """`success.json` 派生为运行中、且步骤尚未开始：依据变化无需 C 失效。"""
    payload = _payload("success")
    payload["steps"][0]["state"] = "pending"
    payload["steps"][0]["current_attempt_id"] = None
    payload["current_attempt_by_step"]["step-1"] = None
    payload["attempts"] = []
    _with_run_control(payload, "running")
    decision = _decide(
        payload,
        change=_change(
            _case(),
            basis=AssertionBasis(
                revision=2,
                state=AssertionBasisState.PRESENT_UNCONFIRMED,
                text="响应码 200 且订单落库",
                text_digest="sha256:basis-2",
            ),
        ),
    )
    assert decision.accepted is True
    assert decision.affected_step_ids == ("step-1",)
    assert decision.invalidated_basis_step_ids == ()
    assert decision.rejudge_case_ids == ()
    assert decision.pause_required is False


def test_a_self_declared_confirmation_is_derived_from_records_not_from_the_request() -> None:
    """请求里写 `confirmed` 不算数；只有绑定新修订与摘要的确认记录才算。"""
    declared = _change(
        _case(),
        basis=AssertionBasis(
            revision=2,
            state=AssertionBasisState.CONFIRMED,
            text="响应码 200 且订单落库",
            text_digest="sha256:basis-2",
        ),
    )
    without_record = _decide(_failure_in_progress(), change=declared)
    assert without_record.accepted is True
    assert without_record.confirmation_required_case_ids == ("case-1",)

    record = ConfirmationRecord(
        confirmation_id="confirmation-1",
        case_id="case-1",
        basis_revision=2,
        basis_text_digest="sha256:basis-2",
        confirmed_at_commit="commit-1",
    )
    with_record = _decide(
        _failure_in_progress(), change=declared, confirmations=(record,)
    )
    assert with_record.confirmation_required_case_ids == ()

    other_case_record = dataclasses.replace(record, case_id="case-other")
    still_required = _decide(
        _failure_in_progress(), change=declared, confirmations=(other_case_record,)
    )
    assert still_required.confirmation_required_case_ids == ("case-1",)


# ------------------------------------------------------------------ 5 计划、快照与驱动


def test_stale_snapshot_is_refused() -> None:
    decision = _decide(_failure_in_progress(), cursor=3)
    assert decision.accepted is False
    assert _codes(decision) == {RuntimeRevisionRefusalCode.STALE_SNAPSHOT.value}


def test_an_unpublished_plan_or_another_plan_revision_is_refused() -> None:
    draft = _decide(
        _failure_in_progress(),
        plan=_plan(status=PlanPublicationStatus.DRAFT),
    )
    assert draft.accepted is False
    assert RuntimeRevisionRefusalCode.PLAN_NOT_PUBLISHED.value in _codes(draft)

    other_revision = _decide(
        _failure_in_progress(),
        plan=_plan(revision=2),
        base_plan_revision_no=2,
    )
    assert other_revision.accepted is False
    assert RuntimeRevisionRefusalCode.PLAN_REVISION_MISMATCH.value in _codes(
        other_revision
    )


def test_a_case_outside_the_frozen_plan_is_refused() -> None:
    decision = _decide(
        _failure_in_progress(),
        change=_change(_case(case_id="case-9")),
    )
    assert decision.accepted is False
    assert _codes(decision) == {
        RuntimeRevisionRefusalCode.FROZEN_CASE_NOT_PROVIDED.value
    }


def test_a_case_revision_that_does_not_advance_is_refused() -> None:
    decision = _decide(_failure_in_progress(), change=_change(_case(), revision=1))
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.CASE_REVISION_NOT_ADVANCED.value in _codes(decision)


def test_the_driver_can_only_narrow_and_expansion_asks_for_a_new_run() -> None:
    narrowed = _decide(_failure_in_progress(), driver=RunDriver.STEPWISE)
    assert narrowed.accepted is True
    assert narrowed.effective_driver is RunDriver.STEPWISE

    expanded_payload = _failure_in_progress()
    expanded_payload["run"]["driver"] = "stepwise"
    expanded = _decide(expanded_payload, driver=RunDriver.PLANNED)
    assert expanded.accepted is False
    assert _codes(expanded) == {RuntimeRevisionRefusalCode.DRIVER_EXPANSION.value}
    assert expanded.new_run_required is True
    assert expanded.effective_driver is RunDriver.STEPWISE


# ------------------------------------------------------------------ 6 不回写、不下结论


def test_the_frozen_plan_and_case_are_not_modified() -> None:
    frozen_case = _case()
    plan = _plan()
    before_case = dataclasses.replace(frozen_case)
    before_plan = dataclasses.replace(plan)
    decision = _decide(
        _failure_in_progress(),
        plan=plan,
        frozen_case=frozen_case,
        change=_change(frozen_case, targets=("step-2",)),
    )
    assert decision.accepted is True
    assert frozen_case == before_case
    assert plan == before_plan
    assert frozen_case.revision == 1
    assert plan.case_revisions[0].revision == 1


def test_the_decision_carries_no_business_verdict() -> None:
    """依据变化的结论归 C／D：B 的决策上没有通过、验证数或证据等级。"""
    decision = _decide(_failure_in_progress())
    names = {field.name for field in dataclasses.fields(decision)}
    assert names.isdisjoint(
        {
            "conclusion",
            "passed",
            "failed",
            "verified",
            "verified_count",
            "evidence_level",
            "conclusion_ceiling",
        }
    )
    assert names >= {"invalidated_basis_step_ids", "rejudge_case_ids", "pause_required"}


def test_the_application_gate_raises_with_the_decision() -> None:
    decision = _decide(_payload("success"))
    with pytest.raises(RuntimeRevisionRefused) as raised:
        assert_runtime_revision_accepted(decision)
    assert raised.value.decision.accepted is False
    assert raised.value.decision.refusals

    accepted = _decide(_failure_in_progress())
    assert assert_runtime_revision_accepted(accepted) is accepted


def test_a_request_must_change_something() -> None:
    with pytest.raises(ValueError, match="at least one case"):
        RuntimeRevisionRequest(
            base_plan_revision_id="plan-1",
            base_plan_revision_no=1,
            observed_snapshot_cursor=11,
            case_changes=(),
            reason="无改动",
            operator_ref="operator-1",
        )


# ------------------------------------------------------------------ 7 冻结计划完整身份（B-15）


def test_a_plan_with_the_same_revision_but_another_id_is_refused() -> None:
    """检查项 B-15：同修订号、不同 `plan_id` 的计划不得被当成冻结依据。

    反例原文是"同修订号但 plan_id=unrelated-plan 的计划也被接受"。
    """
    decision = _decide(
        _failure_in_progress(),
        plan=_plan(plan_id="unrelated-plan"),
    )
    assert decision.accepted is False
    assert _codes(decision) == {
        RuntimeRevisionRefusalCode.PLAN_REVISION_MISMATCH.value
    }
    assert decision.revision_no == 1


def test_a_request_naming_another_plan_id_is_refused() -> None:
    """请求声明的基线计划 ID 与冻结计划不一致：同样拒绝（两个方向都要守）。"""
    decision = _decide(
        _failure_in_progress(),
        base_plan_revision_id="unrelated-plan",
    )
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.PLAN_REVISION_MISMATCH.value in _codes(decision)


def test_a_mismatched_plan_digest_is_refused() -> None:
    """完整身份包括摘要：ID 与修订号都对、摘要对不上也不能当同一份冻结计划。"""
    decision = _decide(
        _failure_in_progress(),
        base_plan_revision_digest="sha256:another-plan-content",
    )
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.PLAN_REVISION_MISMATCH.value in _codes(decision)


def test_the_frozen_plan_identity_is_checked_when_the_digest_is_given() -> None:
    """给出摘要且与事实一致时正常放行：身份核对不是"一律拒绝"。"""
    decision = _decide(_failure_in_progress())
    assert decision.accepted is True
    assert decision.refusals == ()

    # 不给摘要时不做摘要核对（不拿计划内容猜一个来跟自己比），但修订号与 ID 仍必须匹配。
    without_digest = _decide(
        _failure_in_progress(), base_plan_revision_digest=None
    )
    assert without_digest.accepted is True

    wrong_revision = _decide(_failure_in_progress(), base_plan_revision_no=2)
    assert wrong_revision.accepted is False
    assert (
        RuntimeRevisionRefusalCode.PLAN_REVISION_MISMATCH.value
        in _codes(wrong_revision)
    )


def test_replacing_a_mandatory_acceptance_link_is_refused() -> None:
    """检查项 B-15：必测关联只能保留或增加，"替换"同样拒绝。

    反例原文是把必测 Case 的验收关联从 `{AC-A,AC-B}` 改成 `{AC-A,AC-C}`：
    两个集合互不包含，真子集比较发现不了，于是原依据被悄悄换掉。
    """
    frozen = _case(
        acceptance_item_ids=frozenset({"AC-A", "AC-B"}),
    )
    replaced = CaseRuntimeChange(
        next_case=dataclasses.replace(
            frozen,
            revision=2,
            links=CaseLink(
                acceptance_item_ids=frozenset({"AC-A", "AC-C"}),
                critical_path_ids=frozenset({"path-1"}),
            ),
        )
    )
    decision = _decide(
        _failure_in_progress(),
        change=replaced,
        frozen_case=frozen,
    )
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.APPLICABILITY_WEAKENED.value in _codes(decision)


def test_adding_a_mandatory_acceptance_link_is_allowed() -> None:
    """增加关联是允许的：`required ⊆ next` 成立。"""
    frozen = _case(acceptance_item_ids=frozenset({"AC-A"}))
    widened = CaseRuntimeChange(
        next_case=dataclasses.replace(
            frozen,
            revision=2,
            links=CaseLink(
                acceptance_item_ids=frozenset({"AC-A", "AC-C"}),
                critical_path_ids=frozenset({"path-1"}),
            ),
        )
    )
    decision = _decide(
        _failure_in_progress(),
        change=widened,
        frozen_case=frozen,
    )
    assert decision.accepted is True
    assert RuntimeRevisionRefusalCode.APPLICABILITY_WEAKENED.value not in _codes(
        decision
    )


def test_replacing_a_mandatory_critical_path_is_refused() -> None:
    """关键链路同样只增不减（当前 `{path-1}` → 换成 `{path-2}`）。"""
    frozen = _case(critical_paths=frozenset({"path-1"}))
    replaced = CaseRuntimeChange(
        next_case=dataclasses.replace(
            frozen,
            revision=2,
            links=CaseLink(
                acceptance_item_ids=frozenset({"AC-01"}),
                critical_path_ids=frozenset({"path-2"}),
            ),
        )
    )
    decision = _decide(
        _failure_in_progress(),
        change=replaced,
        frozen_case=frozen,
    )
    assert decision.accepted is False
    assert RuntimeRevisionRefusalCode.APPLICABILITY_WEAKENED.value in _codes(decision)


def test_a_derived_payload_is_always_a_fresh_copy() -> None:
    """派生助手不能污染后续读取的夹具（本文件多处复用同一份文件）。"""
    first = _with_run_control(_payload("failure"), "running")
    assert first["run"]["control_state"] == "running"
    assert _payload("failure")["run"]["control_state"] == "completed"
    assert copy.deepcopy(first)["run"]["control_state"] == "running"
