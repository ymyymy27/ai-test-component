"""面板主屏视图（架构 06 §1 PanelDTO 的可落地部分：当前段 + 信息区事实）。

分册原文：

- 架构 06 §1：`PanelDTO` 必需字段组＝「项目/绑定/运行上下文、**当前段**、源修订、信息区、
  `primary_action`/原因、可用动作」，生产方「应用查询与 `interfaces/dto.py`」；
  「视图**不直接读文件、调用适配器或重算判定**」；
- 架构 06 §28：「主屏使用 `primary_action` 及原因 DTO **导航到现有用例**」；
- 设计分册 §3：运行时「情形 → **主动作** → 行为」表（断言失败→查看证据/处理问题；
  环境阻塞→配置该环境；依据未确认→核对依据；停止未确认/副作用未知→核实执行状态；
  待人工操作→继续当前步骤）；
- 设计分册 §2：运行前主按钮随目的为「运行快速检查/运行所选检查/运行完整验证」，计划未确认或关键输入
  缺失时**不发起**、就地显示阻塞原因。

本文件锁定：①`stage` 只由已提交 `control_state` 判定（`not_started`→运行前；运行中七态→运行时；
`completed`/`cancelled`/`execution_error`→运行后），且回执带上判定依据；②主屏"情形"事实全部用
**既有词汇**（`decisive_failure_case_ids`、`gap_codes` = `ReviewGapCode`、待人工/待核实步骤），
不在 DTO 里自造情形枚举或重算判定；③`primary_action` 的动作标识未冻结 → 不给候选（不猜）。
"""

from __future__ import annotations

from typing import Any

import pytest

from aitest.application.review.execution_decision_adapter import build_decision_facts
from aitest.domain.review.reports import evaluate_review
from aitest.interfaces.dto import PanelViewDTO, panel_view_dto
from tests.unit.test_run_view_dto import _facts as _run_facts


def _panel(
    steps: tuple[tuple[str, str, str, str | None, bool], ...],
    *,
    control_state: str,
    selected: tuple[str, ...] = ("case-1",),
    required: tuple[str, ...] | None = None,
) -> PanelViewDTO:
    from aitest.contracts.execution_facts import ExecutionFacts
    from aitest.interfaces.dto import run_view_dto

    raw = _run_facts(steps, selected=selected, required=required).model_dump(mode="json")
    raw["run"]["control_state"] = control_state
    facts = ExecutionFacts.model_validate(raw)
    decision = evaluate_review(build_decision_facts(facts))
    return panel_view_dto(facts, decision, run_view_dto(facts, decision))


def test_stage_follows_the_committed_control_state() -> None:
    steps = (("step-1", "case-1", "pending", None, False),)
    for control_state, expected in (
        ("not_started", "pre_run"),
        ("running", "runtime"),
        ("paused", "runtime"),
        ("pending_verification", "runtime"),
        ("completed", "post_run"),
        ("cancelled", "post_run"),
        ("execution_error", "post_run"),
    ):
        view = _panel(steps, control_state=control_state)
        assert view.stage == expected, control_state
        # 判定依据随回执给出，界面/排错不用猜。
        assert view.control_state == control_state


def test_unknown_control_state_is_refused_instead_of_guessed() -> None:
    """未知控制状态在**事实层**就被拒（枚举受控），DTO 不会按"有没有记录"猜段。"""

    steps = (("step-1", "case-1", "pending", None, False),)
    with pytest.raises(ValueError):
        _panel(steps, control_state="something-else")


def test_panel_carries_the_frozen_case_facts_for_the_design_tables() -> None:
    """情形事实用既有词汇：断言失败用例、缺口码、待人工/待核实步骤。"""

    view = _panel(
        (
            ("step-1", "case-1", "running", "manual_evidence", False),
            ("step-2", "case-1", "pending_verification", "http", False),
        ),
        control_state="running",
        selected=("case-1",),
        required=("case-1",),
    )
    assert isinstance(view, PanelViewDTO)
    assert view.manual_pending_step_ids == ("step-1",)
    assert view.pending_verification_step_ids == ("step-2",)
    # 缺口码取自领域判定（本例：未执行 ⇒ 无有效执行依据／未完成范围）。
    assert "no_valid_execution_basis" in view.gap_codes
    assert view.decisive_failure_case_ids == ()
    # 运行上下文与配对边界同样带出（§4：不能先读视图再取"此刻最新"游标）。
    assert view.snapshot_commit_id and view.snapshot_cursor >= 0
    assert view.tier == "full" and view.driver == "stepwise"


def test_panel_projects_a_decisive_failure_by_case_id() -> None:
    """断言失败情形：`decisive_failure_case_ids` 非空（设计 §3 的"断言失败"行）。"""

    from aitest.application.review.execution_decision_adapter import (
        assertion_outcomes_from_facts,
    )
    from tests.unit.test_assertion_outcome_producer import _facts as _producer_facts
    from tests.unit.test_assertion_outcome_producer import _verification

    facts = _producer_facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1", observation="mismatched"),),
        selected=("case-1",),
    )
    outcomes = assertion_outcomes_from_facts(
        facts, basis_revision_for=lambda _case: 1, basis_confirmed_for=lambda _case: True
    )
    decision = evaluate_review(build_decision_facts(facts, assertion_outcomes=outcomes))
    from aitest.interfaces.dto import run_view_dto

    view = panel_view_dto(facts, decision, run_view_dto(facts, decision))
    assert view.decisive_failure_case_ids == ("case-1",)
    assert view.business_outcome == "failed"


def test_panel_view_is_json_serializable_and_has_no_action_candidates() -> None:
    """`primary_action` 的动作标识未冻结：DTO 不给候选，避免自造动作名。"""

    view = _panel(
        (("step-1", "case-1", "completed", "command", False),),
        control_state="completed",
    )
    payload = view.model_dump(mode="json")
    assert payload["schema_version"] == "aitest.panel-view/1.0"
    assert payload["stage"] == "post_run"
    assert "primary_action" not in payload
    assert isinstance(payload["gap_codes"], list)


def _unused(value: Any) -> None:  # pragma: no cover - 保持 import 语义清晰
    return None
