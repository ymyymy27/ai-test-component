"""模型请求编排：准入、投影、调用、迟到响应。

依据：需求 §7「模型出站」与 P1-AC32；架构文档《01-项目与计划》第 9 节；
`docs/文档-feix-a/B包/14-模型请求编排设计说明.md`。
"""

import pytest

from aitest.application.planning.model_orchestration import (
    OUTBOUND_BLOCKED,
    OUTBOUND_DRAFT_READY,
    OutboundOutcome,
    OutboundRequest,
    ResponseSettlement,
    outbound_request_id,
    policy_record_id,
    request_model_draft,
    settle_response,
)
from aitest.application.planning.model_ports import (
    CredentialResolution,
    CredentialStatus,
    ModelCall,
    ModelCaller,
    ModelCallResult,
    ModelCallStatus,
    Projection,
    ProjectionStatus,
)
from aitest.application.planning.substrate import RecordQuery
from aitest.domain.planning.model_outbound import (
    MaterialKind,
    ModelEndpoint,
    ModelOutboundPolicy,
    ModelResponseEnvelope,
    ModelTaskType,
    OutboundConfirmation,
    ResponseCurrency,
    endpoint_digest,
    material_kinds_digest,
)
from aitest.domain.planning.templates import TemplateRef
from tests.support.memory_model import (
    MemoryCredentialResolver,
    MemoryModelCaller,
    MemoryProjector,
)
from tests.support.memory_substrate import (
    FixedClock,
    MemoryReader,
    MemoryStore,
    MemoryUnitOfWork,
)

PROJECT_ID = "project-ticket"
ALL_KINDS = frozenset(MaterialKind)


def _policy(**overrides: object) -> ModelOutboundPolicy:
    allowed = overrides.pop("allowed_material_kinds", ALL_KINDS)
    endpoint = overrides.pop(
        "endpoint",
        ModelEndpoint(
            provider="deepseek", address="https://api.example/v1", model_id="chat"
        ),
    )
    assert isinstance(allowed, frozenset)
    assert isinstance(endpoint, ModelEndpoint)
    confirmed = overrides.pop("confirmed", True)
    assert isinstance(confirmed, bool)

    draft = ModelOutboundPolicy(
        project_id=PROJECT_ID,
        revision=overrides.pop("revision", 1),  # type: ignore[arg-type]
        endpoint=endpoint,
        allowed_material_kinds=allowed,
        **overrides,  # type: ignore[arg-type]
    )
    if not confirmed:
        return draft
    return ModelOutboundPolicy(
        project_id=draft.project_id,
        revision=draft.revision,
        endpoint=draft.endpoint,
        allowed_material_kinds=draft.allowed_material_kinds,
        source_snippets_enabled=draft.source_snippets_enabled,
        ai_enabled=draft.ai_enabled,
        revoked_material_kinds=draft.revoked_material_kinds,
        confirmation=OutboundConfirmation(
            confirmation_id="confirmation-1",
            endpoint_digest=endpoint_digest(draft.endpoint),
            material_kinds_digest=material_kinds_digest(draft),
            source_snippets_enabled=draft.source_snippets_enabled,
            confirmed_at_commit="commit-1",
        ),
    )


def _world() -> tuple[MemoryUnitOfWork, MemoryReader]:
    store = MemoryStore()
    return MemoryUnitOfWork(store), MemoryReader(store)


class _IntentProbeCaller:
    """调用发生时检查出站意图是否已经提交可见。"""

    def __init__(self, reader: MemoryReader) -> None:
        self._reader = reader
        self.call_count = 0
        self.intent_visible_at_call = False

    def call(self, request: ModelCall) -> ModelCallResult:
        self.call_count += 1
        page = self._reader.query(
            RecordQuery(project_id=PROJECT_ID, aggregate_kind="model_outbound_request")
        )
        self.intent_visible_at_call = any(
            item.payload.get("state") == "intent" for item in page.items
        )
        return ModelCallResult(
            status=ModelCallStatus.OK,
            draft_text="draft: proposed checks",
            provider_request_id="provider-request-1",
        )


