"""模型出站策略、出站准入与迟到响应判定（Sprint 5 无依赖部分）。

每条不变量一条测试，合法与非法都覆盖。依据：
`docs/文档-feix-a/B包/08-模型出站策略与投影规则设计说明.md` 第 3、5 节。
"""

from dataclasses import fields

import pytest

from aitest.domain.planning.model_outbound import (
    MODEL_ENDPOINT_FIELDS,
    TASK_MATERIAL_KINDS,
    MaterialKind,
    ModelEndpoint,
    ModelOutboundPolicy,
    ModelResponseEnvelope,
    ModelTaskType,
    OutboundConfirmation,
    OutboundItem,
    ResponseCurrency,
    endpoint_digest,
    material_kinds_digest,
    response_currency,
    validate_outbound_material,
)


def _endpoint() -> ModelEndpoint:
    return ModelEndpoint(provider="deepseek", address="https://api.example/v1", model_id="chat")


def _policy(**overrides: object) -> ModelOutboundPolicy:
    """构造一份"已确认、允许项目上下文"的合法策略，便于逐项改坏。"""
    allowed = overrides.pop(
        "allowed_material_kinds",
        frozenset({MaterialKind.PROJECT_CONTEXT}),
    )
    endpoint = overrides.pop("endpoint", _endpoint())
    assert isinstance(allowed, frozenset)
    assert isinstance(endpoint, ModelEndpoint)
    confirmed = overrides.pop("confirmed", True)
    assert isinstance(confirmed, bool)

    policy = ModelOutboundPolicy(
        project_id="project-1",
        revision=1,
        endpoint=endpoint,
        allowed_material_kinds=allowed,
        **overrides,  # type: ignore[arg-type]
    )
    if not confirmed:
        return policy
    return ModelOutboundPolicy(
        project_id=policy.project_id,
        revision=policy.revision,
        endpoint=policy.endpoint,
        allowed_material_kinds=policy.allowed_material_kinds,
        source_snippets_enabled=policy.source_snippets_enabled,
        ai_enabled=policy.ai_enabled,
        revoked_material_kinds=policy.revoked_material_kinds,
        confirmation=OutboundConfirmation(
            confirmation_id="confirmation-1",
            endpoint_digest=endpoint_digest(policy.endpoint),
            material_kinds_digest=material_kinds_digest(policy),
            source_snippets_enabled=policy.source_snippets_enabled,
            confirmed_at_commit="commit-1",
        ),
    )


def _item(
    kind: MaterialKind = MaterialKind.PROJECT_CONTEXT,
    field_path: str = "message.context",
    text: str = "project context digest",
) -> OutboundItem:
    return OutboundItem(material_kind=kind, field_path=field_path, text=text)


# ------------------------------------------------------------------ 词汇表


def test_task_material_kinds_cover_every_task_type() -> None:
    assert set(TASK_MATERIAL_KINDS) == set(ModelTaskType)


def test_task_material_kinds_only_list_known_kinds() -> None:
    for task_type, kinds in TASK_MATERIAL_KINDS.items():
        assert kinds, f"{task_type.value} must allow at least one material kind"
        for kind in kinds:
            assert isinstance(kind, MaterialKind)


def test_delivery_draft_must_not_carry_source_snippets() -> None:
    """交付说明草稿不需要源码：该任务不得携带源码片段类别。"""
    assert MaterialKind.SOURCE_SNIPPET not in TASK_MATERIAL_KINDS[ModelTaskType.DELIVERY_DRAFT]


# ------------------------------------------------- 接收目标与凭据正文不可表达


def test_endpoint_requires_all_fields() -> None:
    for field_name in ("provider", "address", "model_id", "purpose"):
        values = {"provider": "p", "address": "a", "model_id": "m", "purpose": "model"}
        values[field_name] = "  "
        with pytest.raises(ValueError, match=field_name):
            ModelEndpoint(**values)  # type: ignore[arg-type]


def test_endpoint_cannot_carry_credential_body() -> None:
    """字段集合锁定：不存在 value / secret / api_key 一类可承载凭据正文的字段。"""
    assert MODEL_ENDPOINT_FIELDS == ("provider", "address", "model_id", "purpose")
    forbidden = {"value", "secret", "api_key", "apikey", "token", "password", "key", "body"}
    assert forbidden.isdisjoint(MODEL_ENDPOINT_FIELDS)


# ------------------------------------------------------------------ 策略构造


def test_policy_defaults_to_source_snippets_disabled() -> None:
    policy = ModelOutboundPolicy(
        project_id="project-1",
        revision=1,
        endpoint=_endpoint(),
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.SOURCE_SNIPPET}
        ),
    )
    assert policy.source_snippets_enabled is False
    assert policy.ai_enabled is True
    assert policy.confirmation is None
    assert policy.revoked_material_kinds == frozenset()


