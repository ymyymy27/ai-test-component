import pytest

from aitest.domain.project.context import Delivery, SelfReport


def _delivery(**overrides: object) -> Delivery:
    base: dict[str, object] = {
        "delivery_id": "delivery-1",
        "task_id": "task-1",
        "version": "v1",
        "run_method": "pytest",
    }
    return Delivery(**(base | overrides))  # type: ignore[arg-type]


def test_delivery_rejects_overlapping_completion_lists() -> None:
    with pytest.raises(ValueError, match="must not overlap"):
        SelfReport(completed=("item",), incomplete=("item",))


def test_delivery_requires_run_method() -> None:
    with pytest.raises(ValueError, match="run_method"):
        _delivery(run_method="")


def test_self_reported_completion_is_not_verification() -> None:
    """开发自述完成与测试验证完成分别展示（P1-FR02）。

    AI 草稿/开发自述不能作为完成证据：自述非空不得使已验证范围非空。
    """
    delivery = _delivery(self_report=SelfReport(completed=("订单创建",)))
    assert delivery.has_self_reported_completion
    assert delivery.verified_in_scope == ()
    assert not delivery.is_verified
    assert delivery.unverified_scope == ()


def test_verified_scope_requires_actual_execution_facts() -> None:
    delivery = _delivery(
        self_report=SelfReport(completed=("订单创建",), incomplete=("订单取消",)),
        verified_in_scope=("订单创建",),
        unverified_scope=("订单取消",),
    )
    assert delivery.is_verified
    assert delivery.completed == ("订单创建",)
    assert delivery.incomplete == ("订单取消",)


def test_verified_and_unverified_scope_must_not_overlap() -> None:
    with pytest.raises(ValueError, match="must not overlap"):
        _delivery(verified_in_scope=("a",), unverified_scope=("a",))


def test_delivery_requires_identity_version_and_revision() -> None:
    with pytest.raises(ValueError, match="delivery_id"):
        _delivery(delivery_id=" ")
    with pytest.raises(ValueError, match="task_id"):
        _delivery(task_id="")
    with pytest.raises(ValueError, match="version"):
        _delivery(version=" ")
    with pytest.raises(ValueError, match="revision"):
        _delivery(revision=0)


def test_a_confirmed_delivery_change_forms_a_new_revision() -> None:
    """已确认交付变化形成新修订，不覆盖历史。"""
    first = _delivery()
    second = _delivery(revision=2, verified_in_scope=("订单创建",))
    assert second.revision > first.revision
    assert first.verified_in_scope == ()
