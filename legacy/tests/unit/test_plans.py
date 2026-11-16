"""计划冻结与发布门禁。"""

import pytest

from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseLayer,
    CaseLink,
    CaseRevisionRef,
    Plan,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
    TemplateVersionRef,
    validate_plan_publication,
)
from aitest.domain.planning.rules import RuleRevisionRef


def _scope(**overrides: object) -> AcceptanceScope:
    base: dict[str, object] = {
        "scope_id": "scope-1",
        "revision": 1,
        "name": "订单模块回归",
        "required_case_ids": frozenset({"case-1", "case-2"}),
        "template_case_ids": frozenset({"case-1"}),
        "objective": "确认订单创建与查询链路可用",
        "dependency_closure_ids": frozenset({"orders"}),
    }
    return AcceptanceScope(**(base | overrides))  # type: ignore[arg-type]


def _case(case_id: str, state: AssertionBasisState, verification: str | None) -> Case:
    basis = (
        AssertionBasis(revision=1, state=state)
        if state is AssertionBasisState.MISSING
        else AssertionBasis(
            revision=1, state=state, text="依据文本", text_digest="sha256:b"
        )
    )
    return Case(
        case_id=case_id,
        revision=1,
        layer=CaseLayer.L2,
        objective="目标",
        preconditions=("前置",),
        inputs=("输入",),
        steps=("步骤",),
        expected="预期",
        verification_method="方法",
        independent_verification=verification,
        links=CaseLink(
            acceptance_item_ids=frozenset({"AC-01"}),
            critical_path_ids=frozenset({"path-1"}),
        ),
        assertion_basis=basis,
    )


def _plan(**overrides: object) -> Plan:
    base: dict[str, object] = {
        "plan_id": "plan-1",
        "revision": 1,
        "scope": _scope(),
        "case_revisions": (
            CaseRevisionRef(case_id="case-1", revision=1, digest="sha256:c1"),
            CaseRevisionRef(case_id="case-2", revision=1, digest="sha256:c2"),
        ),
        "rule_revisions": (
            RuleRevisionRef(rule_id="rule-1", revision=1, digest="sha256:r1"),
        ),
        "template_versions": (
            TemplateVersionRef(
                template_id="http-workflow", version="1.0.0", digest="sha256:t"
            ),
        ),
        "run_tier": RunTier.FULL,
        "initial_driver": RunDriver.PLANNED,
    }
    return Plan(**(base | overrides))  # type: ignore[arg-type]


def test_scope_keeps_template_requirements_inside_required_cases() -> None:
    """T ⊆ M。"""
    with pytest.raises(ValueError, match="template requirements"):
        _scope(template_case_ids=frozenset({"case-3"}))


def test_excluded_cases_require_a_reason() -> None:
    with pytest.raises(ValueError, match="require a reason"):
        _scope(excluded_case_ids=frozenset({"case-9"}))
    scope = _scope(
        excluded_case_ids=frozenset({"case-9"}),
        exclusion_reasons=(("case-9", "本轮不涉及退款流程"),),
    )
    assert scope.exclusion_reasons


def test_applicability_exclusions_require_a_reason() -> None:
    with pytest.raises(ValueError, match="applicability exclusion reason"):
        _scope(applicability_exclusions=(("case-4", " "),))
    scope = _scope(applicability_exclusions=(("case-4", "无页面，人工步骤不适用"),))
    assert scope.applicability_exclusions


def test_full_selection_must_include_all_required_cases() -> None:
    scope = _scope()
    with pytest.raises(ValueError, match="all required"):
        scope.validate_selection(RunTier.FULL, frozenset({"case-1"}))
    scope.validate_selection(RunTier.FULL, frozenset({"case-1", "case-2"}))
    with pytest.raises(ValueError, match="empty"):
        scope.validate_selection(RunTier.ON_DEMAND, frozenset())


def test_plan_freezes_exact_case_and_rule_revisions() -> None:
    plan = _plan()
    assert all(ref.revision >= 1 for ref in plan.case_revisions)
    assert all(ref.digest for ref in plan.rule_revisions)
    with pytest.raises(ValueError, match="revision"):
        CaseRevisionRef(case_id="case-1", revision=0, digest="sha256:c1")


def test_plan_has_no_conclusion_ceiling_field() -> None:
    """结论上限只由档位派生，不进入计划冻结数据。"""
    plan = _plan()
    assert not hasattr(plan, "conclusion_ceiling")


def test_plan_rejects_duplicate_revisions() -> None:
    with pytest.raises(ValueError, match="case_id"):
        _plan(
            case_revisions=(
                CaseRevisionRef(case_id="case-1", revision=1, digest="a"),
                CaseRevisionRef(case_id="case-1", revision=2, digest="b"),
            )
        )


def test_published_plan_requires_a_confirmation() -> None:
    with pytest.raises(ValueError, match="confirmation"):
        _plan(status=PlanPublicationStatus.PUBLISHED)
    published = _plan(
        status=PlanPublicationStatus.PUBLISHED, confirmation_id="confirm-1"
    )
    assert published.confirmation_id == "confirm-1"


def test_plan_has_no_automatic_publication_path() -> None:
    """publish_plan 属人工动作：计划对象上不得出现发布方法。"""
    plan = _plan()
    for name in ("publish", "auto_publish"):
        assert not hasattr(plan, name)


def test_plan_publication_accepts_complete_cases() -> None:
    cases = [
        _case("case-1", AssertionBasisState.CONFIRMED, "独立核验"),
        _case("case-2", AssertionBasisState.PRESENT_UNCONFIRMED, "独立核验"),
    ]
    validate_plan_publication(_plan(), cases)


def test_required_case_with_missing_basis_blocks_plan_publication() -> None:
    """缺依据禁止 full 必测（架构文档第 3 节）。"""
    cases = [
        _case("case-1", AssertionBasisState.MISSING, "独立核验"),
        _case("case-2", AssertionBasisState.CONFIRMED, "独立核验"),
    ]
    with pytest.raises(ValueError, match="missing assertion basis"):
        validate_plan_publication(_plan(), cases)


def test_required_case_without_independent_verification_blocks_publication() -> None:
    cases = [
        _case("case-1", AssertionBasisState.CONFIRMED, None),
        _case("case-2", AssertionBasisState.CONFIRMED, "独立核验"),
    ]
    with pytest.raises(ValueError, match="independent verification"):
        validate_plan_publication(_plan(), cases)


def test_plan_publication_rejects_unknown_frozen_case_revisions() -> None:
    cases = [_case("case-1", AssertionBasisState.CONFIRMED, "独立核验")]
    with pytest.raises(ValueError, match="not provided"):
        validate_plan_publication(_plan(), cases)