def test_policy_requires_identity_and_revision() -> None:
    with pytest.raises(ValueError, match="project_id"):
        ModelOutboundPolicy(
            project_id=" ",
            revision=1,
            endpoint=_endpoint(),
            allowed_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT}),
        )
    with pytest.raises(ValueError, match="revision"):
        ModelOutboundPolicy(
            project_id="project-1",
            revision=0,
            endpoint=_endpoint(),
            allowed_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT}),
        )


def test_policy_requires_at_least_one_allowed_kind() -> None:
    with pytest.raises(ValueError, match="at least one allowed material kind"):
        ModelOutboundPolicy(
            project_id="project-1",
            revision=1,
            endpoint=_endpoint(),
            allowed_material_kinds=frozenset(),
        )


def test_revoked_kinds_must_be_allowed_kinds() -> None:
    with pytest.raises(ValueError, match="subset of the allowed kinds"):
        ModelOutboundPolicy(
            project_id="project-1",
            revision=1,
            endpoint=_endpoint(),
            allowed_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT}),
            revoked_material_kinds=frozenset({MaterialKind.CASE_CONTENT}),
        )


def test_enabling_source_snippets_requires_the_kind() -> None:
    with pytest.raises(ValueError, match="requires the source snippet material kind"):
        ModelOutboundPolicy(
            project_id="project-1",
            revision=1,
            endpoint=_endpoint(),
            allowed_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT}),
            source_snippets_enabled=True,
        )


def test_confirmation_switch_must_match_the_policy() -> None:
    policy = _policy(allowed_material_kinds=frozenset({MaterialKind.SOURCE_SNIPPET}))
    with pytest.raises(ValueError, match="same source snippet switch value"):
        ModelOutboundPolicy(
            project_id=policy.project_id,
            revision=policy.revision,
            endpoint=policy.endpoint,
            allowed_material_kinds=policy.allowed_material_kinds,
            source_snippets_enabled=True,
            confirmation=OutboundConfirmation(
                confirmation_id="confirmation-1",
                endpoint_digest=endpoint_digest(policy.endpoint),
                material_kinds_digest=",".join([MaterialKind.SOURCE_SNIPPET.value]),
                source_snippets_enabled=False,
                confirmed_at_commit="commit-1",
            ),
        )


def test_confirmation_binds_target_and_range() -> None:
    policy = _policy()
    assert policy.is_confirmed_for_current_scope() is True

    # 换接收地址 → 原确认失效，须重新确认（需求 §7"范围或目标变化重新确认"）。
    other = ModelEndpoint(provider="deepseek", address="https://other/v1", model_id="chat")
    moved = _policy(endpoint=other)
    assert moved.is_confirmed_for_current_scope() is True  # 确认是按新目标重新生成的
    unchanged_scope = ModelOutboundPolicy(
        project_id=policy.project_id,
        revision=2,
        endpoint=moved.endpoint,
        allowed_material_kinds=policy.allowed_material_kinds,
        confirmation=policy.confirmation,
    )
    assert unchanged_scope.is_confirmed_for_current_scope() is False


def test_confirmation_binds_material_range() -> None:
    policy = _policy()
    widened = ModelOutboundPolicy(
        project_id=policy.project_id,
        revision=2,
        endpoint=policy.endpoint,
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.CASE_CONTENT}
        ),
        confirmation=policy.confirmation,
    )
    assert widened.is_confirmed_for_current_scope() is False


def test_policy_without_confirmation_is_unconfirmed() -> None:
    policy = ModelOutboundPolicy(
        project_id="project-1",
        revision=1,
        endpoint=_endpoint(),
        allowed_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT}),
    )
    assert policy.is_confirmed_for_current_scope() is False


# ------------------------------------------------------------------ 派生


def test_effective_kinds_subtract_revoked() -> None:
    policy = _policy(
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.CASE_CONTENT}
        ),
        revoked_material_kinds=frozenset({MaterialKind.CASE_CONTENT}),
    )
    assert policy.effective_material_kinds() == frozenset({MaterialKind.PROJECT_CONTEXT})


def test_disabling_ai_yields_an_empty_effective_set() -> None:
    """关闭 AI 阻止新的模型请求；策略本身仍可保存（需求 P1-AC32）。"""
    policy = _policy(ai_enabled=False)
    assert policy.effective_material_kinds() == frozenset()
    assert policy.allowed_material_kinds == frozenset({MaterialKind.PROJECT_CONTEXT})


def test_effective_kinds_does_not_modify_the_policy() -> None:
    policy = _policy(revoked_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT}))
    before = (
        policy.allowed_material_kinds,
        policy.revoked_material_kinds,
        policy.revision,
    )
    policy.effective_material_kinds()
    assert (
        policy.allowed_material_kinds,
        policy.revoked_material_kinds,
        policy.revision,
    ) == before


