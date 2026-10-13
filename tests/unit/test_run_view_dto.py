"""运行视图 DTO（架构 06 §1/§5）：事实 + 同一次领域判定 → RunDTO。

分册要点：

- §1 DTO 归属表：`RunDTO` 必需字段组＝「Run/Step/currentAttempt、自动步骤完成/总数、
  用例 S/M 两组 E/R/V/P/F/U、待人工步骤、阻塞/待核实、有效驱动与修订」，生产方为
  "execution 查询"；「视图不直接读文件、调用适配器或重算判定」。
- §5 统计投影增量：运行 DTO 含 `selected_case_ids(S)`、`required_case_ids(M)`、
  E/R/V/P/F/U 计数「及各自 denominator」；「分别返回 `selected_summary` 与 `required_summary`，
  **缺字段显示未知，不以步骤数推算**」。

本文件锁定：S/M 与 E/R/V/P/F/U 只来自领域 `Coverage`/`DecisionResult`（不重算）、
两套摘要各自带分母、步骤行只投影已提交事实、无当前尝试时尝试状态为 `None`（未知而非推断）、
自动/人工划分按适配器事实。
"""

from __future__ import annotations

from typing import Any

from aitest.application.review.execution_decision_adapter import build_decision_facts
from aitest.domain.review.reports import evaluate_review
from aitest.interfaces.dto import run_view_dto

RUN = "run-1"
PROJECT = "project-1"


def _attempt(
    attempt_id: str,
    *,
    step_id: str,
    adapter_kind: str = "command",
    state: str = "completed",
) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "run_id": RUN,
        "step_id": step_id,
        "attempt_revision": 1,
        "attempt_index": 1,
        "retry_count": 0,
        "state": state,
        "is_current": True,
        "resolved_input_digest": "sha256:input-1",
        "step_revision_ref": {
            "step_revision_id": f"{step_id}-rev",
            "revision_no": 1,
            "digest": f"sha256:{step_id}",
        },
        "source_binding_digest": "sha256:binding-1",
        "side_effect_class": "unknown",
        "adapter_kind": adapter_kind,
        "adapter_version": "1.0.0",
        "capture_completeness": "complete",
    }


def _facts(
    steps: tuple[tuple[str, str, str, str | None, bool], ...],
    *,
    selected: tuple[str, ...],
    required: tuple[str, ...] | None = None,
) -> Any:
    """steps：`(step_id, case_id, state, adapter_kind|None, invalidated)`。"""

    from aitest.contracts.execution_facts import ExecutionFacts

    attempts: list[dict[str, Any]] = []
    current: dict[str, str | None] = {}
    for step_id, _case_id, state, adapter_kind, _invalidated in steps:
        attempt_id = None if adapter_kind is None else f"attempt-{step_id}"
        if attempt_id is not None:
            attempt_state = (
                "completed"
                if state == "completed"
                else "pending_verification"
                if state == "pending_verification"
                else "running"
            )
            attempts.append(
                _attempt(
                    attempt_id, step_id=step_id, adapter_kind=adapter_kind, state=attempt_state
                )
            )
        current[step_id] = attempt_id
    return ExecutionFacts.model_validate(
        {
            "facts_id": f"facts-{RUN}",
            "project_id": PROJECT,
            "run_id": RUN,
            "snapshot_commit_id": "commit-9",
            "snapshot_cursor": 9,
            "snapshot_revision": 1,
            "committed_at": "2026-10-13T09:00:00+00:00",
            "run_revision": 4,
            "plan_revision": {
                "revision_id": "plan-1",
                "revision_no": 2,
                "digest": "sha256:plan-1",
            },
            "runtime_revision_refs": ["runtime-1@1"],
            "run": {
                "run_id": RUN,
                "run_revision": 4,
                "origin_workspace_id": "workspace-1",
                "intent_id": "intent-1",
                "tier": "full",
                "driver": "stepwise",
                "conclusion_ceiling": "passable",
                "plan_revision": {
                    "revision_id": "plan-1",
                    "revision_no": 2,
                    "digest": "sha256:plan-1",
                },
                "environment_ref": "environment-1@1",
                "environment_isolation_mode": "venv",
                "rules_revision": "rules-1@1",
                "control_state": "running",
                "required_scope": list(required if required is not None else selected),
                "selected_scope": list(selected),
            },
            "steps": [
                {
                    "step_id": step_id,
                    "run_id": RUN,
                    "step_revision": 1,
                    "step_revision_ref": {
                        "step_revision_id": f"{step_id}-rev",
                        "revision_no": 1,
                        "digest": f"sha256:{step_id}",
                    },
                    "ordinal": index,
                    "case_id": case_id,
                    "required_for_case": True,
                    "level": "L2",
                    "state": "invalidated" if invalidated else state,
                    "invalidated": invalidated,
                    "assertion_refs": [f"assertion-{step_id}"],
                    "current_attempt_id": current[step_id],
                }
                for index, (step_id, case_id, state, _adapter, invalidated) in enumerate(
                    steps, start=1
                )
            ],
            "attempts": attempts,
            "current_attempt_by_step": current,
        }
    )


