"""准备阶段动作：分析、来源检查、测试发现、回归建议、依据确认、准备运行

原先在 `dispatch/actions/handlers.py`；按动作族拆出，registry 直接绑定。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from aitest.application.dispatch.errors import BUseCaseError as BUseCaseError
from aitest.application.dispatch.params import (
    _as_mapping,
    _as_text,
    _bool_of,
    _command_parameters,
    _command_project_id,
    _enum_of,
    _int_of,
    _model_of,
    _models_of,
    _optional_commit,
    _optional_text,
    _required,
    _revision_of,
    _text_list,
)
from aitest.application.planning.preparation import InputRevisions
from aitest.application.planning.prepare_run import PreparationInputs, prepare_run
from aitest.application.planning.regression_graph import regression_scope
from aitest.application.planning.substrate import (
    read_scoped_record,
)
from aitest.application.project.persistence import (
    load_dependency_graph,
)
from aitest.application.project.test_discovery import TestDiscoveryError
from aitest.application.project.understanding import (
    ProjectUnderstandingError,
    understanding_to_payload,
)
from aitest.contracts.prepared_run import (
    AssertionBasisEntry,
    AuthorizationRequirement,
    BindingFormFact,
    BlockingReason,
    CaseRevisionRef,
    EnvironmentRefFact,
    ExclusionEntry,
    ExecutionSourceBinding,
    FrozenCase,
    GapEntry,
    PlanRevisionRef,
    PreparedRun,
    RuleVersionRef,
    RunDriverFact,
    RunTierFact,
    SkippedScopeEntry,
    SnapshotRef,
    TemplateVersionRef,
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
def handle_analyze_project(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    if deps.source_analysis is None:
        raise BUseCaseError("CAPABILITY_UNAVAILABLE", "source analysis is unavailable")
    parameters = _command_parameters(command)
    project_id = _command_project_id(command)
    binding_revision = getattr(command, "binding_revision", None)
    binding_id = _as_text(_required(parameters, "binding_id"), "binding_id")
    purpose = _as_text(_required(parameters, "purpose"), "purpose")
    source_scope = _as_text(_required(parameters, "source_scope"), "source_scope")
    selected_paths = _text_list(parameters.get("selected_paths", []), "selected_paths")
    source_result = deps.source_analysis.analyze(
        project_id=project_id,
        request_id=_as_text(getattr(command, "request_id", None), "request_id"),
        intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
        binding_id=binding_id,
        binding_revision=_int_of(binding_revision, "binding_revision"),
        expected_revision=_int_of(
            getattr(command, "expected_revision", None), "expected_revision"
        ),
        purpose=purpose,
        source_scope=source_scope,
        selected_paths=selected_paths,
        exclusion_rules=_text_list(parameters.get("exclusion_rules", []), "exclusion_rules"),
        refetch_dependencies=_text_list(
            parameters.get("refetch_dependencies", []), "refetch_dependencies"
        ),
        refetch_scope=_optional_text(parameters.get("refetch_scope"), "refetch_scope"),
    )
    if deps.project_understanding is None:
        # A narrow test harness may register analyze_project without the
        # understanding scan; production bootstrap always supplies it.
        return source_result
    snapshot_id = source_result.get("snapshot_id")
    pinned_snapshot_id = source_result.get("pinned_snapshot_id")
    if (
        not isinstance(snapshot_id, str)
        or not snapshot_id.strip()
        or not isinstance(pinned_snapshot_id, str)
        or not pinned_snapshot_id.strip()
    ):
        raise BUseCaseError(
            "B_UNDERSTANDING_UNVERIFIED",
            "source result lacks the snapshot identity required for understanding",
        )
    include_understanding = parameters.get("include_understanding", False)
    if type(include_understanding) is not bool:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", "include_understanding must be a boolean"
        )
    try:
        summary = deps.project_understanding.understand(
            project_id=project_id,
            source_snapshot_id=snapshot_id,
            pinned_snapshot_id=pinned_snapshot_id,
            scope=source_scope,
            selected_paths=selected_paths,
        )
    except ProjectUnderstandingError as error:
        raise BUseCaseError(error.code, str(error)) from error
    result = {**source_result, **summary}
    if include_understanding:
        understanding = deps.project_understanding.latest(
            project_id=project_id, scope=source_scope
        )
        if understanding is None:
            raise BUseCaseError(
                "B_UNDERSTANDING_UNVERIFIED",
                "the saved project understanding is unavailable after analysis",
            )
        workspace_id = deps.workspace_id
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise BUseCaseError(
                "B_UNDERSTANDING_UNVERIFIED", "workspace identity is unavailable"
            )
        result["understanding"] = understanding_to_payload(
            understanding, project_id=project_id, workspace_id=workspace_id
        )
    return result

def handle_check_source(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    if deps.source_analysis is None:
        raise BUseCaseError("CAPABILITY_UNAVAILABLE", "source analysis is unavailable")
    parameters = _command_parameters(command)
    return deps.source_analysis.check(
        project_id=_command_project_id(command),
        snapshot_id=_as_text(_required(parameters, "snapshot_id"), "snapshot_id"),
        revision=_int_of(_required(parameters, "revision"), "revision"),
    )

def handle_discover_tests(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    if deps.test_discovery is None:
        raise BUseCaseError("CAPABILITY_UNAVAILABLE", "test discovery is unavailable")
    parameters = _command_parameters(command)
    try:
        return deps.test_discovery.discover(
            project_id=_command_project_id(command),
            snapshot_id=_as_text(_required(parameters, "snapshot_id"), "snapshot_id"),
            revision=_int_of(_required(parameters, "revision"), "revision"),
            environment_id=_as_text(
                _required(parameters, "environment_id"), "environment_id"
            ),
            environment_revision=_int_of(
                _required(parameters, "environment_revision"), "environment_revision"
            ),
            selected_paths=_text_list(
                parameters.get("selected_paths", []), "selected_paths"
            ),
            timeout_ms=_int_of(parameters.get("timeout_ms", 120_000), "timeout_ms"),
        )
    except TestDiscoveryError as error:
        raise BUseCaseError(error.code, str(error)) from error

def handle_suggest_regression(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    """Read-only impact suggestion from an exact dependency graph and plan revision."""
    parameters = _command_parameters(command)
    project_id = _command_project_id(command)
    graph_revision = _int_of(_required(parameters, "graph_revision"), "graph_revision")
    plan_id = _as_text(_required(parameters, "plan_id"), "plan_id")
    plan_revision = _int_of(_required(parameters, "plan_revision"), "plan_revision")
    changed_files = _text_list(_required(parameters, "changed_files"), "changed_files")
    graph = load_dependency_graph(
        deps.reader, project_id=project_id, revision=graph_revision
    )
    scope = regression_scope(changed_files, graph)
    plan = read_scoped_record(
        deps.reader, project_id=project_id, aggregate_kind="plan",
        record_id=plan_id, revision=plan_revision,
    ).payload
    refs = plan.get("case_revisions")
    if not isinstance(refs, list):
        raise BUseCaseError("B_INVALID_PARAMETER", "saved plan has no case revisions")
    suggested: list[str] = []
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        case_id, revision = ref.get("case_id"), ref.get("revision")
        if not isinstance(case_id, str) or type(revision) is not int:
            continue
        case = read_scoped_record(
            deps.reader, project_id=project_id, aggregate_kind="case",
            record_id=case_id, revision=revision,
        ).payload
        links = case.get("links")
        modules = links.get("module_ids") if isinstance(links, Mapping) else None
        if isinstance(modules, list) and set(modules) & scope.affected_modules:
            suggested.append(case_id)
    return {
        "directly_changed": sorted(scope.directly_changed),
        "affected_modules": sorted(scope.affected_modules),
        "unregistered_modules": list(scope.unregistered_modules),
        "suggested_case_ids": sorted(set(suggested)),
        # A single-module project has no cross-module edge to propagate.
        # Say so explicitly instead of leaving the caller to infer it.
        "cross_module": (
            {"state": "not_applicable", "reason": "single_module_project"}
            if len(graph.modules) <= 1
            else {"state": "applicable", "reason": None}
        ),
    }

def handle_confirm_basis(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    if deps.basis_confirmations is None:
        raise BUseCaseError("CAPABILITY_UNAVAILABLE", "basis confirmation is unavailable")
    parameters = _command_parameters(command)
    try:
        return deps.basis_confirmations.confirm(
            project_id=_command_project_id(command),
            request_id=_as_text(getattr(command, "request_id", None), "request_id"),
            intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
            case_id=_as_text(_required(parameters, "case_id"), "case_id"),
            case_revision=_revision_of(
                _required(parameters, "case_revision"), "case_revision"
            ),
            basis_revision=_revision_of(
                _required(parameters, "basis_revision"), "basis_revision"
            ),
            basis_text_digest=_as_text(
                _required(parameters, "basis_text_digest"), "basis_text_digest"
            ),
            challenge_id=_optional_text(
                parameters.get("approval_challenge_id"), "approval_challenge_id"
            ),
        )
    except BUseCaseError:
        raise
    except ValueError as error:
        # 用例/依据不存在或无法读取时底层抛 ValueError（如仓储的 "unknown revision"）。
        # 这是**调用方输入**问题，必须落成有编码的错误，不能升级成 INTERNAL_ERROR——
        # 与 `handle_narrow_driver` 等既有 handler 的处理方式一致。
        #
        # 但 `BasisConfirmationError`／`BasisConfirmationConflict` 也继承 `ValueError`，
        # 且**自带业务编码**（`B_BASIS_UNVERIFIED`／`INTENT_CONFLICT`）。这些编码必须原样
        # 保留：压成 `B_INVALID_PARAMETER` 会让调用方无法区分「输入非法」与
        # 「同一意图换了输入」，也会掩盖「依据未核验」。因此只对**没有业务编码**的
        # ValueError 归为输入非法。
        code = getattr(error, "code", None)
        if isinstance(code, str) and code:
            raise BUseCaseError(code, str(error)) from error
        raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error

def handle_prepare_run(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    from aitest.application.errors import CapabilityUnavailable
    from aitest.application.planning.basis_validation import validate_prepared_material
    from aitest.application.planning.preparation import preparation_identity_digest
    from aitest.application.project.source_analysis import SourceAnalysisError

    if _command_parameters(command).get("mode") == "from_saved":
        from aitest.application.planning.saved_preparation import from_saved

        if deps.environment_resolution is None or deps.workspace_id is None:
            raise CapabilityUnavailable("trusted preparation environment is unavailable")
        try:
            inputs = from_saved(
                _command_parameters(command), project_id=_command_project_id(command),
                workspace_id=deps.workspace_id, reader=deps.reader,
                environments=deps.environment_resolution,
            )
        except (ValueError, TypeError, KeyError) as error:
            if getattr(error, "code", None):
                raise
            raise BUseCaseError(
                "B_INVALID_PARAMETER",
                "saved preparation references or execution input are invalid",
            ) from error
    else:
        inputs = _preparation_inputs(command)
    existing = None
    preparation = deps.reader.find_preparation(
        project_id=inputs.project_id,
        client_id=inputs.client_id,
        prepare_request_id=inputs.prepare_request_id,
    )
    if preparation is not None:
        snapshot_id = "prepared-" + preparation_identity_digest(
            project_id=inputs.project_id,
            client_id=inputs.client_id,
            prepare_request_id=inputs.prepare_request_id,
        )
        try:
            saved = read_scoped_record(
                deps.reader,
                project_id=inputs.project_id,
                aggregate_kind="prepared_run",
                record_id=snapshot_id,
                revision=1,
            )
            if saved.payload.get("project_id") != inputs.project_id:
                raise BUseCaseError(
                    "B_INVALID_PARAMETER", "prepared snapshot has another owner"
                )
            existing = PreparedRun.model_validate_json(json.dumps(dict(saved.payload)))
        except (OSError, ValueError):
            existing = None
    source_reasons: tuple[BlockingReason, ...] = ()
    environment_reasons: tuple[BlockingReason, ...] = ()
    try:
        if deps.environment_resolution is None:
            raise ValueError("trusted tested environment resolver is not configured")
        environment = deps.environment_resolution.resolve(
            project_id=inputs.project_id,
            binding_id=inputs.binding_id,
            binding_revision=inputs.binding_revision,
            environment_id=inputs.environment.environment_id,
            environment_revision=inputs.environment.revision,
        )
        # The old request fields are compatibility input, never observed authority.
        inputs = replace(inputs, environment=environment)
    except (ValueError, OSError, KeyError, TypeError) as error:
        environment_reasons = (
            BlockingReason(code="environment_unverified", message=str(error)),
        )
    if deps.source_analysis is not None:
        try:
            source = deps.source_analysis.check(
                project_id=inputs.project_id,
                snapshot_id=inputs.snapshot.source_snapshot_id,
                revision=inputs.snapshot.record_revision,
            )
            changes = _as_mapping(source.get("changes"), "source.changes")
            if (
                changes.get("state") != "unchanged"
                or source.get("binding_state") != "unchanged"
                or source.get("git_state") not in {"not_applicable", "unchanged"}
            ):
                source_reasons = (
                    BlockingReason(
                        code="needs_reprepare",
                        message="Actual source or binding changed/unverified.",
                    ),
                )
        except (SourceAnalysisError, CapabilityUnavailable):
            source_reasons = (
                BlockingReason(
                    code="basis_unverified", message="Actual source cannot be verified."
                ),
            )
    prepared = prepare_run(
        inputs,
        unit_of_work=deps.unit_of_work,
        reader=deps.reader,
        clock=deps.clock,
        existing=existing,
        persist_snapshot=True,
        basis_verifier=lambda candidate: (
            source_reasons
            + environment_reasons
            + validate_prepared_material(
                candidate,
                reader=deps.reader,
                workspace_id=deps.workspace_id,
                approvals=deps.basis_confirmation_proof,
                controlled_writes=deps.controlled_write_proof,
                environment_resolution=deps.environment_resolution,
            )
        ),
    )
    # DTO 与 `BC-001` 冻结的 `PreparedRun` 合同**同一套字段**：直接取模型的 JSON 形态，
    # 不在这里另写一份投影（两份形状一旦分叉，正是"每包自造 API"那类问题）。
    return cast(
        "Mapping[str, object]",
        prepared.model_dump(mode="json"),
    )

def _preparation_inputs(command: object) -> PreparationInputs:
    """把 `Command.parameters` 逐字翻成 `PreparationInputs`。

    规则：**键名与 `PreparationInputs` 一致，不做别名、不补默认值**。
    缺失或非法一律 `B_INVALID_PARAMETER` 并指名是哪一项。
    """
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    revisions = _as_mapping(_required(parameters, "input_revisions"), "input_revisions")
    return PreparationInputs(
        scope_id=_as_text(_required(parameters, "scope_id"), "scope_id"),
        project_id=project_id,
        workspace_id=_as_text(_required(parameters, "workspace_id"), "workspace_id"),
        binding_id=_as_text(_required(parameters, "binding_id"), "binding_id"),
        binding_revision=_int_of(_required(parameters, "binding_revision"), "binding_revision"),
        binding_form=_enum_of(
            BindingFormFact, _required(parameters, "binding_form"), "binding_form"
        ),
        client_id=_as_text(_required(parameters, "client_id"), "client_id"),
        prepare_request_id=_as_text(
            _required(parameters, "prepare_request_id"), "prepare_request_id"
        ),
        input_revisions=InputRevisions(
            project_revision=_revision_of(
                _required(revisions, "project_revision"), "input_revisions.project_revision"
            ),
            binding_revision=_revision_of(
                _required(revisions, "binding_revision"), "input_revisions.binding_revision"
            ),
            snapshot_revision=_revision_of(
                _required(revisions, "snapshot_revision"), "input_revisions.snapshot_revision"
            ),
            environment_revision=_revision_of(
                _required(revisions, "environment_revision"),
                "input_revisions.environment_revision",
            ),
            plan_revision=_revision_of(
                _required(revisions, "plan_revision"), "input_revisions.plan_revision"
            ),
            rules_revision=_revision_of(
                _required(revisions, "rules_revision"), "input_revisions.rules_revision"
            ),
            template_revision=_revision_of(
                _required(revisions, "template_revision"),
                "input_revisions.template_revision",
            ),
            scope_revision=_revision_of(
                _required(revisions, "scope_revision"), "input_revisions.scope_revision"
            ),
        ),
        snapshot=_model_of(SnapshotRef, _required(parameters, "snapshot"), "snapshot"),
        selected_paths=_text_list(_required(parameters, "selected_paths"), "selected_paths"),
        environment=_model_of(
            EnvironmentRefFact, _required(parameters, "environment"), "environment"
        ),
        execution_source=_model_of(
            ExecutionSourceBinding,
            _required(parameters, "execution_source"),
            "execution_source",
        ),
        plan_revision=_model_of(
            PlanRevisionRef, _required(parameters, "plan_revision"), "plan_revision"
        ),
        acceptance_scope_revision=_int_of(
            _required(parameters, "acceptance_scope_revision"),
            "acceptance_scope_revision",
        ),
        rule_versions=_models_of(RuleVersionRef, parameters.get("rule_versions"), "rule_versions"),
        template_versions=_models_of(
            TemplateVersionRef, parameters.get("template_versions"), "template_versions"
        ),
        case_revisions=_models_of(
            CaseRevisionRef, parameters.get("case_revisions"), "case_revisions"
        ),
        frozen_cases=_models_of(FrozenCase, parameters.get("frozen_cases"), "frozen_cases"),
        assertion_bases=_models_of(
            AssertionBasisEntry, parameters.get("assertion_bases"), "assertion_bases"
        ),
        context_gaps=_models_of(GapEntry, parameters.get("context_gaps"), "context_gaps"),
        authorization_requirements=_models_of(
            AuthorizationRequirement,
            parameters.get("authorization_requirements"),
            "authorization_requirements",
        ),
        model_outbound_policy_revision=(
            None
            if parameters.get("model_outbound_policy_revision") is None
            else _int_of(
                parameters["model_outbound_policy_revision"],
                "model_outbound_policy_revision",
            )
        ),
        source_snippets_enabled=_bool_of(
            parameters.get("source_snippets_enabled"), "source_snippets_enabled"
        ),
        exclusion_rules=_text_list(parameters.get("exclusion_rules"), "exclusion_rules"),
        refetch_dependencies=_text_list(
            parameters.get("refetch_dependencies"), "refetch_dependencies"
        ),
        git_base_commit=_optional_commit(parameters.get("git_base_commit"), "git_base_commit"),
        git_diff_digest=_optional_commit(parameters.get("git_diff_digest"), "git_diff_digest"),
        plain_manifest_digest=_optional_commit(
            parameters.get("plain_manifest_digest"), "plain_manifest_digest"
        ),
        run_tier=(
            RunTierFact.QUICK
            if parameters.get("run_tier") is None
            else _enum_of(RunTierFact, parameters["run_tier"], "run_tier")
        ),
        initial_driver=(
            RunDriverFact.PLANNED
            if parameters.get("initial_driver") is None
            else _enum_of(RunDriverFact, parameters["initial_driver"], "initial_driver")
        ),
        template_required_case_ids=_text_list(
            parameters.get("template_required_case_ids"), "template_required_case_ids"
        ),
        frozen_required_case_ids=_text_list(
            parameters.get("frozen_required_case_ids"), "frozen_required_case_ids"
        ),
        selected_case_ids=_text_list(parameters.get("selected_case_ids"), "selected_case_ids"),
        skipped_scope=_models_of(
            SkippedScopeEntry, parameters.get("skipped_scope"), "skipped_scope"
        ),
        applicability_exclusions=_models_of(
            ExclusionEntry,
            parameters.get("applicability_exclusions"),
            "applicability_exclusions",
        ),
    )