def test_accepts_source_snippets_requires_switch_kind_and_no_revocation() -> None:
    enabled = _policy(
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.SOURCE_SNIPPET}
        ),
        source_snippets_enabled=True,
    )
    assert enabled.accepts_source_snippets() is True

    revoked = _policy(
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.SOURCE_SNIPPET}
        ),
        source_snippets_enabled=True,
        revoked_material_kinds=frozenset({MaterialKind.SOURCE_SNIPPET}),
    )
    assert revoked.accepts_source_snippets() is False

    disabled = _policy()
    assert disabled.accepts_source_snippets() is False


# ------------------------------------------------------------------ 准入门禁


def test_valid_material_is_returned_unchanged() -> None:
    policy = _policy()
    items = (_item(), _item(field_path="message.modules", text="module digest"))
    assert validate_outbound_material(
        policy, ModelTaskType.CONTEXT_SUMMARY, items
    ) == items


def test_outbound_rejected_when_ai_is_disabled() -> None:
    policy = _policy(ai_enabled=False)
    with pytest.raises(ValueError, match="AI is disabled"):
        validate_outbound_material(policy, ModelTaskType.CONTEXT_SUMMARY, (_item(),))


def test_empty_material_set_is_not_not_applicable() -> None:
    with pytest.raises(ValueError, match="not not_applicable"):
        validate_outbound_material(_policy(), ModelTaskType.CONTEXT_SUMMARY, ())


def test_outbound_rejected_without_confirmation() -> None:
    policy = _policy(confirmed=False)
    with pytest.raises(ValueError, match="not confirmed"):
        validate_outbound_material(policy, ModelTaskType.CONTEXT_SUMMARY, (_item(),))


def test_outbound_rejected_when_scope_changed_after_confirmation() -> None:
    policy = _policy()
    widened = ModelOutboundPolicy(
        project_id=policy.project_id,
        revision=2,
        endpoint=policy.endpoint,
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.CASE_CONTENT}
        ),
        confirmation=policy.confirmation,
    )
    with pytest.raises(ValueError, match="not confirmed"):
        validate_outbound_material(widened, ModelTaskType.CONTEXT_SUMMARY, (_item(),))


def test_outbound_rejected_for_revoked_kind() -> None:
    policy = _policy(
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.CASE_CONTENT}
        ),
        revoked_material_kinds=frozenset({MaterialKind.CASE_CONTENT}),
    )
    with pytest.raises(ValueError, match="revoked"):
        validate_outbound_material(
            policy,
            ModelTaskType.CASE_SUGGESTION,
            (_item(MaterialKind.CASE_CONTENT, "message.cases"),),
        )


def test_outbound_rejected_for_kind_outside_the_policy() -> None:
    policy = _policy(allowed_material_kinds=frozenset({MaterialKind.PROJECT_CONTEXT}))
    with pytest.raises(ValueError, match="not allowed by the policy"):
        validate_outbound_material(
            policy,
            ModelTaskType.CONTEXT_SUMMARY,
            (_item(MaterialKind.MODULE_DEPENDENCY_FACTS, "message.modules"),),
        )


def test_outbound_rejected_for_kind_outside_the_task() -> None:
    """跨任务夹带：交付说明草稿任务不得携带证据材料。"""
    policy = _policy(
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.EVIDENCE_REFERENCE}
        )
    )
    with pytest.raises(ValueError, match="not allowed for task delivery_draft"):
        validate_outbound_material(
            policy,
            ModelTaskType.DELIVERY_DRAFT,
            (_item(MaterialKind.EVIDENCE_REFERENCE, "message.evidence"),),
        )


def test_source_snippet_rejected_when_switch_is_closed() -> None:
    policy = _policy(allowed_material_kinds=frozenset({MaterialKind.SOURCE_SNIPPET}))
    with pytest.raises(ValueError, match="source snippets are disabled"):
        validate_outbound_material(
            policy,
            ModelTaskType.CONTEXT_SUMMARY,
            (_item(MaterialKind.SOURCE_SNIPPET, "message.source"),),
        )


def test_smuggling_source_code_under_another_kind_is_rejected() -> None:
    """核心反例：源码片段开关关闭时，把源码声明成"项目上下文"也送不出去。

    命中门禁 5（类别不属于该任务）与本条的组合：`DELIVERY_DRAFT` 任务既不允许
    `SOURCE_SNIPPET`，也不允许 `MODULE_DEPENDENCY_FACTS`。
    """
    policy = _policy(
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.MODULE_DEPENDENCY_FACTS}
        )
    )
    with pytest.raises(ValueError, match="not allowed for task"):
        validate_outbound_material(
            policy,
            ModelTaskType.DELIVERY_DRAFT,
            (_item(MaterialKind.MODULE_DEPENDENCY_FACTS, "message.source"),),
        )


