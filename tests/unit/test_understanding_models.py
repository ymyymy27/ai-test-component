"""项目理解、业务功能目录与覆盖投影的领域校验。"""

from dataclasses import fields
from datetime import UTC, datetime

import pytest

from aitest.domain.planning.understanding import (
    BusinessFunction,
    BusinessFunctionCatalog,
    BusinessFunctionKind,
    BusinessFunctionOrigin,
    CapabilityGap,
    CoverageClaim,
    CoverageClaimStatus,
    FunctionTrigger,
    ProjectSignalOrigin,
    ProjectSignals,
    ReviewState,
    TestIntent,
    TestIntentOrigin,
)
from aitest.domain.review.function_coverage import (
    CoverageStatus,
    FunctionCoverage,
    derive_overall_coverage,
)

_NOW = datetime(2026, 11, 7, tzinfo=UTC)


def _function(**overrides: object) -> BusinessFunction:
    base: dict[str, object] = {
        "function_id": "fn-order-create",
        "name": "订单创建",
        "parent_function_id": None,
        "kind": BusinessFunctionKind.OPERATION,
        "trigger": FunctionTrigger.HTTP,
        "business_object": "订单",
        "expected_outcome": "订单创建成功",
        "conditions": ("已登录",),
        "required_dimensions": ("normal", "error"),
        "source_refs": ("src/api/orders.py:12",),
        "origin": BusinessFunctionOrigin.DETERMINISTIC,
        "confidence": 0.9,
        "review_state": ReviewState.UNREVIEWED,
        "understanding_revision": 1,
        "canonical_function_id": None,
        "aliases": (),
        "source_snapshot_id": "snapshot-1",
    }
    return BusinessFunction(**(base | overrides))  # type: ignore[arg-type]


def _catalog(*functions: BusinessFunction) -> BusinessFunctionCatalog:
    return BusinessFunctionCatalog(
        understanding_id="understanding-1", revision=1, functions=functions
    )


def _intent(**overrides: object) -> TestIntent:
    base: dict[str, object] = {
        "intent_id": "intent-1",
        "statement": "订单创建后应可查询",
        "risk": "订单丢失",
        "function_ids": frozenset({"fn-order-create"}),
        "required_dimensions": ("normal", "persistence"),
        "acceptance_condition": "查询返回同一订单",
        "rationale": "新增订单端点",
        "origin": TestIntentOrigin.USER,
        "confidence": 0.8,
        "understanding_revision": 1,
    }
    return TestIntent(**(base | overrides))  # type: ignore[arg-type]


def _claim(**overrides: object) -> CoverageClaim:
    base: dict[str, object] = {
        "claim_id": "claim-1",
        "intent_id": "intent-1",
        "function_id": "fn-order-create",
        "dimension": "normal",
        "plan_revision": 1,
        "status": CoverageClaimStatus.PROVEN,
        "case_ids": frozenset({"case-1"}),
    }
    return CoverageClaim(**(base | overrides))  # type: ignore[arg-type]


def _coverage(**overrides: object) -> FunctionCoverage:
    base: dict[str, object] = {
        "coverage_id": "coverage-1",
        "function_id": "fn-order-create",
        "plan_revision": 1,
        "run_id": None,
        "coverage_by_dimension": {"normal": CoverageStatus.PROVEN},
        "updated_at": _NOW,
    }
    return FunctionCoverage(**(base | overrides))  # type: ignore[arg-type]


# ------------------------------------------------------- BusinessFunction


def test_business_function_rejects_empty_identifier() -> None:
    with pytest.raises(ValueError, match="function_id"):
        _function(function_id="  ")


def test_business_function_rejects_empty_dimensions() -> None:
    with pytest.raises(ValueError, match="required_dimensions"):
        _function(required_dimensions=())


def test_business_function_rejects_blank_dimension() -> None:
    with pytest.raises(ValueError, match="required_dimensions"):
        _function(required_dimensions=("normal", " "))


def test_business_function_rejects_duplicate_dimensions() -> None:
    with pytest.raises(ValueError, match="required_dimensions must be unique"):
        _function(required_dimensions=("normal", "normal"))


def test_business_function_accepts_vocabulary_strings() -> None:
    function = _function(
        kind="capability",
        trigger="cli",
        origin="ai_suggested",
        review_state="needs_review",
    )
    assert function.kind is BusinessFunctionKind.CAPABILITY
    assert function.trigger is FunctionTrigger.CLI
    assert function.origin is BusinessFunctionOrigin.AI_SUGGESTED
    assert function.review_state is ReviewState.NEEDS_REVIEW


