"""模型出站策略与出站准入规则；无 I/O 或框架依赖。

职责边界（一期架构文档《01-项目与计划》第 9 节「AI 调用边界」）：

- 本模块只提供**纯规则**：策略对象、出站准入校验、迟到响应的当前性判定。
- 真正的脱敏字节投影由 `ProjectionPort` 承接、凭据解析由 `SecretPort` 承接、
  请求与响应归一由 `ModelProvider` 承接，持久化统一走工作单元；这些都属应用用例与适配器，
  **不在本模块内实现**。
- 模型**只输出草稿**：`ModelResponseEnvelope.is_draft=False` 在构造时即被拒绝，
  因此本模块不提供"模型结果直接成为结论"的路径。

依据：需求 P1-FR04、P1-FR06、P1-FR17 与 P1-AC32；功能文档第 5 节「记录与来源」
与第 9 节「常用流程与增量合同」；需求 §7「模型出站」。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from enum import StrEnum


class MaterialKind(StrEnum):
    """可以出站的材料类别。

    `SOURCE_SNIPPET` 是**类别之一**而不是一个独立开关位：需求 §7 要求
    "源码片段开关作用所有出站字段及日志/附件投影，不能通过另一类别夹带未授权源码"，
    因此按类别逐项拦截，而不是只在策略上留一个布尔值。
    """

    PROJECT_CONTEXT = "project_context"
    MODULE_DEPENDENCY_FACTS = "module_dependency_facts"
    TEMPLATE_CONTENT = "template_content"
    ACCEPTANCE_ITEM = "acceptance_item"
    DELIVERY_NOTE = "delivery_note"
    CASE_CONTENT = "case_content"
    EVIDENCE_REFERENCE = "evidence_reference"
    EXECUTION_FACT = "execution_fact"
    SOURCE_SNIPPET = "source_snippet"


class ModelTaskType(StrEnum):
    """模型任务类型；**限定该请求可选用的材料类别**。

    架构文档第 9 节："模型任务区分上下文摘要、检查内容、验收建议、交付草稿、
    用例建议与证据分析。任务类型限定可选字段。"
    """

    CONTEXT_SUMMARY = "context_summary"
    CHECK_CONTENT_DRAFT = "check_content_draft"
    ACCEPTANCE_SUGGESTION = "acceptance_suggestion"
    DELIVERY_DRAFT = "delivery_draft"
    CASE_SUGGESTION = "case_suggestion"
    EVIDENCE_ANALYSIS = "evidence_analysis"


#: 任务类型到允许材料类别的映射（架构文档第 9 节"任务类型限定可选字段"）。
TASK_MATERIAL_KINDS: dict[ModelTaskType, frozenset[MaterialKind]] = {
    ModelTaskType.CONTEXT_SUMMARY: frozenset(
        {
            MaterialKind.PROJECT_CONTEXT,
            MaterialKind.MODULE_DEPENDENCY_FACTS,
            MaterialKind.SOURCE_SNIPPET,
        }
    ),
    ModelTaskType.CHECK_CONTENT_DRAFT: frozenset(
        {
            MaterialKind.PROJECT_CONTEXT,
            MaterialKind.MODULE_DEPENDENCY_FACTS,
            MaterialKind.TEMPLATE_CONTENT,
            MaterialKind.ACCEPTANCE_ITEM,
            MaterialKind.SOURCE_SNIPPET,
        }
    ),
    ModelTaskType.ACCEPTANCE_SUGGESTION: frozenset(
        {
            MaterialKind.PROJECT_CONTEXT,
            MaterialKind.ACCEPTANCE_ITEM,
            MaterialKind.TEMPLATE_CONTENT,
        }
    ),
    ModelTaskType.DELIVERY_DRAFT: frozenset(
        {
            MaterialKind.PROJECT_CONTEXT,
            MaterialKind.DELIVERY_NOTE,
        }
    ),
    ModelTaskType.CASE_SUGGESTION: frozenset(
        {
            MaterialKind.PROJECT_CONTEXT,
            MaterialKind.ACCEPTANCE_ITEM,
            MaterialKind.TEMPLATE_CONTENT,
            MaterialKind.CASE_CONTENT,
            MaterialKind.SOURCE_SNIPPET,
        }
    ),
    ModelTaskType.EVIDENCE_ANALYSIS: frozenset(
        {
            MaterialKind.EVIDENCE_REFERENCE,
            MaterialKind.EXECUTION_FACT,
            MaterialKind.SOURCE_SNIPPET,
        }
    ),
}


class ResponseCurrency(StrEnum):
    """迟到响应的当前性。

    `SOURCE_CHANGED` 与 `SUPERSEDED_BY_MANUAL` 都表示**该响应不得作为当前草稿**；
    前者因来源修订变化，后者因用户已人工修改（人工版本优先）。
    """

    CURRENT = "current"
    SOURCE_CHANGED = "source_changed"
    SUPERSEDED_BY_MANUAL = "superseded_by_manual"


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_commit(value: str, name: str) -> None:
    _require_text(value, name)


# ------------------------------------------------------------------ 接收目标与确认


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    """模型接收目标：供应方、地址、模型标识与凭据用途。

    **类型层面无法表达凭据正文**——只有表示用途的 `purpose`，没有 `value` /
    `secret` / `api_key` 一类字段。这是"凭据正文不进配置、日志、面板、导出"的
    第一道防线，测试用 `dataclasses.fields()` 锁定字段集合。
    """

    provider: str
    address: str
    model_id: str
    purpose: str = "model"

    def __post_init__(self) -> None:
        _require_text(self.provider, "provider")
        _require_text(self.address, "address")
        _require_text(self.model_id, "model_id")
        _require_text(self.purpose, "purpose")

    def digest_subject(self) -> str:
        """参与确认绑定的目标身份；不含模型标识以外的时间或密钥信息。"""
        return f"{self.provider}|{self.address}|{self.model_id}|{self.purpose}"


@dataclass(frozen=True, slots=True)
class OutboundConfirmation:
    """出站确认记录，**绑定接收目标与允许类别范围**。

    需求 §7："范围或目标变化**重新确认**"。因此确认绑定的是目标摘要与类别集合摘要，
    不是整个策略对象的修订号——否则无关字段变化也会让用户反复确认。
    """

    confirmation_id: str
    endpoint_digest: str
    material_kinds_digest: str
    source_snippets_enabled: bool
    confirmed_at_commit: str

    def __post_init__(self) -> None:
        _require_text(self.confirmation_id, "confirmation_id")
        _require_text(self.endpoint_digest, "endpoint_digest")
        _require_text(self.material_kinds_digest, "material_kinds_digest")
        _require_commit(self.confirmed_at_commit, "confirmed_at_commit")

    def covers(self, policy: ModelOutboundPolicy) -> bool:
        """当前策略的接收目标与允许范围是否仍在该确认覆盖之内。"""
        return (
            self.endpoint_digest == endpoint_digest(policy.endpoint)
            and self.material_kinds_digest == material_kinds_digest(policy)
            and self.source_snippets_enabled == policy.source_snippets_enabled
        )


def endpoint_digest(endpoint: ModelEndpoint) -> str:
    """接收目标的稳定摘要。真实摘要算法由持久化侧决定，此处只要求内容确定。"""
    return endpoint.digest_subject()


def material_kinds_digest(policy: ModelOutboundPolicy) -> str:
    """允许类别集合的稳定摘要；排序后拼接，保证与集合顺序无关。"""
    return ",".join(sorted(kind.value for kind in policy.allowed_material_kinds))


# ---------------------------------------------------------------------- 出站策略


@dataclass(frozen=True, slots=True)
class ModelOutboundPolicy:
    """项目级模型出站策略，按项目修订（功能文档第 9 节）。

    关键不变量：

    1. **`source_snippets_enabled` 默认为 `False`**（需求 §7"源码片段默认关闭"）；
       开启它的唯一方式是在允许类别里同时列出 `SOURCE_SNIPPET`。
    2. **关闭 AI 不阻止保存策略**，只让 `effective_material_kinds()` 派生空集——
       "关闭 AI 阻止后续模型发送，已有人工/规则/报告继续"（需求 P1-AC32）。
    3. 本对象**不含撤销路径**：撤销通过新增修订表达，历史修订保留。
    """

    project_id: str
    revision: int
    endpoint: ModelEndpoint
    allowed_material_kinds: frozenset[MaterialKind]
    source_snippets_enabled: bool = False
    ai_enabled: bool = True
    confirmation: OutboundConfirmation | None = None
    revoked_material_kinds: frozenset[MaterialKind] = frozenset()

    def __post_init__(self) -> None:
        _require_text(self.project_id, "project_id")
        if self.revision < 1:
            raise ValueError("model outbound policy revision must be >= 1")
        if not self.allowed_material_kinds:
            raise ValueError("an outbound policy requires at least one allowed material kind")
        for kind in sorted(self.allowed_material_kinds, key=lambda item: item.value):
            if not isinstance(kind, MaterialKind):
                raise ValueError(f"unknown material kind: {kind!r}")
        if not self.revoked_material_kinds <= self.allowed_material_kinds:
            raise ValueError("revoked material kinds must be a subset of the allowed kinds")
        if self.source_snippets_enabled and (
            MaterialKind.SOURCE_SNIPPET not in self.allowed_material_kinds
        ):
            raise ValueError("enabling source snippets requires the source snippet material kind")
        if self.confirmation is not None and (
            self.confirmation.source_snippets_enabled != self.source_snippets_enabled
        ):
            raise ValueError("the confirmation must record the same source snippet switch value")

    def effective_material_kinds(self) -> frozenset[MaterialKind]:
        """按 AI 开关与撤销集合派生当前实际可送出的类别；**不修改本对象**。

        关闭 AI 时返回空集：阻止**新的**模型请求；已经发出的请求不宣称已撤回
        （需求 §7）。撤销类别立即生效，不再发送被撤销类别（功能文档第 9 节）。
        """
        if not self.ai_enabled:
            return frozenset()
        return frozenset(self.allowed_material_kinds - self.revoked_material_kinds)

    def is_confirmed_for_current_scope(self) -> bool:
        """当前接收目标与允许范围是否已被确认（需求 §7"首次出站需确认"）。"""
        if self.confirmation is None:
            return False
        return self.confirmation.covers(self)

    def accepts_source_snippets(self) -> bool:
        """源码片段是否真的可送出：开关、类别、撤销三者同时满足才算。"""
        if not self.source_snippets_enabled:
            return False
        return MaterialKind.SOURCE_SNIPPET in self.effective_material_kinds()


# ------------------------------------------------------------------ 出站准入校验


@dataclass(frozen=True, slots=True)
class OutboundItem:
    """一项拟出站材料。

    `text` 应当是**已完成脱敏的文本**——脱敏由 `ProjectionPort`（A）承接，
    本模块只做准入判断，不重复实现一套脱敏，也不修改文本内容。
    """

    material_kind: MaterialKind
    field_path: str
    text: str

    def __post_init__(self) -> None:
        _require_text(self.field_path, "field_path")
        _require_text(self.text, "text")


def validate_outbound_material(
    policy: ModelOutboundPolicy,
    task_type: ModelTaskType,
    items: Sequence[OutboundItem],
) -> tuple[OutboundItem, ...]:
    """校验拟出站材料；不满足则抛 `ValueError` 并指出具体项。

    门禁逐条（设计说明第 3.5 节）：

    1. 关闭 AI 时拒绝整个请求；
    2. 空项集合拒绝（空集不等于"不适用"）；
    3. 未确认接收目标与范围时拒绝；
    4. 类别被撤销时拒绝；
    5. 类别不属于该任务类型的允许子集时拒绝（防跨任务夹带）；
    6. 含源码片段但开关关闭时拒绝；
    7. 含源码片段但该类别未被允许时拒绝；
    8. 同一 `(类别, 字段路径)` 重复时拒绝。

    返回可送出的项（顺序不变）。
    """
    if not policy.ai_enabled:
        raise ValueError("AI is disabled: new model requests are blocked")
    if not items:
        raise ValueError("an empty material set is not not_applicable")
    if not policy.is_confirmed_for_current_scope():
        raise ValueError(
            "the model outbound policy scope is not confirmed: "
            "a changed endpoint or material range requires a new confirmation"
        )

    effective = policy.effective_material_kinds()
    allowed_for_task = TASK_MATERIAL_KINDS[task_type]
    seen: set[tuple[MaterialKind, str]] = set()

    for item in items:
        identity = (item.material_kind, item.field_path)
        if identity in seen:
            raise ValueError(
                f"duplicate outbound item: {item.material_kind.value} at {item.field_path}"
            )
        seen.add(identity)

        if item.material_kind not in effective:
            if item.material_kind in policy.revoked_material_kinds:
                raise ValueError(f"material kind is revoked: {item.material_kind.value}")
            raise ValueError(
                f"material kind is not allowed by the policy: {item.material_kind.value}"
            )

        if item.material_kind not in allowed_for_task:
            raise ValueError(
                f"material kind is not allowed for task {task_type.value}: "
                f"{item.material_kind.value}"
            )

        if item.material_kind is MaterialKind.SOURCE_SNIPPET and (
            not policy.source_snippets_enabled
        ):
            raise ValueError(
                "source snippets are disabled: closing the switch must not send "
                f"source material (at {item.field_path})"
            )

    return tuple(items)


# ------------------------------------------------------------------ 迟到响应判定


@dataclass(frozen=True, slots=True)
class ModelResponseEnvelope:
    """一次模型响应的来源绑定。

    `base_manual_revision` 记录**发出请求时人工版本的修订**：响应的当前性必须与
    "期间用户是否手工改过"比较，而策略修订与人工草稿修订是两套独立计数器。
    """

    response_id: str
    request_revision: int
    source_revision: int
    base_manual_revision: int
    received_at_commit: str
    is_draft: bool = True

    def __post_init__(self) -> None:
        _require_text(self.response_id, "response_id")
        _require_commit(self.received_at_commit, "received_at_commit")
        if self.request_revision < 1:
            raise ValueError("request_revision must be >= 1")
        if self.source_revision < 0:
            raise ValueError("source_revision must be >= 0")
        if self.base_manual_revision < 0:
            raise ValueError("base_manual_revision must be >= 0")
        if not self.is_draft:
            raise ValueError(
                "a model response is a draft: it must not modify assertions, "
                "close issues or produce conclusions"
            )


def response_currency(
    response: ModelResponseEnvelope,
    current_source_revision: int,
    current_policy_revision: int,
    current_manual_revision: int,
) -> ResponseCurrency:
    """判定迟到响应的当前性；**不修改入参**，也不返回可当结论使用的对象。

    判定顺序（设计说明第 3.6 节）：人工版本优先，其次来源修订，最后策略修订。

    - 用户在该请求发出后又手工改过 → `SUPERSEDED_BY_MANUAL`（不覆盖人工版本）；
    - 来源修订已变 → `SOURCE_CHANGED`（标过期）；
    - 策略修订已变 → `SOURCE_CHANGED`（本次请求依据不再是当前依据）；
    - 否则 → `CURRENT`。
    """
    return response_currency_from_facts(
        manual_advanced=current_manual_revision > response.base_manual_revision,
        source_matches=(
            current_source_revision == response.source_revision
            and current_policy_revision == response.request_revision
        ),
    )


def response_currency_from_facts(
    *, manual_advanced: bool, source_matches: bool
) -> ResponseCurrency:
    """Use observed content identity as well as revisions without inventing a revision."""
    if manual_advanced:
        return ResponseCurrency.SUPERSEDED_BY_MANUAL
    if not source_matches:
        return ResponseCurrency.SOURCE_CHANGED
    return ResponseCurrency.CURRENT


#: 供测试锁定"接收目标无法表达凭据正文"的字段集合。
MODEL_ENDPOINT_FIELDS = tuple(field.name for field in fields(ModelEndpoint))


__all__ = [
    "MODEL_ENDPOINT_FIELDS",
    "TASK_MATERIAL_KINDS",
    "MaterialKind",
    "ModelEndpoint",
    "ModelOutboundPolicy",
    "ModelResponseEnvelope",
    "ModelTaskType",
    "OutboundConfirmation",
    "OutboundItem",
    "ResponseCurrency",
    "endpoint_digest",
    "material_kinds_digest",
    "response_currency",
    "response_currency_from_facts",
    "validate_outbound_material",
]
