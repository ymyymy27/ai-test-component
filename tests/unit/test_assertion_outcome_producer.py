"""D-02 断言结果生产者：从**已提交事实**派生断言结果，期望值不靠猜。

分册与合同依据：

- 功能 §148：`V_X⊆E_X∪R_X` 为「依据已确认、所需证据/核验有效且**断言明确通过或失败**者」；
- 功能 §150：`H_X` 是「**发布的必要业务断言**已明确失败」的用例集合，可以包含尚不在 `V_X`
  的用例；普通执行异常、阻塞、取消、未知结果不计入；
- 架构 02 §118/§128：「HTTP适配器返回响应及提取事实，**领域层执行已发布断言**，
  适配器不直接决定报告」；
- 能力清单 §6：独立核验「输入同一业务对象标识和**独立预期**」，`VerificationResult`
  含来源、对象、观察值、**预期引用**、证据和有效性；
- CD-001 §16：「**D 补断言结果生产者**」。

本文件锁定：①比较结果只取自已保存的核验事实（`matched`/`mismatched`），本生产者不发明期望值；
②断言标识只在步骤**唯一一条**时才绑定，多条即不产出（不猜）；③核验带缺口/未知观察值不产出；
④依据修订缺失不产出；⑤`case_assertions_complete` 只在用例**全部必需步骤**都有结果时为真；
⑥产出的结果仍受 `build_decision_facts` 的既有守卫逐项过滤。
"""

from __future__ import annotations

from typing import Any

from aitest.application.review.execution_decision_adapter import (
    AssertionOutcome,
    assertion_outcomes_from_facts,
    build_decision_facts,
)
from aitest.domain.review.reports import BusinessOutcome, evaluate_review

RUN = "run-1"
PROJECT = "project-1"
EVIDENCE = "evidence-1"


def _facts(
    steps: tuple[tuple[str, str, tuple[str, ...]], ...],
    *,
    verifications: tuple[dict[str, Any], ...] = (),
    selected: tuple[str, ...] = ("case-1",),
    attempt_state: str = "completed",
    evidence_attempt: str = "attempt-step-1",
) -> Any:
    """steps：`(step_id, case_id, assertion_refs)`；每个步骤各有一条当前完成尝试。"""

    from aitest.contracts.execution_facts import ExecutionFacts

    attempts = [
        {
            "attempt_id": f"attempt-{step_id}",
            "run_id": RUN,
            "step_id": step_id,
            "attempt_revision": 1,
            "attempt_index": 1,
            "retry_count": 0,
            "state": attempt_state,
            "is_current": True,
            "resolved_input_digest": "sha256:input-1",
            "step_revision_ref": {
                "step_revision_id": f"{step_id}-rev",
                "revision_no": 1,
                "digest": f"sha256:{step_id}",
            },
            "source_binding_digest": "sha256:binding-1",
            "side_effect_class": "unknown",
            "adapter_kind": "command",
            "adapter_version": "1.0.0",
            "capture_completeness": "complete",
        }
        for step_id, _case_id, _refs in steps
    ]
    return ExecutionFacts.model_validate(
        {
            "facts_id": f"facts-{RUN}",
            "project_id": PROJECT,
            "run_id": RUN,
            "snapshot_commit_id": "commit-1",
            "snapshot_cursor": 1,
            "snapshot_revision": 1,
            "committed_at": "2026-10-13T09:00:00+00:00",
            "run_revision": 1,
            "plan_revision": {
                "revision_id": "plan-1",
                "revision_no": 1,
                "digest": "sha256:plan-1",
            },
            "run": {
                "run_id": RUN,
                "run_revision": 1,
                "origin_workspace_id": "workspace-1",
                "intent_id": "intent-1",
                "tier": "full",
                "driver": "planned",
                "conclusion_ceiling": "passable",
                "plan_revision": {
                    "revision_id": "plan-1",
                    "revision_no": 1,
                    "digest": "sha256:plan-1",
                },
                "environment_ref": "environment-1@1",
                "environment_isolation_mode": "venv",
                "rules_revision": "rules-1@1",
                "control_state": "completed",
                "required_scope": list(selected),
                "selected_scope": list(selected),
            },
            "steps": [
                {
                    "step_id": step_id,
                    "run_id": RUN,
                    "step_revision": 1,
                    "step_revision_ref": {
                        "step_revision_id": f"{step_id}-rev",
                        "revision_no": 1,
                        "digest": f"sha256:{step_id}",
                    },
                    "ordinal": index,
                    "case_id": case_id,
                    "required_for_case": True,
                    "level": "L2",
                    "state": "completed",
                    "assertion_refs": list(refs),
                    "current_attempt_id": f"attempt-{step_id}",
                }
                for index, (step_id, case_id, refs) in enumerate(steps, start=1)
            ],
            "attempts": attempts,
            "current_attempt_by_step": {step_id: f"attempt-{step_id}" for step_id, _, _ in steps},
            "evidence_refs": [
                {
                    "evidence_id": EVIDENCE,
                    "evidence_revision": 1,
                    "project_id": PROJECT,
                    "source_instance_id": "instance-1",
                    "run_id": RUN,
                    "step_id": evidence_attempt.removeprefix("attempt-"),
                    "attempt_id": evidence_attempt,
                    "evidence_kind": "command_output",
                    "capture_source": "plugin_runtime",
                    "code_identity": {
                        "binding_kind": "plain",
                        "workspace_ref": "binding-1@1",
                    },
                    "object_digest": "sha256:" + "a" * 64,
                    "object_size": 3,
                    "integrity": "complete",
                    "redaction_state": "not_required",
                    "projection_state": "displayable",
                    "evidence_level": "full_link",
                }
            ],
            "verifications": list(verifications),
            "source_verifications": [
                {
                    "verification_id": "source-verify-1",
                    "project_id": PROJECT,
                    "plan_revision": {
                        "revision_id": "plan-1",
                        "revision_no": 1,
                        "digest": "sha256:plan-1",
                    },
                    "expected_source_binding_digest": "sha256:source-1",
                    "materialized_snapshot_ref": "snapshot-1",
                    "observed_source_digest": "sha256:source-1",
                    "state": "verified",
                    "observed_entry_ref": "entry-1",
                    "gap_ids": [],
                }
            ],
        }
    )


