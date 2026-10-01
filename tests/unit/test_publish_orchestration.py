"""发布编排：规则与计划的发布门禁、落盘与不可变性。

依据：需求 P1-FR05、P1-FR06；架构文档第 3、8 节；
`docs/文档-feix-a/B包/13-发布编排与模板适用条件设计说明.md`。
"""

import pytest

from aitest.application.planning.publish import (
    PublicationResult,
    plan_publication_digest,
    publish_plan,
    publish_rules,
)
from aitest.application.project.context import GAP_NO_MODULES, ContextGap
from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseImportance,
    CaseLayer,
    CaseLink,
    Plan,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
    validate_plan_publication,
)
from aitest.domain.planning.plans import (
    CaseRevisionRef as DomainCaseRevisionRef,
)
from aitest.domain.planning.plans import (
    RuleRevisionRef as DomainRuleRevisionRef,
)
from aitest.domain.planning.plans import (
    TemplateVersionRef as DomainTemplateVersionRef,
)
from aitest.domain.planning.rules import RuleDraft, RuleEnablement, RuleVersion
from tests.support.memory_substrate import MemoryReader, MemoryStore, MemoryUnitOfWork


def _world() -> tuple[MemoryUnitOfWork, MemoryReader]:
    store = MemoryStore()
    return MemoryUnitOfWork(store), MemoryReader(store)


def _gap() -> ContextGap:
    return ContextGap(kind=GAP_NO_MODULES, subject="p1", detail="no module registered")


# ------------------------------------------------------------------ 规则


def _draft(**overrides: object) -> RuleDraft:
    base: dict[str, object] = {
        "rule_id": "rule-1",
        "revision": 1,
        "scope": "http workflows",
        "text": "check the status code and the persisted body",
        "source": "manual",
        "steps": ("call the endpoint", "read it back"),
        "evidence_requirements": ("raw response",),
        "enablement": RuleEnablement.ENABLED,
        "confirmed": True,
    }
    base.update(overrides)
    return RuleDraft(**base)  # type: ignore[arg-type]


def test_rules_are_published_with_the_commit_sequence() -> None:
    unit_of_work, reader = _world()
    result = publish_rules(_draft(), project_id="p1", unit_of_work=unit_of_work, reader=reader)
    assert result.blocked_by == ()
    assert result.value is not None
    assert result.value.confirmation_id == "commit-1"
    assert result.value.digest.startswith("sha256:")
    assert result.value.rule_id == "rule-1"
    assert result.value.steps == ("call the endpoint", "read it back")


def test_unconfirmed_draft_is_refused_with_a_reason() -> None:
    unit_of_work, reader = _world()
    result = publish_rules(
        _draft(enablement=RuleEnablement.DISABLED, confirmed=False),
        project_id="p1",
        unit_of_work=unit_of_work,
    reader=reader,
    )
    assert result.value is None
    assert any("unconfirmed" in reason for reason in result.blocked_by)


def test_refused_rule_publication_writes_nothing() -> None:
    unit_of_work, reader = _world()
    publish_rules(
        _draft(enablement=RuleEnablement.DISABLED, confirmed=False),
        project_id="p1",
        unit_of_work=unit_of_work,
    reader=reader,
    )
    assert unit_of_work.commit_seq() == "commit-0"
    with pytest.raises(ValueError, match="unknown revision"):
        reader.read(aggregate_kind="rule_draft", record_id="rule-1", revision=1)


def test_blocking_context_gap_refuses_rule_publication() -> None:
    unit_of_work, reader = _world()
    result = publish_rules(
        _draft(), project_id="p1", unit_of_work=unit_of_work, reader=reader, context_gaps=(_gap(),)
    )
    assert result.value is None
    assert result.blocked_by == ("no module registered",)


def test_non_blocking_gap_does_not_refuse_publication() -> None:
    from aitest.application.project.context import WARN_UNVERIFIED_DRIVE_KIND

    unit_of_work, reader = _world()
    warning = ContextGap(
        kind=WARN_UNVERIFIED_DRIVE_KIND,
        subject="p1",
        detail="drive kind unknown",
        blocking=False,
    )
    result = publish_rules(
        _draft(), project_id="p1", unit_of_work=unit_of_work, reader=reader, context_gaps=(warning,)
    )
    assert result.value is not None


def test_rule_publication_requires_a_project() -> None:
    unit_of_work, reader = _world()
    with pytest.raises(ValueError, match="project_id"):
        publish_rules(_draft(), project_id="  ", unit_of_work=unit_of_work, reader=reader)


