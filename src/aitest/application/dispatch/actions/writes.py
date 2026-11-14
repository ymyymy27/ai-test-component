"""普通写动作：项目/绑定/环境/依赖图/验收范围/用例/任务/交付/视图偏好。

原先作为闭包定义在 2847 行的 `usecase_registry.build_b_use_case_registry` 里；
拆出后由 registry 以 `writes.xxx(deps, command)` 绑定。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from aitest.application.dispatch.errors import BUseCaseError
from aitest.application.dispatch.params import (
    _as_mapping,
    _as_text,
    _command_expected_revision,
    _command_parameters,
    _command_project_id,
    _expected_revision_or_none,
    _int_of,
    _optional_text,
    _payload_revision_or_none,
    _require_owned_by_command,
    _required,
    _revision_of,
    _stage_result,
)
from aitest.application.planning.persistence import save_acceptance_scope, save_case
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
)
from aitest.application.project.persistence import (
    dependency_graph_record_id,
    save_binding,
    save_delivery,
    save_dependency_graph,
    save_environment,
    save_project,
    save_task,
)
from aitest.application.project.serialization import (
    binding_from_payload,
    delivery_from_payload,
    dependency_graph_from_payload,
    environment_from_payload,
    project_from_payload,
    task_from_payload,
)
from aitest.application.project.view_preference import PreferenceError, save_view_preference
from aitest.application.record_write import RecordWriteIntent
from aitest.contracts.commands import Command

if TYPE_CHECKING:
    from aitest.application.usecase_registry import BUseCaseDependencies

def ordinary_intent(deps: BUseCaseDependencies, command: object) -> RecordWriteIntent:
    if not isinstance(command, Command):
        raise BUseCaseError("B_INVALID_PARAMETER", "record writes require the command contract")
    return RecordWriteIntent.from_command(
        command, reader=deps.reader, workspace_id=deps.workspace_id,
        protector=deps.record_protector,
    )

def handle_save_context(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    record_intent = ordinary_intent(deps, command)
    original = record_intent.original()
    if original is not None:
        return _stage_result(aggregate_kind=original.aggregate_kind,
                             record_id=original.record_id, revision=original.revision)
    project_id = _command_project_id(command)
    raw = _command_parameters(command).get("project")
    payload = _as_mapping(raw, "project")
    try:
        project = project_from_payload(payload)
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", f"invalid project: {error}") from error
    if project.local_project_id != project_id:
        raise BUseCaseError("B_INVALID_PARAMETER", "project belongs to another project")
    if deps.workspace_id is not None and project.workspace_id != deps.workspace_id:
        raise BUseCaseError("B_INVALID_PARAMETER", "project belongs to another workspace")
    staged = save_project(
        project,
        unit_of_work=deps.unit_of_work,
        record_intent=record_intent,
        expected_revision=_command_expected_revision(command),
    )
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=staged.record_id,
        revision=staged.revision,
    )

def handle_save_binding(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    project_id = _command_project_id(command)
    raw = _command_parameters(command).get("binding")
    payload = _as_mapping(raw, "binding")
    try:
        binding = binding_from_payload(payload)
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", f"invalid binding: {error}") from error
    if binding.project_id != project_id:
        raise BUseCaseError("B_INVALID_PARAMETER", "binding belongs to another project")
    if deps.controlled_writes is not None:
        parameters = dict(_command_parameters(command))
        challenge = parameters.pop("approval_challenge_id", None)
        return deps.controlled_writes.save(
            project_id=project_id,
            action="save_binding",
            request_id=_as_text(getattr(command, "request_id", None), "request_id"),
            intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
            expected_revision=_int_of(
                getattr(command, "expected_revision", None), "expected_revision"
            ),
            parameters=parameters,
            challenge_id=_optional_text(challenge, "approval_challenge_id"),
        )
    staged = save_binding(
        binding,
        unit_of_work=deps.unit_of_work,
        expected_revision=_command_expected_revision(command),
    )
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=staged.record_id,
        revision=staged.revision,
    )

def handle_save_environment(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    record_intent = ordinary_intent(deps, command)
    original = record_intent.original()
    if original is not None:
        return _stage_result(aggregate_kind=original.aggregate_kind,
                             record_id=original.record_id, revision=original.revision)
    project_id = _command_project_id(command)
    raw = _command_parameters(command).get("environment")
    payload = _as_mapping(raw, "environment")
    _require_owned_by_command(
        payload, command=command, name="environment", aggregate_kind="environment"
    )
    try:
        environment = environment_from_payload(payload)
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", f"invalid environment: {error}") from error
    parameters = dict(_command_parameters(command))
    if environment.isolation_is_explicitly_disabled or "approval_challenge_id" in parameters:
        if deps.controlled_writes is None:
            from aitest.domain.approvals import ApprovalRequired

            raise ApprovalRequired(
                "non-default isolation requires its core confirmation adapter"
            )
        challenge = parameters.pop("approval_challenge_id", None)
        return deps.controlled_writes.save(
            project_id=project_id,
            action="save_environment",
            request_id=_as_text(getattr(command, "request_id", None), "request_id"),
            intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
            expected_revision=_int_of(
                getattr(command, "expected_revision", None), "expected_revision"
            ),
            parameters=parameters,
            challenge_id=_optional_text(challenge, "approval_challenge_id"),
        )
    staged = save_environment(
        environment,
        project_id=project_id,
        unit_of_work=deps.unit_of_work,
        record_intent=record_intent,
        expected_revision=_command_expected_revision(command),
    )
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=staged.record_id,
        revision=staged.revision,
    )

def handle_save_dependency_graph(
    deps: BUseCaseDependencies, command: object
) -> Mapping[str, object]:
    record_intent = ordinary_intent(deps, command)
    original = record_intent.original()
    if original is not None:
        return _stage_result(aggregate_kind=original.aggregate_kind,
                             record_id=original.record_id, revision=original.revision)
    project_id = _command_project_id(command)
    raw = _command_parameters(command).get("dependency_graph")
    payload = _as_mapping(raw, "dependency_graph")
    try:
        graph = dependency_graph_from_payload(payload)
    except ValueError as error:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"invalid dependency graph: {error}"
        ) from error
    if graph.project_id != project_id:
        raise BUseCaseError(
            "B_INVALID_PARAMETER",
            "dependency graph belongs to another project",
        )
    staged = save_dependency_graph(
        graph,
        unit_of_work=deps.unit_of_work,
        record_intent=record_intent,
        expected_revision=_command_expected_revision(command),
    )
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=dependency_graph_record_id(project_id),
        revision=staged.revision,
    )

def handle_save_acceptance(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    record_intent = ordinary_intent(deps, command)
    original = record_intent.original()
    if original is not None:
        return _stage_result(aggregate_kind=original.aggregate_kind,
                             record_id=original.record_id, revision=original.revision)
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    raw = _required(parameters, "acceptance_scope")
    payload = _as_mapping(raw, "acceptance_scope")
    _require_owned_by_command(
        payload, command=command, name="acceptance_scope", aggregate_kind="acceptance_scope"
    )
    try:
        scope = acceptance_scope_from_payload(payload)
    except ValueError as error:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"invalid acceptance_scope: {error}"
        ) from error
    staged = save_acceptance_scope(
        scope,
        project_id=project_id,
        unit_of_work=deps.unit_of_work,
        record_intent=record_intent,
        expected_revision=_command_expected_revision(command),
    )
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=staged.record_id,
        revision=staged.revision,
    )

def handle_save_case(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    record_intent = ordinary_intent(deps, command)
    original = record_intent.original()
    if original is not None:
        return _stage_result(aggregate_kind=original.aggregate_kind,
                             record_id=original.record_id, revision=original.revision)
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    payload = _as_mapping(_required(parameters, "case"), "case")
    _require_owned_by_command(payload, command=command, name="case", aggregate_kind="case")
    try:
        case = case_from_payload(payload)
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", f"invalid case: {error}") from error
    staged = save_case(
        case,
        project_id=project_id,
        unit_of_work=deps.unit_of_work,
        record_intent=record_intent,
        expected_revision=_command_expected_revision(command),
    )
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=staged.record_id,
        revision=staged.revision,
    )

def handle_save_task(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    record_intent = ordinary_intent(deps, command)
    original = record_intent.original()
    if original is not None:
        return _stage_result(aggregate_kind=original.aggregate_kind,
                             record_id=original.record_id, revision=original.revision)
    _command_project_id(command)  # 写动作必须带项目范围
    parameters = _command_parameters(command)
    payload = _as_mapping(_required(parameters, "task"), "task")
    # 检查项 B-12：任务正文自带项目，必须与命令项目一致。
    _require_owned_by_command(payload, command=command, name="task", aggregate_kind="task")
    try:
        task = task_from_payload(payload)
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", f"invalid task: {error}") from error
    staged = save_task(
        task,
        unit_of_work=deps.unit_of_work,
        record_intent=record_intent,
        expected_revision=_payload_revision_or_none(
            command,
            default=task.revision,
            name="expected_revision",
        ),
    )
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=staged.record_id,
        revision=staged.revision,
    )

def handle_save_delivery(deps: BUseCaseDependencies, command: object) -> Mapping[str, object]:
    record_intent = ordinary_intent(deps, command)
    original = record_intent.original()
    if original is not None:
        return _stage_result(aggregate_kind=original.aggregate_kind,
                             record_id=original.record_id, revision=original.revision)
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    payload = _as_mapping(_required(parameters, "delivery"), "delivery")
    # 检查项 B-12：正文自己的项目必须与命令项目一致。不能像过去那样"用命令项目
    # 覆盖正文项目"再落盘——那会把 A 项目的记录写成 B 项目的，调用方还以为存对了。
    _require_owned_by_command(
        payload, command=command, name="delivery", aggregate_kind="delivery"
    )
    try:
        delivery = delivery_from_payload(payload)
    except ValueError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", f"invalid delivery: {error}") from error
    try:
        staged = save_delivery(
            delivery,
            project_id=project_id,
            unit_of_work=deps.unit_of_work,
            record_intent=record_intent,
            reader=deps.reader,
            task_revision=_revision_of(parameters.get("task_revision", 1), "task_revision"),
            expected_revision=_payload_revision_or_none(
                command,
                default=delivery.revision,
                name="expected_revision",
            ),
        )
    except ValueError as error:
        # B-13：自述的"已验证范围"不接受写入；这是**参数问题**，不是内部错误。
        raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error
    return _stage_result(
        aggregate_kind=staged.aggregate_kind,
        record_id=staged.record_id,
        revision=staged.revision,
    )

def handle_save_project_view_preference(
    deps: BUseCaseDependencies, command: object
) -> Mapping[str, object]:
    """隐藏/恢复项目在列表中的显示（P1-AC34）。

    只写一条 `project_view_preference`：项目、绑定、运行、报告、问题与历史引用
    **都不动**，也不停任何运行；恢复只把可见性置回。只有已保存项目可被隐藏。
    """
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    unknown = set(parameters) - {"hidden"}
    if unknown:
        raise BUseCaseError(
            "B_INVALID_PARAMETER",
            f"save_project_view_preference received unknown parameters: {sorted(unknown)}",
        )
    # `hidden` 必须显式给出**真布尔**：缺省或 null 都不能等于"悄悄恢复可见"。
    raw_hidden = parameters.get("hidden")
    if type(raw_hidden) is not bool:
        raise BUseCaseError("B_INVALID_PARAMETER", "hidden must be a boolean")
    hidden = raw_hidden
    try:
        result = save_view_preference(
            project_id=project_id,
            hidden=hidden,
            unit_of_work=deps.unit_of_work,
            reader=deps.reader,
            expected_revision=_expected_revision_or_none(command),
            commit_sequence=deps.unit_of_work.next_commit_seq(),
        )
    except PreferenceError as error:
        raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error
    return {
        "saved": {
            "aggregate_kind": "project_view_preference",
            "record_id": result.project_id,
            "revision": result.revision,
            "hidden": result.hidden,
        },
        "stopped_runs": False,
        "deleted": False,
    }