def test_source_snippet_rejected_when_kind_is_not_allowed() -> None:
    enabled = _policy(
        allowed_material_kinds=frozenset(
            {MaterialKind.PROJECT_CONTEXT, MaterialKind.SOURCE_SNIPPET}
        ),
        source_snippets_enabled=True,
    )
    narrowed = ModelOutboundPolicy(
        project_id=enabled.project_id,
        revision=2,
        endpoint=enabled.endpoint,
        allowed_material_kinds=frozenset({MaterialKind.SOURCE_SNIPPET}),
        source_snippets_enabled=True,
        revoked_material_kinds=frozenset({MaterialKind.SOURCE_SNIPPET}),
        confirmation=OutboundConfirmation(
            confirmation_id="confirmation-2",
            endpoint_digest=endpoint_digest(enabled.endpoint),
            material_kinds_digest=",".join([MaterialKind.SOURCE_SNIPPET.value]),
            source_snippets_enabled=True,
            confirmed_at_commit="commit-2",
        ),
    )
    with pytest.raises(ValueError, match="revoked"):
        validate_outbound_material(
            narrowed,
            ModelTaskType.CONTEXT_SUMMARY,
            (_item(MaterialKind.SOURCE_SNIPPET, "message.source"),),
        )


def test_duplicate_outbound_item_is_rejected() -> None:
    policy = _policy()
    with pytest.raises(ValueError, match="duplicate outbound item"):
        validate_outbound_material(
            policy,
            ModelTaskType.CONTEXT_SUMMARY,
            (_item(), _item()),
        )


def test_outbound_item_requires_path_and_text() -> None:
    with pytest.raises(ValueError, match="field_path"):
        OutboundItem(material_kind=MaterialKind.PROJECT_CONTEXT, field_path=" ", text="x")
    with pytest.raises(ValueError, match="text"):
        OutboundItem(material_kind=MaterialKind.PROJECT_CONTEXT, field_path="a", text=" ")


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


def test_current_when_nothing_changed() -> None:
    assert (
        response_currency(
            _envelope(),
            current_source_revision=7,
            current_policy_revision=3,
            current_manual_revision=2,
        )
        is ResponseCurrency.CURRENT
    )


def test_manual_edit_supersedes_even_when_source_is_unchanged() -> None:
    assert (
        response_currency(
            _envelope(),
            current_source_revision=7,
            current_policy_revision=3,
            current_manual_revision=4,
        )
        is ResponseCurrency.SUPERSEDED_BY_MANUAL
    )


def test_source_change_marks_the_response_stale() -> None:
    assert (
        response_currency(
            _envelope(),
            current_source_revision=8,
            current_policy_revision=3,
            current_manual_revision=2,
        )
        is ResponseCurrency.SOURCE_CHANGED
    )


def test_policy_revision_change_marks_the_response_stale() -> None:
    assert (
        response_currency(
            _envelope(),
            current_source_revision=7,
            current_policy_revision=4,
            current_manual_revision=2,
        )
        is ResponseCurrency.SOURCE_CHANGED
    )


def test_manual_edit_wins_over_source_change() -> None:
    """人工版本优先：来源也变了，仍按"被人工取代"处理。"""
    assert (
        response_currency(
            _envelope(),
            current_source_revision=99,
            current_policy_revision=99,
            current_manual_revision=5,
        )
        is ResponseCurrency.SUPERSEDED_BY_MANUAL
    )


def test_response_currency_does_not_modify_inputs() -> None:
    envelope = _envelope()
    response_currency(
        envelope,
        current_source_revision=8,
        current_policy_revision=3,
        current_manual_revision=2,
    )
    assert (envelope.request_revision, envelope.source_revision, envelope.base_manual_revision) == (
        3,
        7,
        2,
    )


def test_a_model_response_cannot_be_a_conclusion() -> None:
    with pytest.raises(ValueError, match="draft"):
        _envelope(is_draft=False)


def test_response_envelope_requires_identity_and_commit() -> None:
    with pytest.raises(ValueError, match="response_id"):
        _envelope(response_id=" ")
    with pytest.raises(ValueError, match="received_at_commit"):
        _envelope(received_at_commit=" ")
    with pytest.raises(ValueError, match="request_revision"):
        _envelope(request_revision=0)


def test_response_envelope_field_set_is_locked() -> None:
    names = tuple(field.name for field in fields(ModelResponseEnvelope))
    assert names == (
        "response_id",
        "request_revision",
        "source_revision",
        "base_manual_revision",
        "received_at_commit",
        "is_draft",
    )