def test_business_function_accepts_aliases_and_canonical_pointer() -> None:
    function = _function(
        aliases=("create-order", "下单"),
        canonical_function_id="fn-order-create-v2",
        source_snapshot_id="snapshot-9",
    )
    assert function.aliases == ("create-order", "下单")
    assert function.canonical_function_id == "fn-order-create-v2"
    assert function.source_snapshot_id == "snapshot-9"


def test_business_function_rejects_duplicate_aliases() -> None:
    with pytest.raises(ValueError, match="aliases must be unique"):
        _function(aliases=("create-order", "create-order"))


def test_business_function_rejects_blank_alias() -> None:
    with pytest.raises(ValueError, match="aliases"):
        _function(aliases=(" ",))


def test_business_function_allows_empty_aliases() -> None:
    assert _function(aliases=()).aliases == ()


def test_business_function_rejects_blank_canonical_id() -> None:
    with pytest.raises(ValueError, match="canonical_function_id"):
        _function(canonical_function_id="")


def test_business_function_requires_source_snapshot_id() -> None:
    with pytest.raises(ValueError, match="source_snapshot_id"):
        _function(source_snapshot_id=" ")


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan")])
def test_business_function_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        _function(confidence=confidence)


def test_business_function_accepts_confidence_boundaries() -> None:
    assert _function(confidence=0.0).confidence == 0.0
    assert _function(confidence=1.0).confidence == 1.0


# ------------------------------------------------- BusinessFunctionCatalog


def test_catalog_rejects_duplicate_function_ids() -> None:
    with pytest.raises(ValueError, match="function_id must be unique"):
        _catalog(_function(), _function())


def test_catalog_allows_root_function_without_parent() -> None:
    catalog = _catalog(_function())
    assert catalog.functions[0].parent_function_id is None


def test_catalog_rejects_unresolved_parent() -> None:
    child = _function(function_id="fn-child", parent_function_id="fn-missing")
    with pytest.raises(ValueError, match="unresolved parent"):
        _catalog(child)


def test_catalog_resolves_parent_regardless_of_declaration_order() -> None:
    child = _function(function_id="fn-child", parent_function_id="fn-domain")
    parent = _function(
        function_id="fn-domain", kind=BusinessFunctionKind.CAPABILITY
    )
    catalog = _catalog(child, parent)
    assert [function.function_id for function in catalog.functions] == [
        "fn-child",
        "fn-domain",
    ]


def test_catalog_rejects_parent_cycle() -> None:
    left = _function(function_id="fn-a", parent_function_id="fn-b")
    right = _function(function_id="fn-b", parent_function_id="fn-a")
    with pytest.raises(ValueError, match="cycle"):
        _catalog(left, right)


def test_catalog_rejects_self_parent() -> None:
    with pytest.raises(ValueError, match="cycle"):
        _catalog(_function(function_id="fn-a", parent_function_id="fn-a"))


def test_catalog_accepts_resolved_canonical_function() -> None:
    duplicate = _function(
        function_id="fn-create-alias",
        canonical_function_id="fn-order-create",
    )
    canonical = _function(function_id="fn-order-create")
    catalog = _catalog(duplicate, canonical)
    assert catalog.functions[0].canonical_function_id == "fn-order-create"


def test_catalog_rejects_unresolved_canonical_function() -> None:
    function = _function(canonical_function_id="fn-missing")
    with pytest.raises(ValueError, match="unresolved canonical"):
        _catalog(function)


def test_catalog_rejects_self_canonical_function() -> None:
    function = _function(canonical_function_id="fn-order-create")
    with pytest.raises(ValueError, match="reference itself"):
        _catalog(function)


def test_catalog_rejects_canonical_cycle() -> None:
    first = _function(function_id="fn-a", canonical_function_id="fn-b")
    second = _function(function_id="fn-b", canonical_function_id="fn-a")
    with pytest.raises(ValueError, match="canonical cycle"):
        _catalog(first, second)


# ---------------------------------------------------------- ProjectSignals