def test_a_second_rule_draft_revision_lands_as_a_new_record_revision() -> None:
    unit_of_work, reader = _world()
    publish_rules(_draft(), project_id="p1", unit_of_work=unit_of_work, reader=reader)
    publish_rules(
        _draft(revision=2, text="tighten the assertion"),
        project_id="p1",
        unit_of_work=unit_of_work,
    reader=reader,
    )
    first = reader.read(aggregate_kind="rule_draft", record_id="rule-1", revision=1)
    second = reader.read(aggregate_kind="rule_draft", record_id="rule-1", revision=2)
    assert "status code" in str(first.payload["text"])
    assert second.payload["text"] == "tighten the assertion"


# ------------------------------------------------------------------ 计划


def _case(**overrides: object) -> Case:
    base: dict[str, object] = {
        "case_id": "case-1",
        "revision": 1,
        "layer": CaseLayer.L2,
        "objective": "create a ticket",
        "preconditions": ("the service is running",),
        "inputs": ("payload",),
        "steps": ("create", "read back"),
        "expected": "the ticket is persisted",
        "verification_method": "registered pytest entry",
        "links": CaseLink(
            acceptance_item_ids=frozenset({"ai-1"}),
            module_ids=frozenset({"m1"}),
            environment_ids=frozenset({"env-1"}),
            critical_path_ids=frozenset({"path-1"}),
        ),
        "assertion_basis": AssertionBasis(
            revision=1,
            state=AssertionBasisState.PRESENT_UNCONFIRMED,
            text="read-back proves persistence",
            text_digest="sha256:basis-1",
        ),
        "independent_verification": "read-only query by ticket id",
        "importance": CaseImportance.P0,
    }
    base.update(overrides)
    return Case(**base)  # type: ignore[arg-type]


def _draft_plan(case: Case, **overrides: object) -> Plan:
    scope = AcceptanceScope(
        scope_id="scope-1",
        revision=1,
        name="ticket scope",
        required_case_ids=frozenset({case.case_id}),
        template_case_ids=frozenset({case.case_id}),
        objective="prove ticket creation",
    )
    base: dict[str, object] = {
        "plan_id": "plan-1",
        "revision": 1,
        "scope": scope,
        "case_revisions": (
            DomainCaseRevisionRef(
                case_id=case.case_id, revision=case.revision, digest="sha256:case-1"
            ),
        ),
        "rule_revisions": (
            DomainRuleRevisionRef(rule_id="rule-1", revision=1, digest="sha256:rule-1"),
        ),
        "template_versions": (
            DomainTemplateVersionRef(
                template_id="ticket-workflow", version="1.0.0", digest="sha256:tpl"
            ),
        ),
        "run_tier": RunTier.FULL,
        "initial_driver": RunDriver.PLANNED,
        "status": PlanPublicationStatus.DRAFT,
    }
    base.update(overrides)
    return Plan(**base)  # type: ignore[arg-type]


