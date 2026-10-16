"""`request_regression`（安排回归）生产用例：登记回归关联，不执行、不授权、不关闭。

分册依据：架构 03 §66「处理中 → 待复测：登记修复版本与回归方式」、§117「回归关联：原证据、
事实/假设、修复版本、新Attempt/证据、原关闭条件」、§149「修复关闭读取指定**新回归 Attempt**
及可读证据」、功能 §542「登记修复、**安排回归**及核对关闭依据」。

本文件锁定：①安排回归只在"已登记修复、处置 active、有原复现入口"时成立；②安排**不改状态机
状态、不创建 Attempt、不推进关闭**；③关联内容取自问题记录自身的修复三元组与 links，
不接受调用方自报；④同意图重放幂等；⑤关闭仍然要求**新的实际尝试**。
"""

from __future__ import annotations

from typing import Any

import pytest

from aitest.application.review import defect_management as facade
from aitest.application.review.saved_defects import (
    DefectManagementError,
    IssueGuardRefused,
    IssueRevisionConflict,
)
from aitest.contracts.commands import Command
from aitest.domain.review.defects import (
    IssueClosureEvidence,
    IssueDisposition,
    IssueRecord,
    IssueSeverity,
    IssueStatus,
)
from tests.unit.test_saved_defect_management import _facts, _Harness

PROJECT = "project-1"

_LINKS: dict[str, object] = {
    "module_ids": ["module-a"],
    "layers": ["L2"],
    "case_id": "case-1",
    "run_id": "run-1",
    "attempt_id": "attempt-1",
}


def _ready_harness(tmp_path: Any, *, links: dict[str, object] | None = None) -> _Harness:
    """走到"待复测"的问题（草稿→待确认→已确认→处理中→待复测）。"""

    harness = _Harness(tmp_path, facts=_facts())
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=0,
        parameters={"title": "checkout fails", "links": dict(links or _LINKS)},
        intent_id="intent-1",
        request_id="request-1",
    )
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=1,
        parameters={"awaiting_confirmation": True, "evidence_refs": ["evidence-1"]},
        intent_id="intent-2",
        request_id="request-2",
    )
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=2,
        parameters={"confirm": {"severity": "P1", "owner": "local-owner"}},
        intent_id="intent-3",
        request_id="request-3",
    )
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=3,
        parameters={"start": True},
        intent_id="intent-4",
        request_id="request-4",
    )
    harness.command(
        action="record_fix",
        issue_id="issue-1",
        expected_revision=4,
        parameters={
            "fix_version": "1.2.3",
            "regression_method": "rerun the original case",
            "closure_criteria": "original acceptance criteria",
        },
        intent_id="intent-5",
        request_id="request-5",
    )
    return harness


