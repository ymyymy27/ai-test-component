"""模型请求编排：策略 → 准入 → 投影 → 登记意图 → 调用 → 登记结果。

把 Sprint 5 已实现的三块**纯规则**串起来（`domain/planning/model_outbound.py`）：

| 纯规则（早已实现并有测试） | 本模块如何使用 |
| --- | --- |
| `ModelOutboundPolicy` + `effective_material_kinds()` | 决定哪些类别可送出、源码片段是否可送 |
| `validate_outbound_material()` 九条门禁 | 投影**之前**逐项准入，拦住"跨任务夹带" |
| `response_currency()` | 迟到响应核对来源修订，**人工版本优先** |

**编排层不重复实现任何判定**，只调用上述函数（与 `publish.py` 同一原则）。

## 出站次序（硬性）

外部调用是**不可撤销的副作用**，因此顺序固定为：

1. 准入与投影（步 1—5，全部在事务外，不产生副作用）；
2. **意图落盘并提交**——在调用之前，把"要发什么、发给谁、凭什么"变成已提交事实；
3. **事务外调用**——不持有写事务发起外部请求；
4. **结果落盘并提交**——成功时把草稿正文与其引用放在**同一次提交**里。

反序（先调用后登记）在"调用已发生、进程随后崩溃"时会丢掉整条出站事实，
且事后无法补记。

四条硬约束的落点：

| 约束 | 落点 |
| --- | --- |
| **只发送显式选定的脱敏材料** | 先过九条门禁，再把**投影后**的材料交给调用者；编排不碰原始字节 |
| **凭据正文不进配置、日志、面板、导出** | `CredentialResolver` 不回正文；出站记录只存用途 |
| **模型只输出草稿** | 产出 `GeneratedContent(status=draft)`；响应信封构造期即拒绝非草稿 |
| **迟到响应不覆盖人工版本** | `settle_response()` 用 `response_currency()` 判定，过期即拒绝采用 |

**失败不抛栈**：模型不可用、凭据缺失、投影不全都是**返回态**，因为产品要求
"模型不可用时人工路径仍可用"；抛异常会把失败扩散成调用方崩溃。
失败只回**错误分类**，不回供应商的错误正文（可能回显请求内容）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256

from aitest.application.planning.draft import (
    DraftKind,
    GeneratedContent,
    RevisionContext,
)
from aitest.application.planning.model_ports import (
    CredentialResolver,
    MaterialProjector,
    ModelCall,
    ModelCaller,
    ModelCallResult,
    ModelCallStatus,
    Projection,
    ProjectionStatus,
)
from aitest.application.planning.substrate import (
    RecordQuery,
    RecordReader,
    UnitOfWork,
    transaction,
)
from aitest.application.ports import Clock
from aitest.domain.planning.model_outbound import (
    TASK_MATERIAL_KINDS,
    MaterialKind,
    ModelOutboundPolicy,
    ModelResponseEnvelope,
    ModelTaskType,
    OutboundItem,
    ResponseCurrency,
    response_currency,
    validate_outbound_material,
)
from aitest.domain.planning.templates import TemplateRef

#: 出站结果状态。**没有"已通过"这一档**：模型结果永远只是草稿。
OUTBOUND_DRAFT_READY = "draft_ready"
OUTBOUND_BLOCKED = "blocked"

#: 出站记录的聚合类别（与 `substrate.AggregateKind` 中的取值一致）。
OUTBOUND_AGGREGATE = "model_outbound_request"

#: 草稿的聚合类别（与 `substrate.AggregateKind` 中的取值一致）。
GENERATED_CONTENT_AGGREGATE = "generated_content"

#: 同一出站记录的两个修订各自代表什么；调用方据此区分"意图"与"结果"。
OUTBOUND_STATE_INTENT = "intent"
OUTBOUND_STATE_OUTCOME = "outcome"

_PLACEHOLDER_TEMPLATE = TemplateRef(template_id="manual", version="1.0.0")


@dataclass(frozen=True, slots=True)
class OutboundRequest:
    """一次出站请求的完整记录。

    对应需求 §7"只发送显式选定脱敏材料"：`material_kinds` 与 `item_count`
    让事后可以核对**实际送出了什么**。
    `credential_purpose` 只记**用途**，绝不记凭据正文。
    """

    request_id: str
    project_id: str
    task_type: ModelTaskType
    policy_revision: int
    source_revision: int
    base_manual_revision: int
    material_kinds: tuple[MaterialKind, ...]
    item_count: int
    projection_digest: str
    endpoint_address: str
    model_id: str
    credential_purpose: str
    requested_at: datetime
    provider_request_id: str | None = None
    call_status: str = ""
    error_kind: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "request_id",
            "project_id",
            "projection_digest",
            "endpoint_address",
            "model_id",
            "credential_purpose",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if self.policy_revision < 1:
            raise ValueError("policy_revision must be >= 1")
        if self.source_revision < 0:
            raise ValueError("source_revision must be >= 0")
        if self.base_manual_revision < 0:
            raise ValueError("base_manual_revision must be >= 0")
        if self.item_count < 1:
            raise ValueError("an outbound request must send at least one material item")
        if self.call_status and self.call_status not in {
            ModelCallStatus.OK.value,
            ModelCallStatus.FAILED.value,
        }:
            raise ValueError(f"unknown call status: {self.call_status}")


@dataclass(frozen=True, slots=True)
class OutboundOutcome:
    """编排结果：**要么得到草稿，要么得到阻塞原因**，二者互斥。"""

    status: str
    request: OutboundRequest | None = None
    content: GeneratedContent | None = None
    blocked_by: tuple[str, ...] = ()
    response_currency: ResponseCurrency | None = None

    def __post_init__(self) -> None:
        if self.status == OUTBOUND_DRAFT_READY:
            if self.request is None or self.content is None:
                raise ValueError("a ready draft requires both the request and the content")
            if self.blocked_by:
                raise ValueError("a ready draft must not carry blocking reasons")
            return
        if self.status != OUTBOUND_BLOCKED:
            raise ValueError(f"unknown outcome status: {self.status}")
        if not self.blocked_by:
            raise ValueError("a blocked outbound attempt must name at least one reason")
        if self.content is not None:
            raise ValueError("a blocked outbound attempt must not produce a draft")

    @property
    def is_draft_ready(self) -> bool:
        return self.status == OUTBOUND_DRAFT_READY


def _blocked(*reasons: str) -> OutboundOutcome:
    return OutboundOutcome(status=OUTBOUND_BLOCKED, blocked_by=tuple(reasons))


def policy_record_id(project_id: str) -> str:
    """项目级策略的记录标识（"`ModelOutboundPolicy` 按项目修订"，功能文档第 9 节）。"""
    return f"model-policy:{project_id}"


def outbound_request_id(
    project_id: str, policy_revision: int, task_type: ModelTaskType
) -> str:
    """出站记录标识：按（项目、策略修订、任务）稳定，重复请求不产生第二条记录。"""
    return f"outbound:{project_id}:{policy_revision}:{task_type.value}"


def _current_revision(
    reader: RecordReader, *, project_id: str, record_id: str
) -> int | None:
    """读当前修订；没有记录时返回 `None`（= 新建），避免覆盖既有记录。"""
    page = reader.query(
        RecordQuery(
            project_id=project_id,
            aggregate_kind=OUTBOUND_AGGREGATE,  # type: ignore[arg-type]
            record_id=record_id,
        )
    )
    if not page.items:
        return None
    return max(item.revision for item in page.items)


def _admission_reason(
    policy: ModelOutboundPolicy,
    task_type: ModelTaskType,
    selected: Mapping[MaterialKind, str],
) -> str | None:
    """投影之前的准入检查；返回阻塞原因，`None` 表示通过。

    用**与纯规则相同的顺序**：AI 开关 → 策略确认 → 类别被允许 → 属于该任务 → 源码片段开关。
    """
    if not policy.ai_enabled:
        return "AI is disabled: new model requests are blocked"
    if not policy.is_confirmed_for_current_scope():
        return (
            "the model outbound policy scope is not confirmed: "
            "a changed endpoint or material range requires a new confirmation"
        )

    not_allowed = sorted(
        kind.value
        for kind in selected
        if kind not in policy.effective_material_kinds()
    )
    if not_allowed:
        return "material kinds are not allowed by the policy: " + ", ".join(not_allowed)

    allowed_for_task = TASK_MATERIAL_KINDS[task_type]
    not_for_task = sorted(
        kind.value for kind in selected if kind not in allowed_for_task
    )
    if not_for_task:
        return (
            f"material kinds are not allowed for task {task_type.value}: "
            + ", ".join(not_for_task)
        )

    if (
        MaterialKind.SOURCE_SNIPPET in selected
        and not policy.source_snippets_enabled
    ):
        return (
            "source snippets are disabled: closing the switch must not send "
            "source material"
        )
    return None


def request_model_draft(
    *,
    project_id: str,
    policy: ModelOutboundPolicy,
    task_type: ModelTaskType,
    selected_material: Mapping[MaterialKind, str],
    unit_of_work: UnitOfWork,
    reader: RecordReader,
    projector: MaterialProjector,
    credentials: CredentialResolver,
    caller: ModelCaller,
    clock: Clock,
    source_revision: int,
    base_manual_revision: int,
    draft_kind: str = DraftKind.CHECK_CONTENT,
    template_ref: TemplateRef | None = None,
    project_revision: int = 1,
    binding_revision: int = 1,
    timeout_seconds: int = 60,
) -> OutboundOutcome:
    """编排一次模型请求。

    步骤（严格按需求 §7 与架构文档第 9 节）：

    1. **准入**：AI 开关、策略确认、类别允许、属于该任务、源码片段开关 —— 任一不过即阻塞；
    2. **纯规则的九条门禁**（`validate_outbound_material`）再校验一次，含重复项与空项；
    3. **凭据按用途解析** → 不可用即阻塞（含"用途不匹配"）；
    4. **投影**（脱敏由投影器完成）→ 被排除的材料登记为缺口，**不伪造完整**；
    5. **调用** → 失败即阻塞并归类错误，**不暗换供应方**、**不自动重试**；
    6. 成功 → 登记出站记录并产出 `GeneratedContent(status=draft)`。

    第 1、2 步在**投影之前**：先拦住不该送的材料，再花代价做投影。
    """
    if not project_id.strip():
        raise ValueError("project_id must not be empty")

    reason = _admission_reason(policy, task_type, selected_material)
    if reason is not None:
        return _blocked(reason)

    items = tuple(
        OutboundItem(
            material_kind=kind,
            field_path=f"material.{kind.value}",
            text=text,
        )
        for kind, text in selected_material.items()
    )
    try:
        admissible = validate_outbound_material(policy, task_type, items)
    except ValueError as error:
        return _blocked(str(error))

    resolution = credentials.resolve(purpose="model")
    if not resolution.usable:
        detail = f" ({resolution.detail})" if resolution.detail else ""
        return _blocked(
            f"credential is not usable for purpose 'model': "
            f"{resolution.status.value}{detail}"
        )

    projection: Projection = projector.project(
        material={item.material_kind: item.text for item in admissible},
        source_snippets_enabled=policy.source_snippets_enabled,
    )
    if projection.status is ProjectionStatus.PARTIAL:
        excluded = ", ".join(
            sorted(f"{kind.value}@{path}" for kind, path in projection.excluded)
        )
        return _blocked(
            "material could not be safely projected and was excluded: " + excluded
        )

    call = ModelCall(
        task_type=task_type.value,
        projected=projection.projected,
        projection_digest=projection.projection_digest,
        endpoint_address=policy.endpoint.address,
        model_id=policy.endpoint.model_id,
        timeout_seconds=timeout_seconds,
        policy_revision=policy.revision,
    )

    request = OutboundRequest(
        request_id=outbound_request_id(project_id, policy.revision, task_type),
        project_id=project_id,
        task_type=task_type,
        policy_revision=policy.revision,
        source_revision=source_revision,
        base_manual_revision=base_manual_revision,
        material_kinds=projection.kinds(),
        item_count=len(projection.projected),
        projection_digest=projection.projection_digest,
        endpoint_address=policy.endpoint.address,
        model_id=policy.endpoint.model_id,
        credential_purpose=resolution.purpose,
        requested_at=clock.now(),
    )

    # 步骤 6：**先把出站意图落盘**，再发起外部调用。
    # 反序（先调用后登记）在"调用已发生、进程随后崩溃"时会丢掉整条出站事实，
    # 而模型调用是**不可撤销的副作用**，事后无法补记。
    with transaction(unit_of_work, project_id) as tx:
        tx.stage_record(
            aggregate_kind=OUTBOUND_AGGREGATE,  # type: ignore[arg-type]
            record_id=request.request_id,
            expected_revision=_current_revision(
                reader, project_id=project_id, record_id=request.request_id
            ),
            payload=_intent_payload(request),
        )
        intent = tx.commit()
    intent_revision = intent.revision_of(
        OUTBOUND_AGGREGATE, request.request_id  # type: ignore[arg-type]
    ).revision

    # 步骤 7：事务外调用。**不持有写事务**发起外部请求。
    result: ModelCallResult = caller.call(call)

    # 调用结果并入同一条出站事实：修订 1 是意图，修订 2 是结果。
    request = replace(
        request,
        provider_request_id=result.provider_request_id,
        call_status=result.status.value,
        error_kind=result.error_kind,
    )

    # 步骤 8：提交安全响应；成功时把草稿正文与其引用放在**同一次提交**里。
    content: GeneratedContent | None = None
    with transaction(unit_of_work, project_id) as tx:
        tx.stage_record(
            aggregate_kind=OUTBOUND_AGGREGATE,  # type: ignore[arg-type]
            record_id=request.request_id,
            expected_revision=intent_revision,
            payload=_outcome_payload(request, result),
        )
        if result.status is ModelCallStatus.OK:
            # 草稿引用按**这次出站的修订号**唯一：重发同一条请求会产生新的草稿记录，
            # 不会以"新建"语义覆盖上一次的草稿。
            content = GeneratedContent(
                generated_content_id=(
                    f"draft:{project_id}:{draft_kind}:{request.request_id}:{intent_revision}"
                ),
                project_id=project_id,
                draft_kind=draft_kind,
                template_ref=template_ref if template_ref is not None else _PLACEHOLDER_TEMPLATE,
                revision=intent_revision,
                revision_context=RevisionContext(
                    project_revision=project_revision,
                    binding_revision=binding_revision,
                    template_revision=_template_version(template_ref),
                    source_revision=source_revision,
                ),
                content_digest=_text_digest(result.draft_text),
            )
            tx.stage_record(
                aggregate_kind=GENERATED_CONTENT_AGGREGATE,  # type: ignore[arg-type]
                record_id=content.generated_content_id,
                expected_revision=None,
                payload=_content_payload(content, result.draft_text),
            )
        tx.commit()

    if result.status is not ModelCallStatus.OK:
        # 模型不可用：**人工路径仍可用**，因此返回态而非异常；
        # 也**不在失败重试中暗换供应方**（需求 §7）。
        #
        # 只回**错误分类**，不回 `error_detail`：供应商的错误文本可能回显请求内容，
        # 而请求内容来自被测项目。细节的摘要已随出站记录落盘，供与供应商日志对账。
        return OutboundOutcome(
            status=OUTBOUND_BLOCKED,
            request=request,
            blocked_by=(f"model call failed: {result.error_kind}",),
        )

    assert content is not None
    return OutboundOutcome(
        status=OUTBOUND_DRAFT_READY, request=request, content=content
    )


def _template_version(template_ref: TemplateRef | None) -> str:
    if template_ref is None:
        return _PLACEHOLDER_TEMPLATE.version
    return template_ref.version


def _intent_payload(request: OutboundRequest) -> dict[str, object]:
    """出站**意图**的落盘 payload：在外部调用之前写入，不含任何调用结果。

    字段与结果 payload 保持同一套键，只多一个 `state` 标记，
    使"同一记录的第 1 修订是意图、第 2 修订是结果"可被程序化区分。
    """
    return {
        **_identity_fields(request),
        "requested_at": request.requested_at.isoformat(),
        "state": OUTBOUND_STATE_INTENT,
    }


def _outcome_payload(
    request: OutboundRequest, result: ModelCallResult
) -> dict[str, object]:
    """出站**结果**的落盘 payload：调用之后的真实事实。

    `error_detail` 只落**摘要**，不落正文：供应商的错误文本可能回显请求内容，
    而请求内容取自被测项目。摘要足以与供应商日志对账，正文不留在本地工作空间。
    """
    return {
        **_identity_fields(request),
        "provider_request_id": request.provider_request_id,
        "call_status": request.call_status,
        "error_kind": request.error_kind,
        "error_detail_digest": _text_digest(result.error_detail),
        "error_detail_chars": len(result.error_detail),
        "state": OUTBOUND_STATE_OUTCOME,
    }


def _identity_fields(request: OutboundRequest) -> dict[str, object]:
    """两次提交共用的请求身份字段。**不含任何凭据正文。**"""
    return {
        "project_id": request.project_id,
        "request_id": request.request_id,
        "task_type": request.task_type.value,
        "policy_revision": request.policy_revision,
        "source_revision": request.source_revision,
        "base_manual_revision": request.base_manual_revision,
        "material_kinds": [kind.value for kind in request.material_kinds],
        "item_count": request.item_count,
        "projection_digest": request.projection_digest,
        "endpoint_address": request.endpoint_address,
        "model_id": request.model_id,
        "credential_purpose": request.credential_purpose,
    }


def _content_payload(content: GeneratedContent, draft_text: str) -> dict[str, object]:
    """草稿的落盘 payload：**正文与摘要一起落**。

    只存元数据会让"模型确实产出了什么"没有可核对的字节，
    报告与导出也就无法引用真实内容。
    """
    return {
        "project_id": content.project_id,
        "generated_content_id": content.generated_content_id,
        "draft_kind": content.draft_kind,
        "template_id": content.template_ref.template_id,
        "template_version": content.template_ref.version,
        "revision": content.revision,
        "status": content.status,
        "content_digest": content.content_digest,
        "draft_text": draft_text,
        "revision_context": {
            "project_revision": content.revision_context.project_revision,
            "binding_revision": content.revision_context.binding_revision,
            "template_revision": content.revision_context.template_revision,
            "environment_revision": content.revision_context.environment_revision,
            "source_revision": content.revision_context.source_revision,
            "rules_revision": content.revision_context.rules_revision,
        },
    }


def _text_digest(text: str) -> str | None:
    """文本摘要；空文本返回 `None`（**不是**空串占位）。"""
    if not text.strip():
        return None
    return "sha256:" + sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 迟到响应


@dataclass(frozen=True, slots=True)
class ResponseSettlement:
    """迟到响应的处置结果。

    `accepted=False` 时 `content` 必为 `None`：**过期响应不得成为当前草稿**，
    但 `envelope` 保留作为历史事实（架构文档第 9 节"迟到响应归原来源"）。
    """

    currency: ResponseCurrency
    envelope: ModelResponseEnvelope
    content: GeneratedContent | None = None

    def __post_init__(self) -> None:
        if self.currency is ResponseCurrency.CURRENT:
            if self.content is None:
                raise ValueError("a current response must be settled into a draft")
            return
        if self.content is not None:
            raise ValueError(
                "a stale or superseded response must not become the current draft"
            )

    @property
    def accepted(self) -> bool:
        return self.content is not None


def settle_response(
    *,
    response: ModelResponseEnvelope,
    current_source_revision: int,
    current_policy_revision: int,
    current_manual_revision: int,
    project_id: str,
    draft_kind: str = DraftKind.CHECK_CONTENT,
    template_ref: TemplateRef | None = None,
    project_revision: int = 1,
    binding_revision: int = 1,
) -> ResponseSettlement:
    """处置一次（可能迟到的）模型响应。

    **人工版本优先**：`response_currency()` 先比人工修订，再比来源，最后比策略修订。
    判定为 `CURRENT` 才产出草稿；否则只保留响应事实，**不覆盖当前人工版本**。
    """
    currency = response_currency(
        response,
        current_source_revision=current_source_revision,
        current_policy_revision=current_policy_revision,
        current_manual_revision=current_manual_revision,
    )
    if currency is not ResponseCurrency.CURRENT:
        return ResponseSettlement(currency=currency, envelope=response)

    content = GeneratedContent(
        generated_content_id=f"draft:{project_id}:{draft_kind}:{response.response_id}",
        project_id=project_id,
        draft_kind=draft_kind,
        template_ref=template_ref if template_ref is not None else _PLACEHOLDER_TEMPLATE,
        revision=1,
        revision_context=RevisionContext(
            project_revision=project_revision,
            binding_revision=binding_revision,
            template_revision=_template_version(template_ref),
            source_revision=response.source_revision,
        ),
    )
    return ResponseSettlement(currency=currency, envelope=response, content=content)


__all__ = [
    "GENERATED_CONTENT_AGGREGATE",
    "OUTBOUND_AGGREGATE",
    "OUTBOUND_BLOCKED",
    "OUTBOUND_DRAFT_READY",
    "OUTBOUND_STATE_INTENT",
    "OUTBOUND_STATE_OUTCOME",
    "OutboundOutcome",
    "OutboundRequest",
    "ResponseSettlement",
    "outbound_request_id",
    "policy_record_id",
    "request_model_draft",
    "settle_response",
]
