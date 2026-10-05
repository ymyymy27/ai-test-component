"""Atomic C-package publication through the A workspace unit of work.

The coordinator does not create a second transaction system. It serializes the
checkpoint, evidence references and ExecutionFacts, stages them together in the
caller-owned A UOW, and commits once.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import TypeAdapter

from aitest.application.evidence.publication import (
    EvidencePublicationContext,
    EvidencePublisher,
)
from aitest.application.execution.current import project_current_update
from aitest.application.execution.facts import (
    ExecutionFactsAssembler,
    ExecutionFactsAssembly,
    project_attempt_fact,
)
from aitest.application.ports import RecordRepository
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import AttemptFact, ExecutionFacts
from aitest.domain.evidence.evidence import EvidenceRef
from aitest.domain.execution.dependencies import AttemptInvalidation, invalidate_downstream_attempts
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
    AuthorizationRef,
    PlanRevisionRef,
    RecoveryRecord,
    Run,
    RunControlState,
    Step,
    StepState,
    attempt_start_basis,
    authorization_action_basis,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


class CheckpointPayloadCodec(Protocol):
    def to_payload(self, record: RecoveryRecord) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class ExecutionCommitBatch:
    checkpoint: RecoveryRecord
    evidence_refs: tuple[EvidenceRef, ...]
    facts: ExecutionFacts
    expected_revisions: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionCommitResult:
    staged: tuple[object, ...]
    committed: object
    facts: ExecutionFacts | None = None


class ExecutionCommitCoordinator:
    """Stage C facts and A records in the same short transaction."""

    def __init__(
        self,
        unit_of_work: StageableWorkspaceUnitOfWork,
        *,
        checkpoint_store: CheckpointPayloadCodec | None = None,
        records: RecordRepository | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._checkpoint_store = checkpoint_store
        self._records = records

    def _revision(self, kind: str, record_id: str, expected: int | None = None) -> int | None:
        reader = self._records or self._uow
        current_revision = getattr(reader, "current_revision", None)
        if not callable(current_revision):
            return expected
        current = int(current_revision(aggregate_kind=kind, record_id=record_id))
        if expected is not None and expected != current:
            raise ValueError("revision conflict")
        return current

    def recall_initial_run(
        self, *, run_id: str, project_id: str, fingerprint: str
    ) -> ExecutionFacts | None:
        intent = self._read_payload("execution_intent", "run-registration:" + run_id)
        if intent is None:
            return None
        if (
            intent.get("schema_version") != "aitest.run-registration-intent/1.0"
            or intent.get("project_id") != project_id
            or intent.get("run_id") != run_id
            or intent.get("fingerprint") != fingerprint
            or intent.get("snapshot_revision") != 1
            or not isinstance(intent.get("snapshot_commit_id"), str)
        ):
            raise ValueError("run registration intent conflicts with saved preparation")
        read = getattr(self._records or self._uow, "read", None)
        if not callable(read):
            raise ValueError("original run registration snapshot cannot be read")
        saved = read(
            aggregate_kind="execution_facts", record_id=intent["snapshot_commit_id"], revision=1
        )
        payload = getattr(saved, "payload", None)
        if not isinstance(payload, Mapping) or _payload_digest(payload) != intent.get(
            "snapshot_digest"
        ):
            raise ValueError("original run registration snapshot digest cannot be verified")
        facts = ExecutionFacts.model_validate(payload)
        if (facts.project_id, facts.run_id) != (project_id, run_id):
            raise ValueError("original run registration belongs to another project/run")
        _validate_current_facts(facts)
        return facts

    def stage_initial_run(
        self,
        *,
        run: Run,
        steps: tuple[Step, ...],
        facts: ExecutionFacts,
        fingerprint: str,
        prepared_run_id: str,
        intent_id: str,
    ) -> ExecutionFacts:
        """Stage the initial run in an already open common UOW; fabricate no attempt."""
        projected = ExecutionFactsAssembler().assemble(
            ExecutionFactsAssembly(
                facts_id=facts.facts_id,
                snapshot_commit_id=facts.snapshot_commit_id,
                snapshot_cursor=facts.snapshot_cursor,
                snapshot_revision=facts.snapshot_revision,
                committed_at=facts.committed_at,
                run=run,
                steps=steps,
                attempts=(),
            )
        )
        if (
            run.control_state is not RunControlState.NOT_STARTED
            or run.revision != 1
            or run.started_at is not None
            or run.ended_at is not None
            or projected.run != facts.run
            or projected.steps != facts.steps
            or projected.current_attempt_by_step != facts.current_attempt_by_step
            or set(facts.coverage.mandatory_case_ids) != run.required_scope
            or set(facts.coverage.selected_case_ids) != run.selected_scope
            or facts.coverage.executed_attempt_ids
            or facts.run.control_state.value != "not_started"
            or (facts.project_id, facts.run_id) != (run.project_id, run.run_id)
            or facts.attempts
            or facts.verifications
            or facts.evidence_refs
            or facts.source_check_results
            or facts.source_verifications
            or facts.run.evidence_level is not None
            or facts.run.source_binding_digest is not None
            or any(
                s.state is not StepState.PENDING or s.current_attempt_id is not None for s in steps
            )
            or self.read_current_facts(project_id=run.project_id, run_id=run.run_id) is not None
        ):
            raise ValueError("initial run must contain only unexecuted frozen steps")
        if self._revision("run", run.run_id) or any(
            self._revision("step", s.step_id) for s in steps
        ):
            raise ValueError("run/step identity already exists without this registration intent")
        self._uow.stage_record(
            aggregate_kind="run",
            record_id=run.run_id,
            expected_revision=0,
            payload={"project_id": run.project_id, **_json_payload(TypeAdapter(Run), run)},
        )
        for step in steps:
            self._uow.stage_record(
                aggregate_kind="step",
                record_id=step.step_id,
                expected_revision=0,
                payload={"project_id": run.project_id, **_json_payload(TypeAdapter(Step), step)},
            )
        # Intent, pointer, and snapshot are the last three records of this batch.
        sequence = int(self._uow.next_commit_seq()) + 2
        frozen = facts.model_copy(
            update={
                "snapshot_commit_id": f"commit-{sequence}",
                "snapshot_cursor": sequence,
                "snapshot_revision": 1,
            }
        )
        self._uow.stage_record(
            aggregate_kind="execution_intent",
            record_id="run-registration:" + run.run_id,
            expected_revision=0,
            payload={
                "schema_version": "aitest.run-registration-intent/1.0",
                "project_id": run.project_id,
                "run_id": run.run_id,
                "intent_id": intent_id,
                "prepared_run_id": prepared_run_id,
                "fingerprint": fingerprint,
                "snapshot_commit_id": frozen.snapshot_commit_id,
                "snapshot_revision": 1,
                "snapshot_digest": _payload_digest(frozen.model_dump(mode="json")),
            },
        )
        _staged, published = self._stage_snapshot(frozen)
        if published != frozen:
            raise ValueError("initial run snapshot does not match its reserved commit boundary")
        return published

    def _read_payload(self, kind: str, record_id: str) -> Mapping[str, object] | None:
        current = self._revision(kind, record_id)
        if not current:
            return None
        read = getattr(self._records or self._uow, "read", None)
        if not callable(read):
            raise RuntimeError("committed execution records cannot be read")
        record = read(aggregate_kind=kind, record_id=record_id, revision=current)
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping):
            raise ValueError("committed execution payload is invalid")
        return payload

    def _validate_checkpoint_update(self, project_id: str, checkpoint: RecoveryRecord) -> None:
        attempt = checkpoint.attempt
        cursor = checkpoint.checkpoint
        if (
            checkpoint.project_id not in (None, project_id)
            or (cursor.run_id, cursor.step_id, cursor.attempt_id)
            != (attempt.run_id, attempt.step_id, attempt.attempt_id)
            or (
                cursor.resolved_input_digest
                and cursor.resolved_input_digest != attempt.resolved_input_digest
            )
        ):
            raise ValueError("checkpoint does not match its execution basis")
        previous = self._read_payload("execution_checkpoint", attempt.attempt_id)
        if previous is None:
            return
        if previous.get("project_id") != project_id:
            raise ValueError("checkpoint project cannot be verified")
        previous_attempt = _CHECKPOINT_ADAPTER.validate_python(previous).attempt
        if attempt_start_basis(previous_attempt) != attempt_start_basis(attempt):
            raise ValueError("checkpoint cannot rewrite frozen execution basis")
        if (
            previous_attempt.state is AttemptState.INVALIDATED
            and attempt.state is not AttemptState.INVALIDATED
        ):
            raise ValueError("an outdated attempt cannot become valid without a new attempt")

    def read_current_facts(self, *, project_id: str, run_id: str) -> ExecutionFacts | None:
        """Read exactly the saved publication reference; never infer current from history."""
        pointer = self._read_payload("execution_facts_current", _run_pointer_id(project_id, run_id))
        if pointer is None:
            return None
        if (
            pointer.get("schema_version") != "aitest.execution-facts-reference/1.0"
            or pointer.get("project_id") != project_id
            or pointer.get("run_id") != run_id
            or pointer.get("snapshot_revision") != 1
        ):
            raise ValueError("current execution snapshot reference cannot be verified")
        snapshot_id = pointer.get("snapshot_commit_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("current execution snapshot reference lacks an identity")
        read = getattr(self._records or self._uow, "read", None)
        if not callable(read):
            raise RuntimeError("current execution snapshot cannot be read")
        record = read(aggregate_kind="execution_facts", record_id=snapshot_id, revision=1)
        payload = getattr(record, "payload", None)
        if not isinstance(payload, Mapping) or _payload_digest(payload) != pointer.get("digest"):
            raise ValueError("current execution snapshot digest cannot be verified")
        facts = ExecutionFacts.model_validate(payload)
        if (facts.project_id, facts.run_id, facts.snapshot_commit_id, facts.snapshot_revision) != (
            project_id,
            run_id,
            snapshot_id,
            1,
        ):
            raise ValueError("current execution snapshot identity cannot be verified")
        _validate_current_facts(facts)
        return facts

    def read_checkpoint(self, *, project_id: str, attempt_id: str) -> RecoveryRecord:
        """Recovery sidecars are projections; load the saved authority before using them."""
        payload = self._read_payload("execution_checkpoint", attempt_id)
        if payload is None or payload.get("project_id") != project_id:
            raise ValueError("authoritative recovery checkpoint is unavailable or foreign")
        record = _CHECKPOINT_ADAPTER.validate_python(payload)
        if record.attempt.attempt_id != attempt_id:
            raise ValueError("authoritative recovery checkpoint identity cannot be verified")
        self._validate_checkpoint_update(project_id, record)
        return record

    def read_runtime_revision_facts(self, *, project_id: str, run_id: str) -> ExecutionFacts:
        """Read authority for an assessment; the returned snapshot is not a write grant."""
        facts = self.read_current_facts(project_id=project_id, run_id=run_id)
        if facts is None:
            raise ValueError("runtime revision requires a registered current run")
        frozen_plan = PlanRevisionRef(**facts.plan_revision.model_dump())
        for fact in facts.attempts:
            if fact.is_current:
                saved = self.read_checkpoint(project_id=project_id, attempt_id=fact.attempt_id)
                if saved.attempt.expected_plan_revision_ref != frozen_plan:
                    raise ValueError("current checkpoint does not use the exact frozen run plan")
                _validate_attempt_projection(saved.attempt, fact)
        if self.read_current_facts(project_id=project_id, run_id=run_id) != facts:
            raise ValueError("current execution snapshot changed during runtime assessment")
        return facts

    def find_start(self, *, project_id: str, intent_id: str, fingerprint: str) -> Attempt | None:
        payload = self._read_payload("execution_intent", _intent_record_id(project_id, intent_id))
        if payload is None:
            payload = self._read_payload("execution_intent", f"{project_id}:{intent_id}")
        if payload is None:
            return None
        if payload.get("project_id") != project_id or payload.get("fingerprint") != fingerprint:
            raise ValueError("execution intent conflicts with different input")
        attempt_id = payload.get("attempt_id")
        if not isinstance(attempt_id, str):
            raise ValueError("execution intent lacks an attempt identity")
        checkpoint = self._read_payload("execution_checkpoint", attempt_id)
        if checkpoint is None:
            raise ValueError("execution intent checkpoint is unavailable")
        # 检查点编解码器和领域记录共享相同 JSON 字段合同。
        if checkpoint.get("project_id") != project_id:
            raise ValueError("execution checkpoint project cannot be verified")
        attempt = _CHECKPOINT_ADAPTER.validate_python(checkpoint).attempt
        if attempt.intent_id != intent_id or attempt.intent_digest != fingerprint:
            raise ValueError("execution checkpoint intent basis cannot be verified")
        authorization = attempt.authorization_ref
        if authorization is None:
            raise ValueError("execution checkpoint authorization cannot be verified")
        claim = self._read_payload(
            "execution_authorization", _authorization_record_id(authorization.authorization_id)
        )
        if claim is None or any(
            claim.get(name) != expected
            for name, expected in (
                ("project_id", project_id),
                ("run_id", attempt.run_id),
                ("step_id", attempt.step_id),
                ("attempt_id", attempt.attempt_id),
                ("intent_id", intent_id),
                ("fingerprint", fingerprint),
                ("authorization_id", authorization.authorization_id),
            )
        ):
            raise ValueError("saved authorization consumption cannot be verified")
        if claim.get("schema_version") != "aitest.execution-authorization-claim/1.0":
            raise ValueError("saved authorization claim schema cannot be verified")
        saved_authorization = TypeAdapter(AuthorizationRef).validate_python(
            claim.get("authorization_ref")
        )
        if authorization_action_basis(saved_authorization) != authorization_action_basis(
            authorization
        ):
            raise ValueError("saved authorization basis cannot be verified")
        return attempt

    def claim_start(
        self,
        *,
        project_id: str,
        intent_id: str,
        fingerprint: str,
        checkpoint: RecoveryRecord,
    ) -> Attempt | None:
        """在同一短事务中认领意图和检查点；外部启动只允许新认领者执行。"""
        begin = getattr(self._uow, "begin", None)
        if callable(begin):
            begin(f"execution-{uuid4().hex}", project_id)
        else:
            self._uow.open(project_id)
        try:
            previous = self.find_start(
                project_id=project_id,
                intent_id=intent_id,
                fingerprint=fingerprint,
            )
            if previous is not None:
                self._uow.rollback()
                return previous
            if self._revision("execution_checkpoint", checkpoint.attempt.attempt_id):
                raise ValueError(
                    "attempt already exists without this verified intent; inspect it first"
                )
            current, invalidations = self._prepare_current_start(project_id, checkpoint.attempt)
            attempt = checkpoint.attempt
            if (
                checkpoint.project_id not in (None, project_id)
                or checkpoint.checkpoint.run_id != attempt.run_id
                or checkpoint.checkpoint.step_id != attempt.step_id
                or checkpoint.checkpoint.attempt_id != attempt.attempt_id
                or attempt.intent_id != intent_id
                or attempt.intent_digest != fingerprint
                or attempt.state is not AttemptState.INTENT_RECORDED
                or attempt.execution_handle_ref is not None
                or checkpoint.checkpoint.resolved_input_digest != attempt.resolved_input_digest
                or checkpoint.checkpoint.side_effect_class != attempt.side_effect_class
            ):
                raise ValueError("start checkpoint does not match the claimed execution basis")
            authorization = attempt.authorization_ref
            if authorization is None or (
                authorization.step_revision_ref != attempt.step_revision_ref
                or authorization.intent_id != intent_id
                or authorization.step_id != attempt.step_id
                or authorization.resolved_input_digest != attempt.resolved_input_digest
                or authorization.consumed_by_attempt_id not in (None, attempt.attempt_id)
                or authorization.plan_revision_ref != attempt.expected_plan_revision_ref
            ):
                raise ValueError("start authorization step revision cannot be verified")
            claim_id = _authorization_record_id(authorization.authorization_id)
            if self._revision("execution_authorization", claim_id):
                raise ValueError("authorization is already consumed; inspect the original attempt")
            self._uow.stage_record(
                aggregate_kind="execution_authorization",
                record_id=claim_id,
                expected_revision=self._revision("execution_authorization", claim_id),
                payload={
                    "schema_version": "aitest.execution-authorization-claim/1.0",
                    "project_id": project_id,
                    "run_id": attempt.run_id,
                    "step_id": attempt.step_id,
                    "attempt_id": attempt.attempt_id,
                    "intent_id": intent_id,
                    "fingerprint": fingerprint,
                    "authorization_id": authorization.authorization_id,
                    "authorization_ref": _json_payload(
                        TypeAdapter(type(authorization)), authorization
                    ),
                },
            )
            if callable(getattr(self._records or self._uow, "current_revision", None)):
                self._uow.stage_record(
                    aggregate_kind="execution_intent",
                    record_id=_intent_record_id(project_id, intent_id),
                    expected_revision=0,
                    payload={
                        "project_id": project_id,
                        "intent_id": intent_id,
                        "fingerprint": fingerprint,
                        "attempt_id": checkpoint.attempt.attempt_id,
                    },
                )
            self._uow.stage_record(
                aggregate_kind="execution_checkpoint",
                record_id=checkpoint.attempt.attempt_id,
                expected_revision=self._revision(
                    "execution_checkpoint", checkpoint.attempt.attempt_id
                ),
                payload={
                    **_json_payload(_CHECKPOINT_ADAPTER, checkpoint),
                    "project_id": project_id,
                },
            )
            for invalidation in invalidations:
                previous_record = self._read_payload(
                    "execution_checkpoint", invalidation.attempt.attempt_id
                )
                if previous_record is None:
                    raise ValueError("dependency checkpoint is unavailable")
                old = _CHECKPOINT_ADAPTER.validate_python(previous_record)
                updated = RecoveryRecord(
                    checkpoint=old.checkpoint,
                    attempt=invalidation.attempt,
                    project_id=project_id,
                )
                self._uow.stage_record(
                    aggregate_kind="execution_checkpoint",
                    record_id=invalidation.attempt.attempt_id,
                    expected_revision=self._revision(
                        "execution_checkpoint", invalidation.attempt.attempt_id
                    ),
                    payload=_json_payload(_CHECKPOINT_ADAPTER, updated),
                )
            if current is not None:
                self._stage_snapshot(
                    project_current_update(
                        current,
                        checkpoint.attempt,
                        invalidations=invalidations,
                        committed_at=datetime.now(UTC),
                    ),
                    allow_current_change=True,
                )
            self._uow.commit()
            return None
        except BaseException:
            self._uow.rollback()
            raise

    def _prepare_current_start(
        self,
        project_id: str,
        attempt: Attempt,
    ) -> tuple[ExecutionFacts | None, tuple[AttemptInvalidation, ...]]:
        current = self.read_current_facts(project_id=project_id, run_id=attempt.run_id)
        if current is None:
            if attempt.consumed_outputs or attempt.consumed_conditions:
                raise ValueError("actual dependency basis needs a registered current run")
            return None, ()
        step = next((item for item in current.steps if item.step_id == attempt.step_id), None)
        projected = project_attempt_fact(attempt, is_current=True)
        if step is None or step.step_revision_ref != projected.step_revision_ref:
            raise ValueError("new attempt does not use the registered current step revision")
        plan = PlanRevisionRef(**current.plan_revision.model_dump())
        if attempt.expected_plan_revision_ref != plan or (
            current.run.source_binding_digest is not None
            and attempt.source_binding_digest != current.run.source_binding_digest
        ):
            raise ValueError("new attempt does not use the registered frozen run basis")
        domain: list[Attempt] = []
        for fact in current.attempts:
            if not fact.is_current:
                continue
            payload = self._read_payload("execution_checkpoint", fact.attempt_id)
            if payload is None or payload.get("project_id") != project_id:
                raise ValueError("current dependency checkpoint is unavailable or foreign")
            saved = _CHECKPOINT_ADAPTER.validate_python(payload).attempt
            _validate_attempt_projection(saved, fact)
            domain.append(saved)
        previous_id = current.current_attempt_by_step[attempt.step_id]
        old = next((item for item in domain if item.attempt_id == previous_id), None)
        expected_index = old.attempt_index + 1 if old is not None else 1
        if attempt.attempt_index != expected_index:
            raise ValueError("new attempt index must follow the current attempt")
        current_ids = {item.attempt_id: item for item in domain}
        consumed_ids = {item.upstream_attempt_id for item in attempt.consumed_outputs} | {
            item.upstream_attempt_id for item in attempt.consumed_conditions
        }
        for upstream_id in consumed_ids:
            upstream = current_ids.get(upstream_id)
            if (
                upstream is None
                or upstream.step_id == attempt.step_id
                or upstream.state is not AttemptState.COMPLETED
            ):
                raise ValueError("actual dependency does not use a valid current upstream attempt")
        invalidations = invalidate_downstream_attempts(
            domain,
            previous_plan_revision=plan,
            current_plan_revision=plan,
            affected_upstream_attempt_ids=(previous_id,) if previous_id else (),
        )
        affected_ids = {item.attempt.attempt_id for item in invalidations}
        if consumed_ids & affected_ids:
            raise ValueError(
                "new attempt consumes a downstream basis invalidated by its replacement"
            )
        affected = [item for item in domain if item.attempt_id in affected_ids]
        if old is not None:
            affected.append(old)
        if any(
            item.state
            not in {
                AttemptState.COMPLETED,
                AttemptState.CANCELLED,
                AttemptState.EXECUTION_ERROR,
                AttemptState.INVALIDATED,
            }
            for item in affected
        ):
            raise ValueError("affected execution may be active; stop and verify before replacement")
        if any(
            item.execution_handle_ref is not None
            and (
                item.exit_fact_ref is None
                or item.exit_fact_ref.attempt_id != item.attempt_id
                or item.exit_fact_ref.process_start_identity
                != item.execution_handle_ref.process_start_identity
            )
            for item in affected
        ):
            raise ValueError(
                "affected handle termination is uncertain; stop and verify before replacement"
            )
        return current, invalidations

    def stage(
        self,
        batch: ExecutionCommitBatch,
    ) -> tuple[object, ...]:
        staged, _facts = self._stage_with_facts(batch)
        return staged

    def _stage_with_facts(
        self,
        batch: ExecutionCommitBatch,
    ) -> tuple[tuple[object, ...], ExecutionFacts]:
        facts = batch.facts
        _validate_current_facts(facts)
        if batch.checkpoint.attempt.run_id != facts.run_id:
            raise ValueError("checkpoint and ExecutionFacts must share run_id")
        if batch.checkpoint.attempt.attempt_id == "":
            raise ValueError("checkpoint requires an attempt_id")
        previous = self.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
        if previous is not None:
            _validate_frozen_run_basis(previous, facts)
            _validate_frozen_step_basis(previous, facts)
            _validate_publication_current(previous, facts)

        attempt = batch.checkpoint.attempt
        self._validate_checkpoint_update(facts.project_id, batch.checkpoint)
        if callable(getattr(self._records or self._uow, "current_revision", None)):
            current = [item for item in facts.attempts if item.attempt_id == attempt.attempt_id]
            if len(current) != 1:
                raise ValueError("ExecutionFacts do not match the authoritative checkpoint")
            _validate_attempt_projection(attempt, current[0])
            # Other current steps must be backed by their saved checkpoints,
            # rather than allowing the publisher to declare arbitrary completion.
            for item in facts.attempts:
                if not item.is_current or item.attempt_id == attempt.attempt_id:
                    continue
                payload = self._read_payload("execution_checkpoint", item.attempt_id)
                if payload is None or payload.get("project_id") != facts.project_id:
                    raise ValueError("current attempt checkpoint is unavailable or foreign")
                _validate_attempt_projection(
                    _CHECKPOINT_ADAPTER.validate_python(payload).attempt, item
                )
        for evidence in batch.evidence_refs:
            if (
                evidence.project_id != facts.project_id
                or evidence.run_id != facts.run_id
                or evidence.attempt_id != attempt.attempt_id
                or evidence.step_id != attempt.step_id
            ):
                raise ValueError("evidence and checkpoint must share project/run/step/attempt")

        staged: list[object] = []
        staged.append(
            self._uow.stage_record(
                aggregate_kind="execution_checkpoint",
                record_id=batch.checkpoint.attempt.attempt_id,
                expected_revision=self._revision(
                    "execution_checkpoint",
                    attempt.attempt_id,
                    batch.expected_revisions.get(f"execution_checkpoint:{attempt.attempt_id}"),
                ),
                payload={
                    **(
                        self._checkpoint_store.to_payload(batch.checkpoint)
                        if self._checkpoint_store is not None
                        else _json_payload(_CHECKPOINT_ADAPTER, batch.checkpoint)
                    ),
                    "project_id": facts.project_id,
                },
            )
        )
        for evidence in batch.evidence_refs:
            if evidence.run_id != facts.run_id:
                raise ValueError("evidence and ExecutionFacts must share run_id")
            staged.append(
                self._uow.stage_record(
                    aggregate_kind="evidence_ref",
                    record_id=evidence.evidence_id,
                    expected_revision=self._revision(
                        "evidence_ref",
                        evidence.evidence_id,
                        batch.expected_revisions.get(f"evidence_ref:{evidence.evidence_id}"),
                    ),
                    payload=_json_payload(_EVIDENCE_ADAPTER, evidence),
                )
            )
        snapshot_records, facts = self._stage_snapshot(
            facts, expected_revisions=batch.expected_revisions
        )
        staged.extend(snapshot_records)
        return tuple(staged), facts

    def _stage_snapshot(
        self,
        facts: ExecutionFacts,
        *,
        expected_revisions: Mapping[str, int] | None = None,
        allow_current_change: bool = False,
    ) -> tuple[tuple[object, ...], ExecutionFacts]:
        _validate_current_facts(facts)
        expected_revisions = expected_revisions or {}
        staged: list[object] = []
        next_sequence = getattr(self._uow, "next_commit_seq", None)
        if callable(next_sequence):
            # The pointer is staged first; the snapshot remains the final publication record.
            sequence = int(next_sequence()) + 1
            facts = facts.model_copy(
                update={
                    "snapshot_commit_id": f"commit-{sequence}",
                    "snapshot_cursor": sequence,
                    "snapshot_revision": 1,
                }
            )
            pointer_id = _run_pointer_id(facts.project_id, facts.run_id)
            previous = self.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
            if previous is not None:
                _validate_frozen_run_basis(previous, facts)
                _validate_frozen_step_basis(previous, facts)
            if previous is not None and not allow_current_change:
                _validate_publication_current(previous, facts)
            staged.append(
                self._uow.stage_record(
                    aggregate_kind="execution_facts_current",
                    record_id=pointer_id,
                    expected_revision=self._revision(
                        "execution_facts_current",
                        pointer_id,
                        expected_revisions.get(f"execution_facts_current:{pointer_id}"),
                    ),
                    payload={
                        "schema_version": "aitest.execution-facts-reference/1.0",
                        "project_id": facts.project_id,
                        "run_id": facts.run_id,
                        "snapshot_commit_id": facts.snapshot_commit_id,
                        "snapshot_revision": facts.snapshot_revision,
                        "digest": _payload_digest(facts.model_dump(mode="json")),
                        "previous_snapshot_commit_id": (
                            previous.snapshot_commit_id if previous is not None else None
                        ),
                    },
                )
            )
        staged.append(
            self._uow.stage_record(
                aggregate_kind="execution_facts",
                record_id=facts.snapshot_commit_id,
                expected_revision=self._revision(
                    "execution_facts",
                    facts.snapshot_commit_id,
                    expected_revisions.get(f"execution_facts:{facts.snapshot_commit_id}"),
                ),
                payload=facts.model_dump(mode="json"),
            )
        )
        return tuple(staged), facts

    def stage_and_commit(
        self,
        batch: ExecutionCommitBatch,
    ) -> ExecutionCommitResult:
        try:
            staged, facts = self._stage_with_facts(batch)
            return ExecutionCommitResult(staged=staged, committed=self._uow.commit(), facts=facts)
        except BaseException:
            self._uow.rollback()
            raise

    def commit_checkpoint(
        self,
        *,
        project_id: str,
        checkpoint: RecoveryRecord,
        expected_revision: int | None = None,
    ) -> ExecutionCommitResult:
        """Commit one start/control intent before any external side effect."""
        begin = getattr(self._uow, "begin", None)
        if callable(begin):
            begin(f"execution-{uuid4().hex}", project_id)
        else:
            self._uow.open(project_id)
        try:
            self._validate_checkpoint_update(project_id, checkpoint)
            current = self.read_current_facts(
                project_id=project_id, run_id=checkpoint.attempt.run_id
            )
            staged = self._uow.stage_record(
                aggregate_kind="execution_checkpoint",
                record_id=checkpoint.attempt.attempt_id,
                expected_revision=self._revision(
                    "execution_checkpoint",
                    checkpoint.attempt.attempt_id,
                    expected_revision,
                ),
                payload={
                    **(
                        self._checkpoint_store.to_payload(checkpoint)
                        if self._checkpoint_store is not None
                        else _json_payload(_CHECKPOINT_ADAPTER, checkpoint)
                    ),
                    "project_id": project_id,
                },
            )
            records = [staged]
            facts = None
            if (
                current is not None
                and current.current_attempt_by_step.get(checkpoint.attempt.step_id)
                == checkpoint.attempt.attempt_id
            ):
                snapshot_records, facts = self._stage_snapshot(
                    project_current_update(
                        current,
                        checkpoint.attempt,
                        committed_at=datetime.now(UTC),
                    )
                )
                records.extend(snapshot_records)
            committed = self._uow.commit()
        except BaseException:
            self._uow.rollback()
            raise
        return ExecutionCommitResult(staged=tuple(records), committed=committed, facts=facts)

    def publish_and_stage(
        self,
        *,
        checkpoint: RecoveryRecord,
        evidence_publisher: EvidencePublisher,
        evidence_context: EvidencePublicationContext,
        facts: ExecutionFacts,
        expected_revisions: Mapping[str, int] | None = None,
    ) -> ExecutionCommitResult:
        evidence_refs = evidence_publisher.publish_attempt(evidence_context)
        begin = getattr(self._uow, "begin", None)
        if callable(begin):
            begin(f"execution-{uuid4().hex}", evidence_context.project_id)
        else:
            self._uow.open(evidence_context.project_id)
        try:
            return self.stage_and_commit(
                ExecutionCommitBatch(
                    checkpoint=checkpoint,
                    evidence_refs=evidence_refs,
                    facts=facts,
                    expected_revisions=expected_revisions or {},
                )
            )
        except BaseException:
            self._uow.rollback()
            raise


def _json_payload(adapter: TypeAdapter[Any], value: Any) -> dict[str, object]:
    payload = adapter.dump_python(value, mode="json")
    if not isinstance(payload, dict):
        raise TypeError("serialized C artifact must be a JSON object")
    return cast(dict[str, object], payload)


def _intent_record_id(project_id: str, intent_id: str) -> str:
    identity = json.dumps([project_id, intent_id], ensure_ascii=False, separators=(",", ":"))
    return "execution-intent:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _authorization_record_id(authorization_id: str) -> str:
    # Authorization identities are workspace-wide; a foreign project cannot rebind them.
    return "execution-authorization:" + hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()


def _run_pointer_id(project_id: str, run_id: str) -> str:
    identity = json.dumps([project_id, run_id], ensure_ascii=False, separators=(",", ":"))
    return "execution-facts-current:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _payload_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_current_facts(facts: ExecutionFacts) -> None:
    if facts.run.run_id != facts.run_id or facts.run.run_revision != facts.run_revision:
        raise ValueError("execution snapshot run identity is inconsistent")
    if facts.plan_revision != facts.run.plan_revision:
        raise ValueError("execution snapshot plan identity is inconsistent")
    refs = facts.runtime_revision_refs
    if (
        refs != facts.run.runtime_revision_refs
        or any(not ref.strip() for ref in refs)
        or len(refs) != len(set(refs))
    ):
        raise ValueError("execution snapshot runtime revision sequence is inconsistent")
    steps = {step.step_id: step for step in facts.steps}
    attempts = {attempt.attempt_id: attempt for attempt in facts.attempts}
    if len(steps) != len(facts.steps) or len(attempts) != len(facts.attempts):
        raise ValueError("execution snapshot identities must be unique")
    if set(facts.current_attempt_by_step) != set(steps):
        raise ValueError("execution snapshot current references must cover exactly its steps")
    for step in facts.steps:
        current_id = facts.current_attempt_by_step[step.step_id]
        if step.run_id != facts.run_id or step.current_attempt_id != current_id:
            raise ValueError("execution snapshot step/current attempt identity is inconsistent")
        if current_id is not None:
            current = attempts.get(current_id)
            if current is None or (current.run_id, current.step_id) != (facts.run_id, step.step_id):
                raise ValueError("execution snapshot current attempt is unavailable or foreign")
            if current.step_revision_ref != step.step_revision_ref:
                raise ValueError("current attempt does not use the exact current step revision")
    for attempt in facts.attempts:
        if (
            attempt.run_id != facts.run_id
            or attempt.step_id not in steps
            or (
                attempt.is_current
                != (facts.current_attempt_by_step[attempt.step_id] == attempt.attempt_id)
            )
        ):
            raise ValueError("execution snapshot attempt/current flag is inconsistent")


def _validate_frozen_step_basis(previous: ExecutionFacts, facts: ExecutionFacts) -> None:
    before = {step.step_id: step for step in previous.steps}
    after = {step.step_id: step for step in facts.steps}
    if set(before) != set(after):
        raise ValueError("publication cannot add or remove frozen steps")
    frozen = (
        "case_id",
        "ordinal",
        "required_for_case",
        "level",
        "dependency_step_ids",
        "registered_entry_ref",
        "assertion_refs",
        "evidence_requirement_ids",
        "step_revision_ref",
    )
    if any(
        getattr(after[identity], field) != getattr(step, field)
        for identity, step in before.items()
        for field in frozen
    ):
        raise ValueError("publication cannot rewrite frozen step execution basis")


def _validate_frozen_run_basis(previous: ExecutionFacts, facts: ExecutionFacts) -> None:
    """Progress and attempt claims cannot substitute for controlled runtime actions."""
    frozen = (
        "origin_workspace_id",
        "intent_id",
        "tier",
        "required_scope",
        "selected_scope",
        "plan_revision",
        "environment_ref",
        "environment_isolation_mode",
        "rules_revision",
        "conclusion_ceiling",
        "driver",
        "source_binding_digest",
        "runtime_revision_refs",
    )
    if facts.run_revision < previous.run_revision or any(
        getattr(facts.run, field) != getattr(previous.run, field) for field in frozen
    ):
        raise ValueError("publication cannot rewrite the frozen run identity or runtime revision")
    for scope_field in ("mandatory_case_ids", "selected_case_ids"):
        before = getattr(previous.coverage, scope_field)
        after = getattr(facts.coverage, scope_field)
        if set(before) != set(after) or len(after) != len(set(after)):
            raise ValueError("publication cannot rewrite the frozen coverage scope")


def _validate_publication_current(previous: ExecutionFacts, facts: ExecutionFacts) -> None:
    if any(
        facts.current_attempt_by_step.get(step_id) != attempt_id
        or step_id not in facts.current_attempt_by_step
        for step_id, attempt_id in previous.current_attempt_by_step.items()
    ):
        raise ValueError(
            "publication cannot replace or remove a current attempt; claim a new attempt"
        )


def _validate_attempt_projection(attempt: Attempt, fact: AttemptFact) -> None:
    actual = project_attempt_fact(attempt, is_current=fact.is_current).model_dump(mode="json")
    published = fact.model_dump(mode="json")
    # Redaction summaries have their own EvidencePublisher provenance. All byte
    # identities, offsets, completion flags and capture facts must still match.
    for payload in (actual, published):
        for block in payload["output_blocks"]:
            block.pop("redaction_summary", None)
    if actual != published:
        raise ValueError("ExecutionFacts do not match the authoritative checkpoint projection")


__all__ = [
    "ExecutionCommitBatch",
    "ExecutionCommitCoordinator",
    "ExecutionCommitResult",
    "CheckpointPayloadCodec",
    "StageableWorkspaceUnitOfWork",
]
