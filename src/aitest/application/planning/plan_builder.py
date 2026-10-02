"""由**发布请求**构造 `Plan`：请求形状的落点。

职责分界（见 `docs/文档-feix-a/B包/17-计划发布请求形状草案.md`）：

- 本模块把"请求里的人工给定内容"装配成领域对象：`AcceptanceScope`、`Plan`，
  并**按内容**算出用例与模板的身份摘要；
- **不负责**发布门禁与落盘：门禁在 `domain/planning/plans.py` 的
  `validate_plan_publication()`，落盘在 `application/planning/publish.py` 的 `publish_plan()`。

三条不放进请求的内容（由服务端产生，因此本模块不接受）：

| 内容 | 为什么 |
| --- | --- |
| `Plan.status` | 是发布**结果**，让调用方传等于允许"自报已发布" |
| `Plan.confirmation_id` | 由发布时**本次提交的提交序号**给出 |
| `case_revisions[].digest` | 内容身份，必须按用例内容算，否则可被伪造 |
"""

from __future__ import annotations

from collections.abc import Sequence

from aitest.application.planning.draft import (
    load_template,
    template_draft_text,
    text_digest,
)
from aitest.application.planning.serialization import case_content_digest
from aitest.domain.planning.plans import (
    AcceptanceScope,
    Case,
    CaseRevisionRef,
    Plan,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
    TemplateVersionRef,
)
from aitest.domain.planning.rules import RuleRevisionRef, RuleVersion
from aitest.domain.planning.templates import TemplateRef


def case_revision_ref(case: Case, *, project_id: str) -> CaseRevisionRef:
    """按用例**内容**算出冻结引用；摘要不接受外部传入。"""
    return CaseRevisionRef(
        case_id=case.case_id,
        revision=case.revision,
        digest=case_content_digest(case, project_id=project_id),
    )


def template_version_ref(template_ref: TemplateRef) -> TemplateVersionRef:
    """按**已安装模板资源**的内容算出模板版本引用。

    一期此前没有 `TemplateVersionRef` 的构造点，因此"模板版本的 digest 是什么"
    没有既定口径。这里取"模板内容的规范 JSON 摘要"：
    与草稿正文（`draft.template_draft_text()`）共用同一套序列化，
    不引入第二种表示。模板不存在时由 `load_template()` 抛错，**不填占位值**。
    """
    pack = load_template(template_ref)
    digest = text_digest(template_draft_text(pack))
    if digest is None:
        raise ValueError(f"template {template_ref.template_id} produced no content")
    return TemplateVersionRef(
        template_id=template_ref.template_id,
        version=template_ref.version,
        digest=digest,
    )


def rule_revision_ref(version: RuleVersion) -> RuleRevisionRef:
    """按**已发布的规则版本**算出冻结引用（摘要取自该版本自身）。

    领域没有现成的转换入口，因此这里显式三字段构造：
    摘要来自 `RuleVersion.digest`（发布时按落盘 payload 算出），
    不接受调用方传入，也不填占位值。
    """
    return RuleRevisionRef(
        rule_id=version.rule_id,
        revision=version.revision,
        digest=version.digest,
    )


def build_plan(
    *,
    plan_id: str,
    revision: int,
    scope: AcceptanceScope,
    cases: Sequence[Case],
    project_id: str,
    rule_versions: Sequence[RuleVersion] = (),
    template_refs: Sequence[TemplateRef] = (),
    run_tier: RunTier = RunTier.FULL,
    initial_driver: RunDriver = RunDriver.PLANNED,
) -> Plan:
    """由请求内容装配一份**草稿状态**的 `Plan`。

    进入门禁的那份 `Plan` 一律是草稿：`status` 与 `confirmation_id` 由发布动作产生。
    用例顺序按 `case_id` 规范化，使"同一批用例的不同书写顺序"得到同一份计划。
    """
    if not cases:
        raise ValueError("a plan request must carry at least one case")
    ordered = sorted(cases, key=lambda case: (case.case_id, case.revision))
    return Plan(
        plan_id=plan_id,
        revision=revision,
        scope=scope,
        case_revisions=tuple(
            case_revision_ref(case, project_id=project_id) for case in ordered
        ),
        rule_revisions=tuple(rule_revision_ref(version) for version in rule_versions),
        template_versions=tuple(
            template_version_ref(ref) for ref in template_refs
        ),
        run_tier=run_tier,
        initial_driver=initial_driver,
        status=PlanPublicationStatus.DRAFT,
    )


__all__ = [
    "build_plan",
    "case_revision_ref",
    "rule_revision_ref",
    "template_version_ref",
]
