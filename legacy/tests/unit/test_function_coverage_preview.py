"""计划覆盖预览：从覆盖契约生成确认页可用的 FunctionCoverage。

依据：设计文档 03 第 4.4—4.6、5.5、6.1、8.2—8.3 节（确认页覆盖收据是
计划冻结 `FunctionCoverage` 的预览），以及架构文档《03-报告与缺陷》第 2 节。

本批只验证纯投影：不接报告生成、不接界面、不写记录，也不判断执行证据。
"""

from __future__ import annotations

import pytest

from aitest.application.review.function_coverage_preview import (
    NO_CLAIM_GAP_REASON,
    build_plan_function_coverage,
)
from aitest.domain.planning.plans import CoverageContract, FunctionRequirement
from aitest.domain.planning.understanding import CoverageClaim, CoverageClaimStatus
from aitest.domain.review.function_coverage import (
    CoverageStatus,
    derive_overall_coverage,
)


def _claim(**overrides: object) -> CoverageClaim:
    base: dict[str, object] = {
        "claim_id": "claim-1",
        "intent_id": "intent-1",
        "function_id": "order.create",
        "dimension": "normal",
        "plan_revision": 1,
        "status": CoverageClaimStatus.PROVEN,
        "case_ids": frozenset({"case-1"}),
    }
    return CoverageClaim(**(base | overrides))  # type: ignore[arg-type]


def _contract(**overrides: object) -> CoverageContract:
    base: dict[str, object] = {
        "contract_id": "contract-1",
        "understanding_revision": 1,
        "function_requirements": (
            FunctionRequirement("order.create", ("normal", "error")),
        ),
        "coverage_claims": (
            _claim(dimension="normal", case_ids=frozenset({"case-1"})),
            _claim(
                claim_id="claim-2",
                dimension="error",
                status=CoverageClaimStatus.PARTIAL,
                case_ids=frozenset({"case-2"}),
            ),
        ),
    }
    return CoverageContract(**(base | overrides))  # type: ignore[arg-type]


# ------------------------------------------------------------ planned 汇总


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ((CoverageStatus.PLANNED,), CoverageStatus.PLANNED),
        (
            (CoverageStatus.PLANNED, CoverageStatus.NOT_APPLICABLE),
            CoverageStatus.PLANNED,
        ),
        (
            (CoverageStatus.PLANNED, CoverageStatus.NOT_APPLICABLE, CoverageStatus.PLANNED),
            CoverageStatus.PLANNED,
        ),
        # planned 不能掩盖已登记的能力缺口、未识别区域或未承诺维度。
        (
            (CoverageStatus.PLANNED, CoverageStatus.UNSUPPORTED),
            CoverageStatus.UNSUPPORTED,
        ),
        (
            (CoverageStatus.PLANNED, CoverageStatus.UNKNOWN),
            CoverageStatus.UNKNOWN,
        ),
        (
            (CoverageStatus.PLANNED, CoverageStatus.NOT_PROVEN),
            CoverageStatus.NOT_PROVEN,
        ),
        (
            (
                CoverageStatus.PLANNED,
                CoverageStatus.NOT_PROVEN,
                CoverageStatus.NOT_APPLICABLE,
            ),
            CoverageStatus.NOT_PROVEN,
        ),
        # 混合既知事实时按现有 proven/partial 规则，不把 planned 当成 proven。
        (
            (CoverageStatus.PLANNED, CoverageStatus.PROVEN),
            CoverageStatus.PARTIAL,
        ),
        (
            (CoverageStatus.PLANNED, CoverageStatus.PARTIAL),
            CoverageStatus.PARTIAL,
        ),
    ],
)
def test_planned_aggregation_rules(
    states: tuple[CoverageStatus, ...], expected: CoverageStatus
) -> None:
    assert derive_overall_coverage(states) is expected


def test_planned_does_not_change_legacy_not_proven_aggregation() -> None:
    """已发布投影的既有口径不因新增 planned 改变。"""

    assert (
        derive_overall_coverage(
            (CoverageStatus.PROVEN, CoverageStatus.NOT_PROVEN)
        )
        is CoverageStatus.PARTIAL
    )


# ------------------------------------------------------------ 完整承诺


