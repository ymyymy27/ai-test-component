"""模板加载、草稿生成与定向失效。

依据：`docs/文档-feix-a/B包/12-项目上下文档与草稿生成设计说明.md`；
需求 P1-FR04、P1-AC17；架构文档第 3、9 节。
"""

import pytest

from aitest.application.planning.draft import (
    ApplicabilityAssessment,
    DraftKind,
    GeneratedContent,
    ProjectCapabilities,
    RevisionContext,
    TemplateNotFoundError,
    TemplateRequirements,
    apply_template,
    assess_template,
    draft_expiry,
    list_templates,
    load_template,
)
from aitest.application.project.context import GAP_NO_MODULES, ContextGap
from aitest.domain.planning.templates import TemplateRef


def _context(**overrides: object) -> RevisionContext:
    base: dict[str, object] = {
        "project_revision": 1,
        "binding_revision": 1,
        "template_revision": "1.0.0",
        "environment_revision": 1,
        "source_revision": 1,
        "rules_revision": 1,
    }
    base.update(overrides)
    return RevisionContext(**base)  # type: ignore[arg-type]


def _ref(template_id: str = "python-library", version: str = "1.0.0") -> TemplateRef:
    return TemplateRef(template_id=template_id, version=version)


def _gap() -> ContextGap:
    return ContextGap(kind=GAP_NO_MODULES, subject="p1", detail="no module registered")


# ------------------------------------------------------------------ 模板加载


def test_all_six_builtin_templates_are_listed() -> None:
    summaries = list_templates()
    ids = [summary.template_ref.template_id for summary in summaries]
    assert ids == [
        "agent-workflow",
        "http-workflow",
        "manual-web-workflow",
        "python-library",
        "python-service",
        "ticket-workflow",
    ]


def test_listed_templates_report_a_real_version_and_status() -> None:
    summaries = list_templates()
    for summary in summaries:
        assert summary.template_ref.version == "1.0.0"
        assert summary.implementation_status == "released"
        assert summary.name
        assert summary.applicability
        assert summary.delivery_method in {
            "automatic",
            "registered_command",
            "manual",
            "import",
        }


def test_template_can_be_loaded_by_its_exact_version() -> None:
    pack = load_template(_ref("ticket-workflow"))
    assert pack.template_id == "ticket-workflow"
    assert pack.version == "1.0.0"
    assert pack.items


def test_unknown_template_version_is_a_user_facing_error() -> None:
    """需求 P1-FR04 异常分支：无匹配模板时提示自建或导入。"""
    with pytest.raises(TemplateNotFoundError, match="unknown template version"):
        load_template(_ref("ticket-workflow", "9.9.9"))
    with pytest.raises(TemplateNotFoundError):
        load_template(_ref("no-such-template"))


def test_template_summary_rejects_empty_fields() -> None:
    from aitest.application.planning.draft import TemplateSummary

    with pytest.raises(ValueError, match="name"):
        TemplateSummary(
            template_ref=_ref(),
            name=" ",
            implementation_status="released",
            delivery_method="automatic",
            applicability="x",
        )


# ------------------------------------------------------------------ 草稿生成


def test_apply_template_produces_a_draft() -> None:
    result = apply_template(
        template_ref=_ref("ticket-workflow"),
        project_id="p1",
        revision_context=_context(),
        draft_kind=DraftKind.CHECK_CONTENT,
    )
    assert result.gaps == ()
    assert result.content is not None
    assert result.content.status == "draft"
    assert result.content.template_ref == _ref("ticket-workflow")


def test_generation_is_refused_when_context_has_gaps() -> None:
    """需求 P1-AC17：上下文缺失时列缺口并阻塞，**不编造检查内容**。"""
    result = apply_template(
        template_ref=_ref(),
        project_id="p1",
        revision_context=_context(),
        draft_kind=DraftKind.CHECK_CONTENT,
        gaps=(_gap(),),
    )
    assert result.content is None
    assert result.gaps == (_gap(),)


def test_regenerating_yields_a_new_revision_and_keeps_the_old_one() -> None:
    """人工修订不被重新生成覆盖：两次生成得到两个**不同对象**。"""
    first = apply_template(
        template_ref=_ref(),
        project_id="p1",
        revision_context=_context(),
        draft_kind=DraftKind.CHECK_CONTENT,
        content_revision=1,
    ).content
    second = apply_template(
        template_ref=_ref(),
        project_id="p1",
        revision_context=_context(project_revision=2),
        draft_kind=DraftKind.CHECK_CONTENT,
        content_revision=2,
    ).content
    assert first is not None and second is not None
    assert first.revision == 1
    assert second.revision == 2
    assert first.generated_content_id != second.generated_content_id
    assert first.revision_context.project_revision == 1


def test_generated_content_records_the_actual_revision_set() -> None:
    content = apply_template(
        template_ref=_ref(),
        project_id="p1",
        revision_context=_context(project_revision=3, binding_revision=4),
        draft_kind=DraftKind.CONTEXT_SUMMARY,
    ).content
    assert content is not None
    assert content.revision_context.project_revision == 3
    assert content.revision_context.binding_revision == 4
    assert content.revision_context.template_revision == "1.0.0"


