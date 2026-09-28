"""准备意图与幂等规则（Sprint 2 无依赖部分）。

依据：`docs/文档-feix-a/B包/10-准备意图与幂等规则设计说明.md` 第 3、5 节。
"""

import pytest

from aitest.application.planning.preparation import (
    PAYLOAD_FIELDS,
    InputRevisions,
    PreparationDecision,
    PreparationRecord,
    PreparationRequest,
    changed_inputs,
    decide_preparation,
    payload_hash,
)


def _revisions(**overrides: int) -> InputRevisions:
    base: dict[str, int] = {
        "project_revision": 1,
        "binding_revision": 1,
        "snapshot_revision": 1,
        "environment_revision": 1,
        "plan_revision": 1,
        "rules_revision": 1,
        "template_revision": 1,
        "scope_revision": 1,
    }
    base.update(overrides)
    return InputRevisions(**base)


def _request(**overrides: object) -> PreparationRequest:
    base: dict[str, object] = {
        "project_id": "p1",
        "client_id": "c1",
        "prepare_request_id": "req-1",
        "payload_hash": "sha256:aaa",
        "input_revisions": _revisions(),
    }
    base.update(overrides)
    return PreparationRequest(**base)  # type: ignore[arg-type]


def _record(**overrides: object) -> PreparationRecord:
    base: dict[str, object] = {
        "request": _request(),
        "intent_id": "intent-1",
        "created_at_commit": "commit-1",
    }
    base.update(overrides)
    return PreparationRecord(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ 输入修订


def test_every_revision_must_be_at_least_one() -> None:
    for field_name in (
        "project_revision",
        "binding_revision",
        "snapshot_revision",
        "environment_revision",
        "plan_revision",
        "rules_revision",
        "template_revision",
        "scope_revision",
    ):
        with pytest.raises(ValueError, match=field_name):
            _revisions(**{field_name: 0})


def test_revisions_compare_by_value() -> None:
    assert _revisions() == _revisions()
    assert _revisions(snapshot_revision=2) != _revisions()


def test_changed_inputs_is_empty_for_identical_revisions() -> None:
    assert changed_inputs(_revisions(), _revisions()) == ()


def test_changed_inputs_lists_every_changed_field_in_declaration_order() -> None:
    previous = _revisions()
    current = _revisions(snapshot_revision=2, plan_revision=5, binding_revision=3)
    assert changed_inputs(previous, current) == (
        "binding_revision",
        "snapshot_revision",
        "plan_revision",
    )


# ------------------------------------------------------------------ 请求


def test_request_requires_identity_and_digest() -> None:
    for field_name in (
        "project_id",
        "client_id",
        "prepare_request_id",
        "payload_hash",
    ):
        with pytest.raises(ValueError, match=field_name):
            _request(**{field_name: "  "})


def test_identity_key_is_the_three_part_combination() -> None:
    request = _request()
    assert request.identity_key == ("p1", "c1", "req-1")


def test_records_require_intent_and_commit() -> None:
    with pytest.raises(ValueError, match="intent_id"):
        _record(intent_id=" ")
    with pytest.raises(ValueError, match="created_at_commit"):
        _record(created_at_commit=" ")


def test_intent_must_not_be_the_transport_request_id() -> None:
    """`request_id` 只用于传输去重，不能代替业务身份（架构文档第 11 节）。"""
    with pytest.raises(ValueError, match="transport-level"):
        _record(intent_id="req-1")


# ------------------------------------------------------------------ payload 摘要


def test_payload_hash_is_independent_of_key_order() -> None:
    first = payload_hash({"run_tier": "full", "driver": "planned", "plan_revision": 1})
    second = payload_hash({"plan_revision": 1, "driver": "planned", "run_tier": "full"})
    assert first == second


def test_payload_hash_changes_when_business_input_changes() -> None:
    assert payload_hash({"plan_revision": 1}) != payload_hash({"plan_revision": 2})


def test_payload_hash_normalises_unordered_collections() -> None:
    assert payload_hash({"selected_paths": ["b", "a"]}) == payload_hash(
        {"selected_paths": ["a", "b"]}
    )


def test_payload_hash_has_a_sha256_prefix() -> None:
    assert payload_hash({"plan_revision": 1}).startswith("sha256:")


def test_transport_parameters_are_not_payload_fields() -> None:
    """传输层参数不得进入业务输入摘要，否则"同号重传"会被判成"输入不同"。"""
    for name in ("request_id", "retry_count", "received_at", "transport_attempt"):
        assert name not in PAYLOAD_FIELDS


def test_payload_fields_carry_the_request_side_inputs() -> None:
    for name in (
        "binding_form",
        "selected_paths",
        "exclusion_rules",
        "run_tier",
        "driver",
        "case_revision_ids",
        "rule_version_ids",
        "template_version_ids",
    ):
        assert name in PAYLOAD_FIELDS


def test_observed_source_revisions_are_not_payload_fields() -> None:
    """来源修订是**观察结果**，不是请求内容。

    混进摘要会把"依据需重新准备"误报成"同键异输入冲突"（架构文档第 11 节）。
    """
    for name in (
        "project_revision",
        "binding_revision",
        "snapshot_revision",
        "environment_revision",
        "plan_revision",
        "rules_revision",
        "template_revision",
        "scope_revision",
    ):
        assert name not in PAYLOAD_FIELDS


# ------------------------------------------------------------------ 四态判定


def test_no_record_yields_new() -> None:
    lookup = decide_preparation(_request(), None)
    assert lookup.decision is PreparationDecision.NEW
    assert lookup.intent_id is None
    assert lookup.changed_inputs == ()


def test_same_digest_and_revisions_yields_reused() -> None:
    lookup = decide_preparation(_request(), _record())
    assert lookup.decision is PreparationDecision.REUSED
    assert lookup.intent_id == "intent-1"
    assert lookup.changed_inputs == ()


def test_different_digest_yields_conflict() -> None:
    """同键异输入摘要返回冲突，不覆盖（架构文档第 11 节）。"""
    lookup = decide_preparation(_request(payload_hash="sha256:bbb"), _record())
    assert lookup.decision is PreparationDecision.CONFLICTED
    assert lookup.intent_id is None


def test_same_digest_but_changed_revisions_needs_reprepare() -> None:
    record = _record(request=_request(input_revisions=_revisions(snapshot_revision=1)))
    incoming = _request(input_revisions=_revisions(snapshot_revision=9))
    lookup = decide_preparation(incoming, record)
    assert lookup.decision is PreparationDecision.NEEDS_REPREPARE
    assert lookup.intent_id == "intent-1"
    assert lookup.changed_inputs == ("snapshot_revision",)


def test_conflict_takes_precedence_over_changed_revisions() -> None:
    """摘要不同是冲突，不是"可重试的重新准备"——次序反了会让用户反复失败。"""
    record = _record()
    incoming = _request(
        payload_hash="sha256:ccc",
        input_revisions=_revisions(snapshot_revision=9, plan_revision=4),
    )
    lookup = decide_preparation(incoming, record)
    assert lookup.decision is PreparationDecision.CONFLICTED
    assert lookup.changed_inputs == ()


# ------------------------------------------------------------------ 取消语义


def test_cancelled_record_with_same_input_still_reuses() -> None:
    """取消只记录取消，不自动创建新意图（需求 P1-FR07）。"""
    lookup = decide_preparation(_request(), _record(cancelled=True))
    assert lookup.decision is PreparationDecision.REUSED
    assert lookup.intent_id == "intent-1"


def test_cancelled_record_with_different_digest_still_conflicts() -> None:
    lookup = decide_preparation(
        _request(payload_hash="sha256:ddd"), _record(cancelled=True)
    )
    assert lookup.decision is PreparationDecision.CONFLICTED


def test_cancel_does_not_excuse_changed_revisions() -> None:
    record = _record(cancelled=True)
    incoming = _request(input_revisions=_revisions(plan_revision=7))
    lookup = decide_preparation(incoming, record)
    assert lookup.decision is PreparationDecision.NEEDS_REPREPARE
    assert lookup.changed_inputs == ("plan_revision",)


def test_record_carries_the_commit_sequence_not_a_wall_clock() -> None:
    record = _record(created_at_commit="commit-42")
    assert record.created_at_commit == "commit-42"
    assert record.matches_identity(_request()) is True
    assert record.matches_identity(_request(project_id="p2")) is False
