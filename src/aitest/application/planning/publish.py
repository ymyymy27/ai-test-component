"""发布编排：把领域门禁通过的草稿落成**不可变修订**。

对应需求 P1-FR05（测试规范与规则版本）与 P1-FR06（测试计划与用例生成）；
架构文档《01-项目与计划》第 3、8 节。

本模块只做**编排**：

1. 先施加**领域发布门禁**（`rules.validate_draft_publication`、`plans.validate_plan_publication`）；
2. 再经薄底座 `UnitOfWork` 提交记录，用**提交序号**作为确认标识与业务顺序；
3. 门禁不通过或上下文有阻塞缺口时**返回阻塞结果，不写任何记录、不抛异常**
   （发布是人工动作，失败要能向用户说明原因，而不是抛栈）。

**发布是人工动作**：`publish_rules` / `publish_plan` 在 `contracts/commands.py` 中属
`HUMAN_ACTIONS`；本模块不提供任何"自动发布"路径，也不接受"代表用户确认"的参数。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from aitest.application.planning.substrate import (
    RecordQuery,
    RecordReader,
    UnitOfWork,
)
from aitest.application.project.context import ContextGap, blocking_gaps
from aitest.domain.planning.plans import (
    Case,
    Plan,
    PlanPublicationStatus,
    validate_plan_publication,
)
from aitest.domain.planning.rules import (
    RuleDraft,
    RuleVersion,
    validate_draft_publication,
)


def _digest(payload: object) -> str:
    """规范摘要：键排序、紧凑分隔符，与调用方构造顺序无关。"""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """一次发布尝试的结果：**要么得到已发布版本，要么得到阻塞原因**。"""

    value: RuleVersion | Plan | None
    blocked_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.value is None and not self.blocked_by:
            raise ValueError("a refused publication must name at least one reason")
        if self.value is not None and self.blocked_by:
            raise ValueError("a published value must not carry blocking reasons")


def _blocked(gaps: tuple[ContextGap, ...], gate: str | None = None) -> PublicationResult:
    reasons = list(gaps)
    if gate is not None:
        reasons.append(
            ContextGap(
                kind="publication_gate",
                subject="publication",
                detail=gate,
            )
        )
    return PublicationResult(value=None, blocked_by=tuple(reason.detail for reason in reasons))


def _blocking_or_none(gaps: tuple[ContextGap, ...]) -> tuple[ContextGap, ...]:
    """只有**阻塞级**缺口才拦发布；提示级（如盘符未知）不拦。"""
    return blocking_gaps(gaps)


def _current_revision(
    reader: RecordReader,
    *,
    project_id: str,
    aggregate_kind: str,
    record_id: str,
) -> int | None:
    """读当前修订；没有记录时返回 `None`（= 新建）。

    发布**新修订**时必须把当前修订传下去，否则底座按"新建"处理并报冲突——
    这正是"不自动覆盖"这条守卫在起作用。
    """
    page = reader.query(
        RecordQuery(
            project_id=project_id,
            aggregate_kind=aggregate_kind,  # type: ignore[arg-type]
            record_id=record_id,
        )
    )
    if not page.items:
        return None
    return max(item.revision for item in page.items)


# ------------------------------------------------------------------ 规则发布


def publish_rules(
    draft: RuleDraft,
    *,
    project_id: str,
    unit_of_work: UnitOfWork,
    reader: RecordReader,
    context_gaps: tuple[ContextGap, ...] = (),
) -> PublicationResult:
    """把规则草稿发布为不可变的 `RuleVersion`。

    门禁（逐条）：

    1. 上下文有**阻塞级**缺口 → 拒绝；
    2. `validate_draft_publication()`：草稿必须已确认 → 拒绝；
    3. 提交失败 → 异常向上抛（写入失败**不得**显示为已保存）。

    `confirmation_id` 取**本次提交的提交序号**：发布是人工动作，
    但标识绑定的是这次提交事实，不由调用方传入（不接受"自报确认"）。
    """
    if not project_id.strip():
        raise ValueError("project_id must not be empty")

    blocking = _blocking_or_none(context_gaps)
    if blocking:
        return _blocked(blocking)

    try:
        validate_draft_publication(draft, confirmation_id="pending")
    except ValueError as error:
        return _blocked((), gate=str(error))

    payload = {
        "project_id": project_id,
        "rule_id": draft.rule_id,
        "revision": draft.revision,
        "scope": draft.scope,
        "text": draft.text,
        "source": draft.source,
        "steps": list(draft.steps),
        "evidence_requirements": list(draft.evidence_requirements),
        "unknown_extension_fields": list(draft.unknown_extension_fields),
        "status": "published",
    }
    unit_of_work.open(project_id)
    unit_of_work.stage_record(
        aggregate_kind="rule_version",
        record_id=draft.rule_id,
        expected_revision=_current_revision(
            reader,
            project_id=project_id,
            aggregate_kind="rule_version",
            record_id=draft.rule_id,
        ),
        payload=payload,
    )
    result = unit_of_work.commit()

    version = RuleVersion(
        rule_id=draft.rule_id,
        revision=draft.revision,
        scope=draft.scope,
        text=draft.text,
        steps=draft.steps,
        evidence_requirements=draft.evidence_requirements,
        source=draft.source,
        confirmation_id=result.commit_seq,
        digest=_digest(payload),
    )
    return PublicationResult(value=version)


# ------------------------------------------------------------------ 计划发布


def publish_plan(
    plan: Plan,
    *,
    project_id: str,
    cases: Sequence[Case],
    unit_of_work: UnitOfWork,
    reader: RecordReader,
    context_gaps: tuple[ContextGap, ...] = (),
) -> PublicationResult:
    """把计划发布为带确认标识的不可变修订。

    门禁（逐条）：

    1. 上下文有**阻塞级**缺口 → 拒绝（"上下文缺失时阻塞，不编造结论"）；
    2. `validate_plan_publication()`：`T ⊆ M`、冻结必测不得含依据缺失、
       必测项须有独立核验方式、冻结用例修订必须齐全 → 拒绝；
    3. 提交失败 → 异常向上抛。

    已发布的计划**不重新发布**（`Plan` 不可变，历史运行仍引用原修订）：
    传进来的计划若已是 `published`，直接拒绝并说明。
    """
    if not project_id.strip():
        raise ValueError("project_id must not be empty")

    blocking = _blocking_or_none(context_gaps)
    if blocking:
        return _blocked(blocking)

    if plan.status is PlanPublicationStatus.PUBLISHED:
        return _blocked(
            (),
            gate="a published plan is immutable; publish a new revision instead",
        )

    try:
        validate_plan_publication(plan, cases)
    except ValueError as error:
        return _blocked((), gate=str(error))

    payload = _plan_payload(plan, cases, project_id=project_id)
    unit_of_work.open(project_id)
    unit_of_work.stage_record(
        aggregate_kind="plan",
        record_id=plan.plan_id,
        expected_revision=_current_revision(
            reader,
            project_id=project_id,
            aggregate_kind="plan",
            record_id=plan.plan_id,
        ),
        payload=payload,
    )
    result = unit_of_work.commit()

    published = Plan(
        plan_id=plan.plan_id,
        revision=plan.revision,
        scope=plan.scope,
        case_revisions=plan.case_revisions,
        rule_revisions=plan.rule_revisions,
        template_versions=plan.template_versions,
        run_tier=plan.run_tier,
        initial_driver=plan.initial_driver,
        status=PlanPublicationStatus.PUBLISHED,
        confirmation_id=result.commit_seq,
    )
    return PublicationResult(value=published)


def _plan_payload(
    plan: Plan, cases: Sequence[Case], *, project_id: str
) -> dict[str, object]:
    """计划落盘用的 payload：冻结修订与**实际用例摘要**，便于事后核对。"""
    return {
        "project_id": project_id,
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "scope_id": plan.scope.scope_id,
        "scope_revision": plan.scope.revision,
        "required_case_ids": sorted(plan.scope.required_case_ids),
        "template_case_ids": sorted(plan.scope.template_case_ids),
        "case_revisions": [
            {"case_id": ref.case_id, "revision": ref.revision, "digest": ref.digest}
            for ref in plan.case_revisions
        ],
        "rule_revisions": [
            {"rule_id": ref.rule_id, "revision": ref.revision, "digest": ref.digest}
            for ref in plan.rule_revisions
        ],
        "template_versions": [
            {
                "template_id": ref.template_id,
                "version": ref.version,
                "digest": ref.digest,
            }
            for ref in plan.template_versions
        ],
        "run_tier": plan.run_tier.value,
        "initial_driver": plan.initial_driver.value,
        "cases": [
            {
                "case_id": case.case_id,
                "revision": case.revision,
                "layer": case.layer.value,
                "assertion_basis_state": case.assertion_basis.state.value,
                "independent_verification": case.independent_verification,
                "in_scope": case.case_id in plan.scope.required_case_ids,
            }
            for case in cases
        ],
    }


def plan_publication_digest(
    plan: Plan, cases: Sequence[Case], *, project_id: str
) -> str:
    """计划落盘内容的摘要；供 `PreparedRun.plan_revision.digest` 引用。"""
    return _digest(_plan_payload(plan, cases, project_id=project_id))


__all__ = [
    "PublicationResult",
    "plan_publication_digest",
    "publish_plan",
    "publish_rules",
]
