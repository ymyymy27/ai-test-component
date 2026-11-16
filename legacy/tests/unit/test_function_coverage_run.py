"""运行态覆盖投影：从本轮 Coverage 生成 FunctionCoverage。

依据：设计文档 03 第 4.4—4.6、8.2—8.3 节；架构文档《03-报告与缺陷》第 2 节。

本批只验证纯投影：不计算业务结论或证据等级、不修改 Coverage 集合、不读取
文件、不生成报告 DTO，也不接界面。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aitest.application.review.function_coverage_run import (
    NO_CLAIM_GAP_REASON,
    build_run_function_coverage,
)
from aitest.domain.planning.plans import CoverageContract, FunctionRequirement
from aitest.domain.planning.understanding import CoverageClaim, CoverageClaimStatus
from aitest.domain.review.function_coverage import (
    CoverageStatus,
    FunctionCoverage,
)
from aitest.domain.review.reports import Coverage


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
                case_ids=frozenset({"case-2"}),
            ),
        ),
    }
    return CoverageContract(**(base | overrides))  # type: ignore[arg-type]


def _coverage(**overrides: object) -> Coverage:
    base: dict[str, object] = {
        "selected": frozenset(),
        "required": frozenset(),
        "executed": frozenset(),
        "reused": frozenset(),
        "verified": frozenset(),
        "passed": frozenset(),
    }
    return Coverage(**(base | overrides))  # type: ignore[arg-type]


# ------------------------------------------------------------ 完整验证


def test_all_claim_cases_verified_become_proven() -> None:
    coverage = _coverage(
        selected=frozenset({"case-1", "case-2"}),
        required=frozenset({"case-1", "case-2"}),
        executed=frozenset({"case-1", "case-2"}),
        verified=frozenset({"case-1", "case-2"}),
        passed=frozenset({"case-1", "case-2"}),
    )

    projections = build_run_function_coverage(
        _contract(), coverage=coverage, plan_revision=7, run_id="run-1"
    )

    assert len(projections) == 1
    projection = projections[0]
    assert projection.function_id == "order.create"
    assert projection.plan_revision == 7
    assert projection.run_id == "run-1"
    assert projection.coverage_id == "run-coverage:run-1:order.create"
    assert projection.coverage_by_dimension == {
        "normal": CoverageStatus.PROVEN,
        "error": CoverageStatus.PROVEN,
    }
    assert projection.overall_coverage is CoverageStatus.PROVEN
    assert projection.case_ids == frozenset({"case-1", "case-2"})
    # 投影不携带具体证据/核验引用；已验证不等于把引用凭空补出来。
    assert projection.evidence_refs == ()
    assert projection.verification_refs == ()
    assert projection.gap_reason is None


def test_partially_verified_dimension_is_partial() -> None:
    contract = _contract(
        coverage_claims=(
            _claim(dimension="normal", case_ids=frozenset({"case-1"})),
            _claim(
                claim_id="claim-2",
                dimension="error",
                case_ids=frozenset({"case-2", "case-3"}),
            ),
        )
    )
    coverage = _coverage(
        selected=frozenset({"case-1", "case-2", "case-3"}),
        executed=frozenset({"case-1", "case-2", "case-3"}),
        verified=frozenset({"case-1", "case-2"}),
        passed=frozenset({"case-1", "case-2"}),
    )

    projection = build_run_function_coverage(
        contract, coverage=coverage, plan_revision=1, run_id="run-1"
    )[0]

    assert projection.coverage_by_dimension == {
        "normal": CoverageStatus.PROVEN,
        "error": CoverageStatus.PARTIAL,
    }
    assert projection.overall_coverage is CoverageStatus.PARTIAL
    assert projection.gap_reason is not None
    assert "error: cases not independently verified: case-3" in projection.gap_reason


def test_no_executed_or_verified_claim_case_is_not_proven() -> None:
    coverage = _coverage(selected=frozenset({"case-1", "case-2"}))

    projection = build_run_function_coverage(
        _contract(), coverage=coverage, plan_revision=1, run_id="run-1"
    )[0]

    assert set(projection.coverage_by_dimension.values()) == {
        CoverageStatus.NOT_PROVEN
    }
    assert projection.overall_coverage is CoverageStatus.NOT_PROVEN
    assert projection.gap_reason is not None
    assert "normal: cases not executed: case-1" in projection.gap_reason
    assert "error: cases not executed: case-2" in projection.gap_reason


# ------------------------------------------------------------ 缺口优先级


def test_unsupported_claim_outranks_verified_cases_and_other_claims() -> None:
    contract = _contract(
        coverage_claims=(
            _claim(
                claim_id="claim-unsupported",
                dimension="normal",
                status=CoverageClaimStatus.UNSUPPORTED,
            ),
            _claim(
                claim_id="claim-normal-proven",
                dimension="normal",
                case_ids=frozenset({"case-1"}),
            ),
            _claim(
                claim_id="claim-2",
                dimension="error",
                case_ids=frozenset({"case-2"}),
            ),
        )
    )
    coverage = _coverage(
        selected=frozenset({"case-1", "case-2"}),
        executed=frozenset({"case-1", "case-2"}),
        verified=frozenset({"case-1", "case-2"}),
        passed=frozenset({"case-1", "case-2"}),
    )

    projection = build_run_function_coverage(
        contract, coverage=coverage, plan_revision=1, run_id="run-1"
    )[0]

    assert projection.coverage_by_dimension == {
        "normal": CoverageStatus.UNSUPPORTED,
        "error": CoverageStatus.PROVEN,
    }
    assert projection.overall_coverage is CoverageStatus.UNSUPPORTED
    assert projection.gap_reason is not None
    assert "normal: unsupported" in projection.gap_reason


def test_missing_claim_is_not_proven_with_no_coverage_claim_gap() -> None:
    contract = _contract(
        coverage_claims=(
            _claim(
                claim_id="claim-2",
                dimension="error",
                case_ids=frozenset({"case-2"}),
            ),
        )
    )
    coverage = _coverage(
        selected=frozenset({"case-2"}),
        executed=frozenset({"case-2"}),
        verified=frozenset({"case-2"}),
    )

    projection = build_run_function_coverage(
        contract, coverage=coverage, plan_revision=1, run_id="run-1"
    )[0]

    assert projection.coverage_by_dimension == {
        "normal": CoverageStatus.NOT_PROVEN,
        "error": CoverageStatus.PROVEN,
    }
    assert projection.overall_coverage is CoverageStatus.PARTIAL
    assert projection.gap_reason == NO_CLAIM_GAP_REASON


def test_no_claims_at_all_marks_all_dimensions_not_proven() -> None:
    contract = _contract(coverage_claims=())

    projection = build_run_function_coverage(
        contract, coverage=_coverage(), plan_revision=1, run_id="run-1"
    )[0]

    assert set(projection.coverage_by_dimension.values()) == {
        CoverageStatus.NOT_PROVEN
    }
    assert projection.overall_coverage is CoverageStatus.NOT_PROVEN
    assert projection.case_ids == frozenset()
    # 多个维度都缺 claim 时只保留一条原文缺口，不重复拼接同一原因。
    assert projection.gap_reason == NO_CLAIM_GAP_REASON


# ------------------------------------------------------------ 不在本轮选择范围


def test_unselected_claim_case_prevents_proven() -> None:
    contract = _contract(
        function_requirements=(FunctionRequirement("order.create", ("normal",)),),
        coverage_claims=(
            _claim(dimension="normal", case_ids=frozenset({"case-1", "case-2"})),
        ),
    )
    coverage = _coverage(
        selected=frozenset({"case-1"}),
        executed=frozenset({"case-1"}),
        verified=frozenset({"case-1"}),
        passed=frozenset({"case-1"}),
    )

    projection = build_run_function_coverage(
        contract, coverage=coverage, plan_revision=1, run_id="run-1"
    )[0]

    assert projection.coverage_by_dimension == {
        "normal": CoverageStatus.PARTIAL,
    }
    assert projection.overall_coverage is CoverageStatus.PARTIAL
    assert projection.gap_reason is not None
    assert "normal: cases outside selected scope: case-2" in projection.gap_reason
    assert projection.case_ids == frozenset({"case-1", "case-2"})


def test_unselected_claim_case_alone_is_not_proven() -> None:
    contract = _contract(
        function_requirements=(FunctionRequirement("order.create", ("normal",)),),
        coverage_claims=(
            _claim(dimension="normal", case_ids=frozenset({"case-9"})),
        ),
    )
    coverage = _coverage(
        selected=frozenset({"case-1"}),
        executed=frozenset({"case-1"}),
        verified=frozenset({"case-1"}),
    )

    projection = build_run_function_coverage(
        contract, coverage=coverage, plan_revision=1, run_id="run-1"
    )[0]

    assert projection.coverage_by_dimension == {
        "normal": CoverageStatus.NOT_PROVEN,
    }
    assert projection.overall_coverage is CoverageStatus.NOT_PROVEN
    assert projection.gap_reason is not None
    assert "normal: cases outside selected scope: case-9" in projection.gap_reason


def test_unrelated_unverified_cases_do_not_dilute_function_coverage() -> None:
    """Coverage 是全局集合；只按 claim 用例判定，不让其他功能的用例拖低本功能。"""

    contract = _contract(
        function_requirements=(FunctionRequirement("order.create", ("normal",)),),
        coverage_claims=(
            _claim(dimension="normal", case_ids=frozenset({"case-1"})),
        ),
    )
    coverage = _coverage(
        selected=frozenset({"case-1", "case-9"}),
        executed=frozenset({"case-1"}),
        verified=frozenset({"case-1"}),
        passed=frozenset({"case-1"}),
    )

    projection = build_run_function_coverage(
        contract, coverage=coverage, plan_revision=1, run_id="run-1"
    )[0]

    assert projection.coverage_by_dimension == {
        "normal": CoverageStatus.PROVEN,
    }
    assert projection.overall_coverage is CoverageStatus.PROVEN
    assert projection.case_ids == frozenset({"case-1"})
    assert projection.gap_reason is None


def test_not_applicable_dimension_is_excluded_from_overall_derivation() -> None:
    """不适用维度不计入分母；运行投影只输出适用维度。

    运行态契约的 `required_dimensions` 只包含适用维度，因此本函数不会产出
    `not_applicable`。这里固定 `FunctionCoverage` 的汇总语义：即使上游为某
    功能保留了不适用维度，它也不能把已证明的功能拉成部分覆盖。
    """

    projection = FunctionCoverage(
        coverage_id="run-coverage:run-1:order.create",
        function_id="order.create",
        plan_revision=1,
        run_id="run-1",
        coverage_by_dimension={
            "normal": CoverageStatus.PROVEN,
            "concurrency": CoverageStatus.NOT_APPLICABLE,
        },
        updated_at=datetime.now(UTC),
    )

    assert projection.overall_coverage is CoverageStatus.PROVEN


# ------------------------------------------------------------ 顺序与空契约


def test_function_order_and_case_ids_follow_contract() -> None:
    contract = _contract(
        function_requirements=(
            FunctionRequirement("order.refund", ("normal",)),
            FunctionRequirement("order.create", ("normal",)),
        ),
        coverage_claims=(
            _claim(
                function_id="order.refund",
                dimension="normal",
                case_ids=frozenset({"case-9"}),
            ),
            _claim(
                claim_id="claim-2",
                function_id="order.create",
                dimension="normal",
                case_ids=frozenset({"case-1"}),
            ),
        ),
    )
    coverage = _coverage(
        selected=frozenset({"case-1", "case-9"}),
        executed=frozenset({"case-1", "case-9"}),
        verified=frozenset({"case-1", "case-9"}),
    )

    refund, create = build_run_function_coverage(
        contract, coverage=coverage, plan_revision=3, run_id="run-1"
    )

    assert [item.function_id for item in (refund, create)] == [
        "order.refund",
        "order.create",
    ]
    assert refund.case_ids == frozenset({"case-9"})
    assert create.case_ids == frozenset({"case-1"})


def test_empty_function_requirements_return_empty_tuple() -> None:
    contract = _contract(function_requirements=(), coverage_claims=())

    assert (
        build_run_function_coverage(
            contract, coverage=_coverage(), plan_revision=1, run_id="run-1"
        )
        == ()
    )


# ------------------------------------------------------------ 输入拒绝


def test_non_positive_plan_revision_is_rejected() -> None:
    with pytest.raises(ValueError, match="plan_revision"):
        build_run_function_coverage(
            _contract(), coverage=_coverage(), plan_revision=0, run_id="run-1"
        )


def test_blank_run_id_is_rejected_even_with_empty_contract() -> None:
    contract = _contract(function_requirements=(), coverage_claims=())

    with pytest.raises(ValueError, match="run_id"):
        build_run_function_coverage(
            contract, coverage=_coverage(), plan_revision=1, run_id=" "
        )
