"""FR14 生产用例：报告修订 + 本地复核持久化，判定由已提交事实派生。

报告缺口的形态是"报告/复核只有纯门面，调用方自报结论、无持久化"。本文件用
**真实准备记录**（`tests/support/prepared_run_factory` 的 `prepare_run` 产物）+
**已提交执行事实**驱动 `create_report`，并用真实文件底座证明：

- 判定（业务结论/等级/缺口/覆盖）与冻结输入（范围/来源/环境/规则/验收/模板/计划）
  全部由仓储与事实派生，调用方只能给 `report_id`/`run_id`/`issue_ids`；
- 报告修订不可变、按内容修订追加，幂等重放不新增修订，异输入显式冲突；
- 本地复核**绑定准确报告修订**，复核人取受控会话（AI 中继拒绝）；
- 断言结果尚未有生产端派生（D-02 未闭合）时，报告如实为 `incomplete`，**不伪造通过**。

真实 Trae 端到端与制品导出（`export_report`/`copy_repair_brief`）未验收，见修复证据。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from aitest.application.planning.prepare_run import prepare_run
from aitest.application.planning.serialization import acceptance_scope_to_payload
from aitest.application.ports import EnvironmentResolutionRequest
from aitest.application.review.saved_reports import (
    ReportActorRequired,
    ReportGuardRefused,
    ReportIntentConflict,
    ReportManagementError,
    ReportNotSaved,
    ReportRevisionConflict,
    SavedReportService,
    local_review_from_payload,
    report_from_payload,
)
from aitest.contracts.commands import Command
from aitest.domain.approvals import EntryKind
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.environment_resolution import FixtureEnvironmentResolver
from tests.support.memory_substrate import FixedClock
from tests.support.prepared_run_factory import FIXED_NOW, PROJECT_ID, build_scenario

_RUN_ID = "run-report-1"


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class _Actor:
    def __init__(
        self,
        session_id: str = "session-user-1",
        entry_kind: EntryKind = EntryKind.INTERACTIVE_CLI,
        interactive: bool = True,
    ) -> None:
        self.session_id, self.entry_kind, self.interactive = session_id, entry_kind, interactive


class _Actors:
    def current(self) -> _Actor:
        return _Actor()


class _RelayActors:
    def current(self) -> _Actor:
        return _Actor("relay-1", EntryKind.AGENT_RELAY, False)


class _FactsReader:
    def __init__(self, facts: Any, *, by_run: dict[str, Any] | None = None) -> None:
        self._facts = facts
        self._by_run = by_run or {}

    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> Any:
        if run_id in self._by_run:
            return self._by_run[run_id]
        if self._facts is None:
            raise ValueError("saved run facts are unavailable")
        return self._facts


def _facts(prepared: Any, *, cases: tuple[str, ...], evidence: tuple[str, ...] = ()) -> Any:
    from aitest.contracts.execution_facts import ExecutionFacts

    return ExecutionFacts.model_validate(
        {
            "facts_id": "facts-report-1",
            "project_id": PROJECT_ID,
            "run_id": _RUN_ID,
            "snapshot_commit_id": "commit-9",
            "snapshot_cursor": 9,
            "snapshot_revision": 1,
            "committed_at": "2026-10-12T11:00:00+00:00",
            "run_revision": 1,
            "plan_revision": {
                "revision_id": prepared.plan_revision.revision_id,
                "revision_no": prepared.plan_revision.revision_no,
                "digest": prepared.plan_revision.digest,
            },
            "run": {
                "run_id": _RUN_ID,
                "run_revision": 1,
                "origin_workspace_id": prepared.workspace_id,
                "intent_id": prepared.intent_id,
                "tier": "full",
                "driver": "planned",
                "conclusion_ceiling": "passable",
                "plan_revision": {
                    "revision_id": prepared.plan_revision.revision_id,
                    "revision_no": prepared.plan_revision.revision_no,
                    "digest": prepared.plan_revision.digest,
                },
                "environment_ref": (
                    f"{prepared.environment.environment_id}@{prepared.environment.revision}"
                ),
                "environment_isolation_mode": "venv",
                "rules_revision": prepared.rule_versions[0].rule_id
                + "@"
                + str(prepared.rule_versions[0].revision)
                if prepared.rule_versions
                else "rules-1@1",
                "control_state": "not_started",
                "required_scope": list(cases),
                "selected_scope": list(cases),
            },
            "steps": [],
            "attempts": [],
            "evidence_refs": [
                {
                    "evidence_id": identity,
                    "evidence_revision": 1,
                    "project_id": PROJECT_ID,
                    "source_instance_id": "instance-1",
                    "run_id": _RUN_ID,
                    "step_id": "step-1",
                    "attempt_id": "attempt-1",
                    "evidence_kind": "command_output",
                    "capture_source": "plugin_runtime",
                    "code_identity": {"binding_kind": "plain", "workspace_ref": "binding@1"},
                    "object_digest": "sha256:" + "a" * 64,
                    "object_size": 3,
                    "integrity": "complete",
                    "redaction_state": "not_required",
                    "projection_state": "displayable",
                    "evidence_level": "full_link",
                }
                for identity in evidence
            ],
        }
    )


class _Harness:
    def __init__(
        self,
        tmp_path: Any,
        *,
        scenario_name: str = "plain",
        evidence: tuple[str, ...] = (),
        actors: object | None = None,
        facts_by_run: dict[str, Any] | None = None,
        reuse_references: Any | None = None,
    ) -> None:
        self.scenario = build_scenario(scenario_name)
        plan = self.scenario.plan
        unit = self.scenario.unit_of_work
        unit.open(PROJECT_ID)
        unit.stage_record(
            aggregate_kind="acceptance_scope",
            record_id=plan.scope.scope_id,
            expected_revision=None,
            payload=acceptance_scope_to_payload(plan.scope, project_id=PROJECT_ID),
        )
        unit.commit()
        # 场景工厂不解析环境；报告要求"完整准备"（`validate_preparation_origin` 拒绝
        # 无 resolution 的准备），因此这里用显式合成环境端口重新准备一次并落盘
        # `prepared_run`，与生产 `prepare_run(persist_snapshot=True)` 同形。
        resolved = FixtureEnvironmentResolver().resolve(
            EnvironmentResolutionRequest(
                project_id=PROJECT_ID,
                environment_id=self.scenario.inputs.environment.environment_id,
                isolation_mode=str(self.scenario.inputs.environment.isolation_mode),
                interpreter_requirement="python>=3.13,<3.14",
            )
        )
        self.prepared = prepare_run(
            replace(
                self.scenario.inputs,
                client_id="client-report",
                prepare_request_id="prepare-report-1",
                environment=resolved,
            ),
            unit_of_work=unit,
            reader=self.scenario.reader,
            clock=FixedClock(FIXED_NOW),
        )
        assert self.prepared.environment.resolution is not None
        # 内存底座的提交序号是 `commit-N` 文本，`persist_snapshot=True` 会按整数自增；
        # 因此按生产载荷形态手工冻结 `prepared_run`（与真实核心写入内容一致）。
        unit.open(PROJECT_ID)
        unit.stage_record(
            aggregate_kind="prepared_run",
            record_id=self.prepared.prepared_run_id,
            expected_revision=None,
            payload=self.prepared.model_dump(mode="json"),
        )
        unit.commit()

        self.cases = tuple(case.case_id for case in self.scenario.cases)
        self.repository = FileRecordRepository(tmp_path)
        self.unit = FileUnitOfWork(tmp_path)
        self.facts = _facts(self.prepared, cases=self.cases, evidence=evidence)
        self.facts_reader = _FactsReader(self.facts, by_run=facts_by_run)
        self.service = SavedReportService(
            unit=self.unit,
            reader=self.scenario.reader,
            records=self.repository,
            clock=_Clock(),
            actors=actors or _Actors(),
            runtime=self.facts_reader,
            workspace_id=self.prepared.workspace_id,
            basis_proof=None,
            reuse_references=reuse_references,
        )
        # 导出（FR14 §5/§8）：同一文件底座 + 真实制品端口 + 生产脱敏投影器
        # + 真实对象库（`artifact_digest` 是已提交的永久对象引用）。
        from aitest.application.review.saved_exports import SavedExportService
        from aitest.infrastructure.exports import FileReportArtifactPort
        from aitest.infrastructure.file_store.objects import FileObjectStore
        from aitest.infrastructure.projections import SafeMaterialProjector

        self.artifacts = FileReportArtifactPort(tmp_path)
        self.objects = FileObjectStore(tmp_path)
        self.exports = SavedExportService(
            unit=self.unit,
            reader=self.scenario.reader,
            records=self.repository,
            clock=_Clock(),
            actors=actors or _Actors(),
            runtime=_FactsReader(self.facts),
            workspace_id=self.prepared.workspace_id,
            artifacts=self.artifacts,
            projector=SafeMaterialProjector(),
            objects=self.objects,
            instance_id="instance-r",
        )
        self.save_facts(self.facts)

    def save_facts(self, facts):
        """Persist exact fixture snapshots and real objects; no real execution claim."""
        from uuid import uuid4

        evidence = []
        for item in facts.evidence_refs:
            ref = self.objects.publish_bytes(PROJECT_ID, b"abc", media_type="text/plain")
            evidence.append(
                item.model_copy(
                    update={
                        "object_digest": ref.digest,
                        "object_size": ref.size,
                        "media_type": "text/plain",
                    }
                )
            )
        facts = facts.model_copy(update={"evidence_refs": tuple(evidence)})
        if self.repository.current_revision("execution_facts", facts.snapshot_commit_id):
            facts = facts.model_copy(
                update={
                    "snapshot_commit_id": "fixture-snapshot-" + uuid4().hex,
                    "snapshot_cursor": facts.snapshot_cursor + 1,
                }
            )
        request = "fixture-snapshot-" + uuid4().hex
        self.unit.begin(request, PROJECT_ID, intent_id=request + "-intent")
        self.unit.stage_record(
            aggregate_kind="execution_facts",
            record_id=facts.snapshot_commit_id,
            expected_revision=0,
            payload=facts.model_dump(mode="json"),
        )
        self.unit.commit(request)
        self.facts = facts
        self.facts_reader._facts = facts
        self.exports._runtime = _FactsReader(facts)

    def command(
        self,
        *,
        action: str,
        expected: int,
        parameters: dict[str, object],
        intent_id: str,
        request_id: str,
    ) -> dict[str, object]:
        handler = {
            "create_report": self.service.create_report,
            "record_local_review": self.service.record_local_review,
            "export_report": self.exports.export_report,
        }[action]
        return dict(
            handler(
                Command(
                    request_id=request_id,
                    action=action,
                    project_id=PROJECT_ID,
                    intent_id=intent_id,
                    expected_revision=expected,
                    parameters=parameters,
                )
            )
        )

    def artifact_bytes(self, artifact_ref: str, *, expected_sha256: str) -> bytes:
        from aitest.infrastructure.exports import FileReportArtifactPort

        assert isinstance(self.artifacts, FileReportArtifactPort)
        return self.artifacts.read_artifact(artifact_ref, expected_sha256=expected_sha256)

    def saved_export(self, record_id: str) -> dict[str, object]:
        revision = self.repository.current_revision("report_export", record_id)
        assert revision >= 1
        return dict(
            self.repository.read(
                aggregate_kind="report_export", record_id=record_id, revision=revision
            ).payload
        )

    def saved_report(self, report_id: str) -> dict[str, object]:
        revision = self.repository.current_revision("report", report_id)
        assert revision >= 1
        return dict(
            self.repository.read(
                aggregate_kind="report", record_id=report_id, revision=revision
            ).payload
        )


def _create(
    harness: _Harness,
    *,
    report_id: str = "report-1",
    expected: int = 0,
    intent_id: str = "intent-1",
    request_id: str = "request-1",
    issue_ids: list[str] | None = None,
) -> dict[str, object]:
    return harness.command(
        action="create_report",
        expected=expected,
        parameters={
            "report_id": report_id,
            "run_id": _RUN_ID,
            "issue_ids": issue_ids if issue_ids is not None else [],
        },
        intent_id=intent_id,
        request_id=request_id,
    )


def test_create_report_derives_decision_and_frozen_inputs_from_saved_facts(tmp_path) -> None:
    harness = _Harness(tmp_path, evidence=("evidence-1", "evidence-2"))

    created = _create(harness)
    assert created == {"aggregate_kind": "report", "record_id": "report-1", "revision": 1}

    payload = harness.saved_report("report-1")
    # 最小摘要键（reports.list / reports.by_id 使用）
    assert payload["business_outcome"] == "incomplete"
    assert payload["run_id"] == _RUN_ID
    assert payload["content_revision"] == 1
    assert payload["published_sequence"] >= 1
    # 冻结输入来自已保存准备与范围，而不是参数
    context = payload["context"]
    assert context["scope_name"] == harness.scenario.plan.scope.name
    assert context["source_identity"] == harness.prepared.snapshot.content_identity
    assert context["scope_revision"] == (
        f"scope:{harness.scenario.plan.scope.scope_id}@{harness.scenario.plan.scope.revision}"
    )
    assert context["acceptance_revision"] == str(harness.prepared.acceptance_scope_revision)
    assert context["run_id"] == _RUN_ID
    assert context["policy_version"] == "1.0"
    assert context["plan_revision_refs"]
    assert payload["evidence_refs"] == ["evidence-1", "evidence-2"]
    # 断言结果尚无生产端派生：V/P 为空，结论如实为 incomplete
    decision = payload["decision"]
    assert decision["coverage"]["verified"] == []
    assert decision["coverage"]["passed"] == []
    assert decision["gaps"], "未完成范围必须留下缺口，不能空手报通过"
    assert decision["evidence_grade"] in {None, "A", "B", "C", "D"}

    # 编解码往返一致
    restored = report_from_payload(payload)
    from aitest.application.execution.runtime_revision import SnapshotContentRef
    from aitest.application.review.saved_reports import report_to_payload

    again = report_to_payload(
        restored,
        published_sequence=payload["published_sequence"],
        execution_snapshot_ref=SnapshotContentRef.model_validate(payload["execution_snapshot_ref"]),
        blocking_issue_ids=frozenset(payload["blocking_issue_ids"]),
    )
    assert again == payload


def test_report_revision_is_immutable_and_revision_conflicts_are_structured(tmp_path) -> None:
    harness = _Harness(tmp_path)
    _create(harness)

    with pytest.raises(ReportRevisionConflict) as conflict:
        _create(harness, expected=7, intent_id="intent-2", request_id="request-2")
    assert conflict.value.code == "RECORD_REVISION_CONFLICT"
    assert (conflict.value.expected, conflict.value.current) == (7, 1)

    second = _create(harness, expected=1, intent_id="intent-3", request_id="request-3")
    assert second["revision"] == 2
    first = harness.repository.read(aggregate_kind="report", record_id="report-1", revision=1)
    assert first.payload["content_revision"] == 1
    assert harness.saved_report("report-1")["content_revision"] == 2


def test_intent_replay_is_idempotent_and_different_input_conflicts(tmp_path) -> None:
    harness = _Harness(tmp_path)
    _create(harness)

    replay = _create(harness, intent_id="intent-1", request_id="request-9")
    assert replay["revision"] == 1
    assert harness.repository.current_revision("report", "report-1") == 1

    with pytest.raises(ReportIntentConflict):
        harness.command(
            action="create_report",
            expected=0,
            parameters={"report_id": "report-1", "run_id": "another-run", "issue_ids": []},
            intent_id="intent-1",
            request_id="request-10",
        )


def test_report_freezes_issue_revisions_read_from_the_warehouse(tmp_path) -> None:
    harness = _Harness(tmp_path)
    from aitest.application.review.saved_defects import issue_to_payload
    from aitest.domain.review.defects import IssueRecord, IssueSeverity, IssueStatus

    issue = IssueRecord(
        issue_id="issue-1",
        project_id=PROJECT_ID,
        revision=1,
        title="t",
        status=IssueStatus.CONFIRMED,
        severity=IssueSeverity.P0,
        owner="owner",
        confirmed_by="session:s",
    )
    harness.unit.begin("seed-issue", PROJECT_ID, intent_id="seed-issue-intent")
    harness.unit.stage_record(
        aggregate_kind="issue",
        record_id="issue-1",
        expected_revision=0,
        payload=issue_to_payload(
            issue,
            links={
                "module_ids": [],
                "layers": [],
                "case_id": None,
                "run_id": None,
                "attempt_id": None,
                "regressions": [],
            },
            updated_sequence=1,
            open_state=True,
            blocking="blocking",
        ),
    )
    harness.unit.commit("seed-issue")
    _create(harness, issue_ids=["issue-1"])
    payload = harness.saved_report("report-1")
    assert payload["issue_revisions"] == [["issue-1", 1]]

    with pytest.raises(ReportGuardRefused):
        _create(
            harness,
            report_id="report-2",
            issue_ids=["issue-missing"],
            intent_id="intent-x",
            request_id="request-x",
        )


def test_record_local_review_binds_the_exact_report_revision(tmp_path) -> None:
    harness = _Harness(tmp_path)
    _create(harness)

    with pytest.raises(ReportNotSaved):
        harness.command(
            action="record_local_review",
            expected=0,
            parameters={
                "review_id": "review-0",
                "report_id": "report-missing",
                "conclusion": "accepted",
                "explanation": "looks fine",
            },
            intent_id="intent-r0",
            request_id="request-r0",
        )

    recorded = harness.command(
        action="record_local_review",
        expected=0,
        parameters={
            "review_id": "review-1",
            "report_id": "report-1",
            "conclusion": "accepted",
            "explanation": "reviewed the frozen revision",
        },
        intent_id="intent-r1",
        request_id="request-r1",
    )
    assert recorded == {
        "aggregate_kind": "local_review",
        "record_id": "review-1",
        "revision": 1,
    }
    stored = harness.repository.read(
        aggregate_kind="local_review", record_id="review-1", revision=1
    ).payload
    review = local_review_from_payload(stored)
    assert review.report_id == "report-1"
    assert review.report_revision == 1
    # 复核人取受控会话，不取参数
    assert review.reviewer == "session:session-user-1"
    assert review.conclusion == "accepted"

    # 新报告修订后，复核仍指向原修订；钉住错误修订必须显式冲突
    _create(harness, expected=1, intent_id="intent-2", request_id="request-2")
    with pytest.raises(ReportRevisionConflict):
        harness.command(
            action="record_local_review",
            expected=0,
            parameters={
                "review_id": "review-2",
                "report_id": "report-1",
                "report_revision": 1,
                "conclusion": "rework",
                "explanation": "stale pin",
            },
            intent_id="intent-r2",
            request_id="request-r2",
        )
    pinned = harness.command(
        action="record_local_review",
        expected=0,
        parameters={
            "review_id": "review-3",
            "report_id": "report-1",
            "report_revision": 2,
            "conclusion": "rework",
            "explanation": "second revision reviewed",
        },
        intent_id="intent-r3",
        request_id="request-r3",
    )
    assert pinned["revision"] == 1
    stored = harness.repository.read(
        aggregate_kind="local_review", record_id="review-3", revision=1
    ).payload
    assert stored["report_revision"] == 2


def test_relay_session_cannot_record_a_local_review(tmp_path) -> None:
    harness = _Harness(tmp_path, actors=_RelayActors())
    _create(harness)
    with pytest.raises(ReportActorRequired) as refused:
        harness.command(
            action="record_local_review",
            expected=0,
            parameters={
                "review_id": "review-1",
                "report_id": "report-1",
                "conclusion": "accepted",
                "explanation": "from a relay",
            },
            intent_id="intent-r1",
            request_id="request-r1",
        )
    assert refused.value.code == "AWAITING_USER_CONFIRMATION"


def test_create_report_refuses_unknown_parameters_and_missing_material(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ReportManagementError) as refused:
        harness.command(
            action="create_report",
            expected=0,
            parameters={
                "report_id": "report-1",
                "run_id": _RUN_ID,
                "issue_ids": [],
                "business_outcome": "passed",
            },
            intent_id="intent-1",
            request_id="request-1",
        )
    assert "business_outcome" in str(refused.value)

    # 未保存的运行事实 → 结构化拒绝，而不是 INTERNAL_ERROR 或假报告
    harness.service._runtime = _FactsReader(None)  # noqa: SLF001
    with pytest.raises(Exception) as guard:
        _create(harness, report_id="report-2", request_id="request-3")
    assert getattr(guard.value, "code", None) == "INVALID_REQUEST"


def test_report_is_visible_in_reports_list_and_by_id(tmp_path) -> None:
    """`reports.list` / `reports.by_id` 的固定目录查询必须能取到新报告。"""

    from aitest.application.planning.substrate import RecordQuery

    harness = _Harness(tmp_path)
    _create(harness)
    repository = harness.repository

    by_id = repository.query(RecordQuery(project_id=PROJECT_ID, report_id="report-1"))
    assert [item.record_id for item in by_id.items] == ["report-1"]
    assert by_id.items[0].aggregate_kind == "report"

    by_outcome = repository.query(RecordQuery(project_id=PROJECT_ID, business_outcome="incomplete"))
    assert "report-1" in [item.record_id for item in by_outcome.items]

    # 其它业务结论不得命中该报告（摘要键取自判定，不是占位）。
    passed = repository.query(RecordQuery(project_id=PROJECT_ID, business_outcome="passed"))
    assert "report-1" not in [item.record_id for item in passed.items]


def test_report_marks_the_reuse_candidate_source_as_not_configured(tmp_path) -> None:
    """候选来源未接时必须显式记为"未接"，不能写成"核过 0 条"这样的结论。"""

    harness = _Harness(tmp_path)
    _create(harness)
    payload = harness.saved_report("report-1")
    assert payload["reuse"] == {
        "candidates": None,
        "reason": "reuse_candidate_source_not_configured",
    }
    assert payload["decision"]["coverage"]["reused"] == []


def test_report_records_reuse_denials_with_named_reasons(tmp_path) -> None:
    """有候选但守卫不过时必须留下**具名原因**，而不是静默空集。"""

    from tests.unit.test_reuse_consumption import _reference

    harness = _Harness(
        tmp_path,
        reuse_references=lambda project, run: (_reference(target_run_id=run),),
    )
    _create(harness)
    payload = harness.saved_report("report-1")
    reuse = payload["reuse"]
    assert reuse["candidates"] == 1
    assert reuse["reused"] == []
    assert reuse["denials"] == [
        {
            "case_id": "case-1",
            # 夹具里：用例不在本轮 S（场景用另一组 case_id）、没有源运行事实、
            # 没有 evidence_ref 记录、也没有受控确认链 ⇒ 每条守卫都留下具名原因。
            "reasons": [
                "case_not_in_selected_scope",
                "required_step_mapping_incomplete",
                "source_attempt_unverified",
                "source_identity_unverified",
                "evidence_unreadable",
                "basis_confirmed_unverified",
                "verification_valid_unverified",
                "complete_reuse_basis_unverified",
            ],
        }
    ]
    assert payload["decision"]["coverage"]["reused"] == []


def test_report_handlers_are_dispatchable_and_advertised(tmp_path) -> None:
    from aitest.interfaces.local.api import LocalAPI, Session
    from aitest.interfaces.local.d_registration import report_registration_for

    harness = _Harness(tmp_path)
    api = LocalAPI(
        instance_id="instance-r",
        workspace_id=harness.prepared.workspace_id,
        handlers=report_registration_for(harness.service),
    )
    session = Session(
        session_id="session-1", entry_kind=EntryKind.INTERACTIVE_CLI, interactive=True
    )
    response = api.dispatch(
        Command(
            request_id="req-1",
            action="create_report",
            project_id=PROJECT_ID,
            intent_id="intent-1",
            expected_revision=0,
            parameters={"report_id": "report-1", "run_id": _RUN_ID, "issue_ids": []},
        ),
        session,
    )
    assert response.error is None, response.error
    assert response.result["record_id"] == "report-1"

    doctor = api.dispatch(Command(request_id="req-2", action="doctor"), session)
    advertised = {str(item) for item in doctor.result["supported_actions"]}
    assert {"create_report", "record_local_review"} <= advertised

    # 未知/不支持的动作名不再默认报错为 INTERNAL_ERROR
    unknown = api.dispatch(
        Command(
            request_id="req-3",
            action="export_report",
            project_id=PROJECT_ID,
            intent_id="intent-9",
            expected_revision=0,
            parameters={},
        ),
        session,
    )
    assert unknown.error is not None
    assert unknown.error.code == "CAPABILITY_UNAVAILABLE"
