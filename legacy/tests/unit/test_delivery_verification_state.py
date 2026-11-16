"""交付说明的核验状态：功能 §14 三态词汇 + BD-001 §7.2 展示要求。

分册原文：

- 功能 §14：「**`verification_state` 表示必要核验未完成、材料无效或核验有效**。三个状态
  不能由一个'待核实'布尔值替代」；
- BD-001 §7.2：「开发自述完成与测试验证完成**分列两处**」；「`verified_in_scope` 为空而
  `self_report.completed` 非空时，必须显示为**待验证**，不得显示为已完成」；「AI 草稿不作为
  完成证据」。

本文件锁定：①`verification_state` 只取冻结的三个取值（词汇由测试逐字钉住）；②有已验证范围
⇒ 核验有效；③只有自述完成、没有验证范围 ⇒ **必要核验未完成**（不是"已完成"，也不是
中性布尔）；④自述与验证字段结构分离，自述内容不进入 `is_verified`。
"""

from __future__ import annotations

import pytest

from aitest.domain.project.context import Delivery, SelfReport, VerificationState


def _delivery(**changes: object) -> Delivery:
    base: dict[str, object] = {
        "delivery_id": "delivery-1",
        "task_id": "task-1",
        "version": "0.1",
        "run_method": "python main.py",
    }
    base.update(changes)
    return Delivery(**base)  # type: ignore[arg-type]


def test_vocabulary_is_exactly_the_frozen_three_values() -> None:
    """功能 §14 的三个取值逐字一致；不得再出现 "unverified" 这类自造值。"""

    assert {member.value for member in VerificationState} == {
        "required_verification_incomplete",
        "material_invalid",
        "verification_valid",
    }


def test_self_reported_completion_does_not_make_verification_valid() -> None:
    """BD-001 §7.2：自述称完成而验证范围为空 ⇒ 必要核验未完成（待验证）。"""

    delivery = _delivery(self_report=SelfReport(completed=("developer says done",)))
    assert delivery.has_self_reported_completion is True
    assert delivery.is_verified is False
    assert delivery.verification_state is VerificationState.REQUIRED_VERIFICATION_INCOMPLETE


def test_verified_scope_makes_the_state_valid_regardless_of_self_report() -> None:
    """验证事实由执行侧投影；自述为空或未完成都不影响"核验有效"。"""

    valid = _delivery(verified_in_scope=("ticket-created",))
    assert valid.verification_state is VerificationState.VERIFICATION_VALID

    validated_with_incomplete_self_report = _delivery(
        verified_in_scope=("ticket-created",),
        unverified_scope=("ticket-rejected",),
        self_report=SelfReport(incomplete=("ticket-rejected",)),
    )
    assert (
        validated_with_incomplete_self_report.verification_state
        is VerificationState.VERIFICATION_VALID
    )


def test_empty_scope_without_self_report_is_still_incomplete_not_verified() -> None:
    delivery = _delivery()
    assert delivery.has_self_reported_completion is False
    assert delivery.verification_state is VerificationState.REQUIRED_VERIFICATION_INCOMPLETE


def test_self_report_and_verification_stay_structurally_separate() -> None:
    """自述与验证字段分离：自述不会写进 verified_in_scope，交集必须为空。"""

    delivery = _delivery(
        self_report=SelfReport(completed=("a",), incomplete=("b",)),
        verified_in_scope=("c",),
        unverified_scope=("d",),
    )
    assert delivery.verified_in_scope == ("c",)
    assert delivery.unverified_scope == ("d",)
    assert set(delivery.verified_in_scope) & set(delivery.unverified_scope) == set()

    with pytest.raises(ValueError):
        _delivery(verified_in_scope=("x",), unverified_scope=("x",))
