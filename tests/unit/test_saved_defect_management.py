"""FR15 生产用例：问题生命周期持久化 + 权威关闭依据（D-04 关闭条件）。

本文件覆盖报告指出的缺口形态：「门面接收调用方 ClosureEvidence，没有权威读取新实际
回归并持久关闭的生产用例」。

- 正例：草稿 → 待确认 → 已确认 → 处理中 → 待复测，逐修订写入**真实文件底座**的同一
  工作单元，并写出 `issues.list` 需要的索引入口与最小摘要；
- 反例：调用方不能再自报有效性布尔——非当前尝试、未完成尝试、断言不属于该步骤、无证据、
  证据字节不可读、来源不一致、依赖失效都必须拒绝；修订冲突与同意图异输入必须显式失败；
- 关闭依据的六项事实全部由已提交事实派生，`_closure_evidence` 返回的
  `evidence_refs` 也由服务自己算出，不接受调用方传入。

边界（如实登记）：本文件用**事实替身**驱动派生逻辑，用受控替身核对用例依据确认
（`case_basis` 接缝）；默认的确认链实现由既有 `SavedCaseBasisReader` 及其测试覆盖，
真实 Trae 端到端关闭仍待验收。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from aitest.application.review.saved_defects import (
    IssueGuardRefused,
    IssueIntentConflict,
    IssueRevisionConflict,
    SavedDefectManagement,
)
from aitest.contracts.commands import Command
from aitest.domain.approvals import EntryKind
from aitest.domain.review.defects import IssueRecord, IssueSeverity, IssueStatus
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import LocalAPI, Session


@dataclass(frozen=True, slots=True)
class _Actor:
    session_id: str = "session-user-1"
    entry_kind: EntryKind = EntryKind.INTERACTIVE_CLI
    interactive: bool = True


class _Actors:
    def current(self) -> _Actor:
        return _Actor()


class _RejectingActors:
    def current(self) -> _Actor:
        return _Actor(session_id="relay-1", entry_kind=EntryKind.AGENT_RELAY, interactive=False)


class _FactsReader:
    def __init__(self, facts: object | None) -> None:
        self._facts = facts

    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> object:
        if self._facts is None:
            raise ValueError("saved run facts are unavailable")
        return self._facts


class _Objects:
    """对象存储替身：只接受与引用摘要一致的字节。"""

    def __init__(self, payload: bytes = b"abc") -> None:
        self.payload = payload

    def read_bytes(self, ref: Any) -> bytes:
        import hashlib

        if "sha256:" + hashlib.sha256(self.payload).hexdigest() != ref.digest:
            raise ValueError("object digest mismatch")
        return self.payload


class _UnusedBases:
    """占位受控确认证明：本文件用 `case_basis` 接缝，不驱动真实确认链。"""

    def validate_basis_confirmation(self, *, project_id: str, payload: Any) -> None:
        raise ValueError("controlled basis confirmation is not part of this fixture")


def _attempt(
    *,
    attempt_id: str = "attempt-1",
    step_id: str = "step-1",
    current: bool = True,
    state: str = "completed",
    capture: str = "complete",
) -> Any:
    from aitest.contracts.execution_facts import AttemptFact

    return AttemptFact.model_validate(
        {
            "attempt_id": attempt_id,
            "run_id": "run-1",
            "step_id": step_id,
            "attempt_revision": 1,
            "attempt_index": 1,
            "retry_count": 0,
            "state": state,
            "is_current": current,
            "resolved_input_digest": "sha256:input-1",
            "step_revision_ref": {
                "step_revision_id": "step-rev-1",
                "revision_no": 1,
                "digest": "sha256:step-1",
            },
            "source_binding_digest": "sha256:binding-1",
            "side_effect_class": "unknown",
            "adapter_kind": "command",
            "adapter_version": "1.0.0",
            "capture_completeness": capture,
        }
    )


def _facts(
    *,
    attempt: Any | None = None,
    evidence: tuple[Any, ...] = (),
    verifications: tuple[Any, ...] = (),
    invalidations: tuple[Any, ...] = (),
) -> Any:
    from aitest.contracts.execution_facts import ExecutionFacts

    chosen = attempt or _attempt()
    return ExecutionFacts.model_validate(
        {
            "facts_id": "facts-1",
            "project_id": "project-1",
            "run_id": "run-1",
            "snapshot_commit_id": "commit-7",
            "snapshot_cursor": 7,
            "snapshot_revision": 1,
            "committed_at": "2026-10-12T10:00:00+00:00",
            "run_revision": 1,
            "plan_revision": {
                "revision_id": "plan-1",
                "revision_no": 1,
                "digest": "sha256:plan-1",
            },
            "run": {
                "run_id": "run-1",
                "run_revision": 1,
                "origin_workspace_id": "workspace-1",
                "intent_id": "prepare-intent-1",
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
                "required_scope": ["case-1"],
                "selected_scope": ["case-1"],
            },
            "steps": [
                {
                    "step_id": "step-1",
                    "run_id": "run-1",
                    "step_revision": 1,
                    "step_revision_ref": {
                        "step_revision_id": "step-rev-1",
                        "revision_no": 1,
                        "digest": "sha256:step-1",
                    },
                    "ordinal": 1,
                    "case_id": "case-1",
                    "level": "L2",
                    "state": "completed",
                    "assertion_refs": ["assertion-1"],
                    "current_attempt_id": chosen.attempt_id,
                }
            ],
            "attempts": [chosen.model_dump(mode="json")],
            "current_attempt_by_step": {"step-1": chosen.attempt_id},
            "evidence_refs": [item.model_dump(mode="json") for item in evidence],
            "source_verifications": list(verifications),
            "dependency_invalidations": list(invalidations),
        }
    )


def _evidence(evidence_id: str = "evidence-1", attempt_id: str = "attempt-1") -> Any:
    from aitest.contracts.execution_facts import EvidenceFact

    return EvidenceFact.model_validate(
        {
            "evidence_id": evidence_id,
            "evidence_revision": 1,
            "project_id": "project-1",
            "source_instance_id": "instance-1",
            "run_id": "run-1",
            "step_id": "step-1",
            "attempt_id": attempt_id,
            "evidence_kind": "command_output",
            "capture_source": "plugin_runtime",
            "code_identity": {"binding_kind": "plain", "workspace_ref": "binding-1@1"},
            "object_digest": "sha256:" + __import__("hashlib").sha256(b"abc").hexdigest(),
            "object_size": 3,
            "integrity": "complete",
            "redaction_state": "not_required",
            "projection_state": "displayable",
            "evidence_level": "full_link",
        }
    )


def _verified_source(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "verification_id": "source-verify-1",
        "project_id": "project-1",
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
    payload.update(changes)
    return payload


class _Reader:
    """最小只读替身：本文件不驱动准备记录路径（用 `case_basis` 接缝）。"""

    def _missing(self, *_: object, **__: object) -> object:
        raise ValueError("this fixture does not provide saved preparations")

    read = _missing
    query = _missing
    find_preparation = _missing
    find_preparation_by_intent = _missing


class _Harness:
    def __init__(
        self,
        tmp_path: Any,
        *,
        facts: Any,
        actors: object | None = None,
        objects: object | None = None,
        case_confirmed: bool = True,
    ) -> None:
        self.repository = FileRecordRepository(tmp_path)
        self.unit = FileUnitOfWork(tmp_path)
        # 关闭依据要求"已保存且可读的证据"：把事实里的证据登记为仓储引用记录。
        for item in getattr(facts, "evidence_refs", ()):
            self.repository.append(
                "evidence_ref",
                item.evidence_id,
                self.repository.current_revision("evidence_ref", item.evidence_id),
                {
                    "project_id": "project-1",
                    "evidence_id": item.evidence_id,
                    "attempt_id": item.attempt_id,
                },
            )
        self.service = SavedDefectManagement(
            unit=self.unit,
            reader=_Reader(),
            records=self.repository,
            clock=object(),
            actors=actors or _Actors(),
            runtime=_FactsReader(facts),
            workspace_id="workspace-1",
            objects=objects,
            basis_proof=_UnusedBases(),
            case_basis=lambda project, intent, case: case_confirmed,
        )

    def command(
        self,
        *,
        action: str,
        issue_id: str,
        expected_revision: int,
        parameters: dict[str, object],
        intent_id: str,
        request_id: str,
    ) -> dict[str, object]:
        handler = {
            "update_issue": self.service.update_issue,
            "record_fix": self.service.record_fix,
            "close_issue": self.service.close_issue,
        }[action]
        return dict(
            handler(
                Command(
                    request_id=request_id,
                    action=action,
                    project_id="project-1",
                    intent_id=intent_id,
                    expected_revision=expected_revision,
                    parameters={"issue_id": issue_id, **parameters},
                )
            )
        )

    def saved(self, issue_id: str) -> dict[str, object]:
        revision = self.repository.current_revision("issue", issue_id)
        assert revision >= 1
        record = self.repository.read(
            aggregate_kind="issue", record_id=issue_id, revision=revision
        )
        return dict(record.payload)


def _ready_issue() -> IssueRecord:
    return IssueRecord(
        issue_id="issue-2",
        project_id="project-1",
        revision=5,
        title="t",
        status=IssueStatus.READY_FOR_RETEST,
        severity=IssueSeverity.P2,
        owner="owner",
        confirmed_by="session:session-user-1",
        fix_version="v2",
        regression_method="m",
        closure_criteria="c",
        attempt_ids=frozenset(),
    )


_CLOSE_PARAMETERS = {
    "case_id": "case-1",
    "run_id": "run-1",
    "attempt_id": "attempt-1",
    "assertion_ref": "assertion-1",
    "assertion_outcome": "passed",
}


def test_issue_lifecycle_persists_revisions_and_list_index_entries(tmp_path) -> None:
    harness = _Harness(tmp_path, facts=_facts())

    created = harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=0,
        parameters={
            "title": "checkout fails",
            "links": {"module_ids": ["module-a"], "layers": ["L2"]},
        },
        intent_id="intent-1",
        request_id="request-1",
    )
    assert created == {"aggregate_kind": "issue", "record_id": "issue-1", "revision": 1}
    first = harness.saved("issue-1")
    assert first["status"] == "draft" and first["issue_open"] is True
    assert first["issue_index_entries"], "issues.list 必须拿到索引入口"
    assert first["module_ids"] == ["module-a"] and first["layer"] == "L2"

    assert harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=1,
        parameters={"awaiting_confirmation": True, "evidence_refs": ["evidence-1"]},
        intent_id="intent-2",
        request_id="request-2",
    )["revision"] == 2

    assert harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=2,
        parameters={"confirm": {"severity": "P1", "owner": "local-owner"}},
        intent_id="intent-3",
        request_id="request-3",
    )["revision"] == 3
    third = harness.saved("issue-1")
    assert third["status"] == "confirmed" and third["severity"] == "P1"
    assert third["confirmed_by"] == "session:session-user-1"
    assert third["blocking"] == "blocking"
    assert third["review_state"] == "unreviewed"

    assert harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=3,
        parameters={"start": True},
        intent_id="intent-4",
        request_id="request-4",
    )["revision"] == 4

    assert harness.command(
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
    )["revision"] == 5
    assert harness.saved("issue-1")["status"] == "ready_for_retest"

    # 不可变历史：旧修订仍是草稿，不被新修订覆盖。
    assert (
        harness.repository.read(
            aggregate_kind="issue", record_id="issue-1", revision=1
        ).payload["status"]
        == "draft"
    )


def test_close_issue_derives_every_fact_from_saved_execution(tmp_path) -> None:
    harness = _Harness(
        tmp_path,
        facts=_facts(
            attempt=_attempt(),
            evidence=(_evidence(),),
            verifications=(_verified_source(),),
        ),
        objects=_Objects(),
    )
    evidence = harness.service._closure_evidence(  # noqa: SLF001 - 直接核对派生结果
        _ready_issue(), dict(_CLOSE_PARAMETERS)
    )
    assert evidence.actual_execution is True
    assert evidence.current_regression_attempt is True
    assert evidence.evidence_saved is True
    assert evidence.satisfies_original_criteria is True
    assert evidence.basis_confirmed is True
    assert evidence.source_identity_matched is True
    assert evidence.dependencies_valid is True
    # 证据集合由服务从已提交证据派生，不接受调用方传入。
    assert evidence.evidence_refs == ("evidence-1",)

    failed = harness.service._closure_evidence(  # noqa: SLF001
        _ready_issue(), {**_CLOSE_PARAMETERS, "assertion_outcome": "failed"}
    )
    assert failed.satisfies_original_criteria is False


def test_unknown_close_parameters_are_rejected(tmp_path) -> None:
    harness = _Harness(tmp_path, facts=_facts())
    with pytest.raises(Exception) as refused:
        harness.service._closure_evidence(  # noqa: SLF001
            _ready_issue(), {**_CLOSE_PARAMETERS, "actual_execution": True}
        )
    assert "actual_execution" in str(refused.value)


def test_non_current_or_unfinished_attempts_are_refused(tmp_path) -> None:
    stale = _Harness(
        tmp_path,
        facts=_facts(
            attempt=_attempt(current=False),
            evidence=(_evidence(),),
            verifications=(_verified_source(),),
        ),
        objects=_Objects(),
    )
    with pytest.raises(IssueGuardRefused):
        stale.service._closure_evidence(_ready_issue(), dict(_CLOSE_PARAMETERS))  # noqa: SLF001

    running = _Harness(
        tmp_path,
        facts=_facts(
            attempt=_attempt(state="running", capture="partial"),
            evidence=(_evidence(),),
            verifications=(_verified_source(),),
        ),
        objects=_Objects(),
    )
    with pytest.raises(IssueGuardRefused):
        running.service._closure_evidence(_ready_issue(), dict(_CLOSE_PARAMETERS))  # noqa: SLF001


def test_unknown_assertion_missing_evidence_and_bad_bytes_are_refused(tmp_path) -> None:
    cases = [
        (
            _facts(
                attempt=_attempt(),
                evidence=(_evidence(),),
                verifications=(_verified_source(),),
            ),
            {**_CLOSE_PARAMETERS, "assertion_ref": "not-in-step"},
            None,
        ),
        (
            _facts(attempt=_attempt(), evidence=(), verifications=(_verified_source(),)),
            dict(_CLOSE_PARAMETERS),
            None,
        ),
        (
            _facts(
                attempt=_attempt(),
                evidence=(_evidence(),),
                verifications=(_verified_source(),),
            ),
            dict(_CLOSE_PARAMETERS),
            _Objects(payload=b"different"),
        ),
    ]
    for facts, parameters, objects in cases:
        harness = _Harness(tmp_path, facts=facts, objects=objects)
        with pytest.raises(Exception) as refused:
            harness.service._closure_evidence(_ready_issue(), parameters)  # noqa: SLF001
        assert getattr(refused.value, "code", None) in {
            "INVALID_REQUEST",
            "INTEGRITY_CHECK_FAILED",
        }


def test_source_mismatch_invalidation_and_unconfirmed_basis_are_refused(tmp_path) -> None:
    mismatched = _Harness(
        tmp_path,
        facts=_facts(
            attempt=_attempt(),
            evidence=(_evidence(),),
            verifications=(_verified_source(state="unverified"),),
        ),
        objects=_Objects(),
    )
    with pytest.raises(IssueGuardRefused):
        mismatched.service._closure_evidence(_ready_issue(), dict(_CLOSE_PARAMETERS))  # noqa: SLF001

    invalidated = _Harness(
        tmp_path,
        facts=_facts(
            attempt=_attempt(),
            evidence=(_evidence(),),
            verifications=(_verified_source(),),
            invalidations=(
                {
                    "invalidation_id": "invalidation-1",
                    "run_id": "run-1",
                    "affected_step_id": "step-1",
                    "reason": "upstream changed",
                    "source_revision_ref": "sha256:revision-1",
                    "transitive": False,
                },
            ),
        ),
        objects=_Objects(),
    )
    with pytest.raises(IssueGuardRefused):
        invalidated.service._closure_evidence(_ready_issue(), dict(_CLOSE_PARAMETERS))  # noqa: SLF001

    unconfirmed = _Harness(
        tmp_path,
        facts=_facts(
            attempt=_attempt(), evidence=(_evidence(),), verifications=(_verified_source(),)
        ),
        objects=_Objects(),
        case_confirmed=False,
    )
    with pytest.raises(Exception) as refused:
        unconfirmed.service._closure_evidence(  # noqa: SLF001
            _ready_issue(),
            {**_CLOSE_PARAMETERS, "assertion_ref": "assertion-1"},
        )
    assert getattr(refused.value, "code", None) == "AWAITING_USER_CONFIRMATION"


def test_evidence_object_bytes_must_match_the_reference(tmp_path) -> None:
    harness = _Harness(
        tmp_path,
        facts=_facts(
            attempt=_attempt(), evidence=(_evidence(),), verifications=(_verified_source(),)
        ),
        objects=_Objects(payload=b"different"),
    )
    with pytest.raises(Exception) as refused:
        harness.service._closure_evidence(_ready_issue(), dict(_CLOSE_PARAMETERS))  # noqa: SLF001
    assert getattr(refused.value, "code", None) == "INTEGRITY_CHECK_FAILED"


def test_revision_conflict_intent_conflict_and_idempotent_replay(tmp_path) -> None:
    harness = _Harness(tmp_path, facts=_facts())
    harness.command(
        action="update_issue", issue_id="issue-3", expected_revision=0,
        parameters={"title": "t", "links": {}}, intent_id="i1", request_id="r1",
    )
    with pytest.raises(IssueRevisionConflict) as conflict:
        harness.command(
            action="update_issue", issue_id="issue-3", expected_revision=7,
            parameters={"start": True}, intent_id="i2", request_id="r2",
        )
    assert conflict.value.code == "RECORD_REVISION_CONFLICT"
    assert (conflict.value.expected, conflict.value.current) == (7, 1)

    with pytest.raises(IssueIntentConflict):
        harness.command(
            action="update_issue", issue_id="issue-3", expected_revision=0,
            parameters={"title": "different", "links": {}}, intent_id="i1", request_id="r3",
        )

    replay = harness.command(
        action="update_issue", issue_id="issue-3", expected_revision=0,
        parameters={"title": "t", "links": {}}, intent_id="i1", request_id="r4",
    )
    assert replay["revision"] == 1
    assert harness.repository.current_revision("issue", "issue-3") == 1


def test_relay_session_cannot_confirm(tmp_path) -> None:
    harness = _Harness(tmp_path, facts=_facts(), actors=_RejectingActors())
    harness.command(
        action="update_issue", issue_id="issue-4", expected_revision=0,
        parameters={"title": "t", "links": {}}, intent_id="i1", request_id="r1",
    )
    with pytest.raises(Exception) as refused:
        harness.command(
            action="update_issue", issue_id="issue-4", expected_revision=1,
            parameters={"confirm": {"severity": "P0", "owner": "o"}},
            intent_id="i2", request_id="r2",
        )
    assert getattr(refused.value, "code", None) == "AWAITING_USER_CONFIRMATION"


def test_handlers_are_dispatchable_and_advertised(tmp_path) -> None:
    from aitest.interfaces.local.d_registration import d_registration_for

    harness = _Harness(tmp_path, facts=_facts())
    api = LocalAPI(
        instance_id="instance-d",
        workspace_id="workspace-1",
        handlers=d_registration_for(harness.service),
    )
    session = Session(
        session_id="session-1", entry_kind=EntryKind.INTERACTIVE_CLI, interactive=True
    )
    response = api.dispatch(
        Command(
            request_id="req-1",
            action="update_issue",
            project_id="project-1",
            intent_id="intent-1",
            expected_revision=0,
            parameters={"issue_id": "issue-5", "title": "t", "links": {}},
        ),
        session,
    )
    assert response.error is None, response.error
    assert response.result["record_id"] == "issue-5"

    doctor = api.dispatch(Command(request_id="req-2", action="doctor"), session)
    advertised = {str(item) for item in doctor.result["supported_actions"]}
    assert {"update_issue", "record_fix", "close_issue"} <= advertised

    # AI 中继不得执行人工动作。
    relay = Session(session_id="relay-1", entry_kind=EntryKind.AGENT_RELAY, interactive=False)
    denied = api.dispatch(
        Command(
            request_id="req-3",
            action="close_issue",
            project_id="project-1",
            intent_id="intent-2",
            expected_revision=0,
            parameters={"issue_id": "issue-5"},
        ),
        relay,
    )
    assert denied.error is not None and denied.error.code == "AWAITING_USER_CONFIRMATION"


def test_redeclaring_the_same_evidence_never_writes_an_unreadable_record(tmp_path) -> None:
    """同一证据被重复声明时按**引用集合**合并：不能写出连自己都读不回来的记录。

    反例（修复前）：第二次 `evidence_refs: ["evidence-1"]` 会被追加成
    `["evidence-1", "evidence-1"]`，而读侧严格拒绝重复值 ⇒ 该问题的**后续任何修订**
    都会在 `issue_from_payload` 处整体失败（问题被写坏且无法读出）。
    """

    harness = _Harness(tmp_path, facts=_facts())
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=0,
        parameters={"title": "checkout fails", "links": {}},
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
    # 在后续过渡里重复声明同一证据：合并为一份，而不是写出重复值。
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=2,
        parameters={
            "confirm": {"severity": "P1", "owner": "local-owner"},
            "evidence_refs": ["evidence-1"],
        },
        intent_id="intent-3",
        request_id="request-3",
    )
    payload = harness.saved("issue-1")
    assert payload["evidence_refs"] == ["evidence-1"]

    # 记录仍可读：下一次修订（start）必须成功；写坏则在读取处整体失败。
    harness.command(
        action="update_issue",
        issue_id="issue-1",
        expected_revision=3,
        parameters={"start": True},
        intent_id="intent-4",
        request_id="request-4",
    )
    assert harness.saved("issue-1")["status"] == "in_progress"


def test_issue_index_entries_match_the_documented_upper_bound(tmp_path) -> None:
    """入口数遵守存储合同 `4m+24` 上界（m=已知模块数；未知模块按一个分支计）。"""

    from aitest.application.review.saved_defects import issue_index_entries

    links = {
        "module_ids": tuple(f"m{index}" for index in range(3)),
        "layers": ("L2",),
        "case_id": None,
        "run_id": None,
        "attempt_id": None,
    }
    issue = IssueRecord(
        issue_id="issue-6", project_id="project-1", revision=1, title="t",
        severity=IssueSeverity.P2,
    )
    entries = issue_index_entries(issue, links=links, open_state=True, blocking="non_blocking")
    assert len(entries) == 4 * 3 + 24
    assert {entry["mask"] for entry in entries} == set(range(14))
    assert {entry["view"] for entry in entries} == {"ALL", "OPEN"}

    closed = issue_index_entries(issue, links=links, open_state=False, blocking="non_blocking")
    assert {entry["view"] for entry in closed} == {"ALL"}
