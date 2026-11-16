"""B 包用例注册表：把 B 的用例接到统一入口（`aitest.local/2.0`）。

背景与边界
----------

`docs/一期工程检查-B包.md` 的 **B-01** 指出："项目/发布/准备/模型动作尚无完整核心注册"，
`docs/接口对接/已完成/AB-001-端口与保存/contract.md` 第 8.9 节给出了原因：
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
| `generate_draft` | 应用模板生成草稿（缺口非空即阻塞，不生成） | — |
| `publish_rules` | 发布规则草稿为 `RuleVersion` | `rule_version` |
| `prepare_run` | 编排一次准备，产出 `PreparedRun` | `preparation_record` |
| `query` | 有界查询（读动作，不要求写身份） | — |

此外已注册计划发布、模型策略/生成、独立用例/范围/任务/交付及来源固定/核对。
这些动作复用领域参数适配与端口；是否可用仍取决于实际依赖与能力门禁。

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
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from aitest.application.controlled_write import ControlledWriteService
from aitest.application.dispatch.actions import (
    models,
    plans,
    prepare,
    reads,
    rules,
    runtime,
    writes,
)
from aitest.application.dispatch.actions.rules import _rule_versions_for
from aitest.application.dispatch.errors import BUseCaseError
from aitest.application.planning.basis_confirmation import BasisConfirmationService
from aitest.application.planning.model_orchestration import (
    ModelGenerationConflictError,
)
from aitest.application.planning.model_policy_confirmation import ModelPolicyConfirmationService
from aitest.application.planning.model_ports import (
    CredentialResolver,
    MaterialProjector,
    ModelCaller,
)
from aitest.application.planning.plan_confirmation import (
    PlanConfirmationService,
)
from aitest.application.planning.substrate import (
    ConcurrentEditError,
    IndexMaintenanceRequired,
    InvalidQueryCursor,
    PreparationConflictError,
    RecordReader,
    UnitOfWork,
)
from aitest.application.ports import (
    BasisConfirmationProof,
    Clock,
    ControlledWriteProof,
    ModelPolicyConfirmationProof,
    ModelResponseStore,
    RecordValueProtector,
)
from aitest.application.project.environment_resolution import EnvironmentResolutionService
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.application.project.test_discovery import TestDiscoveryService
from aitest.application.project.understanding import (
    ProjectUnderstandingService,
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
        "revise_pending_steps",
        "narrow_driver",
        "save_context",
        "bind_project",
        "save_binding",
        "save_environment",
        "save_dependency_graph",
        "save_acceptance",
        "save_case",
        "save_task",
        "save_delivery",
        "submit_delivery",
        "save_project_view_preference",
        "generate_draft",
        "resolve_model_response",
        "save_model_outbound_policy",
        # AB-001 方案 2 的注册缺口收口：合同点名的三个动作由本注册表拥有。
        "request_model_draft",
        "export_rules",
        "export_rules_markdown",
        "import_rules",
        "import_rules_markdown",
        "save_rules",
        "publish_rules",
        "save_plan",
        "publish_plan",
        "plan_confirmation_state",
        "prepare_run",
        "query",
        "analyze_project",
        "discover_tests",
        "suggest_regression",
        "check_source",
        "confirm_basis",
    }
)

# ------------------------------------------------------------------ prepare_run 参数适配


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
        except ValidationError as error:
            raise BUseCaseError("B_INVALID_PARAMETER", str(error)) from error
        except PreparationConflictError as error:
            raise BUseCaseError("B_PREPARATION_CONFLICT", str(error)) from error
        except ModelGenerationConflictError as error:
            raise BUseCaseError("B_MODEL_INTENT_CONFLICT", str(error)) from error
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
    model_provider: ModelCaller | None = None
    model_credentials: CredentialResolver | None = None
    material_projector: MaterialProjector | None = None
    workspace_id: str | None = None
    source_analysis: SourceAnalysisService | None = None
    test_discovery: TestDiscoveryService | None = None
    project_understanding: ProjectUnderstandingService | None = None
    basis_confirmations: BasisConfirmationService | None = None
    plan_confirmations: PlanConfirmationService | None = None
    basis_confirmation_proof: BasisConfirmationProof | None = None
    model_responses: ModelResponseStore | None = None
    model_policy_confirmations: ModelPolicyConfirmationService | None = None
    model_policy_proof: ModelPolicyConfirmationProof | None = None
    controlled_writes: ControlledWriteService | None = None
    controlled_write_proof: ControlledWriteProof | None = None
    environment_resolution: EnvironmentResolutionService | None = None
    record_protector: RecordValueProtector | None = None


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

    actions: dict[str, Handler] = {
        "revise_pending_steps": _guard(
            lambda command: runtime.handle_revise_pending_steps(deps, command)
        ),
        "narrow_driver": _guard(lambda command: runtime.handle_narrow_driver(deps, command)),
        "save_context": _guard(lambda command: writes.handle_save_context(deps, command)),
        # 合同名优先（架构05 第3节动作表把「目录绑定：规范化当前目录、选择/建立稳定项目、
        # 取得 binding_revision」定为 `bind_project`）。两个名字都在 PHASE_ONE_ACTIONS 内，
        # `Command` 会强制 project_id + intent_id + expected_revision，写身份校验不再有表外空档。
        "bind_project": _guard(lambda command: writes.handle_save_binding(deps, command)),
        # 兼容别名：既有调用方与测试仍可用。
        "save_binding": _guard(lambda command: writes.handle_save_binding(deps, command)),
        "save_environment": _guard(lambda command: writes.handle_save_environment(deps, command)),
        "save_dependency_graph": _guard(
            lambda command: writes.handle_save_dependency_graph(deps, command)
        ),
        "save_acceptance": _guard(lambda command: writes.handle_save_acceptance(deps, command)),
        "save_project_view_preference": _guard(
            lambda command: writes.handle_save_project_view_preference(deps, command)
        ),
        "save_case": _guard(lambda command: writes.handle_save_case(deps, command)),
        "save_task": _guard(lambda command: writes.handle_save_task(deps, command)),
        "save_delivery": _guard(lambda command: writes.handle_save_delivery(deps, command)),
        "submit_delivery": _guard(lambda command: models.controlled_publication(deps, command)),
        "generate_draft": _guard(lambda command: models.handle_generate_draft(deps, command)),
        "resolve_model_response": _guard(
            lambda command: models.handle_resolve_model_response(deps, command)
        ),
        "save_model_outbound_policy": _guard(
            lambda command: models.handle_model_policy(deps, command)
        ),
        # AB-001 方案 2：合同把 `request_model_draft`、`revise_pending_steps`、`narrow_driver`
        # 记为「注册缺口」，要求由 B 提供 handler、A 装配。本处按**合同动作名**登记既有
        # handler（`handle_model_draft` 此前只定义未登记）。
        "request_model_draft": _guard(lambda command: models.handle_model_draft(deps, command)),
        "export_rules": _guard(lambda command: rules.handle_export_rules(deps, command)),
        "export_rules_markdown": _guard(
            lambda command: rules.handle_export_rules_markdown(deps, command)
        ),
        "import_rules": _guard(lambda command: rules.handle_import_rules(deps, command)),
        "import_rules_markdown": _guard(
            lambda command: rules.handle_import_rules_markdown(deps, command)
        ),
        "save_rules": _guard(lambda command: rules.handle_save_rules(deps, command)),
        "publish_rules": _guard(lambda command: rules.handle_publish_rules(deps, command)),
        "save_plan": _guard(lambda command: plans.handle_save_plan(deps, command)),
        "publish_plan": _guard(lambda command: plans.handle_publish_plan(deps, command)),
        "plan_confirmation_state": _guard(
            lambda command: plans.handle_plan_confirmation_state(deps, command)
        ),
        "prepare_run": _guard(lambda command: prepare.handle_prepare_run(deps, command)),
        "query": _guard(lambda command: reads.handle_query(deps, command)),
        "analyze_project": _guard(lambda command: prepare.handle_analyze_project(deps, command)),
        "discover_tests": _guard(lambda command: prepare.handle_discover_tests(deps, command)),
        "suggest_regression": _guard(
            lambda command: prepare.handle_suggest_regression(deps, command)
        ),
        "check_source": _guard(lambda command: prepare.handle_check_source(deps, command)),
        "confirm_basis": _guard(lambda command: prepare.handle_confirm_basis(deps, command)),
    }
    return BUseCaseRegistry(actions=actions, owned_actions=OWNED_ACTIONS)


__all__ = [
    "OWNED_ACTIONS",
    "BUseCaseDependencies",
    "BUseCaseError",
    "_rule_versions_for",
    "BUseCaseRegistry",
    "build_b_use_case_registry",
]
