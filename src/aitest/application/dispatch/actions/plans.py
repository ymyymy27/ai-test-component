"""计划动作：保存/发布计划与确认状态

原先在 `dispatch/actions/handlers.py`；按动作族拆出，registry 直接绑定。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from aitest.application.dispatch.actions.common import (
    _gaps_of,
    _publication_result,
)
from aitest.application.dispatch.actions.models import controlled_publication
from aitest.application.dispatch.actions.rules import _rule_versions_for
from aitest.application.dispatch.errors import BUseCaseError as BUseCaseError
from aitest.application.dispatch.params import (
    _as_mapping,
    _as_text,
    _command_parameters,
    _command_project_id,
    _enum_of,
    _enum_or,
    _expected_revision_or_none,
    _int_of,
    _optional_text,
    _require_owned_by_command,
    _required,
    _revision_of,
)
from aitest.application.planning.plan_builder import build_plan
from aitest.application.planning.plan_confirmation import (
    PlanConfirmationError,
)
from aitest.application.planning.publish import (
    publish_plan,
    save_plan_draft,
)
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
    confirmation_from_payload,
)
from aitest.domain.planning.plans import (
    Case,
    ConfirmationRecord,
    Plan,
    PlanPublicationStatus,
    RuleRevisionRef,
    RunDriver,
    RunTier,
)
from aitest.domain.planning.plans import (
    CaseRevisionRef as DomainCaseRevisionRef,
)
from aitest.domain.planning.plans import (
    TemplateVersionRef as DomainTemplateVersionRef,
)
from aitest.domain.planning.templates import TemplateRef

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
def handle_save_plan(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    """保存计划**草稿**（功能 P1-FR06"草稿/发布独立"）。

    与 `publish_plan` 的分工只有一条：这里**不跑发布门禁**，草稿允许尚未补齐；
    发布仍走 `publish_plan` 的逐条门禁并产生带确认标识的下一修订。身份、修订与
    "已发布计划不可当草稿覆盖"仍必须核对。
    """
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    plan_id = _as_text(_required(parameters, "plan_id"), "plan_id")
    revision = _revision_of(_required(parameters, "revision"), "revision")
    scope_payload = _as_mapping(_required(parameters, "scope"), "scope")
    _require_owned_by_command(
        scope_payload, command=command, name="scope", aggregate_kind="acceptance_scope"
    )
    scope = acceptance_scope_from_payload(scope_payload)
    raw_cases = _required(parameters, "cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise BUseCaseError("B_INVALID_PARAMETER", "cases must be a non-empty list")
    case_values = []
    for index, item in enumerate(raw_cases):
        payload = _as_mapping(item, f"cases[{index}]")
        _require_owned_by_command(
            payload, command=command, name=f"cases[{index}]", aggregate_kind="case"
        )
        case_values.append(case_from_payload(payload))
    cases = tuple(case_values)
    plan = build_plan(
        plan_id=plan_id,
        revision=revision,
        scope=scope,
        cases=cases,
        project_id=project_id,
        rule_versions=_rule_versions_for(deps, project_id=project_id, parameters=parameters),
        template_refs=_template_refs_for(parameters),
        run_tier=_enum_or(RunTier, parameters.get("run_tier"), "run_tier", RunTier.FULL),
        initial_driver=_enum_or(
            RunDriver,
            parameters.get("initial_driver"),
            "initial_driver",
            RunDriver.PLANNED,
        ),
    )
    try:
        staged = save_plan_draft(
            plan,
            project_id=project_id,
            cases=cases,
            unit_of_work=deps.unit_of_work,
            reader=deps.reader,
            expected_revision=_expected_revision_or_none(command),
        )
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error
    return {
        "saved": {
            "aggregate_kind": staged.aggregate_kind,
            "record_id": staged.record_id,
            "revision": staged.revision,
            "plan_id": plan.plan_id,
            "status": plan.status.value,
        },
        "published": False,
    }

def handle_publish_plan(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    if deps.controlled_writes is None and parameters.get("coverage_contract") is not None:
        raise BUseCaseError(
            "CAPABILITY_UNAVAILABLE",
            "coverage contract requires controlled plan publication",
        )
    plan_id = _as_text(_required(parameters, "plan_id"), "plan_id")
    revision = _revision_of(_required(parameters, "revision"), "revision")
    scope_payload = _as_mapping(_required(parameters, "scope"), "scope")
    _require_owned_by_command(
        scope_payload, command=command, name="scope", aggregate_kind="acceptance_scope"
    )
    scope = acceptance_scope_from_payload(scope_payload)
    raw_cases = _required(parameters, "cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise BUseCaseError("B_INVALID_PARAMETER", "cases must be a non-empty list")
    case_values = []
    for index, item in enumerate(raw_cases):
        payload = _as_mapping(item, f"cases[{index}]")
        _require_owned_by_command(
            payload, command=command, name=f"cases[{index}]", aggregate_kind="case"
        )
        case_values.append(case_from_payload(payload))
    cases = tuple(case_values)
    if deps.controlled_writes is not None:
        raw_grant = parameters.get("plan_grant")
        if raw_grant is not None and deps.plan_confirmations is None:
            raise BUseCaseError(
                "CAPABILITY_UNAVAILABLE",
                "plan confirmation with an execution grant is not configured",
            )
        controlled = controlled_publication(deps, command)
        if raw_grant is None:
            return controlled
        assert deps.plan_confirmations is not None
        request_id = _as_text(getattr(command, "request_id", None), "request_id")
        record_revision = _int_of(
            controlled.get("record_revision"), "record_revision"
        )
        try:
            derived = deps.plan_confirmations.derive(
                project_id=project_id,
                request_id=request_id,
                intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
                plan_id=plan_id,
                plan_record_revision=record_revision,
                cases=cast(Sequence[Mapping[str, object]], raw_cases),
            )
        except PlanConfirmationError as error:
            with suppress(OSError, ValueError, TypeError, KeyError):
                deps.plan_confirmations.mark_state(
                    project_id=project_id,
                    request_id=request_id,
                    plan_id=plan_id,
                    plan_revision=record_revision,
                    state="derivation_failed",
                    reason=str(error),
                )
            raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error
        deps.plan_confirmations.mark_state(
            project_id=project_id,
            request_id=request_id,
            plan_id=plan_id,
            plan_revision=record_revision,
            state="ready",
            grant_id=str(derived["grant_id"]),
        )
        return dict(controlled) | dict(derived)
    if parameters.get("run_tier", "full") == "full":
        raise BUseCaseError(
            "B_INVALID_PARAMETER",
            "full validation requires controlled publication with a coverage contract",
        )
    plan = build_plan(
        plan_id=plan_id,
        revision=revision,
        scope=scope,
        cases=cases,
        project_id=project_id,
        rule_versions=_rule_versions_for(deps, project_id=project_id, parameters=parameters),
        template_refs=_template_refs_for(parameters),
        run_tier=_enum_or(RunTier, parameters.get("run_tier"), "run_tier", RunTier.FULL),
        initial_driver=_enum_or(
            RunDriver,
            parameters.get("initial_driver"),
            "initial_driver",
            RunDriver.PLANNED,
        ),
    )
    result = publish_plan(
        plan,
        project_id=project_id,
        cases=cases,
        unit_of_work=deps.unit_of_work,
        reader=deps.reader,
        expected_revision=_expected_revision_or_none(command),
        context_gaps=_gaps_of(parameters.get("context_gaps"), "context_gaps"),
    )
    return _publication_result(result, published_kind="plan")

def handle_plan_confirmation_state(
    deps: BUseCaseDependencies, command: object
) -> Mapping[str, object]:
    if deps.plan_confirmations is None:
        raise BUseCaseError(
            "CAPABILITY_UNAVAILABLE", "plan confirmation state is unavailable"
        )
    parameters = _command_parameters(command)
    try:
        return deps.plan_confirmations.state(
            project_id=_command_project_id(command),
            plan_id=_as_text(_required(parameters, "plan_id"), "plan_id"),
            plan_revision=_revision_of(
                _required(parameters, "plan_revision"), "plan_revision"
            ),
        )
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error

def _case_revision_refs_of(raw: object, name: str) -> tuple[DomainCaseRevisionRef, ...]:
    refs: list[DomainCaseRevisionRef] = []
    for index, item in enumerate(_list_of({name: raw} if raw is not None else {}, name)):
        entry = _as_mapping(item, f"{name}[{index}]")
        refs.append(
            DomainCaseRevisionRef(
                case_id=_as_text(_required(entry, "case_id"), f"{name}[{index}].case_id"),
                revision=_revision_of(
                    _required(entry, "revision"), f"{name}[{index}].revision"
                ),
                digest=_as_text(_required(entry, "digest"), f"{name}[{index}].digest"),
            )
        )
    return tuple(refs)

def _cases_from_payload(raw: object) -> tuple[Case, ...]:
    """被改动用例的**冻结修订**对象列表；至少一条。"""
    if not isinstance(raw, list) or not raw:
        raise BUseCaseError("B_INVALID_PARAMETER", "cases must be a non-empty list")
    cases: list[Case] = []
    for index, item in enumerate(raw):
        try:
            cases.append(case_from_payload(_as_mapping(item, f"cases[{index}]")))
        except ValueError as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER", f"invalid cases[{index}]: {error}"
            ) from error
    return tuple(cases)

def _confirmations_from_payload(raw: object) -> tuple[ConfirmationRecord, ...]:
    """依据确认记录列表；可以没有（未确认是合法状态）。"""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BUseCaseError("B_INVALID_PARAMETER", "confirmations must be a list")
    confirmations: list[ConfirmationRecord] = []
    for index, item in enumerate(raw):
        try:
            confirmations.append(
                confirmation_from_payload(
                    _as_mapping(item, f"confirmations[{index}]")
                )
            )
        except ValueError as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER", f"invalid confirmations[{index}]: {error}"
            ) from error
    return tuple(confirmations)

def _list_of(parameters: Mapping[str, object], name: str) -> list[object]:
    """可选列表参数：缺省为空列表，给了就必须是 list。"""
    value = parameters.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a list")
    return value

def _plan_from_payload(payload: Mapping[str, object]) -> Plan:
    """按发布口径重建**冻结计划**（运行中修订的对照基线）。

    只重建身份与冻结引用，不重算任何摘要；缺字段或违反领域不变量即拒绝。
    """
    try:
        return Plan(
            plan_id=_as_text(_required(payload, "plan_id"), "plan.plan_id"),
            revision=_revision_of(_required(payload, "revision"), "plan.revision"),
            scope=acceptance_scope_from_payload(
                _as_mapping(_required(payload, "scope"), "plan.scope")
            ),
            case_revisions=_case_revision_refs_of(
                payload.get("case_revisions"), "plan.case_revisions"
            ),
            rule_revisions=_rule_revision_refs_of(
                payload.get("rule_revisions"), "plan.rule_revisions"
            ),
            template_versions=_template_version_refs_of(
                payload.get("template_versions"), "plan.template_versions"
            ),
            run_tier=_enum_of(RunTier, _required(payload, "run_tier"), "plan.run_tier"),
            initial_driver=_enum_of(
                RunDriver, _required(payload, "initial_driver"), "plan.initial_driver"
            ),
            status=_enum_of(
                PlanPublicationStatus, _required(payload, "status"), "plan.status"
            ),
            confirmation_id=_optional_text(
                payload.get("confirmation_id"), "plan.confirmation_id"
            ),
        )
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", f"invalid plan: {error}") from error

def _rule_revision_refs_of(raw: object, name: str) -> tuple[RuleRevisionRef, ...]:
    refs: list[RuleRevisionRef] = []
    for index, item in enumerate(_list_of({name: raw} if raw is not None else {}, name)):
        entry = _as_mapping(item, f"{name}[{index}]")
        refs.append(
            RuleRevisionRef(
                rule_id=_as_text(_required(entry, "rule_id"), f"{name}[{index}].rule_id"),
                revision=_revision_of(
                    _required(entry, "revision"), f"{name}[{index}].revision"
                ),
                digest=_as_text(_required(entry, "digest"), f"{name}[{index}].digest"),
            )
        )
    return tuple(refs)

def _template_version_refs_of(
    raw: object, name: str
) -> tuple[DomainTemplateVersionRef, ...]:
    refs: list[DomainTemplateVersionRef] = []
    for index, item in enumerate(_list_of({name: raw} if raw is not None else {}, name)):
        entry = _as_mapping(item, f"{name}[{index}]")
        refs.append(
            DomainTemplateVersionRef(
                template_id=_as_text(
                    _required(entry, "template_id"), f"{name}[{index}].template_id"
                ),
                version=_as_text(
                    _required(entry, "version"), f"{name}[{index}].version"
                ),
                digest=_as_text(_required(entry, "digest"), f"{name}[{index}].digest"),
            )
        )
    return tuple(refs)

def _template_refs_for(parameters: Mapping[str, object]) -> tuple[TemplateRef, ...]:
    """按 `{template_id, version}` 解析模板引用。"""
    raw = parameters.get("template_versions")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BUseCaseError("B_INVALID_PARAMETER", "template_versions must be a list")
    refs: list[TemplateRef] = []
    for index, item in enumerate(raw):
        entry = _as_mapping(item, f"template_versions[{index}]")
        refs.append(
            TemplateRef(
                template_id=_as_text(
                    _required(entry, "template_id"),
                    f"template_versions[{index}].template_id",
                ),
                version=_as_text(
                    _required(entry, "version"), f"template_versions[{index}].version"
                ),
            )
        )
    return tuple(refs)