def _request(
    *,
    policy: ModelOutboundPolicy | None = None,
    task_type: ModelTaskType = ModelTaskType.CHECK_CONTENT_DRAFT,
    material: dict[MaterialKind, str] | None = None,
    unit_of_work: MemoryUnitOfWork | None = None,
    reader: MemoryReader | None = None,
    projector: MemoryProjector | None = None,
    credentials: MemoryCredentialResolver | None = None,
    caller: ModelCaller | None = None,
    source_revision: int = 1,
    template_ref: TemplateRef | None = None,
) -> OutboundOutcome:
    if unit_of_work is None or reader is None:
        unit_of_work, reader = _world()
    return request_model_draft(
        project_id=PROJECT_ID,
        policy=policy if policy is not None else _policy(),
        task_type=task_type,
        selected_material=(
            material
            if material is not None
            else {MaterialKind.PROJECT_CONTEXT: "the project has two modules"}
        ),
        unit_of_work=unit_of_work,
        reader=reader,
        projector=projector if projector is not None else MemoryProjector(),
        credentials=credentials if credentials is not None else MemoryCredentialResolver(),
        caller=caller if caller is not None else MemoryModelCaller(),
        clock=FixedClock(),
        source_revision=source_revision,
        base_manual_revision=0,
        template_ref=template_ref
        if template_ref is not None
        else TemplateRef(template_id="ticket-workflow", version="1.0.0"),
    )


# ------------------------------------------------------------------ 正常路径


def test_successful_request_produces_a_draft() -> None:
    outcome = _request()
    assert outcome.status == OUTBOUND_DRAFT_READY
    assert outcome.is_draft_ready
    assert outcome.blocked_by == ()
    assert outcome.content is not None
    assert outcome.content.status == "draft"
    assert outcome.content.draft_kind == "check_content"


def test_successful_request_records_what_was_actually_sent() -> None:
    unit_of_work, reader = _world()
    outcome = _request(unit_of_work=unit_of_work, reader=reader)
    assert outcome.request is not None
    assert outcome.request.material_kinds == (MaterialKind.PROJECT_CONTEXT,)
    assert outcome.request.item_count == 1
    assert outcome.request.projection_digest.startswith("sha256:projection-")

    # 修订 1 是**调用之前**落盘的意图，修订 2 才是调用结果。
    intent = reader.read(
        aggregate_kind="model_outbound_request",
        record_id=outcome.request.request_id,
        revision=1,
    )
    assert intent.payload["state"] == "intent"
    assert intent.payload["item_count"] == 1
    assert intent.payload["material_kinds"] == ["project_context"]
    assert "call_status" not in intent.payload

    record = reader.read(
        aggregate_kind="model_outbound_request",
        record_id=outcome.request.request_id,
        revision=2,
    )
    assert record.payload["state"] == "outcome"
    assert record.payload["item_count"] == 1
    assert record.payload["material_kinds"] == ["project_context"]
    assert record.payload["call_status"] == "ok"
    assert record.payload["provider_request_id"] == "provider-request-1"


def test_only_projected_material_reaches_the_caller() -> None:
    """关键：调用者拿到的**只有投影后的字节**，编排不把原始对象交出去。"""
    unit_of_work, reader = _world()
    caller = MemoryModelCaller()
    _request(
        unit_of_work=unit_of_work,
        reader=reader,
        material={MaterialKind.PROJECT_CONTEXT: "module list: ticket, store"},
        caller=caller,
    )
    assert caller.call_count == 1
    sent = caller.calls[0]
    assert sent.projected[0].projected_text == "module list: ticket, store"
    assert sent.projection_digest.startswith("sha256:projection-")
    assert sent.model_id == "chat"
    assert sent.policy_revision == 1


def test_credentials_never_reach_the_record() -> None:
    """需求 P1-AC32：**凭据正文不进配置、日志、面板、导出**。"""
    unit_of_work, reader = _world()
    outcome = _request(unit_of_work=unit_of_work, reader=reader)
    assert outcome.request is not None
    record = reader.read(
        aggregate_kind="model_outbound_request",
        record_id=outcome.request.request_id,
        revision=2,
    )
    dumped = repr(record.payload)
    for marker in ("api_key", "authorization", "bearer", "sk-"):
        assert marker not in dumped.lower()
    # 只记用途，不记凭据本身
    assert record.payload["credential_purpose"] == "model"