def _verification(
    verification_of: str,
    *,
    observation: str = "matched",
    evidence_refs: tuple[str, ...] = (EVIDENCE,),
    gap_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "verification_id": f"verification-{verification_of}",
        "verification_of": verification_of,
        "business_object_id": "object-1",
        "query_method": "read_only_business_query",
        "observation": observation,
        "evidence_refs": list(evidence_refs),
        "gap_ids": list(gap_ids),
    }


def _decide(facts: Any, outcomes: tuple[Any, ...] = ()) -> Any:
    """按"模板必测覆盖齐全、环境已解析"的上下文求判定（只谈断言结果带来的增量）。"""

    from aitest.application.review.execution_decision_adapter import DecisionContextFacts

    context = DecisionContextFacts(
        template_required_case_ids=frozenset(facts.run.selected_scope),
        environment_evidence_complete=True,
    )
    return evaluate_review(
        build_decision_facts(facts, assertion_outcomes=outcomes, context=context)
    )


def test_matched_verification_yields_a_complete_passing_assertion() -> None:
    facts = _facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1"),),
        selected=("case-1",),
    )
    outcomes = assertion_outcomes_from_facts(
        facts, basis_revision_for=lambda _case: 3, basis_confirmed_for=lambda _case: True
    )
    assert len(outcomes) == 1
    item = outcomes[0]
    assert (item.case_id, item.step_id, item.attempt_id, item.assertion_ref) == (
        "case-1",
        "step-1",
        "attempt-step-1",
        "assertion-1",
    )
    assert item.outcome is AssertionOutcome.PASSED
    assert item.basis_revision == 3
    assert item.necessary_assertion is True
    assert item.verification_valid is True and item.dependencies_valid is True
    assert item.case_assertions_complete is True

    decision = _decide(facts, outcomes)
    assert decision.coverage.passed == frozenset({"case-1"})
    assert decision.coverage.verified == frozenset({"case-1"})
    assert decision.business_outcome is BusinessOutcome.PASSED


