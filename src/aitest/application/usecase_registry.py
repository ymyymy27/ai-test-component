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

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import wraps
from typing import TypeVar, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from aitest.application.controlled_write import ControlledWriteService
from aitest.application.planning.basis_confirmation import BasisConfirmationService
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
    ModelGenerationConflictError,
    policy_record_id,
    request_model_draft,
)
from aitest.application.planning.model_policy_confirmation import ModelPolicyConfirmationService
from aitest.application.planning.model_ports import (
    CredentialResolver,
    MaterialProjector,
    ModelCaller,
)
from aitest.application.planning.model_response_resolution import resolve_model_response
from aitest.application.planning.persistence import (
    save_acceptance_scope,
    save_case,
    save_rule_draft,
)
from aitest.application.planning.plan_builder import build_plan
from aitest.application.planning.portable import (
    RULE_PORTABLE_FIELDS,
    export_rule_payloads,
    import_rule_payloads,
)
from aitest.application.planning.preparation import InputRevisions
from aitest.application.planning.prepare_run import PreparationInputs, prepare_run
from aitest.application.planning.publish import (
    PublicationResult,
    payload_digest,
    publish_plan,
    publish_rules,
)
from aitest.application.planning.rules_markdown import (
    rule_draft_from_markdown,
    rule_markdown_from_payload,
)
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
)
from aitest.application.planning.substrate import (
    AggregateKind,
    ConcurrentEditError,
    IndexMaintenanceRequired,
    InvalidQueryCursor,
    PreparationConflictError,
    RecordQuery,
    RecordReader,
    UnitOfWork,
    current_record,
    transaction,
)
from aitest.application.ports import (
    BasisConfirmationProof,
    Clock,
    ControlledWriteProof,
    ModelPolicyConfirmationProof,
    ModelResponseStore,
)
from aitest.application.project.context import ContextGap
from aitest.application.project.persistence import (
    dependency_graph_record_id,
    load_project,
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
from aitest.application.project.source_analysis import SourceAnalysisService
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
from aitest.domain.planning.model_outbound import MaterialKind, ModelOutboundPolicy, ModelTaskType
from aitest.domain.planning.plans import (
    RunDriver,
    RunTier,
)
from aitest.domain.planning.rules import RuleDraft, RuleEnablement, RuleVersion
from aitest.domain.planning.templates import TemplateRef

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
        "save_acceptance",
        "save_case",
        "save_task",
        "save_delivery",
        "generate_draft",
        "resolve_model_response",
        "save_model_outbound_policy",
        "export_rules",
        "export_rules_markdown",
        "import_rules",
        "import_rules_markdown",
        "publish_rules",
        "publish_plan",
        "prepare_run",
        "query",
        "analyze_project",
        "check_source",
        "confirm_basis",
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


def _require_owned_by_command(
    payload: Mapping[str, object],
    *,
    command: object,
    name: str,
    aggregate_kind: str,
) -> None:
    """正文自带的项目必须与命令的项目一致（检查项 B-12）。

    过去只有个别动作（如依赖图、环境）逐个比对，其余动作**直接把命令的项目写进正文**，
    于是"命令项目 A + 正文项目 B"会被静默改写成 A 落盘——调用方以为存的是 B 的记录。
    这里统一在入口处拒绝：正文自带项目就必须对得上；不带项目（`None`）留给持久化层
    按"归属未知"处理，不在这里替它猜一个。
    """
    declared = payload.get("project_id")
    if declared is None:
        return
    if declared != _command_project_id(command):
        raise BUseCaseError(
            "B_INVALID_PARAMETER",
            f"{aggregate_kind} {name} declares project_id {declared!r}, which does not "
            "match the command project",
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


def _publication_result(result: PublicationResult, *, published_kind: str) -> Mapping[str, object]:
    """发布结果：**要么得到已发布版本，要么得到阻塞原因**（`PublicationResult` 的不变量）。"""
    if result.value is None:
        return {"published": False, "blocked_by": list(result.blocked_by)}
    value = result.value
    header: dict[str, object] = {
        "published": True,
        "kind": published_kind,
        "revision": _revision_of(getattr(value, "revision", None), "revision"),
        "record_revision": _revision_of(value.record_revision, "record_revision"),
        "confirmation_id": _as_text(getattr(value, "confirmation_id", None), "confirmation_id"),
    }
    if published_kind == "plan":
        scope = getattr(value, "scope", None)
        header["plan_id"] = _as_text(getattr(value, "plan_id", None), "plan_id")
        header["scope_id"] = _as_text(getattr(scope, "scope_id", None), "scope_id")
        header["scope_revision"] = _revision_of(getattr(scope, "revision", None), "scope_revision")
        header["case_revisions"] = [
            {"case_id": ref.case_id, "revision": ref.revision, "digest": ref.digest}
            for ref in getattr(value, "case_revisions", ())
        ]
        header["rule_revisions"] = [
            {"rule_id": ref.rule_id, "revision": ref.revision, "digest": ref.digest}
            for ref in getattr(value, "rule_revisions", ())
        ]
        header["template_versions"] = [
            {
                "template_id": ref.template_id,
                "version": ref.version,
                "digest": ref.digest,
            }
            for ref in getattr(value, "template_versions", ())
        ]
        return header
    header["rule_id"] = _as_text(getattr(value, "rule_id", None), "rule_id")
    header["digest"] = _as_text(getattr(value, "digest", None), "digest")
    return header


def _enum_or[E: StrEnum](enum: type[E], value: object, name: str, default: E) -> E:
    """可选枚举：缺省时用领域默认值，给了就必须合法。"""
    if value is None:
        return default
    return _enum_of(enum, value, name)


def _rule_versions_for(
    deps: BUseCaseDependencies,
    *,
    project_id: str,
    parameters: Mapping[str, object],
) -> tuple[RuleVersion, ...]:
    """按 `{rule_id, revision}` 读回**已发布**的规则版本。

    摘要取自记录本身；记录不存在时**拒绝**，不填占位值——用占位值填出来的
    计划会在 `PreparedRun.rule_versions` 里带一个假身份，事后无法核对。
    """
    raw = parameters.get("rule_revisions")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BUseCaseError("B_INVALID_PARAMETER", "rule_revisions must be a list")
    versions: list[RuleVersion] = []
    for index, item in enumerate(raw):
        entry = _as_mapping(item, f"rule_revisions[{index}]")
        rule_id = _as_text(_required(entry, "rule_id"), f"rule_revisions[{index}].rule_id")
        revision = _revision_of(_required(entry, "revision"), f"rule_revisions[{index}].revision")
        try:
            record = deps.reader.read(
                aggregate_kind="rule_version", record_id=rule_id, revision=revision
            )
        except Exception as error:
            raise BUseCaseError(
                "B_INVALID_PARAMETER",
                f"no published rule version for {rule_id}@{revision}",
            ) from error
        if record.payload.get("project_id") != project_id:
            raise BUseCaseError("B_INVALID_PARAMETER", "rule version belongs to another project")
        versions.append(_rule_version_from_payload(record.payload, record_revision=record.revision))
    return tuple(versions)


def _rule_version_from_payload(
    payload: Mapping[str, object], *, record_revision: int | None = None
) -> RuleVersion:
    """按 `publish_rules()` 的落盘形状重建规则版本。

    两处**如实说明**（记录里没有这些字段，不假装有）：

    - `digest`：发布时算出来放在内存对象上，**没有落进 payload**。这里用发布器
      公开的同一口径（`publish.payload_digest`）对记录内容重算，因此两者必须一致；
      若将来发布器换了口径，这条重建路径会跟着一致，不会各算一套。
    - `confirmation_id`：取**本次提交的提交序号**，记录里没有该字段。重建时用记录自带的
      提交序号占位（`created_at_commit` 同源语义），**它不参与计划冻结**，
      只用于满足领域对象的不变量。

    缺字段即拒绝，不填默认值（读不懂的记录不得被当成合法版本）。
    """
    digest = payload_digest(dict(payload))
    return RuleVersion(
        rule_id=_as_text(payload.get("rule_id"), "rule_id"),
        revision=_revision_of(payload.get("revision"), "revision"),
        scope=_as_text(payload.get("scope"), "scope"),
        text=_as_text(payload.get("text"), "text"),
        steps=_text_list(payload.get("steps"), "steps"),
        evidence_requirements=_text_list(
            payload.get("evidence_requirements"), "evidence_requirements"
        ),
        source=_as_text(payload.get("source"), "source"),
        confirmation_id=digest,
        digest=digest,
        record_revision=record_revision,
    )


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


def _rule_draft_of(parameters: Mapping[str, object]) -> RuleDraft:
    """把参数翻成 `RuleDraft`；字段名与领域对象**逐字一致**。"""
    enablement = (
        RuleEnablement.DISABLED
        if parameters.get("enablement") is None
        else _enum_of(RuleEnablement, parameters["enablement"], "enablement")
    )
    return RuleDraft(
        rule_id=_as_text(_required(parameters, "rule_id"), "rule_id"),
        revision=_revision_of(_required(parameters, "revision"), "revision"),
        scope=_as_text(_required(parameters, "scope"), "scope"),
        text=_as_text(_required(parameters, "text"), "text"),
        source=_as_text(_required(parameters, "source"), "source"),
        steps=_text_list(parameters.get("steps"), "steps"),
        evidence_requirements=_text_list(
            parameters.get("evidence_requirements"), "evidence_requirements"
        ),
        enablement=enablement,
        confirmed=_bool_of(parameters.get("confirmed"), "confirmed"),
        unknown_extension_fields=_text_list(
            parameters.get("unknown_extension_fields"), "unknown_extension_fields"
        ),
    )


# ------------------------------------------------------------------ prepare_run 参数适配


def _required(parameters: Mapping[str, object], name: str) -> object:
    if name not in parameters:
        raise BUseCaseError("B_INVALID_PARAMETER", f"missing required parameter: {name}")
    return parameters[name]


def _model_of[M: BaseModel](model: type[M], value: object, name: str) -> M:
    """按 `contracts` 的模型解析一个参数；失败时报**字段名**而不是堆栈。"""
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise BUseCaseError(
            "B_INVALID_PARAMETER", f"invalid {name}: {error.error_count()} field error(s)"
        ) from error


def _models_of[M: BaseModel](model: type[M], value: object, name: str) -> tuple[M, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a list")
    return tuple(_model_of(model, item, f"{name}[{index}]") for index, item in enumerate(value))


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


def _payload_revision_or_none(command: object, *, default: int, name: str) -> int | None:
    """取"我看到的修订"，并允许调用方用 `0` 表示**新建**。

    命令层只允许 `expected_revision >= 0`（`0` 是既有的"我认定这是新建"写法），
    而带 `revision` 的领域对象默认从 `1` 起。这里的规则是：

    - `0` / 缺省 → `None`，即按"新建"处理，由底座在已有记录时抛修订冲突；
    - 其余正数 → 原样用于并发校验。

    这样既保住"不自动覆盖"，又不必让调用方把 `1` 写成 `0`。
    """
    value = getattr(command, "expected_revision", None)
    if value is None:
        return None
    number = _int_of(value, name)
    if number == 0:
        return None
    return number


def _expected_revision_or_none(command: object) -> int | None:
    """命令里的"我看到的修订"，`0` 表示**新建**。

    与 `_payload_revision_or_none()` 同一套约定，但不带"缺省回退到某个领域修订"的
    参数：发布的基线只能由调用方给出，不能由应用层从草稿/计划自己的修订号推出来
    （检查项 B-14——那正是"替调用方接受最新基线"）。
    """
    value = getattr(command, "expected_revision", None)
    if value is None:
        return None
    number = _int_of(value, "expected_revision")
    if number == 0:
        return None
    return number


def _optional_revision(value: object, name: str) -> int | None:
    """可选修订：`None` 表示**不适用**（不是 0、不是未知）。"""
    if value is None:
        return None
    return _revision_of(value, name)


def _gaps_of(value: object, name: str) -> tuple[ContextGap, ...]:
    """上下文缺口列表；每一项的字段名与 `ContextGap` 逐字一致。"""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BUseCaseError("B_INVALID_PARAMETER", f"{name} must be a list")
    gaps: list[ContextGap] = []
    for index, item in enumerate(value):
        raw = _as_mapping(item, f"{name}[{index}]")
        prefix = f"{name}[{index}]"
        gaps.append(
            ContextGap(
                kind=_as_text(_required(raw, "kind"), f"{prefix}.kind"),
                subject=_as_text(_required(raw, "subject"), f"{prefix}.subject"),
                detail=_as_text(_required(raw, "detail"), f"{prefix}.detail"),
                blocking=_bool_of(raw.get("blocking"), f"{prefix}.blocking", default=True),
            )
        )
    return tuple(gaps)


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
    basis_confirmations: BasisConfirmationService | None = None
    basis_confirmation_proof: BasisConfirmationProof | None = None
    model_responses: ModelResponseStore | None = None
    model_policy_confirmations: ModelPolicyConfirmationService | None = None
    model_policy_proof: ModelPolicyConfirmationProof | None = None
    controlled_writes: ControlledWriteService | None = None
    controlled_write_proof: ControlledWriteProof | None = None


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

    def handle_analyze_project(command: object) -> Mapping[str, object]:
        if deps.source_analysis is None:
            raise BUseCaseError("CAPABILITY_UNAVAILABLE", "source analysis is unavailable")
        parameters = _command_parameters(command)
        binding_revision = getattr(command, "binding_revision", None)
        return deps.source_analysis.analyze(
            project_id=_command_project_id(command),
            request_id=_as_text(getattr(command, "request_id", None), "request_id"),
            intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
            binding_id=_as_text(_required(parameters, "binding_id"), "binding_id"),
            binding_revision=_int_of(binding_revision, "binding_revision"),
            expected_revision=_int_of(
                getattr(command, "expected_revision", None), "expected_revision"
            ),
            purpose=_as_text(_required(parameters, "purpose"), "purpose"),
            source_scope=_as_text(_required(parameters, "source_scope"), "source_scope"),
            selected_paths=_text_list(parameters.get("selected_paths", []), "selected_paths"),
            exclusion_rules=_text_list(parameters.get("exclusion_rules", []), "exclusion_rules"),
            refetch_dependencies=_text_list(
                parameters.get("refetch_dependencies", []), "refetch_dependencies"
            ),
            refetch_scope=_optional_text(parameters.get("refetch_scope"), "refetch_scope"),
        )

    def handle_check_source(command: object) -> Mapping[str, object]:
        if deps.source_analysis is None:
            raise BUseCaseError("CAPABILITY_UNAVAILABLE", "source analysis is unavailable")
        parameters = _command_parameters(command)
        return deps.source_analysis.check(
            project_id=_command_project_id(command),
            snapshot_id=_as_text(_required(parameters, "snapshot_id"), "snapshot_id"),
            revision=_int_of(_required(parameters, "revision"), "revision"),
        )

    def handle_confirm_basis(command: object) -> Mapping[str, object]:
        if deps.basis_confirmations is None:
            raise BUseCaseError("CAPABILITY_UNAVAILABLE", "basis confirmation is unavailable")
        parameters = _command_parameters(command)
        return deps.basis_confirmations.confirm(
            project_id=_command_project_id(command),
            request_id=_as_text(getattr(command, "request_id", None), "request_id"),
            intent_id=_as_text(getattr(command, "intent_id", None), "intent_id"),
            case_id=_as_text(_required(parameters, "case_id"), "case_id"),
            case_revision=_revision_of(_required(parameters, "case_revision"), "case_revision"),
            basis_revision=_revision_of(_required(parameters, "basis_revision"), "basis_revision"),
            basis_text_digest=_as_text(
                _required(parameters, "basis_text_digest"), "basis_text_digest"
            ),
            challenge_id=_optional_text(
                parameters.get("approval_challenge_id"), "approval_challenge_id"
            ),
        )

    def handle_save_context(command: object) -> Mapping[str, object]:
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
            expected_revision=_command_expected_revision(command),
        )
        return _stage_result(
            aggregate_kind=staged.aggregate_kind,
            record_id=staged.record_id,
            revision=staged.revision,
        )

    def handle_save_binding(command: object) -> Mapping[str, object]:
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

    def handle_save_environment(command: object) -> Mapping[str, object]:
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

    def handle_save_acceptance(command: object) -> Mapping[str, object]:
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
            expected_revision=_command_expected_revision(command),
        )
        return _stage_result(
            aggregate_kind=staged.aggregate_kind,
            record_id=staged.record_id,
            revision=staged.revision,
        )

    def handle_save_case(command: object) -> Mapping[str, object]:
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
            expected_revision=_command_expected_revision(command),
        )
        return _stage_result(
            aggregate_kind=staged.aggregate_kind,
            record_id=staged.record_id,
            revision=staged.revision,
        )

    def handle_save_task(command: object) -> Mapping[str, object]:
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

    def handle_save_delivery(command: object) -> Mapping[str, object]:
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

    def handle_query(command: object) -> Mapping[str, object]:
        project_id = _command_project_id(command)
        parameters = _command_parameters(command)
        raw_kind = parameters.get("aggregate_kind")
        aggregate_kind: AggregateKind | None = None
        if raw_kind is not None:
            kind = _as_text(raw_kind, "aggregate_kind")
            if kind not in _AGGREGATE_KINDS:
                raise BUseCaseError("B_INVALID_PARAMETER", f"unknown aggregate_kind: {kind}")
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
        from aitest.application.errors import CapabilityUnavailable
        from aitest.application.planning.basis_validation import validate_prepared_material
        from aitest.application.planning.preparation import preparation_identity_digest
        from aitest.application.project.source_analysis import SourceAnalysisError

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
                saved = deps.reader.read(
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
                + validate_prepared_material(
                    candidate,
                    reader=deps.reader,
                    workspace_id=deps.workspace_id,
                    approvals=deps.basis_confirmation_proof,
                    controlled_writes=deps.controlled_write_proof,
                )
            ),
        )
        # DTO 与 `BC-001` 冻结的 `PreparedRun` 合同**同一套字段**：直接取模型的 JSON 形态，
        # 不在这里另写一份投影（两份形状一旦分叉，正是"每包自造 API"那类问题）。
        return cast(
            "Mapping[str, object]",
            prepared.model_dump(mode="json"),
        )

    def handle_export_rules(command: object) -> Mapping[str, object]:
        _command_project_id(command)
        parameters = _command_parameters(command)
        raw = parameters.get("rule_versions")
        if raw is None:
            raise BUseCaseError("B_INVALID_PARAMETER", "rule_versions must be a non-empty list")
        versions = _rule_versions_for(
            deps,
            project_id=_command_project_id(command),
            parameters={"rule_revisions": raw},
        )
        # 导出的是**内容**：导入方拿到草稿，"已发布"由各自实例重新发布产生。
        return {"bundle": export_rule_payloads(versions)}

    def handle_import_rules(command: object) -> Mapping[str, object]:
        """导入规则：**只得到草稿**，并作为 `rule_draft` 记录落盘。

        导入不是发布：产出的草稿恒为未确认、未启用；`expected_revision` 与其他写动作同样
        由命令给出（`0` 表示新建），因此"同一 `rule_id` 再导入一次"会按正常修订冲突处理，
        要追加修订就显式给出已看到的修订。
        """
        project_id = _command_project_id(command)
        parameters = _command_parameters(command)
        raw = _required(parameters, "bundle")
        bundle = raw if isinstance(raw, str) else _as_mapping(raw, "bundle")
        if isinstance(bundle, str):
            try:
                decoded: object = json.loads(bundle)
            except json.JSONDecodeError as error:
                raise BUseCaseError(
                    "B_INVALID_PARAMETER", f"bundle is not valid JSON: {error}"
                ) from error
        else:
            decoded = dict(bundle)
        try:
            drafts = import_rule_payloads(decoded)
        except ValueError as error:
            raise BUseCaseError("B_INVALID_PARAMETER", f"invalid bundle: {error}") from error
        if not drafts:
            raise BUseCaseError("B_INVALID_PARAMETER", "bundle carries no rules")

        stored: list[dict[str, object]] = []
        for draft in drafts:
            staged = save_rule_draft(
                draft,
                project_id=project_id,
                unit_of_work=deps.unit_of_work,
                expected_revision=_command_expected_revision(command),
            )
            stored.append(
                {
                    "aggregate_kind": staged.aggregate_kind,
                    "record_id": staged.record_id,
                    "revision": staged.revision,
                    "rule_id": draft.rule_id,
                    "confirmed": draft.confirmed,
                    "enablement": draft.enablement.value,
                }
            )
        return {"imported": stored}

    def handle_export_rules_markdown(command: object) -> Mapping[str, object]:
        """导出规则为 **Markdown**（需求 P1-FR05 的"Markdown 导入导出"）。

        与 `export_rules` 的区别只在**渲染形态**：内容仍是同一条 `rule_version` 的内容，
        字段集合与规范 JSON 完全一致（见 `rules_markdown.py` 的方言定义）。
        可以一次导出多条：返回 `documents`，每项一条规则、各自可独立导入。
        """
        _command_project_id(command)
        parameters = _command_parameters(command)
        raw = parameters.get("rule_versions")
        if raw is None:
            raise BUseCaseError("B_INVALID_PARAMETER", "rule_versions must be a non-empty list")
        versions = _rule_versions_for(
            deps,
            project_id=_command_project_id(command),
            parameters={"rule_revisions": raw},
        )
        bundle = export_rule_payloads(versions)
        rules = bundle["rules"]
        assert isinstance(rules, list)
        # 只把**可携带字段**交给 Markdown 渲染器：`export_rule_version()` 另带
        # `published_confirmation_id` / `published_digest` 这类**本地追溯**字段，
        # 它们不属于可携带格式（Markdown 是给人编辑的，不能携带"某实例已发布"的宣称）。
        documents: list[dict[str, object]] = []
        for payload in rules:
            portable = {key: payload[key] for key in RULE_PORTABLE_FIELDS}
            try:
                markdown = rule_markdown_from_payload(portable)
            except ValueError as error:
                raise BUseCaseError(
                    "B_INVALID_PARAMETER", f"rule payload cannot be rendered: {error}"
                ) from error
            documents.append(
                {
                    "rule_id": payload["rule_id"],
                    "revision": payload["revision"],
                    "markdown": markdown,
                }
            )
        return {"documents": documents}

    def handle_import_rules_markdown(command: object) -> Mapping[str, object]:
        """导入规则 **Markdown**：与 `import_rules` 同一口径——**只得到草稿**、并落 `rule_draft`。

        参数 `markdown` 可以是单份文档（字符串）或多份（列表）；每份解析失败都指名报错，
        不"跳过坏的那份继续导入"（那会让调用方以为全部都进来了）。
        """
        project_id = _command_project_id(command)
        parameters = _command_parameters(command)
        raw = _required(parameters, "markdown")
        if isinstance(raw, str):
            sources = [raw]
        elif isinstance(raw, list):
            if not raw:
                raise BUseCaseError("B_INVALID_PARAMETER", "markdown must be a non-empty list")
            sources = [_as_text(item, f"markdown[{index}]") for index, item in enumerate(raw)]
        else:
            raise BUseCaseError(
                "B_INVALID_PARAMETER", "markdown must be a string or a list of strings"
            )

        stored: list[dict[str, object]] = []
        for index, document in enumerate(sources):
            try:
                draft = rule_draft_from_markdown(document)
            except ValueError as error:
                raise BUseCaseError(
                    "B_INVALID_PARAMETER", f"markdown[{index}] is not a rule document: {error}"
                ) from error
            staged = save_rule_draft(
                draft,
                project_id=project_id,
                unit_of_work=deps.unit_of_work,
                expected_revision=_command_expected_revision(command),
            )
            stored.append(
                {
                    "aggregate_kind": staged.aggregate_kind,
                    "record_id": staged.record_id,
                    "revision": staged.revision,
                    "rule_id": draft.rule_id,
                    "confirmed": draft.confirmed,
                    "enablement": draft.enablement.value,
                }
            )
        return {"imported": stored}

    def handle_publish_plan(command: object) -> Mapping[str, object]:
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

    def handle_generate_draft(command: object) -> Mapping[str, object]:
        project_id = _command_project_id(command)
        parameters = _command_parameters(command)
        mode = parameters.get("generation_mode", "template")
        if mode == "model":
            return handle_model_draft(command)
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

    def handle_publish_rules(command: object) -> Mapping[str, object]:
        project_id = _command_project_id(command)
        parameters = _command_parameters(command)
        draft = _rule_draft_of(_as_mapping(_required(parameters, "draft"), "draft"))
        result = publish_rules(
            draft,
            project_id=project_id,
            unit_of_work=deps.unit_of_work,
            reader=deps.reader,
            # 检查项 B-14：把**调用方所见修订**原样交给发布，不允许发布替调用方接受最新基线。
            # 命令层用 `0` 表达"我认定这是一条新记录"；这里**不**回退到草稿自己的修订号——
            # 那样又变成替调用方猜基线，正是本项要消除的行为。
            expected_revision=_expected_revision_or_none(command),
            context_gaps=_gaps_of(parameters.get("context_gaps"), "context_gaps"),
        )
        return _publication_result(result, published_kind="rule_version")

    def handle_model_policy(command: object) -> Mapping[str, object]:
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

    def handle_model_draft(command: object) -> Mapping[str, object]:
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
            record = deps.reader.read(
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
        )
        return {
            "blocked": result.content is None,
            "status": result.status,
            "blocked_by": list(result.blocked_by),
            "response_currency": result.response_currency,
            "saved_response_ref": result.saved_response_ref,
            "content": _generated_content(result.content) if result.content is not None else None,
        }

    def handle_resolve_model_response(command: object) -> Mapping[str, object]:
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

    actions: dict[str, Handler] = {
        "save_context": _guard(handle_save_context),
        "save_binding": _guard(handle_save_binding),
        "save_environment": _guard(handle_save_environment),
        "save_dependency_graph": _guard(handle_save_dependency_graph),
        "save_acceptance": _guard(handle_save_acceptance),
        "save_case": _guard(handle_save_case),
        "save_task": _guard(handle_save_task),
        "save_delivery": _guard(handle_save_delivery),
        "generate_draft": _guard(handle_generate_draft),
        "resolve_model_response": _guard(handle_resolve_model_response),
        "save_model_outbound_policy": _guard(handle_model_policy),
        "export_rules": _guard(handle_export_rules),
        "export_rules_markdown": _guard(handle_export_rules_markdown),
        "import_rules": _guard(handle_import_rules),
        "import_rules_markdown": _guard(handle_import_rules_markdown),
        "publish_rules": _guard(handle_publish_rules),
        "publish_plan": _guard(handle_publish_plan),
        "prepare_run": _guard(handle_prepare_run),
        "query": _guard(handle_query),
        "analyze_project": _guard(handle_analyze_project),
        "check_source": _guard(handle_check_source),
        "confirm_basis": _guard(handle_confirm_basis),
    }
    return BUseCaseRegistry(actions=actions, owned_actions=OWNED_ACTIONS)


__all__ = [
    "OWNED_ACTIONS",
    "BUseCaseDependencies",
    "BUseCaseError",
    "BUseCaseRegistry",
    "build_b_use_case_registry",
]
