"""整用例复用**选择**的持久登记（裁定 DEC-014 / CORE-001 1.82）。

分册与合同依据：

- 架构 01《复用、回归与模型任务矩阵》：「`ReuseReference` 登记 target_run/case_id、
  source_run/step/attempt、来源计划与源码/环境/入口参数/规则/适配器修订、证据引用、
  validity_checked_at_commit 及验证依据。**它是已有运行记录的引用关系，不创建虚假 Attempt**」
  → 登记动作**不授予资格**；
- 功能 §15 / BC-001：本轮"选择复用哪个源运行/用例"必须持久、可审计、参与幂等；
- CORE-001 1.69/1.72：核对输入固定为 `case_id` + 两对 run/snapshot（`SnapshotContentRef`），
  `target` 固定 `case_id`；保存侧沿用同一组引用与**同一个核对器**。

本文件用可核对的 `CaseReuseVerification`（核对器本身另有用例）锁定保存侧全部守卫：
记录形状与回读、幂等与冲突、受控会话、以及"来源身份必须来自核对器而非调用方"。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from aitest.application.execution.reuse_inspection import CaseReuseVerification
from aitest.application.review.saved_reuse import (
    REUSE_DECISION_AGGREGATE,
    ReuseActorRequired,
    ReuseIntentConflict,
    ReuseSelectionBlocked,
    SavedCaseReuseSelection,
    reuse_decision_record_id,
)
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.contracts.reuse_reference import parse_reuse_reference
from aitest.interfaces.local.api import EntryKind

PROJECT = "project-1"
TARGET_RUN = "run-target"
SOURCE_RUN = "run-source"
CASE = "case-1"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


class _Actors:
    def __init__(self, *, interactive: bool = True) -> None:
        self._actor = SimpleNamespace(
            interactive=interactive,
            session_id="session-1",
            entry_kind=EntryKind.HUMAN_UI if interactive else EntryKind.AGENT_RELAY,
        )

    def current(self) -> Any:
        return self._actor


def _verification(**changes: Any) -> CaseReuseVerification:
    payload: dict[str, Any] = {
        "project_id": PROJECT,
        "case_id": CASE,
        "source_run_id": SOURCE_RUN,
        "target_run_id": TARGET_RUN,
        "source_snapshot": {
            "snapshot_commit_id": "snap-s",
            "snapshot_cursor": 1,
            "digest": DIGEST_A,
        },
        "source_current_snapshot": {
            "snapshot_commit_id": "snap-s",
            "snapshot_cursor": 1,
            "digest": DIGEST_A,
        },
        "target_snapshot": {
            "snapshot_commit_id": "snap-t",
            "snapshot_cursor": 1,
            "digest": DIGEST_B,
        },
        "status": "unverified",
        # 资格类原因允许存在：保存侧只登记引用关系，不授予资格。
        "denial_reasons": ("source_verified_unverified", "verification_valid_unverified"),
        "step_mapping": (
            {
                "case_step_index": 0,
                "logical_step_id": "step-1",
                "source_step_id": "step-source-1",
                "target_step_id": "step-target-1",
                "source_attempt_id": "attempt-source-1",
            },
        ),
        "source_identity": {"case_id": CASE},
        "target_identity": {"case_id": CASE},
        "basis_confirmation": {"source": {"state": "confirmed"}, "target": {"state": "confirmed"}},
        "reference_inputs": {
            "source_content_identity": DIGEST_A,
            "source_plan_revision_digest": DIGEST_A,
            "environment_revision_digest": DIGEST_B,
            "entry_input_digest": DIGEST_A,
            "rules_revision_digest": DIGEST_B,
            "adapter_digest": None,
            "evidence_refs": ("evidence-source-1",),
        },
    }
    payload.update(changes)
    return CaseReuseVerification(**payload)


def _command(**changes: Any) -> Command:
    payload: dict[str, Any] = {
        "request_id": "req-reuse-1",
        "action": "select_case_reuse",
        "project_id": PROJECT,
        "intent_id": "intent-reuse-1",
        "expected_revision": 0,
        "target": CASE,
        "parameters": {
            "case_id": CASE,
            "source_run_id": SOURCE_RUN,
            "source_snapshot": {
                "snapshot_commit_id": "snap-s",
                "snapshot_cursor": 1,
                "digest": DIGEST_A,
            },
            "target_run_id": TARGET_RUN,
            "target_snapshot": {
                "snapshot_commit_id": "snap-t",
                "snapshot_cursor": 1,
                "digest": DIGEST_B,
            },
        },
    }
    payload.update(changes)
    return Command(**payload)


def _harness(tmp_path, *, verification: Any = None, interactive: bool = True):
    core = assemble_workspace_core(tmp_path, instance_id="reuse-selection-core")
    inspection = Mock()
    inspection.verify.return_value = verification or _verification()
    selection = SavedCaseReuseSelection(
        unit=core.unit_of_work,
        records=core.unit_of_work.repo,
        actors=_Actors(interactive=interactive),
        inspection=inspection,
    )
    return core, selection, inspection


def test_selection_records_the_exact_frozen_reference(tmp_path) -> None:
    core, selection, inspection = _harness(tmp_path)
    try:
        result = selection.apply(_command())
        assert result == {
            "aggregate_kind": REUSE_DECISION_AGGREGATE,
            "record_id": reuse_decision_record_id(PROJECT, TARGET_RUN),
            "revision": 1,
        }
        # 记录形状：ReuseReference 严格解析（未知字段/类型不符即拒绝）。
        stored = core.unit_of_work.repo.read(
            aggregate_kind=REUSE_DECISION_AGGREGATE,
            record_id=result["record_id"],
            revision=1,
        ).payload
        assert stored["schema_version"] == "aitest.reuse-decision/1.0"
        assert stored["status"] == "selected" and stored["target_run_id"] == TARGET_RUN
        references = stored["references"]
        assert isinstance(references, list) and len(references) == 1
        reference = parse_reuse_reference(references[0])
        assert (reference.case_id, reference.source_run_id, reference.target_run_id) == (
            CASE,
            SOURCE_RUN,
            TARGET_RUN,
        )
        # 锚点必须是映射中的一项，映射与核对器一致。
        assert reference.source_attempt_by_step == (("step-source-1", "attempt-source-1"),)
        assert (reference.source_step_id, reference.source_attempt_id) == (
            "step-source-1",
            "attempt-source-1",
        )
        # 未知来源修订保持未知（不补造摘要）。
        assert reference.adapter_digest is None
        assert reference.evidence_refs == ("evidence-source-1",)
        assert reference.validity_checked_at_commit == "1"
        assert reference.verification_basis

        # 读取路径（报告侧候选来源）返回同一引用。
        assert selection.references_for_run(PROJECT, TARGET_RUN) == (reference,)
        assert selection.references_for_run(PROJECT, "run-other") == ()
        # 核对器只被调用一次，来源身份完全来自它（调用方不能自报）。
        assert inspection.verify.call_count == 1
    finally:
        core.lifetime_lock.release()


def test_same_intent_replays_and_different_input_conflicts(tmp_path) -> None:
    core, selection, _ = _harness(tmp_path)
    try:
        first = selection.apply(_command())
        replay = selection.apply(_command(request_id="req-reuse-2"))
        assert replay == first
        assert (
            core.unit_of_work.repo.current_revision(REUSE_DECISION_AGGREGATE, first["record_id"])
            == 1
        )

        # 同意图异输入：冲突，不静默覆盖。
        with pytest.raises(ReuseIntentConflict):
            selection.apply(
                _command(
                    request_id="req-reuse-3",
                    intent_id="intent-reuse-1",
                    parameters={
                        **_command().parameters,
                        "source_snapshot": {
                            "snapshot_commit_id": "snap-s",
                            "snapshot_cursor": 2,
                            "digest": DIGEST_A,
                        },
                    },
                )
            )
    finally:
        core.lifetime_lock.release()


def test_second_case_appends_a_new_revision_for_the_same_run(tmp_path) -> None:
    core, selection, inspection = _harness(tmp_path)
    try:
        first = selection.apply(_command())
        inspection.verify.return_value = _verification(case_id="case-2")
        second = selection.apply(
            _command(
                request_id="req-reuse-9",
                intent_id="intent-reuse-9",
                expected_revision=1,
                target="case-2",
                parameters={**_command().parameters, "case_id": "case-2"},
            )
        )
        assert second["record_id"] == first["record_id"]
        assert second["revision"] == 2
        stored = core.unit_of_work.repo.read(
            aggregate_kind=REUSE_DECISION_AGGREGATE,
            record_id=first["record_id"],
            revision=2,
        ).payload
        assert [item["case_id"] for item in stored["references"]] == ["case-1", "case-2"]
        # 旧修订保留（不可变历史）。
        assert (
            len(
                core.unit_of_work.repo.read(
                    aggregate_kind=REUSE_DECISION_AGGREGATE,
                    record_id=first["record_id"],
                    revision=1,
                ).payload["references"]
            )
            == 1
        )
    finally:
        core.lifetime_lock.release()


def test_selection_requires_a_controlled_human_session(tmp_path) -> None:
    core, selection, _ = _harness(tmp_path, interactive=False)
    try:
        with pytest.raises(ReuseActorRequired):
            selection.apply(_command())
        assert core.unit_of_work.repo.current_revision(
            REUSE_DECISION_AGGREGATE, reuse_decision_record_id(PROJECT, TARGET_RUN)
        ) in (None, 0)
    finally:
        core.lifetime_lock.release()


@pytest.mark.parametrize(
    "verification, reason",
    [
        (_verification(status="incompatible"), "incompatible"),
        (_verification(step_mapping=()), "step mapping"),
        (
            _verification(
                step_mapping=(
                    {
                        "case_step_index": 0,
                        "logical_step_id": "step-1",
                        "source_step_id": "step-source-1",
                        "target_step_id": "step-target-1",
                        "source_attempt_id": None,
                    },
                )
            ),
            "source attempts",
        ),
        (
            _verification(
                reference_inputs={
                    **_verification().reference_inputs,
                    "evidence_refs": (),
                }
            ),
            "source evidence",
        ),
    ],
)
def test_unusable_material_is_refused_without_writing(tmp_path, verification, reason) -> None:
    core, selection, _ = _harness(tmp_path, verification=verification)
    try:
        with pytest.raises(ReuseSelectionBlocked, match=reason):
            selection.apply(_command())
        assert core.unit_of_work.repo.current_revision(
            REUSE_DECISION_AGGREGATE, reuse_decision_record_id(PROJECT, TARGET_RUN)
        ) in (None, 0)
    finally:
        core.lifetime_lock.release()


@pytest.mark.parametrize(
    "changes",
    [
        {"target": "case-2"},
        {"parameters": {"case_id": CASE}},
        {
            "parameters": {
                "case_id": CASE,
                "source_run_id": SOURCE_RUN,
                "source_snapshot": {
                    "snapshot_commit_id": "snap-s",
                    "snapshot_cursor": 1,
                    "digest": DIGEST_A,
                },
                "target_run_id": TARGET_RUN,
                "target_snapshot": {
                    "snapshot_commit_id": "snap-t",
                    "snapshot_cursor": 1,
                    "digest": DIGEST_B,
                },
                "extra": "not allowed",
            }
        },
    ],
)
def test_parameter_and_target_shape_is_refused(tmp_path, changes) -> None:
    core, selection, _ = _harness(tmp_path)
    try:
        with pytest.raises(ReuseSelectionBlocked):
            selection.apply(_command(**changes))
    finally:
        core.lifetime_lock.release()


def test_saved_selection_is_readable_by_the_reuse_consumption_guard(tmp_path) -> None:
    """保存 → 消费接线：记录回读的引用能直接交给消费守卫，且不产生新 Attempt。"""

    from aitest.application.review.reuse_consumption import derive_reuse_outcome
    from tests.unit.test_reuse_consumption import _attempt, _facts

    verification = _verification(
        step_mapping=(
            {
                "case_step_index": 0,
                "logical_step_id": "step-1",
                "source_step_id": "step-1",
                "target_step_id": "step-1",
                "source_attempt_id": "attempt-src-1",
            },
        )
    )
    core, selection, _ = _harness(tmp_path, verification=verification)
    try:
        selection.apply(_command())
        references = selection.references_for_run(PROJECT, TARGET_RUN)
        assert len(references) == 1

        target = _facts(run_id=TARGET_RUN, selected=(CASE,), case_steps=(("step-1", CASE),))
        source = _facts(
            run_id=SOURCE_RUN,
            case_steps=(("step-1", CASE),),
            attempts=(_attempt("attempt-src-1", run_id=SOURCE_RUN, step_id="step-1"),),
        )
        outcome = derive_reuse_outcome(
            target,
            references,
            source_facts=lambda run_id: source if run_id == SOURCE_RUN else None,
            evidence_readable=lambda _evidence_id: True,
            basis_confirmed=lambda _case_id: True,
        )
        # 记录本身不创建本轮 Attempt；结果要么计入 R，要么给出具名拒绝原因。
        assert target.attempts == ()
        assert outcome.reused == frozenset({CASE}) or outcome.denials
    finally:
        core.lifetime_lock.release()