def test_project_signals_keep_per_signal_confidence_snapshot() -> None:
    confidence = {"http": 0.8}
    signals = ProjectSignals(
        detected_stack=("python",),
        entry_candidates=("src/app.py",),
        capabilities=("http",),
        risk_signals=("numeric_input",),
        confidence=confidence,
        evidence_refs=("src/app.py:1",),
        origin=ProjectSignalOrigin.AI_SUGGESTED,
    )
    confidence["db"] = 0.1
    assert dict(signals.confidence) == {"http": 0.8}
    assert signals.origin is ProjectSignalOrigin.AI_SUGGESTED


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan")])
def test_project_signals_reject_invalid_confidence(value: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        ProjectSignals(
            detected_stack=("python",),
            entry_candidates=("src/app.py",),
            capabilities=("http",),
            risk_signals=(),
            confidence={"http": value},
            evidence_refs=(),
            origin=ProjectSignalOrigin.DETERMINISTIC,
        )


# ----------------------------------------------------------- CapabilityGap


def test_capability_gap_records_blocking_limitation() -> None:
    gap = CapabilityGap(
        gap_id="gap-1",
        scope="fn-order-create:concurrency",
        missing_capability="parallel-request-injection",
        capability_revision="adapter-1.2.0",
        reason="当前适配器不支持并发请求",
        workaround="人工并发验证",
        blocks_full=True,
        source_refs=("adapter:capabilities",),
    )
    assert gap.blocks_full is True
    assert gap.workaround == "人工并发验证"


# -------------------------------------------------------------- TestIntent


def test_test_intent_requires_a_function() -> None:
    with pytest.raises(ValueError, match="function_ids"):
        _intent(function_ids=frozenset())


def test_test_intent_requires_dimensions() -> None:
    with pytest.raises(ValueError, match="required_dimensions"):
        _intent(required_dimensions=())


def test_test_intent_rejects_duplicate_dimensions() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        _intent(required_dimensions=("normal", "normal"))


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan")])
def test_test_intent_rejects_invalid_confidence(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        _intent(confidence=confidence)


# ----------------------------------------------------------- CoverageClaim


def test_coverage_claim_status_has_no_unknown() -> None:
    assert {status.value for status in CoverageClaimStatus} == {
        "proven",
        "partial",
        "not_proven",
        "unsupported",
    }


def test_coverage_claim_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="unknown"):
        _claim(status="unknown")


def test_coverage_claim_requires_at_least_one_case() -> None:
    with pytest.raises(ValueError, match="at least one case"):
        _claim(case_ids=frozenset())


# ------------------------------------------------------- FunctionCoverage


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ((CoverageStatus.PROVEN,), CoverageStatus.PROVEN),
        ((CoverageStatus.PARTIAL,), CoverageStatus.PARTIAL),
        (
            (CoverageStatus.PROVEN, CoverageStatus.PARTIAL),
            CoverageStatus.PARTIAL,
        ),
        (
            (CoverageStatus.PROVEN, CoverageStatus.PARTIAL, CoverageStatus.NOT_PROVEN),
            CoverageStatus.PARTIAL,
        ),
        (
            (CoverageStatus.PROVEN, CoverageStatus.NOT_PROVEN),
            CoverageStatus.PARTIAL,
        ),
        ((CoverageStatus.NOT_PROVEN,), CoverageStatus.NOT_PROVEN),
        ((CoverageStatus.NOT_APPLICABLE,), CoverageStatus.NOT_APPLICABLE),
        (
            (CoverageStatus.NOT_APPLICABLE, CoverageStatus.NOT_APPLICABLE),
            CoverageStatus.NOT_APPLICABLE,
        ),
        (
            (CoverageStatus.NOT_APPLICABLE, CoverageStatus.PROVEN),
            CoverageStatus.PROVEN,
        ),
        (
            (CoverageStatus.NOT_APPLICABLE, CoverageStatus.NOT_PROVEN),
            CoverageStatus.NOT_PROVEN,
        ),
        (
            (
                CoverageStatus.NOT_APPLICABLE,
                CoverageStatus.PROVEN,
                CoverageStatus.PARTIAL,
            ),
            CoverageStatus.PARTIAL,
        ),
    ],
)
def test_overall_coverage_aggregates_dimension_states(
    states: tuple[CoverageStatus, ...], expected: CoverageStatus
) -> None:
    assert derive_overall_coverage(states) is expected


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (
            (CoverageStatus.PROVEN, CoverageStatus.UNSUPPORTED),
            CoverageStatus.UNSUPPORTED,
        ),
        (
            (CoverageStatus.PARTIAL, CoverageStatus.UNSUPPORTED),
            CoverageStatus.UNSUPPORTED,
        ),
        (
            (CoverageStatus.NOT_PROVEN, CoverageStatus.UNKNOWN),
            CoverageStatus.UNKNOWN,
        ),
        (
            (CoverageStatus.PROVEN, CoverageStatus.UNKNOWN),
            CoverageStatus.UNKNOWN,
        ),
        (
            (CoverageStatus.UNSUPPORTED, CoverageStatus.UNKNOWN),
            CoverageStatus.UNSUPPORTED,
        ),
        (
            (CoverageStatus.PROVEN, CoverageStatus.UNKNOWN, CoverageStatus.UNSUPPORTED),
            CoverageStatus.UNSUPPORTED,
        ),
        (
            (CoverageStatus.NOT_APPLICABLE, CoverageStatus.UNSUPPORTED),
            CoverageStatus.UNSUPPORTED,
        ),
        (
            (CoverageStatus.NOT_APPLICABLE, CoverageStatus.UNKNOWN),
            CoverageStatus.UNKNOWN,
        ),
    ],
)
def test_unknown_and_unsupported_outrank_covered_states(
    states: tuple[CoverageStatus, ...], expected: CoverageStatus
) -> None:
    assert derive_overall_coverage(states) is expected


