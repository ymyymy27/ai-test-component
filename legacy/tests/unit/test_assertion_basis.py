"""断言依据三态与派生规则。

依据架构文档《01-项目与计划》第 3 节：派生值不回写冻结 Case；
不能将文本已改变的旧确认带到新依据。
"""

from collections.abc import Sequence

import pytest

from aitest.domain.planning.plans import (
    AssertionBasis,
    AssertionBasisState,
    ConfirmationRecord,
    effective_assertion_basis_state,
)


def _basis(**overrides: object) -> AssertionBasis:
    base: dict[str, object] = {
        "revision": 1,
        "state": AssertionBasisState.PRESENT_UNCONFIRMED,
        "text": "订单创建后可通过订单号查询到",
        "text_digest": "sha256:basis-1",
    }
    return AssertionBasis(**(base | overrides))  # type: ignore[arg-type]


def _confirmation(**overrides: object) -> ConfirmationRecord:
    base: dict[str, object] = {
        "confirmation_id": "confirm-1",
        "case_id": "case-1",
        "basis_revision": 1,
        "basis_text_digest": "sha256:basis-1",
        "confirmed_at_commit": "0007",
    }
    return ConfirmationRecord(**(base | overrides))  # type: ignore[arg-type]


def _state(
    basis: AssertionBasis, confirmations: Sequence[ConfirmationRecord]
) -> AssertionBasisState:
    """本模块的派生调用都以 `case-1` 为主体；跨用例反例单独写。"""
    return effective_assertion_basis_state(basis, confirmations, case_id="case-1")


def test_missing_basis_must_not_carry_text() -> None:
    AssertionBasis(revision=1, state=AssertionBasisState.MISSING)
    with pytest.raises(ValueError, match="must not carry text"):
        _basis(state=AssertionBasisState.MISSING)


def test_present_basis_requires_text_and_digest() -> None:
    with pytest.raises(ValueError, match="assertion basis text"):
        _basis(text="")
    with pytest.raises(ValueError, match="text_digest"):
        _basis(text_digest="")


def test_basis_revision_must_be_published() -> None:
    with pytest.raises(ValueError, match="revision"):
        _basis(revision=0)


def test_confirmation_binds_revision_and_digest() -> None:
    with pytest.raises(ValueError, match="basis_revision"):
        _confirmation(basis_revision=0)
    with pytest.raises(ValueError, match="basis_text_digest"):
        _confirmation(basis_text_digest=" ")
    with pytest.raises(ValueError, match="confirmed_at_commit"):
        _confirmation(confirmed_at_commit="")


def test_missing_stays_missing_even_with_confirmations() -> None:
    """缺失无法被确认补齐。"""
    basis = AssertionBasis(revision=1, state=AssertionBasisState.MISSING)
    assert (
        _state(basis, [_confirmation()])
        is AssertionBasisState.MISSING
    )


def test_matching_confirmation_derives_confirmed() -> None:
    assert (
        _state(_basis(), [_confirmation()])
        is AssertionBasisState.CONFIRMED
    )


def test_no_confirmation_derives_present_unconfirmed() -> None:
    assert (
        _state(_basis(), [])
        is AssertionBasisState.PRESENT_UNCONFIRMED
    )


def test_changed_basis_text_invalidates_the_old_confirmation() -> None:
    """依据文本变化后旧确认失效（架构文档第 3 节）。"""
    basis_v2 = _basis(revision=2, text_digest="sha256:basis-2")
    assert (
        _state(basis_v2, [_confirmation()])
        is AssertionBasisState.PRESENT_UNCONFIRMED
    )
    # 同一修订但文本摘要不同，同样失效
    basis_same_revision_new_text = _basis(text_digest="sha256:basis-other")
    assert (
        _state(basis_same_revision_new_text, [_confirmation()])
        is AssertionBasisState.PRESENT_UNCONFIRMED
    )


def test_reconfirmation_of_the_new_revision_restores_confirmed() -> None:
    basis_v2 = _basis(revision=2, text_digest="sha256:basis-2")
    fresh = _confirmation(basis_revision=2, basis_text_digest="sha256:basis-2")
    assert (
        _state(basis_v2, [fresh])
        is AssertionBasisState.CONFIRMED
    )


def test_derivation_does_not_mutate_the_frozen_basis() -> None:
    """派生值不回写冻结 Case（架构文档第 3 节）。"""
    basis = _basis()
    before = (basis.revision, basis.state, basis.text, basis.text_digest)
    _state(basis, [_confirmation()])
    assert (basis.revision, basis.state, basis.text, basis.text_digest) == before


def test_derivation_returns_a_state_and_no_count() -> None:
    """补充确认不能直接把已验证数加一：派生函数不返回计数。"""
    result = _state(_basis(), [_confirmation()])
    assert isinstance(result, AssertionBasisState)


def test_confirmation_matching_is_exact_on_both_fields() -> None:
    basis = _basis()
    assert _confirmation().matches(basis, case_id="case-1")
    assert not _confirmation(basis_revision=2).matches(basis, case_id="case-1")
    assert not _confirmation(basis_text_digest="sha256:other").matches(basis, case_id="case-1")


# ------------------------------------------------- B-CONFIRMATION-01 反例


def test_a_confirmation_from_another_case_does_not_confirm_this_case() -> None:
    """反例：`case-1` 无确认，只传入 `other-case` 的同修订同摘要确认。

    **必须仍为 `present_unconfirmed`**。修前 `matches()` 只比修订与摘要，
    别的用例的确认会把本用例判成 `confirmed`——这是把"别人的确认"算成本用例的
    依据有效性证据，属伪造有效确认。
    """
    other_case_confirmation = _confirmation(case_id="other-case")
    assert (
        effective_assertion_basis_state(
            _basis(), [other_case_confirmation], case_id="case-1"
        )
        is AssertionBasisState.PRESENT_UNCONFIRMED
    )
    # 同一份确认对自己的用例仍然有效。
    assert (
        effective_assertion_basis_state(
            _basis(), [other_case_confirmation], case_id="other-case"
        )
        is AssertionBasisState.CONFIRMED
    )


def test_case_derives_its_own_state_from_its_own_confirmations() -> None:
    """`Case.effective_basis_state()` 只认本用例自己的确认。"""
    from tests.support.prepared_run_factory import build_scenario  # noqa: PLC0415

    case = build_scenario("git").cases[0]
    foreign = ConfirmationRecord(
        confirmation_id="confirm-foreign",
        case_id="another-case",
        basis_revision=case.assertion_basis.revision,
        basis_text_digest=case.assertion_basis.text_digest,
        confirmed_at_commit="0007",
    )
    assert case.effective_basis_state([foreign]) is AssertionBasisState.PRESENT_UNCONFIRMED

    own = ConfirmationRecord(
        confirmation_id="confirm-own",
        case_id=case.case_id,
        basis_revision=case.assertion_basis.revision,
        basis_text_digest=case.assertion_basis.text_digest,
        confirmed_at_commit="0007",
    )
    assert case.effective_basis_state([own, foreign]) is AssertionBasisState.CONFIRMED
