"""整用例复用（R）的**消费**规则：要么按完整守卫计入，要么给出具名拒绝原因。

缺口形态：`build_decision_facts` 曾把 `reused` 写成固定空集——"没有候选"与"消费不了"
在报告里长得一样，且看不出原因。本文件锁定十条守卫（BC-001 §986 + 功能 §15）：

1. 引用必须指向本运行；
2. 用例属于本轮选择 S；
3. 本轮未为该用例建立任何新 Attempt（含可选步骤）；
4. 引用覆盖全部必需步骤，且与本轮步骤集合一致；
5. 映射到的源 Attempt 属于源运行、已完成、采集完整；
6. 源内容身份由源运行**已保存的来源核实事实**证明一致；
7. 引用到的证据已保存且可读（逐条核对）；
8. 该用例断言依据经受控确认链核对；
9. 源运行存在有效核验（有证据且无缺口）；
10. 本轮没有影响该用例步骤的失效。

任一缺失/未知/不一致即拒绝，并保留**具名原因**；同一用例多条引用整体拒绝。
"""

from __future__ import annotations

from typing import Any

from aitest.application.review.reuse_consumption import (
    REUSE_GUARDS,
    ReuseDenial,
    ReuseOutcome,
    derive_reuse_outcome,
)
from aitest.contracts.reuse_reference import ReuseReference, parse_reuse_reference


def _facts(
    *,
    run_id: str = "run-target",
    selected: tuple[str, ...] = ("case-1",),
    case_steps: tuple[tuple[str, str], ...] = (("step-1", "case-1"),),
    attempts: tuple[dict[str, Any], ...] = (),
    source_verifications: tuple[dict[str, Any], ...] = (),
    invalidations: tuple[dict[str, Any], ...] = (),
    verifications: tuple[dict[str, Any], ...] = (),
) -> Any:
    from aitest.contracts.execution_facts import ExecutionFacts

    return ExecutionFacts.model_validate(
        {
            "facts_id": f"facts-{run_id}",
            "project_id": "project-1",
            "run_id": run_id,
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
                "run_id": run_id,
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
                "control_state": "not_started",
                "required_scope": list(selected),
                "selected_scope": list(selected),
            },
            "steps": [
                {
                    "step_id": step_id,
                    "run_id": run_id,
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
                    "assertion_refs": [f"assertion-{step_id}"],
                    "current_attempt_id": None,
                }
                for index, (step_id, case_id) in enumerate(case_steps, start=1)
            ],
            "attempts": list(attempts),
            "source_verifications": list(source_verifications),
            "dependency_invalidations": list(invalidations),
            "verifications": list(verifications),
        }
    )


def _attempt(
    attempt_id: str = "attempt-src-1",
    *,
    run_id: str = "run-source",
    step_id: str = "step-1",
    state: str = "completed",
    capture: str = "complete",
) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "run_id": run_id,
        "step_id": step_id,
        "attempt_revision": 1,
        "attempt_index": 1,
        "retry_count": 0,
        "state": state,
        "is_current": True,
        "resolved_input_digest": "sha256:input-1",
        "step_revision_ref": {
            "step_revision_id": "step-1-rev",
            "revision_no": 1,
            "digest": "sha256:step-1",
        },
        "source_binding_digest": "sha256:binding-1",
        "side_effect_class": "unknown",
        "adapter_kind": "command",
        "adapter_version": "1.0.0",
        "capture_completeness": capture,
    }


def _verified_source(materialized: str = "snapshot-content-1") -> dict[str, Any]:
    return {
        "verification_id": "source-verify-1",
        "project_id": "project-1",
        "plan_revision": {
            "revision_id": "plan-1",
            "revision_no": 1,
            "digest": "sha256:plan-1",
        },
        "expected_source_binding_digest": "sha256:source-1",
        "materialized_snapshot_ref": materialized,
        "observed_source_digest": "sha256:source-1",
        "state": "verified",
        "observed_entry_ref": "entry-1",
        "gap_ids": [],
    }


def _verification(
    evidence: tuple[str, ...] = ("evidence-1",),
    gaps: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "verification_id": "verification-1",
        "verification_of": "attempt-src-1",
        "business_object_id": "order-1",
        "query_method": "read_only_business_query",
        "observation": "matched",
        "target_deployment_ref": "deployment-1",
        "evidence_refs": list(evidence),
        "gap_ids": list(gaps),
        "verified_at": "2026-10-13T08:00:00+00:00",
    }


def _reference(**overrides: object) -> ReuseReference:
    payload: dict[str, object] = {
        "schema_version": "aitest.reuse-reference/1.0",
        "target_run_id": "run-target",
        "case_id": "case-1",
        "source_run_id": "run-source",
        "source_step_id": "step-1",
        "source_attempt_id": "attempt-src-1",
        "source_attempt_by_step": (("step-1", "attempt-src-1"),),
        "source_content_identity": "snapshot-content-1",
        "source_plan_revision_digest": "sha256:" + "a" * 64,
        "environment_revision_digest": "sha256:" + "b" * 64,
        "entry_input_digest": "sha256:" + "c" * 64,
        "rules_revision_digest": "sha256:" + "d" * 64,
        "adapter_digest": "sha256:" + "e" * 64,
        "evidence_refs": ("evidence-1",),
        "validity_checked_at_commit": "7",
        "verification_basis": ("read-only query against the same object",),
    }
    payload.update(overrides)
    # 与生产同一条路径：JSON 规范字节 → 严格重建（形状校验只在这里发生一次）。
    return parse_reuse_reference(payload)


