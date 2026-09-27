"""用例字段、关联与发布门禁。"""

import pytest

from aitest.domain.planning.plans import (
    AssertionBasis,
    AssertionBasisState,
    Case,
    CaseImportance,
    CaseLayer,
    CaseLink,
    ConfirmationRecord,
    validate_case_publication,
)


def _link(**overrides: object) -> CaseLink:
    base: dict[str, object] = {
        "acceptance_item_ids": frozenset({"AC-01"}),
        "module_ids": frozenset({"orders"}),
        "critical_path_ids": frozenset({"orders-write-read"}),
    }
    return CaseLink(**(base | overrides))  # type: ignore[arg-type]


def _basis(state: AssertionBasisState = AssertionBasisState.PRESENT_UNCONFIRMED) -> AssertionBasis:
    if state is AssertionBasisState.MISSING:
        return AssertionBasis(revision=1, state=state)
    return AssertionBasis(
        revision=1,
        state=state,
        text="订单创建后可按订单号查询到",
        text_digest="sha256:basis-1",
    )


def _case(**overrides: object) -> Case:
    base: dict[str, object] = {
        "case_id": "case-1",
        "revision": 1,
        "layer": CaseLayer.L2,
        "objective": "订单创建后可通过订单号查询到",
        "preconditions": ("已登记订单服务入口",),
        "inputs": ("有效订单参数",),
        "steps": ("调用创建接口", "按订单号查询"),
        "expected": "查询结果与创建输入一致",
        "verification_method": "独立只读查询比对",
        "independent_verification": "独立只读查询同一订单号",
        "links": _link(),
        "assertion_basis": _basis(),
        "mock_scope": (),
        "importance": CaseImportance.P1,
    }
    return Case(**(base | overrides))  # type: ignore[arg-type]


def test_case_requires_identity_and_published_revision() -> None:
    with pytest.raises(ValueError, match="case_id"):
        _case(case_id=" ")
    with pytest.raises(ValueError, match="case revision"):
        _case(revision=0)
    with pytest.raises(ValueError, match="objective"):
        _case(objective="")


def test_case_without_expected_result_must_not_be_a_complete_case() -> None:
    """没有预期结果不得标为可直接验收的完整用例（需求 P1-FR06）。"""
    with pytest.raises(ValueError, match="expected"):
        _case(expected=" ")
    with pytest.raises(ValueError, match="verification_method"):
        _case(verification_method="")


def test_case_without_preconditions_must_not_be_a_complete_case() -> None:
    with pytest.raises(ValueError, match="precondition"):
        _case(preconditions=())
    with pytest.raises(ValueError, match="preconditions"):
        _case(preconditions=("  ",))


def test_case_requires_at_least_one_step() -> None:
    with pytest.raises(ValueError, match="step"):
        _case(steps=())


def test_case_link_requires_an_acceptance_item() -> None:
    """验收项是关联链的起点。"""
    with pytest.raises(ValueError, match="acceptance item"):
        _link(acceptance_item_ids=frozenset())


def test_empty_critical_paths_require_an_explicit_reason() -> None:
    """缺清单字段仍阻止发布；确无适用链路时保存空集合及理由。"""
    with pytest.raises(ValueError, match="applicability reason"):
        _link(critical_path_ids=frozenset())
    link = _link(
        critical_path_ids=frozenset(),
        no_critical_path_reason="纯函数用例没有跨系统链路",
    )
    assert link.no_critical_path_reason is not None


def test_reason_for_empty_paths_must_not_accompany_a_non_empty_set() -> None:
    with pytest.raises(ValueError, match="must not accompany"):
        _link(no_critical_path_reason="不应出现")


def test_case_link_rejects_empty_identifiers() -> None:
    with pytest.raises(ValueError, match="module_ids"):
        _link(module_ids=frozenset({" "}))


def test_case_exposes_derived_basis_state_without_writing_it_back() -> None:
    case = _case()
    confirmation = ConfirmationRecord(
        confirmation_id="confirm-1",
        case_id="case-1",
        basis_revision=1,
        basis_text_digest="sha256:basis-1",
        confirmed_at_commit="0007",
    )
    assert (
        case.effective_basis_state([confirmation]) is AssertionBasisState.CONFIRMED
    )
    # 冻结值不变
    assert case.assertion_basis.state is AssertionBasisState.PRESENT_UNCONFIRMED


def test_publication_gate_accepts_a_complete_case() -> None:
    validate_case_publication(_case())


def test_publication_gate_rejects_missing_independent_verification() -> None:
    """缺失独立核验方式时该用例不得计入已验证。"""
    with pytest.raises(ValueError, match="independent verification"):
        validate_case_publication(_case(independent_verification=None))


def test_missing_assertion_basis_does_not_block_publication() -> None:
    """发布状态与依据确认状态分别校验。

    依据缺失阻止的是**进入 full 必测**，不是发布本身。
    """
    case = _case(assertion_basis=_basis(AssertionBasisState.MISSING))
    validate_case_publication(case)
    assert case.assertion_basis.state is AssertionBasisState.MISSING


def test_publication_gate_rejects_case_without_critical_path_or_reason() -> None:
    """空链路无理由的形态在 `CaseLink` 构造时即被拒绝，因此拼不出来。

    这里验证等价路径：带理由的空链路可通过门禁，说明"有理由"是放行条件。
    """
    with pytest.raises(ValueError, match="applicability reason"):
        _link(critical_path_ids=frozenset())
    allowed = _case(
        links=_link(
            critical_path_ids=frozenset(),
            no_critical_path_reason="纯函数用例没有跨系统链路",
        )
    )
    validate_case_publication(allowed)