def test_mismatched_verification_becomes_a_decisive_failure_even_before_completeness() -> None:
    """功能 §150：H 可以包含尚不在 V 的用例——必要断言已明确失败即计入。"""

    facts = _facts(
        (
            ("step-1", "case-1", ("assertion-1",)),
            ("step-2", "case-1", ("assertion-2",)),
        ),
        verifications=(_verification("attempt-step-2", observation="mismatched"),),
        selected=("case-1",),
        evidence_attempt="attempt-step-2",
    )
    outcomes = assertion_outcomes_from_facts(
        facts, basis_revision_for=lambda _case: 1, basis_confirmed_for=lambda _case: True
    )
    assert [item.outcome for item in outcomes] == [AssertionOutcome.FAILED]
    # step-1 没有结果 ⇒ 该用例断言不齐全，失败不得写成"整用例已验证失败"。
    assert outcomes[0].case_assertions_complete is False

    decision = _decide(facts, outcomes)
    assert decision.coverage.failed == frozenset()
    assert decision.coverage.verified == frozenset()
    assert decision.decisive_failure_case_ids == frozenset({"case-1"})
    assert decision.business_outcome is BusinessOutcome.FAILED


def test_untrusted_or_ambiguous_observations_produce_no_outcome() -> None:
    """核验带缺口、观察未知、断言标识不唯一、非当前尝试、缺依据修订：一律不产出。"""

    gapped = _facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1", gap_ids=("observation_incomplete",)),),
    )
    assert (
        assertion_outcomes_from_facts(
            gapped, basis_revision_for=lambda _case: 1, basis_confirmed_for=lambda _case: True
        )
        == ()
    )

    unknown = _facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1", observation="unknown"),),
    )
    assert (
        assertion_outcomes_from_facts(
            unknown, basis_revision_for=lambda _case: 1, basis_confirmed_for=lambda _case: True
        )
        == ()
    )

    ambiguous = _facts(
        (("step-1", "case-1", ("assertion-1", "assertion-2")),),
        verifications=(_verification("attempt-step-1"),),
    )
    assert (
        assertion_outcomes_from_facts(
            ambiguous, basis_revision_for=lambda _case: 1, basis_confirmed_for=lambda _case: True
        )
        == ()
    )

    no_basis = _facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1"),),
    )
    assert (
        assertion_outcomes_from_facts(
            no_basis, basis_revision_for=lambda _case: None, basis_confirmed_for=lambda _case: True
        )
        == ()
    )

    not_completed = _facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1"),),
        attempt_state="running",
    )
    assert (
        assertion_outcomes_from_facts(
            not_completed,
            basis_revision_for=lambda _case: 1,
            basis_confirmed_for=lambda _case: True,
        )
        == ()
    )

    no_evidence = _facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1", evidence_refs=()),),
    )
    assert (
        assertion_outcomes_from_facts(
            no_evidence, basis_revision_for=lambda _case: 1, basis_confirmed_for=lambda _case: True
        )
        == ()
    )


def test_unconfirmed_basis_keeps_the_case_unverified() -> None:
    """依据未确认：功能 §148 要求 V 只含"依据已确认"者，结果存在但不计通过。"""

    facts = _facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1"),),
        selected=("case-1",),
    )
    outcomes = assertion_outcomes_from_facts(
        facts, basis_revision_for=lambda _case: 2, basis_confirmed_for=lambda _case: False
    )
    assert len(outcomes) == 1 and outcomes[0].basis_confirmed is False

    decision = _decide(facts, outcomes)
    assert decision.coverage.passed == frozenset()
    assert decision.coverage.verified == frozenset()
    assert decision.business_outcome is BusinessOutcome.INCOMPLETE


def test_evidence_bound_to_another_attempt_is_filtered_by_the_existing_guard() -> None:
    """生产者只做"事实→结果"；证据是否属于该 Attempt 由既有守卫决定（不重复实现）。"""

    facts = _facts(
        (("step-1", "case-1", ("assertion-1",)),),
        verifications=(_verification("attempt-step-1"),),
        selected=("case-1",),
        evidence_attempt="attempt-other",
    )
    outcomes = assertion_outcomes_from_facts(
        facts, basis_revision_for=lambda _case: 1, basis_confirmed_for=lambda _case: True
    )
    assert len(outcomes) == 1  # 事实层面有结果
    decision = _decide(facts, outcomes)
    assert decision.coverage.passed == frozenset()  # 但证据不属于该尝试 ⇒ 不计通过
