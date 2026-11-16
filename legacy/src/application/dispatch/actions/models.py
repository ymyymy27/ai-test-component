"""模型与受控发布动作

原先在 `dispatch/actions/handlers.py`；按动作族拆出，registry 直接绑定。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from aitest.application.dispatch.actions.common import _gaps_of
from aitest.application.dispatch.errors import BUseCaseError as BUseCaseError
from aitest.application.dispatch.params import (
    _as_mapping,
    _as_text,
    _command_expected_revision,
    _command_parameters,
    _command_project_id,
    _enum_of,
    _int_of,
    _optional_revision,
    _optional_text,
    _required,
    _revision_of,
    _stage_result,
)
from aitest.application.planning.draft import (
    DraftResult,
    RevisionContext,
    apply_template,
    generated_content_payload,
    load_template,
    template_draft_text,
    text_digest,
)
from aitest.application.planning.model_basis import ModelGenerationBasis
from aitest.application.planning.model_orchestration import (
    policy_record_id,
    request_model_draft,
)
from aitest.application.planning.model_response_resolution import resolve_model_response
from aitest.application.planning.proposal import (
    PLAN_PROPOSAL_SCHEMAS,
    PlanProposalError,
    parse_plan_proposal,
)
from aitest.application.planning.substrate import (
    current_record,
    read_scoped_record,
    transaction,
)
from aitest.application.planning.test_selection import (
    TEST_SELECTION_SCHEMA,
    TestSelectionError,
    materialize_test_selection,
    parse_test_selection,
)
from aitest.application.project.persistence import (
    load_project,
)
from aitest.domain.planning.model_outbound import MaterialKind, ModelOutboundPolicy, ModelTaskType
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
def handle_generate_draft(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    mode = parameters.get("generation_mode", "template")
    if mode == "model":
        return handle_model_draft(deps, command)
    if mode != "template":
        raise BUseCaseError("B_INVALID_PARAMETER", "unknown generation_mode")
    template_ref = TemplateRef(
        template_id=_as_text(
            _required(
                _as_mapping(_required(parameters, "template_ref"), "template_ref"),
                "template_id",
            ),
            "template_ref.template_id",
        ),
        version=_as_text(
            _required(
                _as_mapping(_required(parameters, "template_ref"), "template_ref"),
                "version",
            ),
            "template_ref.version",
        ),
    )
    context = _as_mapping(_required(parameters, "revision_context"), "revision_context")
    result = apply_template(
        template_ref=template_ref,
        project_id=project_id,
        revision_context=RevisionContext(
            project_revision=_revision_of(
                _required(context, "project_revision"),
                "revision_context.project_revision",
            ),
            binding_revision=_revision_of(
                _required(context, "binding_revision"),
                "revision_context.binding_revision",
            ),
            template_revision=_as_text(
                _required(context, "template_revision"),
                "revision_context.template_revision",
            ),
            environment_revision=_optional_revision(
                context.get("environment_revision"),
                "revision_context.environment_revision",
            ),
            source_revision=_optional_revision(
                context.get("source_revision"), "revision_context.source_revision"
            ),
            rules_revision=_optional_revision(
                context.get("rules_revision"), "revision_context.rules_revision"
            ),
        ),
        draft_kind=_as_text(_required(parameters, "draft_kind"), "draft_kind"),
        gaps=_gaps_of(parameters.get("gaps"), "gaps"),
        content_revision=_revision_of(
            parameters.get("content_revision") or 1, "content_revision"
        ),
    )
    if result.content is None:
        # 缺口非空：**不生成草稿**，因此也没有正文可落盘（返回缺口即结果）。
        return _draft_result(result)

    # 正文与摘要一起落盘：只存元数据会让"到底产出了什么"没有可核对的字节。
    pack = load_template(template_ref)
    draft_text = template_draft_text(pack)
    content = replace(result.content, content_digest=text_digest(draft_text))
    with transaction(deps.unit_of_work, project_id) as tx:
        staged = tx.stage_record(
            aggregate_kind="generated_content",
            record_id=content.generated_content_id,
            expected_revision=None,
            payload=generated_content_payload(content, draft_text),
        )
        tx.commit()
    return {
        "blocked": False,
        "content": _generated_content(content),
        "record": {
            "aggregate_kind": staged.aggregate_kind,
            "record_id": staged.record_id,
            "revision": staged.revision,
        },
    }

def handle_model_policy(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    if deps.model_policy_confirmations is not None:
        policy_input = dict(parameters)
        challenge = policy_input.pop("approval_challenge_id", None)
        return deps.model_policy_confirmations.save(
            project_id=project_id,
            request_id=_as_text(getattr(command, "request_id", None), "request_id"),
            intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
            expected_revision=_int_of(
                getattr(command, "expected_revision", None), "expected_revision"
            ),
            parameters=policy_input,
            challenge_id=_optional_text(challenge, "approval_challenge_id"),
        )
    load_project(
        deps.reader,
        project_id=project_id,
        revision=_revision_of(
            _required(parameters, "project_revision"),
            "project_revision",
        ),
    )
    raw = _as_mapping(_required(parameters, "policy"), "policy")
    try:
        policy = TypeAdapter(ModelOutboundPolicy).validate_python(raw)
    except (ValueError, TypeError) as error:
        raise BUseCaseError("B_INVALID_PARAMETER", "invalid model outbound policy") from error
    if policy.project_id != project_id:
        raise BUseCaseError("B_INVALID_PARAMETER", "policy belongs to another project")
    expected = _command_expected_revision(command)
    if expected is None or policy.revision != expected + 1:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", "policy revision must follow expected_revision"
        )
    with transaction(deps.unit_of_work, project_id) as tx:
        # 人工确认时间由本次核心提交决定，客户端不能伪造历史提交号。
        if policy.confirmation is not None:
            policy = replace(
                policy,
                confirmation=replace(
                    policy.confirmation,
                    confirmed_at_commit=tx.next_commit_seq(),
                ),
            )
        payload = TypeAdapter(ModelOutboundPolicy).dump_python(policy, mode="json")
        staged = tx.stage_record(
            aggregate_kind="model_outbound_policy",
            record_id=policy_record_id(project_id),
            expected_revision=None if expected == 0 else expected,
            payload=payload,
        )
        tx.commit()
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=staged.record_id,
        revision=staged.revision,
    )

def handle_model_draft(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    timeout = _command_parameters(command).get("timeout_seconds", 60)
    if type(timeout) is not int or not 10 <= timeout <= 120:
        raise BUseCaseError("B_INVALID_PARAMETER", "model timeout must be 10 to 120 seconds")
    if (
        deps.model_provider is None
        or deps.model_credentials is None
        or deps.material_projector is None
        or deps.model_responses is None
    ):
        return {"blocked": True, "status": "blocked", "blocked_by": ["model_not_configured"]}
    parameters = _command_parameters(command)
    project_id = _command_project_id(command)
    project_revision = _revision_of(
        _required(parameters, "project_revision"),
        "project_revision",
    )
    try:
        load_project(deps.reader, project_id=project_id, revision=project_revision)
    except (ValueError, TypeError, KeyError) as error:
        raise BUseCaseError("B_INVALID_PARAMETER", "saved project is unavailable") from error
    revision = _revision_of(_required(parameters, "policy_revision"), "policy_revision")
    try:
        record = read_scoped_record(
            deps.reader,
            project_id=project_id,
            aggregate_kind="model_outbound_policy",
            record_id=policy_record_id(project_id),
            revision=revision,
        )
        policy = TypeAdapter(ModelOutboundPolicy).validate_python(record.payload)
    except (ValueError, TypeError, KeyError) as error:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", "saved outbound policy is unavailable"
        ) from error
    # 最新策略的关闭/撤销立即生效，旧授权不能绕过新修订。
    latest_policy = current_record(
        deps.reader,
        project_id=project_id,
        aggregate_kind="model_outbound_policy",
        record_id=policy_record_id(project_id),
    )
    if latest_policy is None or latest_policy.revision != revision:
        return {"blocked": True, "status": "blocked", "blocked_by": ["stale_outbound_policy"]}
    if deps.model_policy_proof is not None:
        deps.model_policy_proof.validate_model_policy(
            project_id=project_id, record_revision=record.revision, payload=record.payload
        )

    def basis_is_current() -> bool:
        try:
            latest = current_record(
                deps.reader,
                project_id=project_id,
                aggregate_kind="model_outbound_policy",
                record_id=policy_record_id(project_id),
            )
            project = current_record(
                deps.reader,
                project_id=project_id,
                aggregate_kind="project",
                record_id=project_id,
            )
            if deps.model_policy_proof is not None and latest is not None:
                deps.model_policy_proof.validate_model_policy(
                    project_id=project_id,
                    record_revision=latest.revision,
                    payload=latest.payload,
                )
        except (ValueError, OSError, RuntimeError, KeyError, TypeError):
            return False
        return (
            latest is not None
            and latest.revision == revision
            and project is not None
            and project.revision == project_revision
        )

    selected = _as_mapping(_required(parameters, "selected_material"), "selected_material")
    material = {
        _enum_of(MaterialKind, key, "material_kind"): _as_text(value, "material text")
        for key, value in selected.items()
    }
    source_revision = _int_of(_required(parameters, "source_revision"), "source_revision")
    manual_revision = _int_of(
        _required(parameters, "base_manual_revision"), "base_manual_revision"
    )
    try:
        basis = ModelGenerationBasis(
            reader=deps.reader,
            project_id=project_id,
            source_revision=source_revision,
            base_manual_revision=manual_revision,
            source_ref=parameters.get("source_ref"),
            manual_ref=parameters.get("manual_ref"),
            sources=deps.source_analysis,
        )
    except (ValueError, TypeError, KeyError, OSError):
        return {"blocked": True, "status": "blocked", "blocked_by": ["model_basis_unverified"]}
    if source_revision == 0 and MaterialKind.SOURCE_SNIPPET in material:
        return {
            "blocked": True,
            "status": "blocked",
            "blocked_by": ["source_reference_required"],
        }
    output_schema = parameters.get("output_schema")
    if output_schema is not None:
        output_schema = _as_text(output_schema, "output_schema")
        if output_schema not in PLAN_PROPOSAL_SCHEMAS | {TEST_SELECTION_SCHEMA}:
            raise BUseCaseError("B_INVALID_PARAMETER", "unknown output_schema")
    template_ref = None
    raw_template = parameters.get("template_ref")
    if raw_template is not None:
        reference = _as_mapping(raw_template, "template_ref")
        template_ref = TemplateRef(
            template_id=_as_text(
                _required(reference, "template_id"), "template_ref.template_id"
            ),
            version=_as_text(
                _required(reference, "version"), "template_ref.version"
            ),
        )
    result = request_model_draft(
        project_id=project_id,
        policy=policy,
        task_type=_enum_of(ModelTaskType, _required(parameters, "task_type"), "task_type"),
        selected_material=material,
        unit_of_work=deps.unit_of_work,
        reader=deps.reader,
        projector=deps.material_projector,
        credentials=deps.model_credentials,
        caller=deps.model_provider,
        clock=deps.clock,
        source_revision=source_revision,
        base_manual_revision=manual_revision,
        generation_request_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
        project_revision=project_revision,
        draft_kind=_as_text(_required(parameters, "draft_kind"), "draft_kind"),
        binding_revision=basis.binding_revision,
        basis_is_current=basis_is_current,
        basis=basis,
        response_store=deps.model_responses,
        timeout_seconds=timeout,
        template_ref=template_ref,
        output_schema=output_schema,
    )
    draft_text: str | None = None
    if result.content is not None:
        saved_draft = read_scoped_record(
            deps.reader, project_id=project_id, aggregate_kind="generated_content",
            record_id=result.content.generated_content_id, revision=1,
        ).payload
        if (saved_draft.get("generated_content_id") != result.content.generated_content_id
                or saved_draft.get("content_digest") != result.content.content_digest):
            raise BUseCaseError("B_INVALID_PARAMETER", "saved model draft identity differs")
        raw_draft = saved_draft.get("draft_text")
        if not isinstance(raw_draft, str):
            raise BUseCaseError("B_INVALID_PARAMETER", "saved model draft text is unavailable")
        draft_text = raw_draft
        if text_digest(draft_text) != result.content.content_digest:
            raise BUseCaseError("B_INVALID_PARAMETER", "saved model draft digest differs")
    proposal = None
    proposal_error = None
    selection = None
    selection_error = None
    selection_warnings: list[str] = []
    if output_schema in PLAN_PROPOSAL_SCHEMAS and draft_text is not None:
        try:
            proposal = parse_plan_proposal(draft_text)
        except PlanProposalError as error:
            proposal_error = error.as_result()
    elif output_schema == TEST_SELECTION_SCHEMA and draft_text is not None:
        try:
            selection = parse_test_selection(draft_text)
            inventory_text = material.get(MaterialKind.CASE_CONTENT)
            if not isinstance(inventory_text, str):
                raise TestSelectionError("TEST INVENTORY material is missing")
            inventory = json.loads(inventory_text)
            if not isinstance(inventory, dict):
                raise TestSelectionError("TEST INVENTORY material is not an object")
            _, _, selection_warnings = materialize_test_selection(
                selection,
                inventory,
                project_id=project_id,
                environment_id="preview",
                scope_id="preview",
            )
        except (
            TestSelectionError, ValueError, TypeError, KeyError, json.JSONDecodeError,
        ) as error:
            selection = None
            selection_error = str(error)
    blocked = (
        result.content is None
        or proposal_error is not None
        or selection_error is not None
    )
    return {
        "draft_text": draft_text,
        "blocked": blocked,
        "status": (
            "blocked"
            if proposal_error is not None or selection_error is not None
            else result.status
        ),
        "blocked_by": (
            ["structured_output_invalid"]
            if proposal_error is not None or selection_error is not None
            else list(result.blocked_by)
        ),
        "proposal": (
            proposal.model_dump(mode="json") if proposal is not None else None
        ),
        "proposal_error": proposal_error,
        "test_selection": (
            selection.model_dump(mode="json") if selection is not None else None
        ),
        "selection_error": selection_error,
        "selection_warnings": selection_warnings,
        "response_currency": result.response_currency,
        "saved_response_ref": result.saved_response_ref,
        "content": _generated_content(result.content) if result.content is not None else None,
    }

def handle_resolve_model_response(
    deps: BUseCaseDependencies, command: object
) -> Mapping[str, object]:
    if deps.model_responses is None:
        return {
            "blocked": True,
            "status": "blocked",
            "blocked_by": ["response_store_unavailable"],
        }
    parameters = _command_parameters(command)
    if getattr(command, "expected_revision", None) != 1:
        raise BUseCaseError("B_INVALID_PARAMETER", "original intent revision must be 1")
    try:
        result = resolve_model_response(
            project_id=_command_project_id(command),
            request_id=_as_text(
                _required(parameters, "outbound_request_id"), "outbound_request_id"
            ),
            expected_revision=1,
            saved_response_ref=_as_mapping(
                _required(parameters, "saved_response_ref"), "saved_response_ref"
            ),
            reader=deps.reader,
            unit_of_work=deps.unit_of_work,
            response_store=deps.model_responses,
            sources=deps.source_analysis,
        )
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", "original model intent unavailable"
        ) from error
    return {
        "blocked": result.content is None,
        "status": result.status,
        "blocked_by": list(result.blocked_by),
        "response_currency": result.response_currency,
        "saved_response_ref": result.saved_response_ref,
        "content": _generated_content(result.content) if result.content is not None else None,
    }

def controlled_publication(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    if deps.controlled_writes is None:
        raise BUseCaseError("CAPABILITY_UNAVAILABLE", "controlled record write is unavailable")
    if getattr(command, "action", None) == "submit_delivery" and (
        getattr(command, "target", None) != _command_parameters(command).get("submission_id")
    ):
        raise BUseCaseError(
            "B_INVALID_PARAMETER", "submission target differs from its identity"
        )
    parameters = dict(_command_parameters(command))
    challenge = parameters.pop("approval_challenge_id", None)
    return deps.controlled_writes.save(
        project_id=_command_project_id(command),
        action=_as_text(getattr(command, "action", None), "action"),
        request_id=_as_text(getattr(command, "request_id", None), "request_id"),
        intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
        expected_revision=_int_of(
            getattr(command, "expected_revision", None), "expected_revision"
        ),
        parameters=parameters,
        challenge_id=_optional_text(challenge, "approval_challenge_id"),
    )

def _draft_result(result: DraftResult) -> Mapping[str, object]:
    """草稿结果：草稿与缺口**不会同时出现**，据此分支出结果。

    缺口是**正常结果**（"列缺口并阻塞"），不是错误：转成 `blocked=true` + 缺口清单。
    """
    if result.content is None:
        return {
            "blocked": True,
            "gaps": [_plain_gap(gap) for gap in result.gaps],
        }
    return {
        "blocked": False,
        "content": _generated_content(result.content),
    }

def _plain_gap(gap: object) -> Mapping[str, object]:
    return {
        "kind": _as_text(getattr(gap, "kind", None), "gap.kind"),
        "subject": _as_text(getattr(gap, "subject", None), "gap.subject"),
        "detail": _as_text(getattr(gap, "detail", None), "gap.detail"),
        "blocking": bool(getattr(gap, "blocking", True)),
    }

def _generated_content(content: object) -> Mapping[str, object]:
    template_ref = getattr(content, "template_ref", None)
    context = getattr(content, "revision_context", None)
    return {
        "generated_content_id": _as_text(
            getattr(content, "generated_content_id", None), "generated_content_id"
        ),
        "project_id": _as_text(getattr(content, "project_id", None), "project_id"),
        "draft_kind": _as_text(getattr(content, "draft_kind", None), "draft_kind"),
        "revision": _int_of(getattr(content, "revision", None), "revision"),
        "status": _as_text(getattr(content, "status", None), "status"),
        "content_digest": getattr(content, "content_digest", None),
        "template_ref": {
            "template_id": _as_text(getattr(template_ref, "template_id", None), "template_id"),
            "version": _as_text(getattr(template_ref, "version", None), "version"),
        },
        "revision_context": {
            "project_revision": _revision_of(
                getattr(context, "project_revision", None),
                "revision_context.project_revision",
            ),
            "binding_revision": _optional_revision(
                getattr(context, "binding_revision", None),
                "revision_context.binding_revision",
            ),
            "template_revision": _as_text(
                getattr(context, "template_revision", None),
                "revision_context.template_revision",
            ),
        },
    }