def test_plan_is_published_with_the_commit_sequence() -> None:
    unit_of_work, reader = _world()
    case = _case()
    result = publish_plan(
        _draft_plan(case),
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert result.blocked_by == ()
    assert result.value is not None
    assert isinstance(result.value, Plan)
    assert result.value.status is PlanPublicationStatus.PUBLISHED
    assert result.value.confirmation_id == "commit-1"
    assert result.value.scope.name == "ticket scope"


def test_the_draft_plan_is_left_untouched() -> None:
    """发布产出**新对象**；传入的草稿计划（冻结对象）不被改写。"""
    unit_of_work, reader = _world()
    case = _case()
    draft = _draft_plan(case)
    publish_plan(draft, project_id="p1", cases=[case], unit_of_work=unit_of_work, reader=reader)
    assert draft.status is PlanPublicationStatus.DRAFT
    assert draft.confirmation_id is None


def test_plan_with_a_missing_basis_in_required_scope_is_refused() -> None:
    unit_of_work, reader = _world()
    case = _case(
        assertion_basis=AssertionBasis(revision=1, state=AssertionBasisState.MISSING)
    )
    result = publish_plan(
        _draft_plan(case),
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert result.value is None
    assert any("missing assertion basis" in reason for reason in result.blocked_by)


def test_plan_without_independent_verification_is_refused() -> None:
    unit_of_work, reader = _world()
    case = _case(independent_verification=None)
    result = publish_plan(
        _draft_plan(case),
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert result.value is None
    assert any("independent verification" in reason for reason in result.blocked_by)


def test_plan_missing_a_frozen_case_revision_is_refused() -> None:
    unit_of_work, reader = _world()
    case = _case()
    result = publish_plan(
        _draft_plan(case),
        project_id="p1",
        cases=[],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert result.value is None
    assert any("not provided" in reason for reason in result.blocked_by)


# ------------------------------------------------- B-PUBLICATION-01 反例


def test_a_required_case_without_a_frozen_revision_is_refused() -> None:
    """反例：必测集合含有**没有冻结引用**的用例。

    修前门禁取 `M ∩ frozen_ids`，缺项被静默滤掉，计划照常发布——
    等于把"必测"缩水后当作完整范围发布。
    """
    unit_of_work, reader = _world()
    case = _case()
    scope = AcceptanceScope(
        scope_id="scope-1",
        revision=1,
        name="ticket scope",
        required_case_ids=frozenset({"case-1", "case-missing"}),
        template_case_ids=frozenset({"case-1"}),
        objective="prove ticket creation",
    )
    result = publish_plan(
        _draft_plan(case, scope=scope),
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert result.value is None
    assert any("no frozen case revision" in reason for reason in result.blocked_by)


def test_a_provided_case_must_match_the_frozen_revision() -> None:
    """反例：冻结 `case-1@1` 却提供 `case-1@2`。

    修前只核 ID 不核修订，发布门禁会对着**另一个修订**的依据与核验方式做判断。
    """
    unit_of_work, reader = _world()
    frozen = _case()
    provided = _case(revision=2)
    result = publish_plan(
        _draft_plan(frozen),
        project_id="p1",
        cases=[provided],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert result.value is None
    assert any("does not match the frozen revision" in reason for reason in result.blocked_by)


def test_the_required_gate_reads_the_frozen_revision_only() -> None:
    """必测门禁按**冻结修订**的用例判定：拿修订不符的用例顶替不能绕过门禁。"""
    unit_of_work, reader = _world()
    frozen = _case()  # 依据齐全
    broken_v2 = _case(
        revision=2,
        assertion_basis=AssertionBasis(revision=1, state=AssertionBasisState.MISSING),
    )
    result = publish_plan(
        _draft_plan(frozen),
        project_id="p1",
        cases=[broken_v2],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert result.value is None
    assert any("does not match the frozen revision" in reason for reason in result.blocked_by)


def test_publishing_an_already_published_plan_is_refused() -> None:
    """计划不可变：已发布的计划不能原地再发布，历史运行仍引用原修订。"""
    unit_of_work, reader = _world()
    case = _case()
    published = publish_plan(
        _draft_plan(case),
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
    ).value
    assert published is not None
    again = publish_plan(
        published,
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert again.value is None
    assert any("immutable" in reason for reason in again.blocked_by)


def test_blocking_context_gap_refuses_plan_publication() -> None:
    unit_of_work, reader = _world()
    case = _case()
    result = publish_plan(
        _draft_plan(case),
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
        context_gaps=(_gap(),),
    )
    assert result.value is None
    assert result.blocked_by == ("no module registered",)


def test_refused_plan_publication_writes_nothing() -> None:
    unit_of_work, reader = _world()
    case = _case(independent_verification=None)
    publish_plan(
        _draft_plan(case),
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert unit_of_work.commit_seq() == "commit-0"
    with pytest.raises(ValueError, match="unknown revision"):
        reader.read(aggregate_kind="plan", record_id="plan-1", revision=1)


def test_published_plan_payload_is_readable_by_exact_revision() -> None:
    unit_of_work, reader = _world()
    case = _case()
    publish_plan(
        _draft_plan(case),
        project_id="p1",
        cases=[case],
        unit_of_work=unit_of_work,
        reader=reader,
    )
    record = reader.read(aggregate_kind="plan", record_id="plan-1", revision=1)
    assert record.payload["project_id"] == "p1"
    assert record.payload["required_case_ids"] == ["case-1"]
    assert record.payload["run_tier"] == "full"


def test_plan_publication_digest_is_stable_and_content_bound() -> None:
    case = _case()
    plan = _draft_plan(case)
    first = plan_publication_digest(plan, [case], project_id="p1")
    assert first == plan_publication_digest(plan, [case], project_id="p1")
    assert first != plan_publication_digest(plan, [case], project_id="p2")


def test_publication_can_be_gated_by_the_domain_function_directly() -> None:
    """编排调用的是既有领域门禁，二者结论必须一致（防止两套判定）。"""
    case = _case()
    plan = _draft_plan(case)
    validate_plan_publication(plan, [case])  # 不抛即通过

    unit_of_work, reader = _world()
    assert (
        publish_plan(
            plan,
            project_id="p1",
            cases=[case],
            unit_of_work=unit_of_work,
            reader=reader,
        ).value
        is not None
    )


# ------------------------------------------------------------------ 结果类型


def test_publication_result_requires_exactly_one_of_value_or_reasons() -> None:
    with pytest.raises(ValueError, match="at least one reason"):
        PublicationResult(value=None)
    with pytest.raises(ValueError, match="must not carry blocking reasons"):
        PublicationResult(
            value=RuleVersion(
                rule_id="rule-1",
                revision=1,
                scope="s",
                text="t",
                steps=("a",),
                evidence_requirements=(),
                source="manual",
                confirmation_id="commit-1",
                digest="sha256:d",
            ),
            blocked_by=("x",),
        )
