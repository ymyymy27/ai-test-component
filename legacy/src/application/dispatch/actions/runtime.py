"""运行中修订动作

原先在 `dispatch/actions/handlers.py`；按动作族拆出，registry 直接绑定。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from aitest.application.dispatch.actions.plans import (
    _cases_from_payload,
    _confirmations_from_payload,
    _plan_from_payload,
)
from aitest.application.dispatch.errors import BUseCaseError as BUseCaseError
from aitest.application.dispatch.params import (
    _as_mapping,
    _as_text,
    _bool_of,
    _command_parameters,
    _enum_of,
    _int_of,
    _model_of,
    _optional_text,
    _required,
    _revision_of,
    _text_list,
)
from aitest.application.planning.run_mode import request_runtime_revision
from aitest.application.planning.serialization import (
    case_from_payload,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.planning.plans import (
    RunDriver,
    narrow_driver,
)
from aitest.domain.planning.runtime_revision import (
    CaseRuntimeChange,
    RuntimeRevisionDecision,
    RuntimeRevisionRequest,
)

_AGGREGATE_KINDS: frozenset[str] = frozenset(
    {
        "project",
        "binding",
        "module",
        "dependency_set",
        "task",
        "delivery",
        "delivery_submission",
        "acceptance_item",
        "environment",
        "source_snapshot",
        "source_pin_intent",
        "source_binding_current",
        "template_ref",
        "generated_content",
        "rule_draft",
        "rule_version",
        "case",
        "case_link",
        "plan",
        "acceptance_scope",
        "preparation_record",
        "model_outbound_policy",
        "model_outbound_request",
        "prepared_run",
        "project_view_preference",
        # 人工补证记录（只含人工描述与引用，不含证据正文）：列表可读，正文按引用取。
        "manual_evidence",
        # 本地复核记录：按报告修订独立保存，供复核追溯与导出引用。
        "local_review",
        # 复用选择记录：面板读取当前目标运行的已选复用修订后再提交新选择。
        "reuse_decision",
        # 报告修订：面板「问题与报告」页按项目列报告；索引层已有 reports 分区。
        "report",
    }
)


if TYPE_CHECKING:
    from aitest.application.usecase_registry import BUseCaseDependencies
def handle_narrow_driver(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    parameters = _command_parameters(command)
    try:
        current = RunDriver(_as_text(_required(parameters, "current"), "current"))
        requested = RunDriver(
            _as_text(_required(parameters, "requested"), "requested")
        )
        result = narrow_driver(current, requested)
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error
    return {"driver": result.value}

def handle_revise_pending_steps(
    deps: BUseCaseDependencies, command: object
) -> Mapping[str, object]:
    parameters = _command_parameters(command)
    plan = _plan_from_payload(_as_mapping(_required(parameters, "plan"), "plan"))
    cases = _cases_from_payload(_required(parameters, "cases"))
    confirmations = _confirmations_from_payload(
        _required(parameters, "confirmations")
    )
    request = _runtime_revision_request_from_payload(
        _as_mapping(_required(parameters, "request"), "request")
    )
    facts = _model_of(ExecutionFacts, _required(parameters, "facts"), "facts")
    decision = request_runtime_revision(
        plan=plan,
        cases=cases,
        confirmations=confirmations,
        request=request,
        facts=facts,
    )
    return _runtime_revision_result(decision)

def _runtime_revision_request_from_payload(
    payload: Mapping[str, object],
) -> RuntimeRevisionRequest:
    """运行中修订请求；`case_changes` 至少一条（领域不变量）。"""
    raw_changes = _required(payload, "case_changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", "request.case_changes must be a non-empty list"
        )
    changes: list[CaseRuntimeChange] = []
    for index, item in enumerate(raw_changes):
        prefix = f"request.case_changes[{index}]"
        entry = _as_mapping(item, prefix)
        try:
            next_case = case_from_payload(
                _as_mapping(_required(entry, "next_case"), f"{prefix}.next_case")
            )
        except ValueError as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER", f"invalid {prefix}.next_case: {error}"
            ) from error
        changes.append(
            CaseRuntimeChange(
                next_case=next_case,
                target_step_ids=_text_list(
                    entry.get("target_step_ids"), f"{prefix}.target_step_ids"
                ),
                remove_from_required=_bool_of(
                    entry.get("remove_from_required"), f"{prefix}.remove_from_required"
                ),
            )
        )
    try:
        return RuntimeRevisionRequest(
            base_plan_revision_id=_as_text(
                _required(payload, "base_plan_revision_id"),
                "request.base_plan_revision_id",
            ),
            base_plan_revision_no=_revision_of(
                _required(payload, "base_plan_revision_no"),
                "request.base_plan_revision_no",
            ),
            base_plan_revision_digest=_optional_text(
                payload.get("base_plan_revision_digest"),
                "request.base_plan_revision_digest",
            ),
            observed_snapshot_cursor=_int_of(
                _required(payload, "observed_snapshot_cursor"),
                "request.observed_snapshot_cursor",
            ),
            case_changes=tuple(changes),
            reason=_as_text(_required(payload, "reason"), "request.reason"),
            operator_ref=_as_text(
                _required(payload, "operator_ref"), "request.operator_ref"
            ),
            requested_driver=(
                None
                if payload.get("requested_driver") is None
                else _enum_of(
                    RunDriver, payload["requested_driver"], "request.requested_driver"
                )
            ),
        )
    except ValueError as error:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"invalid request: {error}"
        ) from error

def _runtime_revision_result(decision: RuntimeRevisionDecision) -> Mapping[str, object]:
    """运行中修订决策：只搬运交接清单，不产生任何业务结论。"""
    return {
        "accepted": decision.accepted,
        "revision_no": decision.revision_no,
        "effective_driver": decision.effective_driver.value,
        "snapshot_commit_id": decision.snapshot_commit_id,
        "snapshot_cursor": decision.snapshot_cursor,
        "refusals": [
            {"code": refusal.code.value, "detail": refusal.detail}
            for refusal in decision.refusals
        ],
        "affected_step_ids": list(decision.affected_step_ids),
        "preserved_step_ids": list(decision.preserved_step_ids),
        "invalidated_basis_step_ids": list(decision.invalidated_basis_step_ids),
        "rejudge_case_ids": list(decision.rejudge_case_ids),
        "confirmation_required_case_ids": list(
            decision.confirmation_required_case_ids
        ),
        "pause_required": decision.pause_required,
        "new_run_required": decision.new_run_required,
    }