def _outcome(
    *,
    facts: Any | None = None,
    references: tuple[ReuseReference, ...] | None = None,
    source: Any | None = None,
    evidence_readable: bool = True,
    basis_confirmed: bool = True,
) -> ReuseOutcome:
    return derive_reuse_outcome(
        facts if facts is not None else _facts(),
        references if references is not None else (_reference(),),
        source_facts=lambda _run_id: source if source is not None else _facts(
            run_id="run-source",
            selected=("case-1",),
            case_steps=(("step-1", "case-1"),),
            attempts=(_attempt(),),
            source_verifications=(_verified_source(),),
            verifications=(_verification(),),
        ),
        evidence_readable=lambda _evidence_id: evidence_readable,
        basis_confirmed=lambda _case_id: basis_confirmed,
    )


def test_complete_guards_count_the_case_as_reused() -> None:
    outcome = _outcome()
    assert outcome.reused == frozenset({"case-1"})
    assert outcome.denials == ()


def test_reference_for_another_run_is_denied() -> None:
    outcome = _outcome(references=(_reference(target_run_id="run-other"),))
    assert outcome.reused == frozenset()
    assert outcome.reasons_for("case-1") == ("reference_not_for_this_run",)


def test_case_outside_selected_scope_is_denied() -> None:
    outcome = _outcome(facts=_facts(selected=("case-2",)))
    assert outcome.reasons_for("case-1") == ("case_not_in_selected_scope",)


def test_new_attempt_in_this_run_cancels_whole_case_reuse() -> None:
    target = _facts(attempts=(_attempt(attempt_id="attempt-new", run_id="run-target"),))
    outcome = _outcome(facts=target)
    assert outcome.reused == frozenset()
    assert outcome.reasons_for("case-1") == ("target_case_already_started",)


def test_incomplete_step_mapping_is_denied() -> None:
    two_steps = _facts(
        case_steps=(("step-1", "case-1"), ("step-2", "case-1")),
    )
    outcome = _outcome(facts=two_steps)
    assert outcome.reasons_for("case-1") == ("required_step_mapping_incomplete",)


def test_unfinished_source_attempt_is_denied() -> None:
    source = _facts(
        run_id="run-source",
        case_steps=(("step-1", "case-1"),),
        attempts=(_attempt(state="running", capture="partial"),),
        source_verifications=(_verified_source(),),
        verifications=(_verification(),),
    )
    outcome = _outcome(source=source)
    assert outcome.reasons_for("case-1") == ("source_attempt_unverified",)


def test_source_identity_or_verification_missing_is_denied() -> None:
    no_identity = _facts(
        run_id="run-source",
        case_steps=(("step-1", "case-1"),),
        attempts=(_attempt(),),
        verifications=(_verification(),),
    )
    outcome = _outcome(source=no_identity)
    assert "source_identity_unverified" in outcome.reasons_for("case-1")

    no_verification = _facts(
        run_id="run-source",
        case_steps=(("step-1", "case-1"),),
        attempts=(_attempt(),),
        source_verifications=(_verified_source(),),
    )
    outcome = _outcome(source=no_verification)
    assert outcome.reasons_for("case-1") == ("verification_valid_unverified",)

    # "有核验但带缺口"不算有效核验。
    gap_verification = _facts(
        run_id="run-source",
        case_steps=(("step-1", "case-1"),),
        attempts=(_attempt(),),
        source_verifications=(_verified_source(),),
        verifications=(_verification(gaps=("verification_incomplete",)),),
    )
    outcome = _outcome(source=gap_verification)
    assert outcome.reasons_for("case-1") == ("verification_valid_unverified",)


def test_unreadable_evidence_and_unconfirmed_basis_are_denied() -> None:
    outcome = _outcome(evidence_readable=False, basis_confirmed=False)
    assert outcome.reasons_for("case-1") == (
        "evidence_unreadable",
        "basis_confirmed_unverified",
    )


def test_downstream_invalidation_is_denied() -> None:
    invalidated = _facts(
        invalidations=(
            {
                "invalidation_id": "invalidation-1",
                "run_id": "run-target",
                "affected_step_id": "step-1",
                "reason": "upstream changed",
                "source_revision_ref": "sha256:revision-1",
                "transitive": False,
            },
        )
    )
    outcome = _outcome(facts=invalidated)
    assert outcome.reasons_for("case-1") == ("dependencies_valid_unverified",)


def test_denied_reasons_follow_the_documented_guard_order() -> None:
    """多条守卫同时不满足时，原因顺序稳定且都在 REUSE_GUARDS 之内。"""

    target = _facts(selected=("case-2",))
    outcome = _outcome(
        facts=target,
        references=(_reference(),),
        source=None if False else _facts(run_id="run-source"),
        evidence_readable=False,
        basis_confirmed=False,
    )
    reasons = outcome.reasons_for("case-1")
    assert reasons == tuple(reason for reason in REUSE_GUARDS if reason in set(reasons))
    assert set(reasons) <= set(REUSE_GUARDS)


def test_conflicting_references_deny_the_case_as_a_whole() -> None:
    outcome = _outcome(
        references=(
            _reference(),
            _reference(
                source_attempt_id="attempt-other",
                source_attempt_by_step=(("step-1", "attempt-other"),),
            ),
        )
    )
    assert outcome.reused == frozenset()
    assert outcome.reasons_for("case-1") == ("conflicting_reuse_references",)


def test_outcome_refuses_to_reuse_and_deny_the_same_case() -> None:
    import pytest

    with pytest.raises(ValueError):
        ReuseOutcome(
            reused=frozenset({"case-1"}),
            denials=(ReuseDenial(case_id="case-1", reasons=("x",)),),
        )
    with pytest.raises(ValueError):
        ReuseDenial(case_id="case-1", reasons=())
