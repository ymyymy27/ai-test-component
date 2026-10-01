"""B 包用例注册表：把 B 的用例接到统一入口（`aitest.local/2.0`）。

背景与边界
----------

`docs/一期工程检查-B包.md` 的 **B-01** 指出："项目/发布/准备/模型动作尚无完整核心注册"，
`docs/接口对接/进行中/AB-001-端口与保存/contract.md` 第 8.9 节给出了原因：
`bootstrap` 的 `Handler = Callable[[Command], Mapping]` **没有依赖注入参数**，
所以 `register_use_cases` 注册的 handler 拿不到工作单元与只读仓储。

本模块的解法是：**在注册时把依赖闭包进 handler**。这样

- 不改 A 的 `Handler` 类型、不改 A 的装配点（`bootstrap.py` 仍是 A 唯一所有）；
- B 的动作在**当前装配形态下就能**经 `LocalAPI.dispatch()` 跑到真实存储；
- 等 A 的装配点支持注入后，只要替换构造处，动作表与测试都不用改。

`interfaces/local/b_registration.py` 负责把本模块的产物交给 `register_use_cases`；
本模块自己**不 import** `bootstrap` / `interfaces` / `infrastructure`。

动作清单与依据
--------------

动作名取自 `contracts/commands.py` 的一期动作表；`save_context` 这一名字同时覆盖
"项目 + 模块随项目落盘"（`application/project/persistence.py` 的记录约定）。

| 动作 | 语义 | 记录类别 |
| --- | --- | --- |
| `save_context` | 保存项目（模块随项目一起） | `project` |
| `save_binding` | 保存 Git/plain 绑定 | `binding` |
| `save_environment` | 保存环境引用 | `environment` |
| `save_dependency_graph` | 保存模块依赖图 | `dependency_set` |
| `prepare_run` | 编排一次准备，产出 `PreparedRun` | `preparation_record` |
| `query` | 有界查询（读动作，不要求写身份） | — |

**尚未包括**（避免读者以为已经覆盖）：`publish_rules`、`publish_plan`、`generate_draft`、
模型出站类动作。它们需要各自的参数适配，另行分批。

`prepare_run` 的参数形状
-----------------------

`PreparationInputs` 有二十余个字段，这里**逐字**接收同名字段（`parameters` 下的键名与
`prepare_run.PreparationInputs` 一致），不做别名、不做默认值填充：

- 嵌套的 `contracts.prepared_run` 模型用 `model_validate` 解析后**原样**交给
  `PreparationInputs`，因此字段名与 `BC-001` 冻结的 `PreparedRun` 合同**只有一套**；
- 缺失或非法的字段一律 `B_INVALID_PARAMETER` 并指名**是哪个字段**（不猜、不补默认值）；
- 成功时返回 `PreparedRun` 的 DTO（`model_dump(mode="json")`），字段与 `BC-001` 一致；
- **幂等由 `prepare_run()` 自己判定**（`REUSED` / `CONFLICTED` / `NEEDS_REPREPARE`），
  本适配层不重复实现，也不吞掉 `CONFLICTED` 的异常——它经 `_guard` 变成
  `B_PREPARATION_CONFLICT`。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import wraps
from typing import TypeVar, cast

from pydantic import BaseModel, ValidationError

from aitest.application.planning.preparation import InputRevisions
from aitest.application.planning.prepare_run import PreparationInputs, prepare_run
from aitest.application.planning.substrate import (
    AggregateKind,
    ConcurrentEditError,
    IndexMaintenanceRequired,
    InvalidQueryCursor,
    PreparationConflictError,
    RecordQuery,
    RecordReader,
    UnitOfWork,
)
from aitest.application.ports import Clock
from aitest.application.project.persistence import (
    dependency_graph_record_id,
    save_binding,
    save_dependency_graph,
    save_environment,
    save_project,
)
from aitest.application.project.serialization import (
    binding_from_payload,
    dependency_graph_from_payload,
    environment_from_payload,
    project_from_payload,
)
from aitest.contracts.prepared_run import (
    AssertionBasisEntry,
    AuthorizationRequirement,
    BindingFormFact,
    CaseRevisionRef,
    EnvironmentRefFact,
    ExclusionEntry,
    ExecutionSourceBinding,
    FrozenCase,
    GapEntry,
    PlanRevisionRef,
    RuleVersionRef,
    RunDriverFact,
    RunTierFact,
    SkippedScopeEntry,
    SnapshotRef,
    TemplateVersionRef,
)

#: 与 `aitest.bootstrap.Handler` 形状一致（`Callable[[Command], Mapping[str, object]]`）。
#: 这里**不 import** 它，避免 `application` 依赖入口层；结构兼容由
#: `interfaces/local/b_registration.py` 的显式 cast 与 mypy 共同保证。
Handler = Callable[[object], Mapping[str, object]]

_ModelT = TypeVar("_ModelT", bound=BaseModel)
_StrEnumT = TypeVar("_StrEnumT", bound=StrEnum)

#: 本注册表拥有的动作名；`doctor` 与合同测试用它核对暴露面。
OWNED_ACTIONS: frozenset[str] = frozenset(
    {
        "save_context",
        "save_binding",
        "save_environment",
        "save_dependency_graph",
        "prepare_run",
        "query",
    }
)

_AGGREGATE_KINDS: frozenset[str] = frozenset(
    {
        "project",
        "binding",
        "module",
        "dependency_set",
        "task",
        "delivery",
        "acceptance_item",
        "environment",
        "source_snapshot",
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
    }
)


class BUseCaseError(RuntimeError):
    """带结构化错误码的用例错误。

    `LocalAPI.dispatch()` 用 `getattr(exc, "code", "INTERNAL_ERROR")` 取错误码，
    因此这里给出 `code` 属性，让失败在协议层是**明确分类**而不是 `INTERNAL_ERROR`。
    模块内**不** import `contracts.errors`：错误码是入口层的词汇，由入口侧解释。
    """

    def __init__(self, code: str, message: str) -> None:
        if not code.strip():
            raise ValueError("error code must not be empty")
        self.code = code
        super().__init__(message)


def _as_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a non-empty string")
    return value


def _as_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be an object")
    return value


def _as_limit(value: object) -> int:
    if value is None:
        return 50
    if isinstance(value, bool) or not isinstance(value, int):
        raise BUseCaseError("B_INVALID_PARAMETER", "limit must be an integer")
    if value < 1:
        raise BUseCaseError("B_INVALID_PARAMETER", "limit must be >= 1")
    return value


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _as_text(value, name)


def _stage_result(
    *,
    aggregate_kind: str,
    record_id: str,
    revision: int,
) -> Mapping[str, object]:
    """写动作的成功结果：**只报这次真正提交的事实**。

    这里**不报 `commit_seq`**：`application/project/persistence.py` 的 `save_*`
    自带 `open`/`commit` 并只返回 `StagedRevision`，提交序号在返回时已经前进，
    事后补读会拿到**下一次**的序号。宁可少报一个字段，也不报一个会误导"业务顺序"的值。
    """
    return {
        "aggregate_kind": aggregate_kind,
        "record_id": record_id,
        "revision": revision,
    }


def _command_project_id(command: object) -> str:
    return _as_text(getattr(command, "project_id", None), "project_id")


def _command_expected_revision(command: object) -> int | None:
    value: object = getattr(command, "expected_revision", None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BUseCaseError("B_INVALID_PARAMETER", "expected_revision must be an integer")
    return value


def _command_parameters(command: object) -> Mapping[str, object]:
    return _as_mapping(getattr(command, "parameters", None), "parameters")


def _command_action(command: object) -> str:
    return _as_text(getattr(command, "action", None), "action")


# ------------------------------------------------------------------ prepare_run 参数适配


def _required(parameters: Mapping[str, object], name: str) -> object:
    if name not in parameters:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"missing required parameter: {name}"
        )
    return parameters[name]


def _model_of[M: BaseModel](model: type[M], value: object, name: str) -> M:
    """按 `contracts` 的模型解析一个参数；失败时报**字段名**而不是堆栈。"""
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"invalid {name}: {error.error_count()} field error(s)"
        ) from error


def _models_of[M: BaseModel](
    model: type[M], value: object, name: str
) -> tuple[M, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a list")
    return tuple(
        _model_of(model, item, f"{name}[{index}]") for index, item in enumerate(value)
    )


def _text_list(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a list of strings")
    out: list[str] = []
    for index, item in enumerate(value):
        out.append(_as_text(item, f"{name}[{index}]"))
    return tuple(out)


def _enum_of[E: StrEnum](enum: type[E], value: object, name: str) -> E:
    text = _as_text(value, name)
    try:
        return enum(text)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum)
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"invalid {name}: {text!r}; allowed: {allowed}"
        ) from error


def _bool_of(value: object, name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a boolean")
    return value


def _int_of(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be an integer")
    return value


def _revision_of(value: object, name: str) -> int:
    """修订号：整数且 `>= 1`（`InputRevisions` 的既有约定是"修订从 1 起"）。"""
    number = _int_of(value, name)
    if number < 1:
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be >= 1")
    return number


def _optional_commit(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _as_text(value, name)


def _preparation_inputs(command: object) -> PreparationInputs:
    """把 `Command.parameters` 逐字翻成 `PreparationInputs`。

    规则：**键名与 `PreparationInputs` 一致，不做别名、不补默认值**。
    缺失或非法一律 `B_INVALID_PARAMETER` 并指名是哪一项。
    """
    project_id = _command_project_id(command)
    parameters = _command_parameters(command)
    revisions = _as_mapping(
        _required(parameters, "input_revisions"), "input_revisions"
    )
    return PreparationInputs(
        project_id=project_id,
        workspace_id=_as_text(_required(parameters, "workspace_id"), "workspace_id"),
        binding_id=_as_text(_required(parameters, "binding_id"), "binding_id"),
        binding_revision=_int_of(
            _required(parameters, "binding_revision"), "binding_revision"
        ),
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
        selected_paths=_text_list(
            _required(parameters, "selected_paths"), "selected_paths"
        ),
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
        rule_versions=_models_of(
            RuleVersionRef, parameters.get("rule_versions"), "rule_versions"
        ),
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
        git_base_commit=_optional_commit(
            parameters.get("git_base_commit"), "git_base_commit"
        ),
        git_diff_digest=_optional_commit(
            parameters.get("git_diff_digest"), "git_diff_digest"
        ),
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
        selected_case_ids=_text_list(
            parameters.get("selected_case_ids"), "selected_case_ids"
        ),
        skipped_scope=_models_of(
            SkippedScopeEntry, parameters.get("skipped_scope"), "skipped_scope"
        ),
        applicability_exclusions=_models_of(
            ExclusionEntry,
            parameters.get("applicability_exclusions"),
            "applicability_exclusions",
        ),
    )


def _guard(handler: Handler) -> Handler:
    """把已知底座异常翻译成结构化错误码。

    `LocalAPI.dispatch()` 用 `getattr(exc, "code", "INTERNAL_ERROR")` 取错误码；
    不翻译的话"修订冲突""索引待重建"会显示成 `INTERNAL_ERROR`，
    调用方无法区分"我拿到的修订旧了""索引要重建"与"实现有 bug"。
    """

    @wraps(handler)
    def wrapped(command: object) -> Mapping[str, object]:
        try:
            return handler(command)
        except BUseCaseError:
            raise
        except PreparationConflictError as error:
            raise BUseCaseError("B_PREPARATION_CONFLICT", str(error)) from error
        except ConcurrentEditError as error:
            raise BUseCaseError("B_REVISION_CONFLICT", str(error)) from error
        except IndexMaintenanceRequired as error:
            raise BUseCaseError("B_INDEX_MAINTENANCE_REQUIRED", str(error)) from error
        except InvalidQueryCursor as error:
            raise BUseCaseError("B_INVALID_QUERY_CURSOR", str(error)) from error

    return wrapped


@dataclass(frozen=True, slots=True)
class BUseCaseDependencies:
    """B 的用例在统一入口上运行所需的依赖。

    `unit_of_work` / `reader` 是 B 的窄底座协议（`substrate.py`），
    由装配点注入 A 的实现或 `substrate_adapter.py` 的转接头。
    """

    unit_of_work: UnitOfWork
    reader: RecordReader
    clock: Clock


@dataclass(frozen=True, slots=True)
class BUseCaseRegistry:
    """B 的动作表：动作名 → 已闭包依赖的 handler。"""

    actions: Mapping[str, Handler]
    owned_actions: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.actions:
            raise ValueError("B use case registry must not be empty")
        declared = set(self.owned_actions) if self.owned_actions else set(self.actions)
        unknown = set(self.actions) - declared
        if unknown:
            raise ValueError(f"actions not owned by this registry: {sorted(unknown)}")

    def as_registration(self) -> Mapping[str, Handler]:
        """交给 `register_use_cases` 的映射。"""
        return dict(self.actions)


def build_b_use_case_registry(deps: BUseCaseDependencies) -> BUseCaseRegistry:
    """按依赖构造 B 的动作表；每个 handler 都闭包了 `deps`。"""

    def handle_save_context(command: object) -> Mapping[str, object]:
        _command_project_id(command)  # 写动作必须带项目范围
        raw = _command_parameters(command).get("project")
        payload = _as_mapping(raw, "project")
        try:
            project = project_from_payload(payload)
        except ValueError as error:
            raise BUseCaseError("B_INVALID_PARAMETER", f"invalid project: {error}") from error
        staged = save_project(
            project,
            unit_of_work=deps.unit_of_work,
            expected_revision=_command_expected_revision(command),
        )
        return _stage_result(
            aggregate_kind=staged.aggregate_kind,
            record_id=staged.record_id,
            revision=staged.revision,
        )

    def handle_save_binding(command: object) -> Mapping[str, object]:
        _command_project_id(command)  # 写动作必须带项目范围
        raw = _command_parameters(command).get("binding")
        payload = _as_mapping(raw, "binding")
        try:
            binding = binding_from_payload(payload)
        except ValueError as error:
            raise BUseCaseError("B_INVALID_PARAMETER", f"invalid binding: {error}") from error
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

    def handle_save_environment(command: object) -> Mapping[str, object]:
        project_id = _command_project_id(command)
        raw = _command_parameters(command).get("environment")
        payload = _as_mapping(raw, "environment")
        try:
            environment = environment_from_payload(payload)
        except ValueError as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER", f"invalid environment: {error}"
            ) from error
        staged = save_environment(
            environment,
            project_id=project_id,
            unit_of_work=deps.unit_of_work,
            expected_revision=_command_expected_revision(command),
        )
        return _stage_result(
            aggregate_kind=staged.aggregate_kind,
            record_id=staged.record_id,
            revision=staged.revision,
        )

    def handle_save_dependency_graph(command: object) -> Mapping[str, object]:
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
            expected_revision=_command_expected_revision(command),
        )
        return _stage_result(
            aggregate_kind=staged.aggregate_kind,
            record_id=dependency_graph_record_id(project_id),
            revision=staged.revision,
        )

    def handle_query(command: object) -> Mapping[str, object]:
        project_id = _command_project_id(command)
        parameters = _command_parameters(command)
        raw_kind = parameters.get("aggregate_kind")
        aggregate_kind: AggregateKind | None = None
        if raw_kind is not None:
            kind = _as_text(raw_kind, "aggregate_kind")
            if kind not in _AGGREGATE_KINDS:
                raise BUseCaseError(
                    "B_INVALID_PARAMETER", f"unknown aggregate_kind: {kind}"
                )
            aggregate_kind = kind  # type: ignore[assignment]
        page = deps.reader.query(
            RecordQuery(
                project_id=project_id,
                aggregate_kind=aggregate_kind,
                record_id=_optional_text(parameters.get("record_id"), "record_id"),
                limit=_as_limit(parameters.get("limit")),
                cursor=_optional_text(parameters.get("cursor"), "cursor"),
            )
        )
        return {
            "items": [
                {
                    "aggregate_kind": record.aggregate_kind,
                    "record_id": record.record_id,
                    "revision": record.revision,
                }
                for record in page.items
            ],
            "next_cursor": page.next_cursor,
        }

    def handle_prepare_run(command: object) -> Mapping[str, object]:
        inputs = _preparation_inputs(command)
        prepared = prepare_run(
            inputs,
            unit_of_work=deps.unit_of_work,
            reader=deps.reader,
            clock=deps.clock,
        )
        # DTO 与 `BC-001` 冻结的 `PreparedRun` 合同**同一套字段**：直接取模型的 JSON 形态，
        # 不在这里另写一份投影（两份形状一旦分叉，正是"每包自造 API"那类问题）。
        return cast(
            "Mapping[str, object]",
            prepared.model_dump(mode="json"),
        )

    actions: dict[str, Handler] = {
        "save_context": _guard(handle_save_context),
        "save_binding": _guard(handle_save_binding),
        "save_environment": _guard(handle_save_environment),
        "save_dependency_graph": _guard(handle_save_dependency_graph),
        "prepare_run": _guard(handle_prepare_run),
        "query": _guard(handle_query),
    }
    return BUseCaseRegistry(actions=actions, owned_actions=OWNED_ACTIONS)


__all__ = [
    "OWNED_ACTIONS",
    "BUseCaseDependencies",
    "BUseCaseError",
    "BUseCaseRegistry",
    "build_b_use_case_registry",
]
