"""受控人工补证入口（`attach_evidence`）的生产行为测试。

报告点名的缺口是「缺受控人工补证入口」。本文件锁定：材料按内容寻址永久保存、
同时留下 `evidence_ref` 事实与 `manual_evidence` 记录，**不产生业务通过、不改状态**；
不安全/不可核对/非当前尝试的材料一律拒绝并给出具名原因。

夹具：真实文件底座（records + 对象库）+ 真实 `prepared_run`（取自场景工厂，
按生产形态 `prepare_run` 写出并校验原始回执）+ 运行事实替身。真实 Trae 与真实
人工会话仍未验收。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from aitest.application.evidence.manual_capture import (
    MANUAL_EVIDENCE_AGGREGATE,
    ManualEvidenceBlocked,
    SavedManualEvidenceCapture,
)
from aitest.application.planning.prepare_run import prepare_run
from aitest.application.planning.serialization import acceptance_scope_to_payload
from aitest.application.ports import EnvironmentResolutionRequest
from aitest.contracts.commands import Command
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.environment_resolution import FixtureEnvironmentResolver
from tests.support.memory_substrate import FixedClock
from tests.support.prepared_run_factory import FIXED_NOW, PROJECT_ID, build_scenario

_RUN_ID = "run-manual-1"


class _Coordinator:
    def __init__(self, facts: Any) -> None:
        self._facts = facts

    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> Any:
        if self._facts is None:
            raise ValueError("saved run facts are unavailable")
        return self._facts


class _Router:
    """准备材料走场景底座，业务记录走真实文件底座（两个存储的测试替身）。"""

    def __init__(self, scenario_reader: Any, repository: FileRecordRepository) -> None:
        self._scenario, self._repository = scenario_reader, repository

    def find_preparation_by_intent(self, *, intent_id: str) -> Any:
        return self._scenario.find_preparation_by_intent(intent_id=intent_id)

    def read(self, *, aggregate_kind: str, record_id: str, revision: int) -> Any:
        if aggregate_kind in {"prepared_run", "preparation_record", "acceptance_scope"}:
            return self._scenario.read(
                aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
            )
        return self._repository.read(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision
        )

    def current_revision(self, *, aggregate_kind: str, record_id: str) -> int:
        if aggregate_kind in {"prepared_run", "preparation_record", "acceptance_scope"}:
            return self._scenario.current_revision(
                aggregate_kind=aggregate_kind, record_id=record_id
            )
        return self._repository.current_revision(aggregate_kind, record_id)


def _facts(prepared: Any, *, attempt_state: str = "completed", current: bool = True) -> Any:
    from aitest.contracts.execution_facts import ExecutionFacts

    attempt = {
        "attempt_id": "attempt-1",
        "run_id": _RUN_ID,
        "step_id": "step-1",
        "attempt_revision": 1,
        "attempt_index": 1,
        "retry_count": 0,
        "state": attempt_state,
        "is_current": current,
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
        "capture_completeness": "complete",
    }
    return ExecutionFacts.model_validate(
        {
            "facts_id": "facts-manual-1",
            "project_id": PROJECT_ID,
            "run_id": _RUN_ID,
            "snapshot_commit_id": "commit-9",
            "snapshot_cursor": 9,
            "snapshot_revision": 1,
            "committed_at": "2026-10-13T09:30:00+00:00",
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
                "rules_revision": "rules-1@1",
                "control_state": "not_started",
                "required_scope": ["case-1"],
                "selected_scope": ["case-1"],
            },
            "steps": [
                {
                    "step_id": "step-1",
                    "run_id": _RUN_ID,
                    "step_revision": 1,
                    "step_revision_ref": {
                        "step_revision_id": "step-1-rev",
                        "revision_no": 1,
                        "digest": "sha256:step-1",
                    },
                    "ordinal": 1,
                    "case_id": "case-1",
                    "required_for_case": True,
                    "level": "L2",
                    "state": "completed",
                    "assertion_refs": ["assertion-1"],
                    "current_attempt_id": "attempt-1",
                }
            ],
            "attempts": [attempt],
            "current_attempt_by_step": {"step-1": "attempt-1" if current else None},
        }
    )


class _Harness:
    def __init__(self, tmp_path: Any, *, attempt_state: str = "completed", current: bool = True):
        self.scenario = build_scenario("plain")
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
                client_id="client-manual",
                prepare_request_id="prepare-manual-1",
                environment=resolved,
            ),
            unit_of_work=unit,
            reader=self.scenario.reader,
            clock=FixedClock(FIXED_NOW),
        )
        unit.open(PROJECT_ID)
        unit.stage_record(
            aggregate_kind="prepared_run",
            record_id=self.prepared.prepared_run_id,
            expected_revision=None,
            payload=self.prepared.model_dump(mode="json"),
        )
        unit.commit()

        self.repository = FileRecordRepository(tmp_path)
        self.unit = FileUnitOfWork(tmp_path)
        self.objects = FileObjectStore(tmp_path)
        self.facts = _facts(self.prepared, attempt_state=attempt_state, current=current)
        self.service = SavedManualEvidenceCapture(
            coordinator=_Coordinator(self.facts),
            unit=self.unit,
            reader=_Router(self.scenario.reader, self.repository),
            objects=self.objects,
            workspace_id=self.prepared.workspace_id,
            protector=_refuse_credentials,
        )

    def attach(self, **overrides: object) -> Any:
        parameters: dict[str, object] = {
            "run_id": _RUN_ID,
            "step_id": "step-1",
            "attempt_id": "attempt-1",
            "operation_type": "form",
            "target_ref": "checkout form",
            "observed_result": "the order was created and the id was shown",
            "business_object_id": "order-1",
        }
        parameters.update(overrides)
        return self.service.apply(
            Command(
                request_id="req-manual-1",
                action="attach_evidence",
                project_id=PROJECT_ID,
                intent_id="intent-manual-1",
                expected_revision=0,
                parameters=parameters,
            )
        )

    def payload(self, record_id: str) -> dict[str, object]:
        revision = self.repository.current_revision(MANUAL_EVIDENCE_AGGREGATE, record_id)
        assert revision == 1
        return dict(
            self.repository.read(
                aggregate_kind=MANUAL_EVIDENCE_AGGREGATE, record_id=record_id, revision=revision
            ).payload
        )


def _refuse_credentials(value: Any) -> Any:
    """替身保护器：只要出现疑似凭据行就"改变"材料（生产为已知凭据登记表）。"""

    text = str(value)
    if "password" in text or "Authorization:" in text:
        return {"blocked": True}
    return value


def test_manual_evidence_is_saved_permanently_without_business_pass(tmp_path) -> None:
    harness = _Harness(tmp_path)
    result = harness.attach()

    assert result["capture_source"] == "manual"
    assert result["evidence_kind"] == "manual_step"
    assert result["evidence_level"] == "manual_associated"
    assert result["business_passed"] is False
    assert result["requires_verification"] is True
    assert result["gaps"] == ["manual_capture", "independent_verification_unconfirmed"]

    evidence_id = str(result["evidence_id"])
    stored = harness.repository.read(
        aggregate_kind="evidence_ref", record_id=evidence_id, revision=1
    ).payload
    assert stored["run_id"] == _RUN_ID
    assert stored["step_id"] == "step-1"
    assert stored["attempt_id"] == "attempt-1"
    assert stored["capture_source"] == "manual"
    assert stored["integrity"] == "complete"
    assert stored["code_identity"]["binding_kind"] == "plain"

    record = harness.payload(str(result["record_id"]))
    assert record["business_passed"] is False
    assert record["observed_result"].startswith("the order was created")
    assert record["operation_type"] == "form"
    # 材料按内容寻址永久保存，且字节与引用一致。
    ref = StoredObjectRef(**record["object_ref"])
    material = harness.objects.read_bytes(ref)
    assert material == harness.objects.read_bytes(ref)
    assert ref.digest == record["material_digest"]


def test_repeat_with_the_same_intent_is_idempotent(tmp_path) -> None:
    harness = _Harness(tmp_path)
    first = harness.attach()
    again = harness.attach()
    assert again == first
    record_id = str(first["record_id"])
    assert harness.repository.current_revision(MANUAL_EVIDENCE_AGGREGATE, record_id) == 1


def test_credentials_and_unsafe_attachments_are_refused(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ManualEvidenceBlocked) as refused:
        harness.attach(observed_result="the response header was Authorization: Bearer xyz")
    assert "credentials" in str(refused.value)

    # 附件必须是已按内容寻址保存、可核对的文本对象。
    attached = harness.objects.publish_bytes(
        PROJECT_ID, b"no credentials here", media_type="text/plain"
    )
    from dataclasses import asdict

    result = harness.attach(attachment_refs=[asdict(attached)])
    assert result["business_passed"] is False
    record = harness.payload(str(result["record_id"]))
    assert record["attachment_refs"][0]["digest"] == attached.digest

    with pytest.raises(ManualEvidenceBlocked):
        harness.attach(attachment_refs=[{**asdict(attached), "digest": "sha256:" + "a" * 64}])


def test_non_current_or_unfinished_attempt_is_refused(tmp_path) -> None:
    stale = _Harness(tmp_path, current=False)
    with pytest.raises(ManualEvidenceBlocked) as refused:
        stale.attach()
    assert "current attempt" in str(refused.value)

    running = _Harness(tmp_path, attempt_state="running")
    with pytest.raises(ManualEvidenceBlocked) as refused:
        running.attach()
    assert "completed attempt" in str(refused.value)


def test_unknown_fields_and_operation_types_are_named(tmp_path) -> None:
    harness = _Harness(tmp_path)
    with pytest.raises(ManualEvidenceBlocked) as unknown:
        harness.attach(operation_type="telepathy")
    assert "telepathy" in str(unknown.value)

    with pytest.raises(ManualEvidenceBlocked) as extra:
        harness.attach(assertion_passed=True)
    assert "assertion_passed" in str(extra.value)

    with pytest.raises(ManualEvidenceBlocked) as missing:
        harness.attach(observed_result="")
    assert "observed_result" in str(missing.value)
