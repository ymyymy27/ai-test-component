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
| `query` | 有界查询（读动作，不要求写身份） | — |

**尚未包括**（本文件末尾登记，避免读者以为已经覆盖）：`prepare_run`、`publish_rules`、
`publish_plan`、`generate_draft`、模型出站类动作。它们需要各自的参数适配，另行分批。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import wraps

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

#: 与 `aitest.bootstrap.Handler` 形状一致（`Callable[[Command], Mapping[str, object]]`）。
#: 这里**不 import** 它，避免 `application` 依赖入口层；结构兼容由
#: `interfaces/local/b_registration.py` 的显式 cast 与 mypy 共同保证。
Handler = Callable[[object], Mapping[str, object]]

#: 本注册表拥有的动作名；`doctor` 与合同测试用它核对暴露面。
OWNED_ACTIONS: frozenset[str] = frozenset(
    {
        "save_context",
        "save_binding",
        "save_environment",
        "save_dependency_graph",
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

    actions: dict[str, Handler] = {
        "save_context": _guard(handle_save_context),
        "save_binding": _guard(handle_save_binding),
        "save_environment": _guard(handle_save_environment),
        "save_dependency_graph": _guard(handle_save_dependency_graph),
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