def test_overall_coverage_all_dimensions_not_applicable() -> None:
    assert (
        derive_overall_coverage(
            (CoverageStatus.NOT_APPLICABLE, CoverageStatus.NOT_APPLICABLE)
        )
        is CoverageStatus.NOT_APPLICABLE
    )


def test_overall_coverage_ignores_not_applicable_dimensions() -> None:
    assert (
        derive_overall_coverage(
            (
                CoverageStatus.PROVEN,
                CoverageStatus.NOT_APPLICABLE,
                CoverageStatus.PROVEN,
            )
        )
        is CoverageStatus.PROVEN
    )


def test_overall_coverage_rejects_empty_status_set() -> None:
    with pytest.raises(ValueError, match="at least one"):
        derive_overall_coverage([])


def test_function_coverage_overall_is_derived_from_dimensions() -> None:
    coverage = _coverage(
        coverage_by_dimension={
            "normal": CoverageStatus.PROVEN,
            "error": CoverageStatus.NOT_PROVEN,
        }
    )
    assert coverage.overall_coverage is CoverageStatus.PARTIAL


def test_function_coverage_overall_cannot_be_hand_filled() -> None:
    assert "overall_coverage" not in {
        field.name for field in fields(FunctionCoverage)
    }
    with pytest.raises(TypeError):
        FunctionCoverage(
            coverage_id="coverage-1",
            function_id="fn-order-create",
            plan_revision=1,
            run_id=None,
            coverage_by_dimension={"normal": CoverageStatus.PROVEN},
            updated_at=_NOW,
            overall_coverage=CoverageStatus.PROVEN,  # type: ignore[call-arg]
        )


def test_function_coverage_snapshots_dimension_mapping() -> None:
    dimensions = {"normal": CoverageStatus.PROVEN}
    coverage = _coverage(coverage_by_dimension=dimensions)
    dimensions["error"] = CoverageStatus.UNKNOWN
    assert coverage.overall_coverage is CoverageStatus.PROVEN


def test_function_coverage_accepts_status_strings() -> None:
    coverage = _coverage(coverage_by_dimension={"normal": "proven"})
    assert coverage.overall_coverage is CoverageStatus.PROVEN


def test_function_coverage_rejects_empty_dimensions() -> None:
    with pytest.raises(ValueError, match="coverage_by_dimension"):
        _coverage(coverage_by_dimension={})


def test_function_coverage_rejects_blank_dimension_name() -> None:
    with pytest.raises(ValueError, match="coverage dimension"):
        _coverage(coverage_by_dimension={" ": CoverageStatus.PROVEN})


def test_function_coverage_rejects_unmapped_status() -> None:
    with pytest.raises(ValueError, match="coverage status"):
        _coverage(coverage_by_dimension={"normal": "deferred"})


def test_function_coverage_all_dimensions_not_applicable() -> None:
    coverage = _coverage(
        coverage_by_dimension={
            "concurrency": CoverageStatus.NOT_APPLICABLE,
        }
    )
    assert coverage.overall_coverage is CoverageStatus.NOT_APPLICABLE


def test_function_coverage_ignores_not_applicable_dimensions() -> None:
    coverage = _coverage(
        coverage_by_dimension={
            "normal": CoverageStatus.PROVEN,
            "concurrency": CoverageStatus.NOT_APPLICABLE,
        }
    )
    assert coverage.overall_coverage is CoverageStatus.PROVEN


def test_function_coverage_requires_plan_or_run() -> None:
    with pytest.raises(ValueError, match="plan revision or a run id"):
        _coverage(plan_revision=None, run_id=None)


def test_function_coverage_requires_positive_plan_revision() -> None:
    with pytest.raises(ValueError, match="plan_revision"):
        _coverage(plan_revision=0)


def test_function_coverage_accepts_run_only_projection() -> None:
    coverage = _coverage(plan_revision=None, run_id="run-1")
    assert coverage.run_id == "run-1"