def test_all_required_dimensions_with_claims_become_planned() -> None:
    contract = _contract()

    projections = build_plan_function_coverage(contract, plan_revision=7)

    assert len(projections) == 1
    coverage = projections[0]
    assert coverage.function_id == "order.create"
    assert coverage.plan_revision == 7
    assert coverage.run_id is None
    assert coverage.coverage_by_dimension == {
        "normal": CoverageStatus.PLANNED,
        "error": CoverageStatus.PLANNED,
    }
    assert coverage.overall_coverage is CoverageStatus.PLANNED
    assert coverage.gap_reason is None
    assert coverage.case_ids == frozenset({"case-1", "case-2"})
    assert coverage.evidence_refs == ()
    assert coverage.verification_refs == ()


def test_proven_and_partial_claims_are_both_only_planned() -> None:
    """claim 的 proven/partial 是计划意图，预览不能把它写成执行结果。"""

    contract = _contract(
        coverage_claims=(
            _claim(status=CoverageClaimStatus.PROVEN),
            _claim(
                claim_id="claim-2",
                dimension="error",
                status=CoverageClaimStatus.PARTIAL,
            ),
        )
    )

    projections = build_plan_function_coverage(contract, plan_revision=1)

    assert set(projections[0].coverage_by_dimension.values()) == {
        CoverageStatus.PLANNED
    }


def test_claims_do_not_supply_evidence_or_verification_refs() -> None:
    projection = build_plan_function_coverage(_contract(), plan_revision=1)[0]

    assert projection.evidence_refs == ()
    assert projection.verification_refs == ()


# ------------------------------------------------------------ 缺口


def test_missing_dimension_claim_is_not_proven_and_blocks_function() -> None:
    contract = _contract(
        coverage_claims=(
            _claim(dimension="normal", case_ids=frozenset({"case-1"})),
        )
    )

    coverage = build_plan_function_coverage(contract, plan_revision=1)[0]

    assert coverage.coverage_by_dimension == {
        "normal": CoverageStatus.PLANNED,
        "error": CoverageStatus.NOT_PROVEN,
    }
    assert coverage.overall_coverage is CoverageStatus.NOT_PROVEN
    assert coverage.gap_reason == NO_CLAIM_GAP_REASON


def test_no_claims_at_all_marks_every_dimension_not_proven() -> None:
    contract = _contract(coverage_claims=())

    coverage = build_plan_function_coverage(contract, plan_revision=1)[0]

    assert set(coverage.coverage_by_dimension.values()) == {
        CoverageStatus.NOT_PROVEN
    }
    assert coverage.overall_coverage is CoverageStatus.NOT_PROVEN
    assert coverage.gap_reason == NO_CLAIM_GAP_REASON
    assert coverage.case_ids == frozenset()


def test_unsupported_claim_outranks_not_proven_and_planned() -> None:
    contract = _contract(
        coverage_claims=(
            _claim(dimension="normal", status=CoverageClaimStatus.UNSUPPORTED),
            _claim(
                claim_id="claim-2",
                dimension="normal",
                status=CoverageClaimStatus.NOT_PROVEN,
            ),
            _claim(
                claim_id="claim-3",
                dimension="normal",
                status=CoverageClaimStatus.PROVEN,
            ),
            _claim(claim_id="claim-4", dimension="error"),
        )
    )

    coverage = build_plan_function_coverage(contract, plan_revision=1)[0]

    assert coverage.coverage_by_dimension == {
        "normal": CoverageStatus.UNSUPPORTED,
        "error": CoverageStatus.PLANNED,
    }
    assert coverage.overall_coverage is CoverageStatus.UNSUPPORTED
    assert coverage.gap_reason is None


def test_not_proven_claim_outranks_planned_within_its_dimension() -> None:
    contract = _contract(
        coverage_claims=(
            _claim(dimension="normal", status=CoverageClaimStatus.NOT_PROVEN),
            _claim(claim_id="claim-2", dimension="error"),
        )
    )

    coverage = build_plan_function_coverage(contract, plan_revision=1)[0]

    assert coverage.coverage_by_dimension == {
        "normal": CoverageStatus.NOT_PROVEN,
        "error": CoverageStatus.PLANNED,
    }
    assert coverage.overall_coverage is CoverageStatus.NOT_PROVEN
    assert coverage.gap_reason is None