def test_generated_content_cannot_be_published() -> None:
    with pytest.raises(ValueError, match="always a draft"):
        GeneratedContent(
            generated_content_id="d1",
            project_id="p1",
            draft_kind=DraftKind.CHECK_CONTENT,
            template_ref=_ref(),
            revision=1,
            revision_context=_context(),
            status="published",
        )


def test_draft_result_requires_exactly_one_of_content_or_gaps() -> None:
    from aitest.application.planning.draft import DraftResult

    with pytest.raises(ValueError, match="at least one gap"):
        DraftResult(content=None)
    with pytest.raises(ValueError, match="must not carry blocking gaps"):
        DraftResult(
            content=apply_template(
                template_ref=_ref(),
                project_id="p1",
                revision_context=_context(),
                draft_kind=DraftKind.CHECK_CONTENT,
            ).content,
            gaps=(_gap(),),
        )


# ------------------------------------------------------------------ 定向失效


def test_unchanged_context_does_not_expire_the_draft() -> None:
    content = apply_template(
        template_ref=_ref(),
        project_id="p1",
        revision_context=_context(),
        draft_kind=DraftKind.CHECK_CONTENT,
    ).content
    assert content is not None
    assert draft_expiry(content, _context()) == ()


def test_only_the_changed_source_is_reported() -> None:
    """**定向**过期：未变的来源不出现在结果里，未变部分仍可复用。"""
    content = apply_template(
        template_ref=_ref(),
        project_id="p1",
        revision_context=_context(),
        draft_kind=DraftKind.CHECK_CONTENT,
    ).content
    assert content is not None
    assert draft_expiry(content, _context(binding_revision=5)) == ("binding_revision",)
    assert draft_expiry(
        content, _context(project_revision=2, rules_revision=7)
    ) == ("project_revision", "rules_revision")


def test_template_version_change_expires_the_draft() -> None:
    content = apply_template(
        template_ref=_ref(),
        project_id="p1",
        revision_context=_context(),
        draft_kind=DraftKind.CHECK_CONTENT,
    ).content
    assert content is not None
    assert draft_expiry(content, _context(template_revision="2.0.0")) == (
        "template_revision",
    )


def test_inapplicable_sources_never_expire_a_draft() -> None:
    """`None` 表示该类来源**不适用**，不是"未知"：不参与比较。"""
    content = apply_template(
        template_ref=_ref(),
        project_id="p1",
        revision_context=_context(environment_revision=None, source_revision=None),
        draft_kind=DraftKind.CONTEXT_SUMMARY,
    ).content
    assert content is not None
    assert draft_expiry(content, _context(environment_revision=9, source_revision=9)) == ()


def test_revision_context_requires_positive_revisions() -> None:
    with pytest.raises(ValueError, match="project_revision"):
        _context(project_revision=0)
    with pytest.raises(ValueError, match="template_revision"):
        _context(template_revision=" ")
    with pytest.raises(ValueError, match="rules_revision"):
        _context(rules_revision=0)


# ------------------------------------------------------------------ 模板适用条件


def test_template_applies_when_every_requirement_is_met() -> None:
    assessment = assess_template(
        TemplateRequirements(requires_entry_point=True),
        ProjectCapabilities(has_entry_point=True),
    )
    assert assessment.applicable is True
    assert assessment.missing == ()


def test_missing_entry_point_names_the_reason() -> None:
    """施工清单要求"不适用项给出原因"，因此要**具体缺哪一项**，不是只回布尔。"""
    assessment = assess_template(
        TemplateRequirements(requires_entry_point=True),
        ProjectCapabilities(has_entry_point=False),
    )
    assert assessment.applicable is False
    assert assessment.missing == ("entry_point",)


def test_every_missing_requirement_is_listed() -> None:
    assessment = assess_template(
        TemplateRequirements(
            requires_entry_point=True,
            requires_http_target=True,
            requires_agent_model=True,
            requires_frontend=True,
            requires_database_verification=True,
        ),
        ProjectCapabilities(),
    )
    assert assessment.missing == (
        "entry_point",
        "http_target",
        "agent_model",
        "frontend",
        "database_verification",
    )


def test_requirements_not_declared_are_not_checked() -> None:
    """模板没要求的能力，项目没有也不构成不适用。"""
    assessment = assess_template(
        TemplateRequirements(requires_entry_point=True, requires_http_target=False),
        ProjectCapabilities(has_entry_point=True, has_http_target=False),
    )
    assert assessment.applicable is True


def test_assessment_cannot_be_both_applicable_and_missing() -> None:
    with pytest.raises(ValueError, match="must not report missing requirements"):
        ApplicabilityAssessment(applicable=True, missing=("http_target",))
    with pytest.raises(ValueError, match="must name what is missing"):
        ApplicabilityAssessment(applicable=False)