def _arrange(
    harness: _Harness,
    *,
    expected_revision: int,
    intent_id: str,
    request_id: str,
    parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    return dict(
        harness.service.request_regression(
            Command(
                request_id=request_id,
                action="request_regression",
                project_id=PROJECT,
                intent_id=intent_id,
                expected_revision=expected_revision,
                parameters={"issue_id": "issue-1", **(parameters or {})},
            )
        )
    )


def test_arranging_a_regression_records_the_association_without_changing_state(tmp_path) -> None:
    harness = _ready_harness(tmp_path)
    before = harness.saved("issue-1")
    assert before["status"] == "ready_for_retest"
    # 原复现尝试记在**关联**里（links.attempt_id）；attempt_ids 记录经证据/关闭绑定的尝试。
    assert before["links"]["attempt_id"] == "attempt-1"
    assert before["attempt_ids"] == []

    result = _arrange(
        harness,
        expected_revision=5,
        intent_id="intent-regression",
        request_id="request-regression",
        parameters={"note": "复测按原验收标准"},
    )
    assert result == {"aggregate_kind": "issue", "record_id": "issue-1", "revision": 6}

    after = harness.saved("issue-1")
    # 安排回归不改状态、不加尝试：新尝试仍须由受控执行入口产生。
    assert after["status"] == "ready_for_retest"
    assert after["attempt_ids"] == []
    assert after["disposition"] == "active"
    assert isinstance(after["regression_request_id"], str) and after["regression_request_id"]

    regressions = after["links"]["regressions"]  # type: ignore[index]
    assert len(regressions) == 1
    entry = regressions[0]
    assert entry["status"] == "arranged"
    # 关联内容全部来自问题记录自身（修复三元组 + 原复现入口），不是调用方自报。
    assert entry["fix_version"] == "1.2.3"
    assert entry["regression_method"] == "rerun the original case"
    assert entry["closure_criteria"] == "original acceptance criteria"
    assert entry["original_case_id"] == "case-1"
    assert entry["original_run_id"] == "run-1"
    assert entry["original_attempt_id"] == "attempt-1"
    assert entry["note"] == "复测按原验收标准"
    assert entry["regression_request_id"] == after["regression_request_id"]

    # 旧修订保留为历史（不可变）。
    assert (
        harness.repository.read(aggregate_kind="issue", record_id="issue-1", revision=5).payload[
            "status"
        ]
        == "ready_for_retest"
    )


def test_same_intent_replays_without_a_second_arrangement(tmp_path) -> None:
    harness = _ready_harness(tmp_path)
    first = _arrange(
        harness, expected_revision=5, intent_id="intent-regression", request_id="request-a"
    )
    replay = _arrange(
        harness, expected_revision=5, intent_id="intent-regression", request_id="request-b"
    )
    assert replay == first
    assert harness.repository.current_revision("issue", "issue-1") == 6
    assert len(harness.saved("issue-1")["links"]["regressions"]) == 1  # type: ignore[index]


def test_closing_still_requires_a_new_actual_attempt(tmp_path) -> None:
    """安排回归不等于执行：用**原尝试**关闭仍被拒（域守卫 + 用例层各证一次）。"""

    # 域守卫：关闭证据必须指向**新的**实际回归尝试（架构 03 §149）。
    issue = IssueRecord(
        issue_id="issue-1",
        project_id=PROJECT,
        revision=7,
        title="checkout fails",
        status=IssueStatus.READY_FOR_RETEST,
        severity=IssueSeverity.P1,
        owner="local-owner",
        confirmed_by="session:session-user-1",
        fix_version="1.2.3",
        regression_method="rerun the original case",
        closure_criteria="original acceptance criteria",
        evidence_refs=("evidence-1",),
        attempt_ids=frozenset({"attempt-1"}),
    )
    evidence = IssueClosureEvidence(
        case_id="case-1",
        attempt_id="attempt-1",
        actual_execution=True,
        current_regression_attempt=True,
        evidence_saved=True,
        satisfies_original_criteria=True,
        basis_confirmed=True,
        source_identity_matched=True,
        dependencies_valid=True,
        evidence_refs=("evidence-1",),
    )
    with pytest.raises(ValueError) as same_attempt:
        facade.close_issue(issue, expected_revision=7, evidence=evidence)
    assert "new actual regression attempt" in str(same_attempt.value)

    # 用例层：安排回归后立刻用原尝试关闭同样被拒，且不产生新修订。
    harness = _ready_harness(tmp_path)
    _arrange(
        harness, expected_revision=5, intent_id="intent-regression", request_id="request-regression"
    )
    with pytest.raises(IssueGuardRefused):
        harness.command(
            action="close_issue",
            issue_id="issue-1",
            expected_revision=6,
            parameters={
                "case_id": "case-1",
                "run_id": "run-1",
                "attempt_id": "attempt-1",
                "assertion_ref": "assertion-1",
                "assertion_outcome": "passed",
            },
            intent_id="intent-close",
            request_id="request-close",
        )
    assert harness.repository.current_revision("issue", "issue-1") == 6


def test_status_disposition_and_missing_links_are_refused(tmp_path) -> None:
    """状态/处置/原复现入口任一不满足即拒绝，且不产生新修订。"""

    # ① 尚未登记修复（处理中）：不能安排回归。
    harness = _Harness(tmp_path / "in-progress", facts=_facts())
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=0,
        parameters={"title": "checkout fails", "links": dict(_LINKS)},
        intent_id="intent-1",
        request_id="request-1",
    )
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=1,
        parameters={"awaiting_confirmation": True, "evidence_refs": ["evidence-1"]},
        intent_id="intent-2",
        request_id="request-2",
    )
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=2,
        parameters={"confirm": {"severity": "P1", "owner": "local-owner"}},
        intent_id="intent-3",
        request_id="request-3",
    )
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=3,
        parameters={"start": True},
        intent_id="intent-4",
        request_id="request-4",
    )
    with pytest.raises(IssueGuardRefused) as not_ready:
        _arrange(harness, expected_revision=4, intent_id="intent-x", request_id="request-x")
    assert "ready_for_retest" in str(not_ready.value)
    assert harness.repository.current_revision("issue", "issue-1") == 4

    # ② 非 active 处置（核实非缺陷）停止原修复流程：不得借安排回归翻回处理中。
    closed = _ready_harness(tmp_path / "closed")
    closed.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=5,
        parameters={
            "disposition": {
                "kind": "non_defect",
                "reason": "verified false alarm",
                "evidence_refs": ["evidence-1"],
            }
        },
        intent_id="intent-nondefect",
        request_id="request-nondefect",
    )
    assert closed.saved("issue-1")["disposition"] == "non_defect"
    # 非 active 处置下不得安排回归：状态守卫或处置守卫都必须拒绝（具体处置守卫由下面的
    # 域级用例单独钉住）。
    with pytest.raises(IssueGuardRefused) as non_active:
        _arrange(closed, expected_revision=6, intent_id="intent-y", request_id="request-y")
    assert "refused this transition" in str(non_active.value)

    # ③ 缺原复现入口（问题没有 case/run/attempt 关联）：不能安排回归。
    bare = _ready_harness(tmp_path / "bare", links={"module_ids": ["module-a"]})
    assert bare.saved("issue-1")["links"]["attempt_id"] is None
    with pytest.raises(IssueGuardRefused) as no_entry:
        _arrange(bare, expected_revision=5, intent_id="intent-z", request_id="request-z")
    assert "original attempt" in str(no_entry.value)


