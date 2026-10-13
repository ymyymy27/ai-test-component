"""`ReuseReference` 与架构 01 复用记录的**合同一致性**（待裁定第 1 项的收窄证据）。

分册原文（架构 01《复用、回归与模型任务矩阵》）：

> ReuseReference 登记 target_run/case_id、source_run/step/attempt、来源计划与
> 源码/环境/入口参数/规则/适配器修订、证据引用、validity_checked_at_commit
> 及验证依据。它是**已有运行记录的引用关系**，不创建虚假 Attempt，不增加
> 执行器或独立服务。

本文件锁定"记录形状已冻结、消费侧已实现，只差保存侧的**动作名与幂等键**"这一结论：

1. 架构 01 列出的每一项登记内容都在 `ReuseReference` 里有一一对应的字段；
2. 形状是内容寻址的规范字节（同内容同摘要、改一个字节即变），可安全落盘与核对；
3. 非法/未知/缺项一律拒绝（不补造未知摘要）；
4. 消费侧 `derive_reuse_outcome` 只凭这份记录 + 事实派生 R 或**具名拒绝原因**，
   **不创建 Attempt**（与"不创建虚假 Attempt"一致）。
"""

from __future__ import annotations

from typing import Any

import pytest

from aitest.application.review.reuse_consumption import derive_reuse_outcome
from aitest.contracts.reuse_reference import (
    SCHEMA_VERSION,
    ReuseReference,
    parse_reuse_reference,
    reuse_reference_digest,
    reuse_reference_payload,
)


def _reference(**changes: Any) -> ReuseReference:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "target_run_id": "run-target",
        "case_id": "case-1",
        "source_run_id": "run-source",
        "source_step_id": "step-1",
        "source_attempt_id": "attempt-src-1",
        "source_attempt_by_step": (("step-1", "attempt-src-1"),),
        "source_plan_revision_digest": "sha256:" + "a" * 64,
        "source_content_identity": "sha256:" + "b" * 64,
        "environment_revision_digest": "sha256:" + "c" * 64,
        "entry_input_digest": "sha256:" + "d" * 64,
        "rules_revision_digest": "sha256:" + "e" * 64,
        "adapter_digest": "sha256:" + "f" * 64,
        "evidence_refs": ("evidence-1",),
        "validity_checked_at_commit": "7",
        "verification_basis": ("source attempt completed with saved evidence",),
    }
    payload.update(changes)
    return ReuseReference.model_validate(payload)


def test_every_registered_item_of_the_frozen_sentence_has_a_field() -> None:
    """架构 01 的登记项逐项对应（缺一项即为记录形状漏项）。"""

    fields = set(ReuseReference.model_fields)
    for name in (
        "target_run_id",  # target_run
        "case_id",  # case_id
        "source_run_id",  # source_run
        "source_step_id",  # step
        "source_attempt_id",  # attempt
        "source_plan_revision_digest",  # 来源计划修订
        "source_content_identity",  # 来源源码
        "environment_revision_digest",  # 来源环境修订
        "entry_input_digest",  # 入口参数修订
        "rules_revision_digest",  # 规则修订
        "adapter_digest",  # 适配器修订
        "evidence_refs",  # 证据引用
        "validity_checked_at_commit",  # validity_checked_at_commit
        "verification_basis",  # 验证依据
    ):
        assert name in fields, name


def test_shape_is_content_addressed_and_stable() -> None:
    reference = _reference()
    same = _reference()
    assert reuse_reference_payload(reference) == reuse_reference_payload(same)
    assert reuse_reference_digest(reference) == reuse_reference_digest(same)

    changed = _reference(case_id="case-2")
    assert reuse_reference_digest(changed) != reuse_reference_digest(reference)

    # 严格重建：已保存载荷必须能原样读回（多余字段/类型转换一律拒绝）。
    payload = reference.model_dump(mode="json")
    assert parse_reuse_reference(payload) == reference
    with pytest.raises(ValueError):
        parse_reuse_reference({**payload, "unknown_field": 1})
    with pytest.raises(ValueError):
        parse_reuse_reference({**payload, "validity_checked_at_commit": 7})


def test_unknown_source_revisions_stay_unknown_instead_of_being_invented() -> None:
    reference = _reference(
        source_plan_revision_digest=None,
        environment_revision_digest=None,
        entry_input_digest=None,
        rules_revision_digest=None,
        adapter_digest=None,
    )
    assert reference.source_plan_revision_digest is None
    assert parse_reuse_reference(reference.model_dump(mode="json")) == reference
    # 非法摘要不因"未知"而被接受。
    with pytest.raises(ValueError):
        _reference(source_plan_revision_digest="plan-1")


def test_consumption_derives_r_or_a_named_refusal_without_creating_attempts() -> None:
    """消费侧只凭记录 + 事实派生 R 或具名拒绝原因，不创建 Attempt。"""

    from tests.unit.test_reuse_consumption import _attempt, _facts

    facts = _facts(
        run_id="run-target",
        selected=("case-1",),
        case_steps=(("step-1", "case-1"),),
    )
    source = _facts(
        run_id="run-source",
        case_steps=(("step-1", "case-1"),),
        attempts=(_attempt("attempt-src-1", run_id="run-source", step_id="step-1"),),
    )
    outcome = derive_reuse_outcome(
        facts,
        (_reference(),),
        source_facts=lambda _run_id: source,
        evidence_readable=lambda _evidence_id: True,
        basis_confirmed=lambda _case_id: True,
    )
    # 记录本身不产生本轮 Attempt：目标事实里仍然没有任何尝试。
    assert facts.attempts == ()
    assert outcome.reused or outcome.denials
    # 无论复用成立与否，都必须给出可核对的结果或**具名**原因（不静默空集）。
    assert outcome.reused == frozenset({"case-1"}) or outcome.denials