def test_material_containing_a_credential_is_never_sent() -> None:
    """含疑似凭据的材料**整项排除并阻塞**，绝不"剥掉一行继续发"。

    这比"部分脱敏后放行"更保守，因为部分材料被摘掉后，
    模型看到的就不是完整上下文，而报告里也没有对应缺口 —— 那会伪造完整证据。
    """
    unit_of_work, reader = _world()
    caller = MemoryModelCaller()
    outcome = _request(
        unit_of_work=unit_of_work,
        reader=reader,
        material={
            MaterialKind.PROJECT_CONTEXT: "module list\napi_key=SUPER-SECRET-VALUE"
        },
        caller=caller,
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("excluded" in reason for reason in outcome.blocked_by)
    assert caller.call_count == 0


def test_clean_material_is_sent_unchanged() -> None:
    unit_of_work, reader = _world()
    caller = MemoryModelCaller()
    outcome = _request(
        unit_of_work=unit_of_work,
        reader=reader,
        material={MaterialKind.PROJECT_CONTEXT: "module list: ticket, store"},
        caller=caller,
    )
    assert outcome.status == OUTBOUND_DRAFT_READY
    assert caller.call_count == 1
    assert caller.calls[0].projected[0].projected_text == "module list: ticket, store"


def test_material_that_is_entirely_credential_is_excluded() -> None:
    """整项都是疑似凭据 → 排除并显示缺口，**不把空内容当成功**。"""
    unit_of_work, reader = _world()
    caller = MemoryModelCaller()
    outcome = _request(
        unit_of_work=unit_of_work,
        reader=reader,
        material={MaterialKind.PROJECT_CONTEXT: "api_key=SUPER-SECRET-VALUE"},
        caller=caller,
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("excluded" in reason for reason in outcome.blocked_by)
    assert caller.call_count == 0


# ------------------------------------------------------------------ 准入阻塞


def test_disabled_ai_blocks_the_request_without_calling_out() -> None:
    unit_of_work, reader = _world()
    caller = MemoryModelCaller()
    outcome = _request(
        policy=_policy(ai_enabled=False),
        unit_of_work=unit_of_work,
        reader=reader,
        caller=caller,
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("AI is disabled" in reason for reason in outcome.blocked_by)
    assert caller.call_count == 0
    assert unit_of_work.commit_seq() == "commit-0"


def test_unconfirmed_scope_blocks_the_request() -> None:
    outcome = _request(policy=_policy(confirmed=False))
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("not confirmed" in reason for reason in outcome.blocked_by)


def test_material_outside_the_policy_is_blocked() -> None:
    outcome = _request(
        policy=_policy(allowed_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT})),
        material={MaterialKind.CASE_CONTENT: "case body"},
        task_type=ModelTaskType.CASE_SUGGESTION,
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("not allowed by the policy" in reason for reason in outcome.blocked_by)


def test_material_outside_the_task_is_blocked() -> None:
    """跨任务夹带：交付说明草稿任务不得携带证据材料。"""
    outcome = _request(
        task_type=ModelTaskType.DELIVERY_DRAFT,
        material={MaterialKind.EVIDENCE_REFERENCE: "evidence body"},
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("not allowed for task" in reason for reason in outcome.blocked_by)


def test_source_snippet_is_blocked_when_the_switch_is_closed() -> None:
    outcome = _request(
        material={MaterialKind.SOURCE_SNIPPET: "def handler(): ..."},
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("source snippets are disabled" in reason for reason in outcome.blocked_by)


def test_smuggling_a_snippet_under_another_kind_is_blocked() -> None:
    """核心反例：源码片段开关关闭时，把源码声明成"项目上下文"也送不出去。

    `DELIVERY_DRAFT` 任务既不允许 `SOURCE_SNIPPET`，也不允许 `MODULE_DEPENDENCY_FACTS`，
    因此这一步在**准入**就被拦下。
    """
    outcome = _request(
        task_type=ModelTaskType.DELIVERY_DRAFT,
        material={MaterialKind.MODULE_DEPENDENCY_FACTS: "def handler(): ..."},
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("not allowed for task" in reason for reason in outcome.blocked_by)


def test_revoked_kind_is_blocked() -> None:
    outcome = _request(
        policy=_policy(
            allowed_material_kinds=frozenset(
                {MaterialKind.PROJECT_CONTEXT, MaterialKind.CASE_CONTENT}
            ),
            revoked_material_kinds=frozenset({MaterialKind.CASE_CONTENT}),
        ),
        task_type=ModelTaskType.CASE_SUGGESTION,
        material={MaterialKind.CASE_CONTENT: "case body"},
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("not allowed by the policy" in reason for reason in outcome.blocked_by)


# ------------------------------------------------------------------ 凭据


def test_missing_credential_blocks_without_calling_out() -> None:
    unit_of_work, reader = _world()
    caller = MemoryModelCaller()
    outcome = _request(
        credentials=MemoryCredentialResolver(available_purposes=()),
        unit_of_work=unit_of_work,
        reader=reader,
        caller=caller,
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("credential is not usable" in reason for reason in outcome.blocked_by)
    assert caller.call_count == 0


def test_credential_for_another_purpose_is_not_reused() -> None:
    """凭据分用途：只有 HTTP 凭据时，模型出站不得借用它。"""
    outcome = _request(
        credentials=MemoryCredentialResolver(available_purposes=("http",))
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("purpose_mismatch" in reason for reason in outcome.blocked_by)


def test_credential_resolution_carries_no_body() -> None:
    resolution = MemoryCredentialResolver().resolve(purpose="model")
    assert resolution.usable
    assert not hasattr(resolution, "secret")
    assert not hasattr(resolution, "value")
    with pytest.raises(ValueError, match="explain why"):
        CredentialResolution(status=CredentialStatus.MISSING, purpose="model")


# ------------------------------------------------------------------ 投影缺口


def test_excluded_material_blocks_and_names_the_gap() -> None:
    """无法安全投影 → 排除并显示缺口，**不伪造完整证据**。"""
    unit_of_work, reader = _world()
    caller = MemoryModelCaller()
    outcome = _request(
        projector=MemoryProjector(force_exclusions=(MaterialKind.PROJECT_CONTEXT,)),
        unit_of_work=unit_of_work,
        reader=reader,
        caller=caller,
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("excluded" in reason for reason in outcome.blocked_by)
    assert caller.call_count == 0


def test_partial_projection_must_name_what_it_excluded() -> None:
    with pytest.raises(ValueError, match="must name the excluded material"):
        Projection(status=ProjectionStatus.PARTIAL)
    with pytest.raises(ValueError, match="must not exclude material"):
        Projection(
            status=ProjectionStatus.COMPLETE,
            projected=(),
            excluded=((MaterialKind.PROJECT_CONTEXT, "material.project_context"),),
        )


# ------------------------------------------------------------------ 模型失败


def test_model_failure_blocks_and_classifies_the_error() -> None:
    unit_of_work, reader = _world()
    outcome = _request(
        caller=MemoryModelCaller(error_kind="timeout", error_detail="after 60s"),
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert any("timeout" in reason for reason in outcome.blocked_by)
    assert outcome.content is None
    assert outcome.request is not None
    assert outcome.request.error_kind == "timeout"


def test_model_failure_still_records_the_outbound_fact() -> None:
    """失败也是一次真实发生的出站事实，必须留记录（需求要求保留请求关联编号与错误）。"""
    unit_of_work, reader = _world()
    outcome = _request(
        caller=MemoryModelCaller(error_kind="rate_limited"),
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert outcome.request is not None
    record = reader.read(
        aggregate_kind="model_outbound_request",
        record_id=outcome.request.request_id,
        revision=2,
    )
    assert record.payload["call_status"] == "failed"
    assert record.payload["error_kind"] == "rate_limited"


def test_a_failed_call_does_not_echo_the_provider_detail() -> None:
    """失败只回错误分类；供应商的错误正文可能回显请求内容，不落盘也不回显。"""
    unit_of_work, reader = _world()
    marker = "echoed material: module list ticket, store"
    outcome = _request(
        caller=MemoryModelCaller(error_kind="timeout", error_detail=marker),
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert outcome.status == OUTBOUND_BLOCKED
    assert all(marker not in reason for reason in outcome.blocked_by)
    assert outcome.request is not None
    record = reader.read(
        aggregate_kind="model_outbound_request",
        record_id=outcome.request.request_id,
        revision=2,
    )
    assert marker not in repr(record.payload)
    # 细节只留摘要与长度，供与供应商日志对账
    detail_digest = record.payload["error_detail_digest"]
    assert isinstance(detail_digest, str)
    assert detail_digest.startswith("sha256:")
    assert record.payload["error_detail_chars"] == len(marker)


def test_the_intent_is_committed_before_the_model_is_called() -> None:
    """外部调用是**不可撤销的副作用**：调用发生时必须已经能读到出站意图。"""
    unit_of_work, reader = _world()
    caller = _IntentProbeCaller(reader)
    _request(unit_of_work=unit_of_work, reader=reader, caller=caller)
    assert caller.call_count == 1
    assert caller.intent_visible_at_call is True


def test_the_successful_call_commits_the_intent_before_the_outcome() -> None:
    """意图与结果分属两次提交，序号说明调用发生在两次提交之间。"""
    unit_of_work, reader = _world()
    outcome = _request(unit_of_work=unit_of_work, reader=reader)
    assert outcome.request is not None
    assert unit_of_work.commit_seq() == "commit-2"
    assert (
        reader.read(
            aggregate_kind="model_outbound_request",
            record_id=outcome.request.request_id,
            revision=1,
        ).payload["state"]
        == "intent"
    )


def test_the_draft_text_is_stored_with_its_digest() -> None:
    """草稿正文与摘要一起落盘：只存元数据会让"模型产出了什么"没有可核对的字节。"""
    from hashlib import sha256

    unit_of_work, reader = _world()
    outcome = _request(unit_of_work=unit_of_work, reader=reader)
    assert outcome.content is not None
    expected = "sha256:" + sha256(b"draft: proposed checks").hexdigest()
    assert outcome.content.content_digest == expected

    page = reader.query(
        RecordQuery(
            project_id=PROJECT_ID, aggregate_kind="generated_content"
        )
    )
    assert len(page.items) == 1
    stored = page.items[0].payload
    assert stored["draft_text"] == "draft: proposed checks"
    assert stored["content_digest"] == expected
    assert stored["generated_content_id"] == outcome.content.generated_content_id


def test_failure_does_not_retry_or_switch_provider() -> None:
    """需求 §7：**不在失败重试中暗换供应商**。"""
    unit_of_work, reader = _world()
    caller = MemoryModelCaller(error_kind="timeout")
    _request(caller=caller, unit_of_work=unit_of_work, reader=reader)
    assert caller.call_count == 1
    assert caller.calls[0].endpoint_address == "https://api.example/v1"


def test_human_path_stays_available_after_a_failure() -> None:
    """模型不可用时人工路径仍可用：编排只返回阻塞态，不抛异常。"""
    unit_of_work, reader = _world()
    outcome = _request(
        caller=MemoryModelCaller(error_kind="timeout"),
        unit_of_work=unit_of_work,
        reader=reader,
    )
    assert outcome.status == OUTBOUND_BLOCKED


def test_repeating_the_same_request_lands_a_new_record_revision() -> None:
    unit_of_work, reader = _world()
    first = _request(unit_of_work=unit_of_work, reader=reader)
    second = _request(unit_of_work=unit_of_work, reader=reader)
    assert first.request is not None and second.request is not None
    assert first.request.request_id == second.request.request_id
    # 每条请求两次提交：意图一次、结果一次。
    assert unit_of_work.commit_seq() == "commit-4"
    assert (
        reader.read(
            aggregate_kind="model_outbound_request",
            record_id=first.request.request_id,
            revision=2,
        ).payload["policy_revision"]
        == 1
    )
    assert (
        reader.read(
            aggregate_kind="model_outbound_request",
            record_id=first.request.request_id,
            revision=3,
        ).payload["state"]
        == "intent"
    )
    assert first.content is not None and second.content is not None
    assert first.content.generated_content_id != second.content.generated_content_id


# ------------------------------------------------------------------ 迟到响应


def _envelope(**overrides: object) -> ModelResponseEnvelope:
    values: dict[str, object] = {
        "response_id": "response-1",
        "request_revision": 3,
        "source_revision": 7,
        "base_manual_revision": 2,
        "received_at_commit": "commit-9",
    }
    values.update(overrides)
    return ModelResponseEnvelope(**values)  # type: ignore[arg-type]


def test_current_response_becomes_a_draft() -> None:
    settlement = settle_response(
        response=_envelope(),
        current_source_revision=7,
        current_policy_revision=3,
        current_manual_revision=2,
        project_id=PROJECT_ID,
    )
    assert settlement.currency is ResponseCurrency.CURRENT
    assert settlement.accepted
    assert settlement.content is not None
    assert settlement.content.status == "draft"


def test_manual_edit_supersedes_a_late_response() -> None:
    """人工版本优先：用户已手改，迟到响应**不得覆盖**。"""
    settlement = settle_response(
        response=_envelope(),
        current_source_revision=7,
        current_policy_revision=3,
        current_manual_revision=4,
        project_id=PROJECT_ID,
    )
    assert settlement.currency is ResponseCurrency.SUPERSEDED_BY_MANUAL
    assert settlement.accepted is False
    assert settlement.content is None
    assert settlement.envelope.response_id == "response-1"


def test_changed_source_marks_the_response_stale() -> None:
    settlement = settle_response(
        response=_envelope(),
        current_source_revision=8,
        current_policy_revision=3,
        current_manual_revision=2,
        project_id=PROJECT_ID,
    )
    assert settlement.currency is ResponseCurrency.SOURCE_CHANGED
    assert settlement.accepted is False


def test_changed_policy_marks_the_response_stale() -> None:
    settlement = settle_response(
        response=_envelope(),
        current_source_revision=7,
        current_policy_revision=4,
        current_manual_revision=2,
        project_id=PROJECT_ID,
    )
    assert settlement.currency is ResponseCurrency.SOURCE_CHANGED


def test_settlement_cannot_accept_a_stale_response() -> None:
    from aitest.application.planning.draft import GeneratedContent, RevisionContext

    with pytest.raises(ValueError, match="must not become the current draft"):
        ResponseSettlement(
            currency=ResponseCurrency.SOURCE_CHANGED,
            envelope=_envelope(),
            content=GeneratedContent(
                generated_content_id="d1",
                project_id=PROJECT_ID,
                draft_kind="check_content",
                template_ref=TemplateRef(template_id="t", version="1.0.0"),
                revision=1,
                revision_context=RevisionContext(
                    project_revision=1, binding_revision=1, template_revision="1.0.0"
                ),
            ),
        )
    with pytest.raises(ValueError, match="must be settled into a draft"):
        ResponseSettlement(currency=ResponseCurrency.CURRENT, envelope=_envelope())


# ------------------------------------------------------------------ 值对象


def test_outcome_requires_exactly_one_of_draft_or_reasons() -> None:
    with pytest.raises(ValueError, match="at least one reason"):
        OutboundOutcome(status=OUTBOUND_BLOCKED)
    with pytest.raises(ValueError, match="requires both the request and the content"):
        OutboundOutcome(status=OUTBOUND_DRAFT_READY)
    with pytest.raises(ValueError, match="unknown outcome status"):
        OutboundOutcome(status="passed")


def test_call_result_cannot_mix_success_and_failure() -> None:
    with pytest.raises(ValueError, match="must carry draft text"):
        ModelCallResult(status=ModelCallStatus.OK)
    with pytest.raises(ValueError, match="must not carry draft text"):
        ModelCallResult(
            status=ModelCallStatus.FAILED, draft_text="x", error_kind="timeout"
        )
    with pytest.raises(ValueError, match="must classify its error"):
        ModelCallResult(status=ModelCallStatus.FAILED)


def test_request_requires_positive_revisions_and_non_empty_identity() -> None:
    with pytest.raises(ValueError, match="request_id"):
        OutboundRequest(
            request_id=" ",
            project_id=PROJECT_ID,
            task_type=ModelTaskType.CONTEXT_SUMMARY,
            policy_revision=1,
            source_revision=1,
            base_manual_revision=0,
            material_kinds=(MaterialKind.PROJECT_CONTEXT,),
            item_count=1,
            projection_digest="sha256:p",
            endpoint_address="https://api.example/v1",
            model_id="chat",
            credential_purpose="model",
            requested_at=FixedClock().now(),
        )
    with pytest.raises(ValueError, match="policy_revision"):
        OutboundRequest(
            request_id="r1",
            project_id=PROJECT_ID,
            task_type=ModelTaskType.CONTEXT_SUMMARY,
            policy_revision=0,
            source_revision=1,
            base_manual_revision=0,
            material_kinds=(MaterialKind.PROJECT_CONTEXT,),
            item_count=1,
            projection_digest="sha256:p",
            endpoint_address="https://api.example/v1",
            model_id="chat",
            credential_purpose="model",
            requested_at=FixedClock().now(),
        )


def test_record_ids_are_stable_and_project_scoped() -> None:
    assert policy_record_id("p1") == "model-policy:p1"
    assert (
        outbound_request_id("p1", 2, ModelTaskType.CASE_SUGGESTION)
        == "outbound:p1:2:case_suggestion"
    )
