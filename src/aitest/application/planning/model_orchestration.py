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

from aitest.application.planning.draft import (
    DraftKind,
    GeneratedContent,
    RevisionContext,
    generated_content_payload,
    text_digest,
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

#: 复用/冲突判定的结果状态。
#: `OUTBOUND_UNRESOLVED` 表示**同一业务意图已有出站意图、但没有已提交的结果**——
#: 此时不得盲目重发（外部副作用不可撤销），由调用方去核对原出站事实后再决定。
OUTBOUND_UNRESOLVED = "unresolved"

#: 已知凭据在落盘正文里的替换标记。**只记标记，不记原值。**
CREDENTIAL_PLACEHOLDER = "[FILTERED-CREDENTIAL]"

#: 过滤策略版本；策略口径变化时必须提升，便于事后判断某份草稿按哪一版过滤。
CREDENTIAL_FILTER_POLICY = "known-values@1"

#: 短于该长度的已知凭据**不接受**：过短的串无法被可靠识别，
#: 与其"假装过滤干净"，不如在调用之前就拒绝（fail closed）。
MIN_FILTERABLE_CREDENTIAL_LENGTH = 4

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
    """编排结果：**要么得到草稿，要么得到阻塞原因**，二者互斥。

    `reused_from` 非空表示这次**没有调用模型**，结果是按已提交的出站事实读回的
    （检查项 B-10）。它保留原出站 payload，使调用方能区分
    "新调用的草稿"与"复用的原结果"。
    """

    status: str
    request: OutboundRequest | None = None
    content: GeneratedContent | None = None
    blocked_by: tuple[str, ...] = ()
    response_currency: ResponseCurrency | None = None
    reused_from: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.status == OUTBOUND_DRAFT_READY:
            if self.request is None or self.content is None:
                raise ValueError("a ready draft requires both the request and the content")
            if self.blocked_by:
                raise ValueError("a ready draft must not carry blocking reasons")
            return
        if self.status not in {OUTBOUND_BLOCKED, OUTBOUND_UNRESOLVED}:
            raise ValueError(f"unknown outcome status: {self.status}")
        if not self.blocked_by:
            raise ValueError("a blocked outbound attempt must name at least one reason")
        if self.content is not None:
            raise ValueError("a blocked outbound attempt must not produce a draft")
        if self.status == OUTBOUND_UNRESOLVED and self.request is None:
            raise ValueError("an unresolved attempt must report the outbound request")

    @property
    def is_draft_ready(self) -> bool:
        return self.status == OUTBOUND_DRAFT_READY

    @property
    def is_reused(self) -> bool:
        """这次结果是复用已提交事实得到的，**没有产生新的外部调用**。"""
        return self.reused_from is not None


def _blocked(*reasons: str) -> OutboundOutcome:
    return OutboundOutcome(status=OUTBOUND_BLOCKED, blocked_by=tuple(reasons))


def policy_record_id(project_id: str) -> str:
    """项目级策略的记录标识（"`ModelOutboundPolicy` 按项目修订"，功能文档第 9 节）。"""
    return f"model-policy:{project_id}"


def outbound_request_id(
    project_id: str,
    policy_revision: int,
    task_type: ModelTaskType,
    generation_request_id: str | None = None,
) -> str:
    """出站记录标识。

    - 给出 `generation_request_id` 时按**（项目, 业务请求号）**稳定：同一业务意图
      永远映射到同一条记录，于是重传能读到原事实（检查项 B-10）；
    - 未给出时沿用旧的**（项目, 策略修订, 任务）**口径，保持既有行为可复现。
    """
    if generation_request_id is not None:
        if not generation_request_id.strip():
            raise ValueError("generation_request_id must not be empty when given")
        return f"outbound:{project_id}:{generation_request_id}"
    return f"outbound:{project_id}:{policy_revision}:{task_type.value}"


class ModelGenerationConflictError(RuntimeError):
    """同一业务请求号配上了不同输入（检查项 B-10）。

    不覆盖、不静默换输入：调用方要么按**原输入**重传（拿到原结果），
    要么用一个**新的** `generation_request_id` 明确重新生成。
    """


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


def _current_payload(
    reader: RecordReader, *, project_id: str, record_id: str
) -> dict[str, object] | None:
    """读该出站记录的**最新修订** payload；没有记录时返回 `None`。

    用于复用判定：`state=intent` 表示"调用发生过、结果未提交"；
    `state=outcome` 表示"结果已提交，可据此复用"。
    """
    page = reader.query(
        RecordQuery(
            project_id=project_id,
            aggregate_kind=OUTBOUND_AGGREGATE,  # type: ignore[arg-type]
            record_id=record_id,
        )
    )
    if not page.items:
        return None
    latest = max(page.items, key=lambda item: item.revision)
    return dict(latest.payload)


def _payload_revision(payload: Mapping[str, object], name: str) -> int:
    """从落盘 payload 读必需的修订号；缺失或类型不符即报错，不猜默认值。"""
    value = payload.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer in the stored payload")
    return value


def _optional_revision(payload: Mapping[str, object], name: str) -> int | None:
    """从落盘 payload 读可选修订号；`None` 表示**不适用**。"""
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer when present")
    return value


def _recall_existing_draft(
    reader: RecordReader, *, project_id: str, payload: Mapping[str, object]
) -> GeneratedContent | None:
    """按已提交的出站事实**读回原草稿对象**。

    只做"按引用读取"，不重算摘要、不重建正文——原结果就是原结果。
    """
    generated_content_id = payload.get("generated_content_id")
    if not isinstance(generated_content_id, str) or not generated_content_id.strip():
        return None
    revision = payload.get("generated_content_revision")
    if not isinstance(revision, int):
        return None
    try:
        record = reader.read(
            aggregate_kind=GENERATED_CONTENT_AGGREGATE,  # type: ignore[arg-type]
            record_id=generated_content_id,
            revision=revision,
        )
    except Exception:  # 记录缺失/不可读：不足以复用，交由调用方重新决定
        return None
    stored = dict(record.payload)
    template_id = stored.get("template_id")
    template_version = stored.get("template_version")
    context = stored.get("revision_context")
    if not isinstance(template_id, str) or not isinstance(template_version, str):
        return None
    if not isinstance(context, Mapping):
        return None
    raw_digest = stored.get("content_digest")
    content_digest: str | None = raw_digest if isinstance(raw_digest, str) else None
    return GeneratedContent(
        generated_content_id=generated_content_id,
        project_id=project_id,
        draft_kind=str(stored.get("draft_kind", "")),
        template_ref=TemplateRef(template_id=template_id, version=template_version),
        revision=revision,
        revision_context=RevisionContext(
            project_revision=_payload_revision(context, "project_revision"),
            binding_revision=_payload_revision(context, "binding_revision"),
            template_revision=str(context.get("template_revision", "")),
            environment_revision=_optional_revision(context, "environment_revision"),
            source_revision=_optional_revision(context, "source_revision"),
            rules_revision=_optional_revision(context, "rules_revision"),
        ),
        status=str(stored.get("status", "draft")),
        content_digest=content_digest,
    )


def _filter_known_credentials(
    text: str, known_credentials: tuple[str, ...]
) -> tuple[str, int]:
    """把**已知凭据值**从正文里剔除；返回（过滤后正文, 替换次数）。

    这是"落盘前"的最后一道闸（检查项 B-03）：供应商把请求内容回显在响应里时，
    凭据会顺着模型结果进入 `generated_content`。过滤按**精确值**做——
    已知凭据是确定的字符串，不需要猜模式，也不会误伤无关文本。
    只记替换次数，**不记被替换的原值**。
    """
    filtered = text
    replacements = 0
    # 长值优先：短值可能是长值的一部分，先替换长的可避免留下残片。
    for value in sorted(set(known_credentials), key=len, reverse=True):
        if value and value in filtered:
            replacements += filtered.count(value)
            filtered = filtered.replace(value, CREDENTIAL_PLACEHOLDER)
    return filtered, replacements


def _reject_short_credentials(known_credentials: tuple[str, ...]) -> str | None:
    """返回拒绝原因；`None` 表示凭据集合可用于过滤。"""
    too_short = sorted(
        {
            value
            for value in known_credentials
            if value and len(value) < MIN_FILTERABLE_CREDENTIAL_LENGTH
        }
    )
    if too_short:
        return (
            "known credential value(s) are too short to filter reliably "
            f"(min {MIN_FILTERABLE_CREDENTIAL_LENGTH} chars): "
            f"{len(too_short)} value(s)"
        )
    return None


def _credential_filter_payload(replacements: int) -> dict[str, object]:
    """落盘用的过滤事实：**只说做过什么，不含任何凭据正文或摘要**。"""
    return {
        "policy": CREDENTIAL_FILTER_POLICY,
        "replacements": replacements,
        "filtered": replacements > 0,
    }


def _generation_identity(request: OutboundRequest) -> dict[str, object]:
    """同一业务意图下"输入是否相同"的判定依据。

    **不含 `requested_at`**：时间不同不构成"异输入"，否则重传会被误判为冲突。
    也不含 `request_id`：身份由调用方给出的 `generation_request_id` 表达，
    这里只判"同键是否同输入"。
    """
    return {
        "task_type": request.task_type.value,
        "policy_revision": request.policy_revision,
        "source_revision": request.source_revision,
        "base_manual_revision": request.base_manual_revision,
        "material_kinds": [kind.value for kind in request.material_kinds],
        "item_count": request.item_count,
        "projection_digest": request.projection_digest,
        "endpoint_address": request.endpoint_address,
        "model_id": request.model_id,
    }


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
    generation_request_id: str | None = None,
    known_credentials: tuple[str, ...] = (),
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
    5. **同一业务意图的复用判定** → 同键同输入返回原结果、同键异输入报冲突、
       仅有意图而无结果则拒绝盲发（检查项 B-10）；
    6. **调用** → 失败即阻塞并归类错误，**不暗换供应方**、**不自动重试**；
    7. **落盘前凭据过滤** → 已知凭据在写进 `generated_content` 之前被剔除（检查项 B-03）；
    8. 成功 → 登记出站记录并产出 `GeneratedContent(status=draft)`。

    第 1、2 步在**投影之前**：先拦住不该送的材料，再花代价做投影。

    另外**策略必须属于本次请求的项目**（检查项 B-12）：`ModelOutboundPolicy` 自带
    `project_id`，过去准入只看 AI 开关、确认状态与材料类别，不看策略归谁，于是
    "用 A 项目的出站策略为 B 项目准备请求"也能通过。策略是项目级授权，
    借用别的项目的策略等于绕过本项目的出站授权。

    ## `generation_request_id`：一次"明确生成"的持久业务身份（B-10）

    过去出站记录 ID 只按（项目, 策略修订, 任务）算，两次调用**共用同一条记录**，
    却各调一次模型、各存一份草稿——稳定记录 ID 并没有去重外部副作用。
    现在由调用方给出 `generation_request_id`（**持久业务请求号**）：

    - 同键**同输入** → 返回**原结果**，不再调用模型（不产生第二次外部副作用）；
    - 同键**异输入** → `ModelGenerationConflictError`（不覆盖、不静默换输入）；
    - **明确重新生成** → 调用方给**新的** `generation_request_id`；
    - 同键已有意图但结果未提交（响应丢失）→ 返回 `OUTBOUND_UNRESOLVED`，**不盲目重发**，
      由调用方先核对原出站事实。`generation_request_id` 省略时退化为旧的
      （项目, 策略修订, 任务）口径，保持既有行为可复现。

    ## `known_credentials`：落盘前的已知凭据（B-03）

    传入本项目已知的凭据值，模型响应在写进 `generated_content` **之前**按精确值剔除，
    并在 payload 里登记"过滤了几个"（不记原值）。值过短（
    `< MIN_FILTERABLE_CREDENTIAL_LENGTH`）时**在调用之前**拒绝：无法可靠识别的凭据
    不接受"假装过滤干净"。
    """
    if generation_request_id is not None and not generation_request_id.strip():
        raise ValueError("generation_request_id must be a non-empty string when given")
    if not project_id.strip():
        raise ValueError("project_id must not be empty")
    if policy.project_id != project_id:
        return _blocked(
            f"the model outbound policy belongs to project {policy.project_id!r}, "
            f"not to {project_id!r}"
        )

    short_credential = _reject_short_credentials(known_credentials)
    if short_credential is not None:
        return _blocked(short_credential)

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
        request_id=outbound_request_id(
            project_id, policy.revision, task_type, generation_request_id
        ),
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

    # 步骤 5：**同一业务意图的复用 / 冲突 / 未决判定**（检查项 B-10）。
    # 判定只读、且在写事务之外完成，避免持锁期间做查询。
    identity = _generation_identity(request)
    existing = _current_payload(
        reader, project_id=project_id, record_id=request.request_id
    )
    if existing is not None:
        stored_identity = existing.get("generation_identity")
        if stored_identity is not None:
            if not isinstance(stored_identity, Mapping):
                raise ValueError("stored generation_identity must be an object")
            if dict(stored_identity) != identity:
                raise ModelGenerationConflictError(
                    f"{request.request_id} was already used with different input"
                )
        if existing.get("state") == OUTBOUND_STATE_OUTCOME:
            recalled = _recall_existing_draft(
                reader, project_id=project_id, payload=existing
            )
            recallable = {
                **existing,
                "state": OUTBOUND_STATE_OUTCOME,
            }
            if existing.get("call_status") != ModelCallStatus.OK.value:
                # 上次调用**已失败并落盘**：原结果就是"失败"，不重发（不自动重试）。
                return OutboundOutcome(
                    status=OUTBOUND_BLOCKED,
                    request=request,
                    blocked_by=(
                        "an identical generation request already failed: "
                        f"{existing.get('error_kind')}",
                    ),
                )
            if recalled is not None:
                # **复用原结果**：不再调用模型，外部副作用为零。
                return OutboundOutcome(
                    status=OUTBOUND_DRAFT_READY,
                    request=request,
                    content=recalled,
                    reused_from=recallable,
                )
        if existing.get("state") == OUTBOUND_STATE_INTENT:
            # 意图已落盘、结果未提交：外部调用**可能已经发生**。盲目重发会制造
            # 第二次不可撤销的副作用，因此交由调用方核对原事实后决定。
            return OutboundOutcome(
                status=OUTBOUND_UNRESOLVED,
                request=request,
                blocked_by=(
                    "an earlier attempt for this generation request has no committed "
                    "result; inspect the outbound record before deciding to regenerate",
                ),
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
            payload=_intent_payload(request, identity=identity),
        )
        intent = tx.commit()
    intent_revision = intent.revision_of(
        OUTBOUND_AGGREGATE, request.request_id  # type: ignore[arg-type]
    ).revision

    # 步骤 6b：事务外调用。**不持有写事务**发起外部请求。
    result: ModelCallResult = caller.call(call)

    # 调用结果并入同一条出站事实：修订 1 是意图，修订 2 是结果。
    request = replace(
        request,
        provider_request_id=result.provider_request_id,
        call_status=result.status.value,
        error_kind=result.error_kind,
    )

    # 步骤 7：**落盘前的凭据过滤**（检查项 B-03）。供应商回显请求内容时，
    # 凭据会顺着模型结果进入 generated_content；过滤必须发生在写盘之前。
    filtered_text, replacements = _filter_known_credentials(
        result.draft_text, tuple(known_credentials)
    )
    filter_fact = _credential_filter_payload(replacements)

    # 步骤 8：提交安全响应；成功时把草稿正文与其引用放在**同一次提交**里。
    content: GeneratedContent | None = None
    with transaction(unit_of_work, project_id) as tx:
        tx.stage_record(
            aggregate_kind=OUTBOUND_AGGREGATE,  # type: ignore[arg-type]
            record_id=request.request_id,
            expected_revision=intent_revision,
            payload=_outcome_payload(
                request,
                result,
                identity=identity,
                generated_content_id=(
                    f"draft:{project_id}:{draft_kind}:{request.request_id}:{intent_revision}"
                    if result.status is ModelCallStatus.OK
                    else None
                ),
                generated_content_revision=(
                    intent_revision if result.status is ModelCallStatus.OK else None
                ),
                credential_filter=filter_fact,
            ),
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
                # 摘要按**过滤后**的正文算：它就是将来会被读回的那份字节。
                content_digest=text_digest(filtered_text),
            )
            tx.stage_record(
                aggregate_kind=GENERATED_CONTENT_AGGREGATE,  # type: ignore[arg-type]
                record_id=content.generated_content_id,
                expected_revision=None,
                payload=generated_content_payload(
                    content, filtered_text, credential_filter=filter_fact
                ),
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


def _intent_payload(
    request: OutboundRequest, *, identity: Mapping[str, object]
) -> dict[str, object]:
    """出站**意图**的落盘 payload：在外部调用之前写入，不含任何调用结果。

    字段与结果 payload 保持同一套键，只多一个 `state` 标记，
    使"同一记录的第 1 修订是意图、第 2 修订是结果"可被程序化区分。

    `generation_identity` 是"同键是否同输入"的判定依据（检查项 B-10）：
    它随**意图**一起落盘，因此重传时不必重新推导就能判冲突。
    """
    return {
        **_identity_fields(request),
        "requested_at": request.requested_at.isoformat(),
        "generation_identity": dict(identity),
        "state": OUTBOUND_STATE_INTENT,
    }


def _outcome_payload(
    request: OutboundRequest,
    result: ModelCallResult,
    *,
    identity: Mapping[str, object],
    generated_content_id: str | None,
    generated_content_revision: int | None,
    credential_filter: Mapping[str, object],
) -> dict[str, object]:
    """出站**结果**的落盘 payload：调用之后的真实事实。

    `error_detail` 只落**摘要**，不落正文：供应商的错误文本可能回显请求内容，
    而请求内容取自被测项目。摘要足以与供应商日志对账，正文不留在本地工作空间。

    `generated_content_id` / `generated_content_revision` 让复用分支能**按引用读回
    原草稿**（检查项 B-10）；`credential_filter` 记录落盘前过滤掉了几个已知凭据
    （检查项 B-03），**只有计数，没有原值或摘要**。
    """
    return {
        **_identity_fields(request),
        "provider_request_id": request.provider_request_id,
        "call_status": request.call_status,
        "error_kind": request.error_kind,
        "error_detail_digest": text_digest(result.error_detail),
        "error_detail_chars": len(result.error_detail),
        "generation_identity": dict(identity),
        "generated_content_id": generated_content_id,
        "generated_content_revision": generated_content_revision,
        "credential_filter": dict(credential_filter),
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
    "CREDENTIAL_FILTER_POLICY",
    "CREDENTIAL_PLACEHOLDER",
    "GENERATED_CONTENT_AGGREGATE",
    "MIN_FILTERABLE_CREDENTIAL_LENGTH",
    "ModelGenerationConflictError",
    "OUTBOUND_AGGREGATE",
    "OUTBOUND_BLOCKED",
    "OUTBOUND_DRAFT_READY",
    "OUTBOUND_STATE_INTENT",
    "OUTBOUND_STATE_OUTCOME",
    "OUTBOUND_UNRESOLVED",
    "OutboundOutcome",
    "OutboundRequest",
    "ResponseSettlement",
    "outbound_request_id",
    "policy_record_id",
    "request_model_draft",
    "settle_response",
]