def _dto(steps: tuple[tuple[str, str, str, str | None, bool], ...], **scopes: Any) -> Any:
    facts = _facts(steps, **scopes)
    return run_view_dto(facts, evaluate_review(build_decision_facts(facts)))


def test_run_view_carries_scope_groups_and_both_summaries_with_denominators() -> None:
    dto = _dto(
        (
            ("step-1", "case-1", "completed", "command", False),
            ("step-2", "case-2", "running", "manual_evidence", False),
            ("step-3", "case-3", "blocked", "command", False),
        ),
        selected=("case-1", "case-2", "case-3"),
        required=("case-1", "case-2"),
    )
    assert dto.run_id == RUN
    assert dto.project_id == PROJECT
    assert dto.run_revision == 4
    assert dto.snapshot_commit_id == "commit-9" and dto.snapshot_cursor == 9
    assert dto.plan_revision_ref == "plan-1@2"
    assert dto.runtime_revision_refs == ("runtime-1@1",)
    assert dto.driver == "stepwise" and dto.control_state == "running"
    assert dto.tier == "full" and dto.conclusion_ceiling == "passable"
    # S/M 两组与 E/R/V/P/F/U 只来自领域覆盖。
    assert dto.selected_case_ids == ("case-1", "case-2", "case-3")
    assert dto.required_case_ids == ("case-1", "case-2")
    assert dto.executed_case_ids == ("case-1",)  # 只有 case-1 有已完成当前尝试
    assert dto.reused_case_ids == ()
    assert dto.verified_case_ids == () and dto.passed_case_ids == ()
    assert dto.failed_case_ids == () and dto.unverified_case_ids == ("case-1", "case-2", "case-3")
    assert dto.coverage.selected_applicable_count == 3
    assert dto.coverage.required_applicable_count == 2
    assert dto.coverage.executed_count == 1
    # 两套摘要各自带分母，未验证数按范围算（不跨范围混算）。
    assert dto.selected_summary.scope_kind == "selected"
    assert dto.selected_summary.denominator == 3
    assert dto.selected_summary.executed_count == 1
    assert dto.selected_summary.unverified_count == 3
    assert dto.required_summary.scope_kind == "required"
    assert dto.required_summary.denominator == 2
    assert dto.required_summary.executed_count == 1
    assert dto.required_summary.unverified_count == 2


def test_run_view_projects_steps_and_splits_automatic_from_manual() -> None:
    dto = _dto(
        (
            ("step-1", "case-1", "completed", "command", False),
            ("step-2", "case-2", "running", "manual_evidence", False),
            ("step-3", "case-3", "blocked", "command", False),
            ("step-4", "case-3", "pending_verification", "http", False),
            ("step-5", "case-3", "completed", "command", True),
        ),
        selected=("case-1", "case-2", "case-3"),
        required=("case-1",),
    )
    assert [row.step_id for row in dto.steps] == [
        "step-1",
        "step-2",
        "step-3",
        "step-4",
        "step-5",
    ]
    first = dto.steps[0]
    assert (first.case_id, first.state, first.attempt_state, first.adapter_kind) == (
        "case-1",
        "completed",
        "completed",
        "command",
    )
    assert first.current_attempt_id == "attempt-step-1"
    # 人工适配器：进入"待人工"，且不计入自动步骤。
    assert dto.steps[1].adapter_kind == "manual_evidence"
    assert dto.manual_pending_step_ids == ("step-2",)
    assert dto.automatic_step_count == 4
    assert dto.automatic_completed_count == 1  # step-5 已失效，不算完成
    assert dto.blocked_step_ids == ("step-3",)
    assert dto.pending_verification_step_ids == ("step-4",)
    assert dto.invalidated_step_ids == ("step-5",)
    assert dto.steps[4].invalidated is True
    assert dto.steps[4].state == "invalidated"


def test_missing_current_attempt_is_unknown_not_inferred() -> None:
    """事实里没有当前尝试时，尝试状态与适配器都是 `None`（未知），不用状态倒推。"""

    dto = _dto(
        (("step-1", "case-1", "pending", None, False),),
        selected=("case-1",),
    )
    row = dto.steps[0]
    assert row.current_attempt_id is None
    assert row.attempt_state is None
    assert row.adapter_kind is None
    # 未知适配器的步骤不计入自动完成；范围里也没有已执行用例。
    assert dto.automatic_completed_count == 0
    assert dto.executed_case_ids == ()
    assert dto.selected_summary.unverified_count == 1


def test_run_view_is_json_serializable_and_carries_no_domain_objects() -> None:
    dto = _dto(
        (("step-1", "case-1", "completed", "command", False),),
        selected=("case-1",),
    )
    payload = dto.model_dump(mode="json")
    assert payload["schema_version"] == "aitest.run-view/1.0"
    assert payload["steps"][0]["step_id"] == "step-1"
    assert payload["selected_summary"]["scope_kind"] == "selected"
