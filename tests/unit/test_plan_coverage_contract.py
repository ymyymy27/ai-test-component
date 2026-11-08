"""覆盖契约领域模型：功能 × 必要维度、模板项两轴处置与旧计划兼容。

依据：设计文档 03 第 4.3—4.6、5.2—5.5 节；需求 P1-FR04、P1-FR06、P1-AC24；
功能文档第 3 节；架构文档《01-项目与计划》第 3 节。

本批只验证领域模型与纯校验，不接入发布/准备/报告门禁。
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from aitest.domain.planning.plans import (
    AcceptanceScope,
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseLayer,
    CaseLink,
    CaseRevisionRef,
    CoverageContract,
    FunctionRequirement,
    Plan,
    PlanPublicationStatus,
    RunDriver,
    RunTier,
    TemplateApplicability,
    TemplateCoverage,
    TemplateDisposition,
    TemplateVersionRef,
    validate_coverage_contract,
    validate_plan_publication,
)
from aitest.domain.planning.rules import RuleRevisionRef
from aitest.domain.planning.understanding import CoverageClaim, CoverageClaimStatus


def _claim(**overrides: object) -> CoverageClaim:
    base: dict[str, object] = {
        "claim_id": "claim-1",
        "intent_id": "intent-1",
        "function_id": "billing.create",
        "dimension": "normal",
        "plan_revision": 1,
        "status": CoverageClaimStatus.PROVEN,
        "case_ids": frozenset({"case-1"}),
    }
    return CoverageClaim(**(base | overrides))  # type: ignore[arg-type]


def _contract(**overrides: object) -> CoverageContract:
    base: dict[str, object] = {
        "contract_id": "contract-1",
        "understanding_revision": 3,
        "function_requirements": (
            FunctionRequirement("billing.create", ("normal", "error")),
            FunctionRequirement("billing.refund", ("normal", "boundary", "error")),
        ),
        "template_dispositions": (
            TemplateDisposition(
                "tpl-http-status",
                TemplateApplicability.APPLICABLE,
                TemplateCoverage.COVERED,
            ),
            TemplateDisposition(
                "tpl-db-migration",
                TemplateApplicability.NOT_APPLICABLE,
                TemplateCoverage.UNCOVERED,
            ),
        ),
        "critical_path_ids": ("path-write", "path-read"),
        "coverage_claims": (
            _claim(claim_id="claim-create-normal"),
            _claim(claim_id="claim-create-error", dimension="error"),
            _claim(claim_id="claim-refund-normal", function_id="billing.refund"),
            _claim(
                claim_id="claim-refund-boundary",
                function_id="billing.refund",
                dimension="boundary",
            ),
            _claim(
                claim_id="claim-refund-error",
                function_id="billing.refund",
                dimension="error",
            ),
        ),
    }
    return CoverageContract(**(base | overrides))  # type: ignore[arg-type]


def _case() -> Case:
    return Case(
        case_id="case-1",
        revision=1,
        layer=CaseLayer.L2,
        objective="确认订单创建后可以读回",
        preconditions=("已登记入口",),
        inputs=("订单号",),
        steps=("创建订单", "读回订单"),
        expected="读回内容与创建内容一致",
        verification_method="独立查询同一订单号",
        independent_verification="只读查询同一订单号",
        links=CaseLink(
            acceptance_item_ids=frozenset({"AC-01"}),
            critical_path_ids=frozenset({"path-1"}),
        ),
        assertion_basis=AssertionBasis(
            revision=1,
            state=AssertionBasisState.CONFIRMED,
            text="读回内容证明订单已持久化",
            text_digest="sha256:basis",
        ),
    )


def _plan(**overrides: object) -> Plan:
    base: dict[str, object] = {
        "plan_id": "plan-1",
        "revision": 1,
        "scope": AcceptanceScope(
            scope_id="scope-1",
            revision=1,
            name="订单模块回归",
            required_case_ids=frozenset({"case-1"}),
            template_case_ids=frozenset({"case-1"}),
            objective="确认订单创建与查询链路可用",
        ),
        "case_revisions": (
            CaseRevisionRef(case_id="case-1", revision=1, digest="sha256:c1"),
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


# ------------------------------------------------------------ 旧计划兼容


def test_legacy_plan_without_coverage_contract_still_constructs() -> None:
    """旧调用点不传新字段时必须保持原行为。"""
    draft = _plan()
    assert draft.coverage_contract is None

    published = _plan(
        status=PlanPublicationStatus.PUBLISHED, confirmation_id="confirmation-1"
    )
    assert published.coverage_contract is None
    assert published.confirmation_id == "confirmation-1"


def test_coverage_contract_is_the_last_plan_field_with_a_none_default() -> None:
    """新字段追加在末尾，旧关键字/位置参数构造不受影响。"""
    plan_fields = fields(Plan)
    assert plan_fields[-1].name == "coverage_contract"
    assert plan_fields[-1].default is None


def test_plan_carries_an_optional_coverage_contract() -> None:
    contract = _contract()
    plan = _plan(coverage_contract=contract)
    assert plan.coverage_contract is contract


def test_legacy_plan_payload_key_set_is_unchanged() -> None:
    """没有覆盖契约时，既有发布 payload 的键集合必须逐项不变。"""
    from aitest.application.planning.publish import _plan_payload

    payload = _plan_payload(_plan(), [_case()], project_id="project-1")

    assert set(payload) == {
        "project_id",
        "plan_id",
        "revision",
        "scope_id",
        "scope_revision",
        "required_case_ids",
        "template_case_ids",
        "case_revisions",
        "rule_revisions",
        "template_versions",
        "run_tier",
        "initial_driver",
        "cases",
    }
    assert "coverage_contract" not in payload


# ------------------------------------------------------------ 纯校验


def test_valid_coverage_contract_passes_validation() -> None:
    validate_coverage_contract(_contract())


def test_publication_requires_a_claim_for_every_function_dimension() -> None:
    plan = _plan(coverage_contract=_contract(coverage_claims=()))
    with pytest.raises(ValueError, match="coverage claim missing"):
        validate_plan_publication(plan, [_case()])


def test_publication_rejects_claim_for_unfrozen_case() -> None:
    contract = _contract()
    contract = replace(
        contract,
        coverage_claims=tuple(
            replace(claim, case_ids=frozenset({"case-missing"}))
            if claim.claim_id == "claim-create-normal"
            else claim
            for claim in contract.coverage_claims
        ),
    )
    plan = _plan(coverage_contract=contract)
    with pytest.raises(ValueError, match="unfrozen cases"):
        validate_plan_publication(plan, [_case()])


def test_publication_rejects_claim_for_another_plan_revision() -> None:
    contract = _contract()
    contract = replace(
        contract,
        coverage_claims=(
            replace(contract.coverage_claims[0], plan_revision=2),
            *contract.coverage_claims[1:],
        ),
    )
    plan = _plan(coverage_contract=contract)
    with pytest.raises(ValueError, match="plan revision differs"):
        validate_plan_publication(plan, [_case()])


# ------------------------------------------------------------ 覆盖承诺


def test_valid_coverage_claim_passes_validation() -> None:
    validate_coverage_contract(
        _contract(coverage_claims=(_claim(), _claim(claim_id="claim-2"))),
    )


def test_duplicate_claim_id_is_rejected() -> None:
    contract = _contract(
        coverage_claims=(_claim(), _claim(dimension="error")),
    )
    with pytest.raises(ValueError, match="claim_id must be unique"):
        validate_coverage_contract(contract)


def test_claim_referencing_an_unknown_function_is_rejected() -> None:
    contract = _contract(
        coverage_claims=(_claim(function_id="billing.unknown"),),
    )
    with pytest.raises(ValueError, match="unknown function"):
        validate_coverage_contract(contract)


def test_claim_referencing_an_unknown_dimension_is_rejected() -> None:
    contract = _contract(
        coverage_claims=(_claim(dimension="concurrency"),),
    )
    with pytest.raises(ValueError, match="dimension is not required"):
        validate_coverage_contract(contract)


def test_claim_requires_at_least_one_case_at_construction() -> None:
    """case_ids 非空沿用 `CoverageClaim` 自己的构造校验。"""
    with pytest.raises(ValueError, match="at least one case"):
        _claim(case_ids=frozenset())


def test_duplicate_function_id_is_rejected() -> None:
    contract = _contract(
        function_requirements=(
            FunctionRequirement("billing.create", ("normal",)),
            FunctionRequirement("billing.create", ("error",)),
        )
    )
    with pytest.raises(ValueError, match="function_id must be unique"):
        validate_coverage_contract(contract)


def test_empty_required_dimensions_are_rejected() -> None:
    contract = _contract(
        function_requirements=(FunctionRequirement("billing.create", ()),)
    )
    with pytest.raises(ValueError, match="required_dimensions must not be empty"):
        validate_coverage_contract(contract)


def test_duplicate_required_dimensions_are_rejected() -> None:
    contract = _contract(
        function_requirements=(
            FunctionRequirement("billing.create", ("normal", "normal")),
        )
    )
    with pytest.raises(ValueError, match="required_dimensions must be unique"):
        validate_coverage_contract(contract)


def test_blank_required_dimension_is_rejected() -> None:
    contract = _contract(
        function_requirements=(FunctionRequirement("billing.create", ("normal", " ")),)
    )
    with pytest.raises(ValueError, match="required_dimensions must not contain empty"):
        validate_coverage_contract(contract)


def test_invalid_template_applicability_is_rejected() -> None:
    contract = _contract(
        template_dispositions=(
            TemplateDisposition(
                "tpl-http-status",
                "bogus",  # type: ignore[arg-type]
                TemplateCoverage.COVERED,
            ),
        )
    )
    with pytest.raises(ValueError, match="applicability must be one of"):
        validate_coverage_contract(contract)


def test_invalid_template_coverage_is_rejected() -> None:
    contract = _contract(
        template_dispositions=(
            TemplateDisposition(
                "tpl-http-status",
                TemplateApplicability.APPLICABLE,
                "partial",  # type: ignore[arg-type]
            ),
        )
    )
    with pytest.raises(ValueError, match="coverage must be one of"):
        validate_coverage_contract(contract)


def test_wire_enum_values_are_accepted() -> None:
    """落盘形状使用枚举字符串值；校验函数按值接受，不要求调用方先包装。"""
    contract = _contract(
        template_dispositions=(
            TemplateDisposition(
                "tpl-http-status",
                "applicable",  # type: ignore[arg-type]
                "covered",  # type: ignore[arg-type]
            ),
        )
    )
    validate_coverage_contract(contract)


def test_duplicate_critical_path_ids_are_rejected() -> None:
    contract = _contract(critical_path_ids=("path-write", "path-write"))
    with pytest.raises(ValueError, match="critical_path_ids must be unique"):
        validate_coverage_contract(contract)


def test_blank_critical_path_id_is_rejected() -> None:
    contract = _contract(critical_path_ids=("path-write", " "))
    with pytest.raises(ValueError, match="critical_path_ids must not contain empty"):
        validate_coverage_contract(contract)


def test_understanding_revision_must_be_positive() -> None:
    contract = _contract(understanding_revision=0)
    with pytest.raises(ValueError, match="understanding_revision must be >= 1"):
        validate_coverage_contract(contract)