def test_unknown_parameters_and_revision_conflict_fail_explicitly(tmp_path) -> None:
    harness = _ready_harness(tmp_path)
    with pytest.raises(DefectManagementError) as unknown:
        _arrange(
            harness,
            expected_revision=5,
            intent_id="intent-unknown",
            request_id="request-unknown",
            parameters={"attempt_id": "attempt-9"},
        )
    assert "attempt_id" in str(unknown.value)

    with pytest.raises(IssueRevisionConflict):
        _arrange(
            harness, expected_revision=4, intent_id="intent-conflict", request_id="request-conflict"
        )


def test_non_active_disposition_guard_holds_at_the_domain_level() -> None:
    issue = IssueRecord(
        issue_id="issue-1",
        project_id=PROJECT,
        revision=6,
        title="checkout fails",
        status=IssueStatus.READY_FOR_RETEST,
        severity=IssueSeverity.P1,
        owner="local-owner",
        confirmed_by="session:session-user-1",
        disposition=IssueDisposition.DEFERRED,
        disposition_reason="waiting for vendor",
        fix_version="1.2.3",
        regression_method="rerun the original case",
        closure_criteria="original acceptance criteria",
    )
    with pytest.raises(ValueError) as refused:
        facade.request_regression(issue, expected_revision=6, regression_request_id="regression-1")
    assert "non-active disposition" in str(refused.value)