def test_unsupported_outranks_missing_claim_gap() -> None:
    """同一功能既缺 claim 又已登记不支持时，功能级汇总仍是 unsupported。"""

    contract = _contract(
        coverage_claims=(
            _claim(dimension="normal", status=CoverageClaimStatus.UNSUPPORTED),
        )
    )

    coverage = build_plan_function_coverage(contract, plan_revision=1)[0]

    assert coverage.overall_coverage is CoverageStatus.UNSUPPORTED
    assert coverage.gap_reason == NO_CLAIM_GAP_REASON


# ------------------------------------------------------------ 顺序、身份与范围


def test_function_order_follows_contract_order() -> None:
    contract = _contract(
        function_requirements=(
            FunctionRequirement("order.refund", ("normal",)),
            FunctionRequirement("order.create", ("normal",)),
        ),
        coverage_claims=(
            _claim(function_id="order.refund", dimension="normal"),
            _claim(
                claim_id="claim-2",
                function_id="order.create",
                dimension="normal",
            ),
        ),
    )

    projections = build_plan_function_coverage(contract, plan_revision=3)

    assert [item.function_id for item in projections] == [
        "order.refund",
        "order.create",
    ]
    assert [item.coverage_id for item in projections] == [
        "plan-coverage:3:order.refund",
        "plan-coverage:3:order.create",
    ]


def test_dimension_order_follows_requirement_order() -> None:
    contract = _contract(
        function_requirements=(
            FunctionRequirement("order.create", ("error", "normal")),
        )
    )

    coverage = build_plan_function_coverage(contract, plan_revision=1)[0]

    assert list(coverage.coverage_by_dimension) == ["error", "normal"]


def test_case_ids_are_per_function_union_of_matching_claims() -> None:
    contract = _contract(
        function_requirements=(
            FunctionRequirement("order.create", ("normal", "error")),
            FunctionRequirement("order.query", ("normal",)),
        ),
        coverage_claims=(
            _claim(dimension="normal", case_ids=frozenset({"case-1", "case-2"})),
            _claim(
                claim_id="claim-2",
                dimension="error",
                case_ids=frozenset({"case-3"}),
            ),
            _claim(
                claim_id="claim-3",
                function_id="order.query",
                dimension="normal",
                case_ids=frozenset({"case-9"}),
            ),
        ),
    )

    create, query = build_plan_function_coverage(contract, plan_revision=1)

    assert create.case_ids == frozenset({"case-1", "case-2", "case-3"})
    assert query.case_ids == frozenset({"case-9"})


def test_empty_function_requirements_return_empty_tuple() -> None:
    """契约可以没有任何功能承诺；预览返回空元组，不伪造一条覆盖记录。"""

    contract = _contract(function_requirements=(), coverage_claims=())

    assert build_plan_function_coverage(contract, plan_revision=1) == ()


# ------------------------------------------------------------ 输入拒绝


def test_claim_for_unrequired_dimension_is_rejected() -> None:
    contract = _contract(
        function_requirements=(FunctionRequirement("order.create", ("normal",)),),
        coverage_claims=(
            _claim(dimension="permission"),
        ),
    )

    with pytest.raises(ValueError, match="dimension is not required"):
        build_plan_function_coverage(contract, plan_revision=1)


def test_claim_for_unknown_function_is_rejected() -> None:
    contract = _contract(
        coverage_claims=(
            _claim(function_id="order.missing", dimension="normal"),
        )
    )

    with pytest.raises(ValueError, match="unknown function"):
        build_plan_function_coverage(contract, plan_revision=1)


def test_duplicate_function_requirement_is_rejected() -> None:
    contract = _contract(
        function_requirements=(
            FunctionRequirement("order.create", ("normal",)),
            FunctionRequirement("order.create", ("error",)),
        ),
        coverage_claims=(),
    )

    with pytest.raises(ValueError, match="function_id must be unique"):
        build_plan_function_coverage(contract, plan_revision=1)


def test_non_positive_plan_revision_is_rejected() -> None:
    with pytest.raises(ValueError, match="plan_revision"):
        build_plan_function_coverage(_contract(), plan_revision=0)
