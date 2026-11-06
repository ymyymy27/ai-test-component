"""Execute a frozen HTTP workflow as one non-process Attempt."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from aitest.application.errors import CapabilityUnavailable
from aitest.application.evidence.query_materials import _evidence_record
from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import (
    _evidence_fact,
    _trace_node_fact,
    project_attempt_fact,
)
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.application.execution.step_content import StepContentReader
from aitest.application.ports import (
    EvidenceObjectStore,
    HttpWorkflowCapture,
    HttpWorkflowPort,
    RecordRepository,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.contracts.http_workflow import HttpWorkflowSpec, parse_http_workflow
from aitest.contracts.prepared_run import PreparedRun
from aitest.domain.evidence.evidence import (
    CodeIdentity,
    EvidenceCaptureSource,
    EvidenceIntegrity,
    EvidenceKind,
    EvidenceLevel,
    EvidenceRef,
    RedactionState,
    TraceNode,
    TraceNodeState,
    TraceNodeType,
    TruthClass,
)
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    CaptureCompleteness,
    RecoveryCheckpoint,
    RecoveryRecord,
)
from aitest.domain.execution.sources import SourceBindingKind


class HttpWorkflowBlocked(ValueError):
    code = "EXECUTION_BLOCKED"


class SavedHttpWorkflowExecution:
    def __init__(
        self,
        authorizations: ExecutionAuthorizationService,
        coordinator: ExecutionCommitCoordinator,
        objects: EvidenceObjectStore,
        *,
        workspace_id: str,
        instance_id: str,
        adapter: HttpWorkflowPort | None,
    ) -> None:
        self.authorizations = authorizations
        self.coordinator = coordinator
        self.objects = objects
        self.workspace_id = workspace_id
        self.instance_id = instance_id
        self.adapter = adapter

    def execute(
        self, *, project_id: str, intent_id: str, action_id: str, step_id: str
    ) -> Mapping[str, object]:
        _, action = self.authorizations.resolver.read(project_id, action_id)
        if (intent_id, step_id) != (action.request.intent_id, action.request.step_id):
            raise HttpWorkflowBlocked("HTTP workflow intent or target differs from its action")
        if action.request.registered_entry.adapter_kind is not AdapterKind.HTTP:
            raise HttpWorkflowBlocked("the saved action is not an HTTP workflow")
        if self.adapter is None:
            raise CapabilityUnavailable("an HTTP workflow adapter is not configured")
        authorization = action.request.authorization_ref
        self.authorizations._origin(project_id, authorization.authorization_id)
        saved = self.coordinator.find_start(
            project_id=project_id,
            intent_id=intent_id,
            fingerprint=execution_start_fingerprint(action.attempt, action.request),
        )
        if saved is not None:
            current = self.coordinator.read_current_facts(
                project_id=project_id, run_id=action.request.run_id
            )
            if current is None:
                raise HttpWorkflowBlocked("the saved HTTP attempt facts are unavailable")
            return {
                "attempt": project_attempt_fact(
                    saved,
                    is_current=current.current_attempt_by_step.get(step_id)
                    == saved.attempt_id,
                ).model_dump(mode="json"),
                "execution_facts": current.model_dump(mode="json"),
            }
        run, step, prepared, _ = self.authorizations._context(
            project_id, action.request.run_id, step_id
        )
        content = StepContentReader(
            cast(RecordRepository, self.coordinator._records or self.coordinator._uow)
        ).read(run=run, step=step)
        spec = parse_http_workflow(content.checked_case().inputs)
        fingerprint = execution_start_fingerprint(action.attempt, action.request)
        attempt = replace(action.attempt, intent_digest=fingerprint)
        checkpoint = RecoveryRecord(
            RecoveryCheckpoint(
                last_committed_stage="intent_recorded",
                run_id=attempt.run_id,
                step_id=attempt.step_id,
                attempt_id=attempt.attempt_id,
                resolved_input_digest=attempt.resolved_input_digest,
                side_effect_class=attempt.side_effect_class,
            ),
            attempt,
            project_id=project_id,
        )
        self.coordinator.claim_start(
            project_id=project_id,
            intent_id=intent_id,
            fingerprint=fingerprint,
            checkpoint=checkpoint,
        )
        started = datetime.now(UTC)
        capture = self.adapter.execute(spec)
        ended = datetime.now(UTC)
        completed = replace(
            attempt,
            state=AttemptState.COMPLETED,
            capture_completeness=CaptureCompleteness.COMPLETE,
            structured_result_ref="http-workflow:" + action.attempt.attempt_id,
            started_at=started,
            ended_at=ended,
        )
        self.coordinator.commit_checkpoint(
            project_id=project_id,
            checkpoint=RecoveryRecord(
                RecoveryCheckpoint(
                    last_committed_stage="completed",
                    run_id=completed.run_id,
                    step_id=completed.step_id,
                    attempt_id=completed.attempt_id,
                    resolved_input_digest=completed.resolved_input_digest,
                    side_effect_class=completed.side_effect_class,
                ),
                completed,
                project_id=project_id,
            ),
        )
        evidence = self._publish_evidence(
            project_id=project_id,
            prepared=prepared,
            attempt=completed,
            spec=spec,
            capture=capture,
        )
        current = self._stage_evidence(project_id, completed, evidence, capture)
        return {
            "attempt": project_attempt_fact(completed, is_current=True).model_dump(
                mode="json"
            ),
            "execution_facts": current.model_dump(mode="json"),
        }

    def _publish_evidence(
        self,
        *,
        project_id: str,
        prepared: PreparedRun,
        attempt: Attempt,
        spec: HttpWorkflowSpec,
        capture: HttpWorkflowCapture,
    ) -> tuple[EvidenceRef, ...]:
        by_id = {item.request_id: item for item in spec.requests}
        identity = CodeIdentity(
            binding_kind=SourceBindingKind(prepared.binding_form.value),
            workspace_ref=self.workspace_id,
            commit_id=prepared.git_base_commit,
            file_manifest_digest=prepared.plain_manifest_digest,
            revision_ref=prepared.snapshot.source_snapshot_id,
        )
        evidence: list[EvidenceRef] = []
        for exchange in capture.exchanges:
            request = by_id[exchange.request_id]
            material = {
                "schema_version": "aitest.http-exchange/1.0",
                "request": {
                    "request_id": request.request_id,
                    "method": request.method,
                    "url": request.url,
                    "headers": list(request.headers),
                    "body": request.body,
                },
                "response": {
                    "status": exchange.status,
                    "headers": list(exchange.response_headers),
                    "body": exchange.body.decode("utf-8", errors="replace"),
                    "body_complete": exchange.body_complete,
                    "error_class": exchange.error_class,
                    "error_detail": exchange.error_detail,
                    "extracted": dict(exchange.extracted),
                    "assertions": [
                        {"assertion_id": item[0], "matched": item[1], "actual": item[2]}
                        for item in exchange.assertion_results
                    ],
                    "request_log_ref": exchange.request_log_ref,
                },
            }
            content = json.dumps(
                material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            ref = self.objects.publish_bytes(project_id, content, media_type="application/json")
            stored = self.objects.read_bytes(ref)
            evidence.append(
                EvidenceRef(
                    evidence_id=f"evidence:{attempt.attempt_id}:http:{exchange.request_id}",
                    project_id=project_id,
                    source_instance_id=self.instance_id,
                    run_id=attempt.run_id,
                    step_id=attempt.step_id,
                    attempt_id=attempt.attempt_id,
                    evidence_kind=EvidenceKind.HTTP_RESPONSE,
                    capture_source=EvidenceCaptureSource.PLUGIN_RUNTIME,
                    object_digest=ref.digest,
                    object_size=ref.size,
                    code_identity=identity,
                    integrity=EvidenceIntegrity.COMPLETE,
                    redaction_state=(
                        RedactionState.NOT_REQUIRED
                        if stored == content
                        else RedactionState.REDACTED
                    ),
                    evidence_level=EvidenceLevel.MANUAL_ASSOCIATED,
                    media_type="application/json",
                    created_at=datetime.now(UTC),
                )
            )
        return tuple(evidence)

    def _stage_evidence(
        self,
        project_id: str,
        attempt: Attempt,
        evidence: tuple[EvidenceRef, ...],
        capture: HttpWorkflowCapture,
    ) -> ExecutionFacts:
        unit = self.coordinator._uow
        unit.begin("http-workflow-" + uuid4().hex, project_id)
        try:
            current = self.coordinator.read_runtime_revision_facts(
                project_id=project_id, run_id=attempt.run_id
            )
            facts = tuple(_evidence_fact(item, {}) for item in evidence)
            traces = tuple(
                TraceNode(
                    trace_node_id=f"trace:{attempt.attempt_id}:{exchange.request_id}",
                    run_id=attempt.run_id,
                    actual_node_id=exchange.request_id,
                    node_type=TraceNodeType.REQUEST,
                    state=(
                        TraceNodeState.FAILED
                        if exchange.error_class is not None
                        or (exchange.status is not None and exchange.status >= 400)
                        else TraceNodeState.SUCCEEDED
                        if exchange.status is not None and exchange.body_complete
                        else TraceNodeState.UNKNOWN
                    ),
                    attempt_id=attempt.attempt_id,
                    explicit_relation_ref=(
                        f"evidence:{attempt.attempt_id}:http:{exchange.request_id}"
                    ),
                    assertion_refs=tuple(item[0] for item in exchange.assertion_results),
                    truth_class=TruthClass.REAL,
                    navigation_path=exchange.url,
                )
                for exchange in capture.exchanges
            )
            trace_facts = tuple(_trace_node_fact(item) for item in traces)
            updated = current.model_copy(
                update={
                    "evidence_refs": (*current.evidence_refs, *facts),
                    "trace_nodes": (*current.trace_nodes, *trace_facts),
                }
            )
            self.coordinator._stage_snapshot(updated)
            for item in facts:
                unit.stage_record(
                    aggregate_kind="evidence_ref",
                    record_id=item.evidence_id,
                    expected_revision=0,
                    payload={"project_id": project_id, **_evidence_record(item)},
                )
            unit.commit()
            return updated
        except BaseException:
            unit.rollback()
            raise


__all__ = ["HttpWorkflowBlocked", "SavedHttpWorkflowExecution"]
