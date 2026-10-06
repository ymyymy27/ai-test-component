"""Atomic C-package publication through the A workspace unit of work.

The coordinator does not create a second transaction system. It serializes the
checkpoint, evidence references and ExecutionFacts, stages them together in the
caller-owned A UOW, and commits once.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import TypeAdapter

from aitest.application.evidence.publication import (
    EvidencePublicationContext,
    EvidencePublisher,
)
from aitest.application.execution.authorization_index import read_authorization_index
from aitest.application.execution.current import project_current_update
from aitest.application.execution.facts import (
    ExecutionFactsAssembler,
    ExecutionFactsAssembly,
    _run_fact,
    _step_fact,
    execution_payload_digest,
    project_attempt_fact,
    project_attempt_update,
    validate_frozen_run_basis,
    validate_frozen_step_basis,
)
from aitest.application.execution.run_record import read_run_record, run_record_payload
from aitest.application.execution.runtime_revision import (
    RunRevisionRecord,
    RuntimePlanningRecordReader,
    SavedRuntimeRevisionReader,
    SnapshotContentRef,
    StepContentChange,
    request_from_payload,
    require_runtime_boundary,
    revision_input_digest,
    revision_record_id,
    revision_request_payload,
)
from aitest.application.execution.step_content import StepContentReader
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.saved_runtime_revision import SavedRuntimeRevisionAssessment
from aitest.application.planning.serialization import case_content_digest, case_to_payload
from aitest.application.ports import (
    BasisConfirmationProof,
    ControlledWriteProof,
    ExecutionAuthorizationProof,
    RecordRepository,
)
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    AttemptFact,
    DependencyInvalidationFact,
    ExecutionFacts,
    FactCompleteness,
    RunControlStateFact,
    StepRevisionRefFact,
    StepStateFact,
)
from aitest.contracts.prepared_run import CaseRevisionRef, FrozenCaseStep, RunDriverFact
from aitest.contracts.prepared_run import PlanRevisionRef as PlanContentRef
from aitest.domain.evidence.evidence import EvidenceRef
from aitest.domain.execution.dependencies import AttemptInvalidation, invalidate_downstream_attempts
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
    AuthorizationRef,
    InputRef,
    PlanRevisionRef,
    RecoveryRecord,
    Run,
    RunControlState,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
    attempt_start_basis,
    authorization_action_basis,
)
from aitest.domain.planning.plans import Plan
from aitest.domain.planning.runtime_revision import RuntimeRevisionRefused, RuntimeRevisionRequest

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


def _validate_original_grant_proof(proof: object, authorization: AuthorizationRef) -> None:
    """Reject unreadable consumer receipts before publishing an external start."""
    if not isinstance(proof, Mapping) or set(proof) != {
        "grant_id",
        "grant_revision",
        "grant_digest",
        "state_revision",
    }:
        raise ValueError("original authorization proof has missing or unknown fields")
    digest = proof["grant_digest"]
    if (
        proof["grant_id"] != authorization.authorization_id
        or type(proof["grant_revision"]) is not int
        or proof["grant_revision"] != 1
        or type(proof["state_revision"]) is not int
        or proof["state_revision"] != 2
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
        or any(char not in "0123456789abcdef" for char in digest[7:])
    ):
        raise ValueError("original authorization proof has invalid identity, revision or digest")


def _require_record_envelope(record: object, kind: str, identity: str, revision: int) -> None:
    if type(getattr(record, "revision", None)) is not int or (
        getattr(record, "aggregate_kind", None),
        getattr(record, "record_id", None),
        getattr(record, "revision", None),
    ) != (kind, identity, revision):
        raise ValueError("saved execution record envelope cannot be verified")


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
        approvals: BasisConfirmationProof | None = None,
        controlled_writes: ControlledWriteProof | None = None,
        execution_authorizations: ExecutionAuthorizationProof | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._checkpoint_store = checkpoint_store
        self._records = records
        self._approvals, self._controlled_writes = approvals, controlled_writes
        self._execution_authorizations = execution_authorizations

    def _revision(self, kind: str, record_id: str, expected: int | None = None) -> int | None:
        reader = self._records or self._uow
        current_revision = getattr(reader, "current_revision", None)
        if not callable(current_revision):
            return expected
        current = current_revision(aggregate_kind=kind, record_id=record_id)
        if (
            type(current) is not int
            or current < 0
            or (expected is not None and (type(expected) is not int or expected < 0))
        ):
            raise ValueError("warehouse revision requires an exact nonnegative integer")
        if expected is not None and expected != current:
            raise ValueError("revision conflict")
        return current

    def recall_initial_run(
        self,
        *,
        run_id: str,
        project_id: str,
        fingerprint: str,
        intent_id: str,
        prepared_run_id: str,
        workspace_id: str,
    ) -> ExecutionFacts | None:
        reader = self._records or self._uow
        current = getattr(reader, "current_revision", None)
        if not callable(current):
            raise ValueError("original registration receipt reader is unavailable")
        identity = "run-registration:" + run_id
        revision = current(aggregate_kind="execution_intent", record_id=identity)
        if type(revision) is not int or revision not in {0, 1}:
            raise ValueError("original registration receipt has an unverified warehouse revision")
        if revision == 0:
            return None
        intent = self._registration_payload("execution_intent", identity, project_id)
        if (
            set(intent)
            != {
                "schema_version",
                "project_id",
                "run_id",
                "intent_id",
                "prepared_run_id",
                "fingerprint",
                "snapshot_commit_id",
                "snapshot_revision",
                "snapshot_digest",
            }
            or intent.get("schema_version") != "aitest.run-registration-intent/1.0"
            or intent.get("project_id") != project_id
            or intent.get("run_id") != run_id
            or intent.get("intent_id") != intent_id
            or intent.get("prepared_run_id") != prepared_run_id
            or intent.get("fingerprint") != fingerprint
            or type(intent.get("snapshot_revision")) is not int
            or intent.get("snapshot_revision") != 1
            or not isinstance(intent.get("snapshot_commit_id"), str)
            or not intent["snapshot_commit_id"]
        ):
            raise ValueError("run registration intent conflicts with saved preparation")
        payload = self._registration_payload(
            "execution_facts", str(intent["snapshot_commit_id"]), project_id
        )
        if _payload_digest(payload) != intent.get("snapshot_digest"):
            raise ValueError("original run registration snapshot digest cannot be verified")
        facts = ExecutionFacts.model_validate_json(json.dumps(dict(payload)), strict=True)
        if (
            (facts.project_id, facts.run_id, facts.run.origin_workspace_id)
            != (project_id, run_id, workspace_id)
            or facts.snapshot_commit_id != intent["snapshot_commit_id"]
            or facts.snapshot_revision != 1
        ):
            raise ValueError("original run registration belongs to another project/run")
        _validate_current_facts(facts)
        raw_run = self._registration_payload("run", run_id, project_id)
        run = read_run_record(raw_run)
        if (
            (run.run_id, run.project_id, run.origin_workspace_id)
            != (run_id, project_id, workspace_id)
            or run.frozen_input_refs
            != (InputRef(prepared_run_id, f"prepared_run:{prepared_run_id}@1", fingerprint, True),)
            or _run_fact(run) != facts.run
        ):
            raise ValueError("original registration run does not prove this frozen preparation")
        if self._records is None:
            raise ValueError("original registration step material reader is unavailable")
        contents = StepContentReader(self._records)
        for fact in facts.steps:
            raw_step = self._registration_payload("step", fact.step_id, project_id)
            step = TypeAdapter(Step).validate_json(
                json.dumps({key: value for key, value in raw_step.items() if key != "project_id"}),
                strict=True,
            )
            if (
                dict(raw_step)
                != {"project_id": project_id, **_json_payload(TypeAdapter(Step), step)}
                or _step_fact(step) != fact
            ):
                raise ValueError(
                    "original registration step projection differs from saved authority"
                )
            try:
                contents.read(run=run, step=step)
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ValueError("original registration step content cannot be verified") from error
        return facts

    def _registration_payload(
        self, kind: str, identity: str, project_id: str
    ) -> Mapping[str, object]:
        read = getattr(self._records or self._uow, "read", None)
        if not callable(read):
            raise ValueError("original registration material reader is unavailable")
        try:
            saved = read(aggregate_kind=kind, record_id=identity, revision=1)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError("original registration material is unavailable") from error
        if (
            getattr(saved, "aggregate_kind", None),
            getattr(saved, "record_id", None),
            getattr(saved, "revision", None),
        ) != (kind, identity, 1) or type(getattr(saved, "revision", None)) is not int:
            raise ValueError("original registration material envelope cannot be verified")
        payload = getattr(saved, "payload", None)
        if not isinstance(payload, Mapping) or payload.get("project_id") != project_id:
            raise ValueError("original registration material has a different project")
        return payload

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
            payload=run_record_payload(run),
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
        _require_record_envelope(record, kind, record_id, current)
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
            set(pointer)
            != {
                "schema_version",
                "project_id",
                "run_id",
                "snapshot_commit_id",
                "snapshot_revision",
                "digest",
                "previous_snapshot_commit_id",
            }
            or pointer.get("schema_version") != "aitest.execution-facts-reference/1.0"
            or pointer.get("project_id") != project_id
            or pointer.get("run_id") != run_id
            or type(pointer.get("snapshot_revision")) is not int
            or pointer.get("snapshot_revision") != 1
        ):
            raise ValueError("current execution snapshot reference cannot be verified")
        snapshot_id = pointer.get("snapshot_commit_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ValueError("current execution snapshot reference lacks an identity")
        previous = pointer.get("previous_snapshot_commit_id")
        if previous is not None and (
            not isinstance(previous, str) or not previous.strip() or previous == snapshot_id
        ):
            raise ValueError("current execution snapshot reference has invalid history identity")
        if self._revision("execution_facts", snapshot_id) != 1:
            raise ValueError("current execution snapshot must remain immutable at revision 1")
        read = getattr(self._records or self._uow, "read", None)
        if not callable(read):
            raise RuntimeError("current execution snapshot cannot be read")
        record = read(aggregate_kind="execution_facts", record_id=snapshot_id, revision=1)
        _require_record_envelope(record, "execution_facts", snapshot_id, 1)
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
            # Historical activity also constrains edits; it requires the same authority.
            saved = self.read_checkpoint(project_id=project_id, attempt_id=fact.attempt_id)
            if saved.attempt.expected_plan_revision_ref != frozen_plan:
                raise ValueError("runtime checkpoint does not use the exact frozen run plan")
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
        intent_key = _intent_record_id(project_id, intent_id)
        if not self._revision("execution_intent", intent_key):
            intent_key = f"{project_id}:{intent_id}"
        if self._revision("execution_intent", intent_key) != 1:
            raise ValueError("saved execution intent must remain immutable at revision 1")
        if set(payload) != {"project_id", "intent_id", "fingerprint", "attempt_id"} or (
            payload.get("intent_id") != intent_id
        ):
            raise ValueError("execution intent has unknown fields or identity")
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
        if claim.get("schema_version") != "aitest.execution-authorization-claim/1.1":
            raise ValueError("legacy authorization claim has no original proof; inspect history")
        if (
            set(claim)
            != {
                "schema_version",
                "project_id",
                "run_id",
                "step_id",
                "attempt_id",
                "intent_id",
                "fingerprint",
                "authorization_id",
                "authorization_ref",
                "original_grant_proof",
            }
            or self._revision(
                "execution_authorization", _authorization_record_id(authorization.authorization_id)
            )
            != 1
        ):
            raise ValueError(
                "saved original authorization claim is not immutable or has extra fields"
            )
        saved_authorization = TypeAdapter(AuthorizationRef).validate_json(
            json.dumps(claim.get("authorization_ref")), strict=True
        )
        if claim["authorization_ref"] != _json_payload(
            TypeAdapter(AuthorizationRef), saved_authorization
        ):
            raise ValueError("saved authorization reference has unknown fields")
        if authorization_action_basis(saved_authorization) != authorization_action_basis(
            authorization
        ):
            raise ValueError("saved authorization basis cannot be verified")
        proof = claim.get("original_grant_proof")
        if self._execution_authorizations is None or not isinstance(proof, Mapping):
            raise ValueError("saved original authorization proof is unavailable")
        _validate_original_grant_proof(proof, authorization)
        self._execution_authorizations.validate_occupation(
            project_id=project_id, attempt=attempt, proof=proof
        )
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
        previous = self.find_start(
            project_id=project_id, intent_id=intent_id, fingerprint=fingerprint
        )
        if previous is not None:
            return previous
        if self._execution_authorizations is None:
            raise ValueError("new execution requires original authorization proof")
        # Source/environment checks are outside the start transaction. A concurrent
        # original publication is recovered, never converted into another execution.
        try:
            self._execution_authorizations.validate_new(
                project_id=project_id, attempt=checkpoint.attempt
            )
        except (OSError, ValueError, KeyError, TypeError):
            previous = self.find_start(
                project_id=project_id, intent_id=intent_id, fingerprint=fingerprint
            )
            if previous is None:
                raise
            return previous
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
            if current is None or current.run.control_state not in {
                RunControlStateFact.NOT_STARTED,
                RunControlStateFact.RUNNING,
            }:
                raise ValueError("new execution requires an admitted current run control state")
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
            superseded = {item.attempt.attempt_id for item in invalidations}
            previous_attempt_id = current.current_attempt_by_step[attempt.step_id]
            if previous_attempt_id is not None:
                superseded.add(previous_attempt_id)
            original_grant_proof = self._execution_authorizations.stage_occupation(
                project_id=project_id,
                attempt=attempt,
                superseded_attempt_ids=tuple(sorted(superseded)),
                changed_step_ids=tuple(
                    sorted(
                        {attempt.step_id}
                        | {
                            item.attempt.step_id
                            for item in invalidations
                            if current.current_attempt_by_step.get(item.attempt.step_id)
                            == item.attempt.attempt_id
                        }
                    )
                ),
            )
            _validate_original_grant_proof(original_grant_proof, authorization)
            self._uow.stage_record(
                aggregate_kind="execution_authorization",
                record_id=claim_id,
                expected_revision=self._revision("execution_authorization", claim_id),
                payload={
                    "schema_version": "aitest.execution-authorization-claim/1.1",
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
                    "original_grant_proof": dict(original_grant_proof),
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

    def _stage_authorization_revocations(
        self,
        *,
        before: ExecutionFacts,
        superseded_attempt_ids: tuple[str, ...],
        changed_step_ids: tuple[str, ...],
    ) -> None:
        if not superseded_attempt_ids and not changed_step_ids:
            return
        if self._execution_authorizations is None:
            _revision, unused = read_authorization_index(
                cast(RecordRepository, self._records or self._uow),
                before.run.origin_workspace_id,
                before.project_id,
                before.run_id,
            )
            if unused:
                raise ValueError("runtime revision needs the unused authorization consumer")
            return
        self._execution_authorizations.stage_revoke_affected(
            project_id=before.project_id,
            run_id=before.run_id,
            superseded_attempt_ids=superseded_attempt_ids,
            changed_step_ids=changed_step_ids,
        )

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
        current_ids = {
            item.attempt_id: item
            for item in domain
            if current.current_attempt_by_step.get(item.step_id) == item.attempt_id
        }
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
            _validate_publication_current(previous, facts)
            _validate_frozen_run_basis(previous, facts)
            _validate_frozen_step_basis(previous, facts)

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

    def stage_runtime_revision(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: Plan,
        request: RuntimeRevisionRequest,
        intent_id: str,
        confirmation_ids: tuple[str, ...] = (),
    ) -> tuple[ExecutionFacts, bool]:
        """Reassess and derive one exact revision in the caller's short transaction.

        This action constructs the permitted projection itself. It accepts no
        replacement facts, revision sequence number or generic admission bypass.
        Proposed Case revisions must already be accurately saved by B.
        """
        if self._records is None or not intent_id.strip():
            raise ValueError("runtime revision requires saved authority and a persistent intent")
        reader = SavedRuntimeRevisionReader(self._records)
        record_id = revision_record_id(project_id, run_id, intent_id)
        request_payload = revision_request_payload(request, project_id)
        request_from_payload(request_payload, project_id)
        fingerprint = revision_input_digest(
            project_id=project_id,
            run_id=run_id,
            request_payload=request_payload,
            confirmation_ids=confirmation_ids,
        )
        if self._revision("run_plan_revision", record_id):
            original = reader.read_record(
                project_id=project_id, reference=f"run_plan_revision:{record_id}@1"
            )
            if original.run_id != run_id or original.input_digest != fingerprint:
                raise ValueError("runtime revision intent conflicts with saved input")
            result = reader.read_snapshot(
                project_id=project_id, run_id=run_id, reference=original.result_snapshot
            )
            if (
                len(result.runtime_revision_refs) != original.revision_no
                or result.runtime_revision_refs[-1] != original.reference
                or result.facts_id != "runtime-revision:" + record_id
            ):
                raise ValueError("runtime revision receipt does not identify its exact result")
            return result, False
        before = self.read_runtime_revision_facts(project_id=project_id, run_id=run_id)
        assessment = SavedRuntimeRevisionAssessment(
            reader=RuntimePlanningRecordReader(reader, project_id),
            execution=self,
            runtime_basis=reader,
            approvals=self._approvals,
            controlled_writes=self._controlled_writes,
        )
        decision = assessment.assess(
            project_id=project_id,
            run_id=run_id,
            plan=plan,
            request=request,
            confirmation_ids=confirmation_ids,
        )
        if not decision.accepted:
            raise RuntimeRevisionRefused(decision)
        require_runtime_boundary(before)
        if self.read_runtime_revision_facts(project_id=project_id, run_id=run_id) != before:
            raise ValueError("runtime snapshot changed before revision staging")
        initial_cases = assessment._cases(project_id, plan)
        cases = {
            case.case_id: case
            for case in reader.read_effective_cases(
                facts=before, plan=plan, initial_cases=initial_cases
            )
        }
        changes = {change.next_case.case_id: change for change in request.case_changes}
        for change in changes.values():
            saved = reader._read(
                "case", change.next_case.case_id, change.next_case.revision, project_id
            )
            if saved != case_to_payload(change.next_case, project_id=project_id):
                raise ValueError("proposed runtime case differs from its saved exact revision")
            if len(change.next_case.steps) != len(cases[change.next_case.case_id].steps):
                raise ValueError(
                    "changed step layout requires explicit registration; mapping unknown"
                )
            cases[change.next_case.case_id] = change.next_case
        original_run = TypeAdapter(Run).validate_python(reader._read("run", run_id, 1, project_id))
        materials = {}
        step_changes = []
        reference = f"run_plan_revision:{record_id}@1"
        next_steps = []
        for fact in before.steps:
            if fact.step_id not in decision.affected_step_ids:
                next_steps.append(fact)
                continue
            step = TypeAdapter(Step).validate_python(
                reader._read("step", fact.step_id, 1, project_id)
            )
            step = replace(
                step,
                step_revision_ref=StepRevisionRef(**fact.step_revision_ref.model_dump()),
                level=StepLevel(fact.level.value),
            )
            body = StepContentReader(self._records).read(run=original_run, step=step)
            case = cases[fact.case_id]
            body = body.model_copy(
                update={
                    "case_revision_ref": CaseRevisionRef(
                        case_id=case.case_id,
                        revision=case.revision,
                        digest=case_content_digest(case, project_id=project_id),
                    ),
                    "case_content": case_to_payload(case, project_id=project_id),
                    "frozen_step": FrozenCaseStep.model_validate(
                        {
                            "step_id": body.frozen_step.step_id,
                            "layer": case.layer.value,
                            "objective": case.steps[body.case_step_index],
                            "expected": case.expected,
                        }
                    ),
                }
            )
            body.checked_case()
            next_ref = StepRevisionRefFact(
                step_revision_id="step-revision-" + payload_digest([record_id, fact.step_id])[7:],
                revision_no=1,
                digest=payload_digest(body.model_dump(mode="json")),
            )
            materials[next_ref.step_revision_id] = body
            step_changes.append(
                StepContentChange(
                    step_id=fact.step_id, previous_ref=fact.step_revision_ref, next_ref=next_ref
                )
            )
            next_steps.append(
                fact.model_copy(
                    update={
                        "step_revision_ref": next_ref,
                        "step_revision": 1,
                        "level": type(fact.level)(case.layer.value),
                    }
                )
            )
        checkpoints = {
            fact.attempt_id: self.read_checkpoint(project_id=project_id, attempt_id=fact.attempt_id)
            for fact in before.attempts
        }
        seeds = {
            attempt.attempt_id
            for attempt in before.attempts
            if attempt.step_id in decision.invalidated_basis_step_ids
        }
        consumers = invalidate_downstream_attempts(
            tuple(checkpoint.attempt for checkpoint in checkpoints.values()),
            previous_plan_revision=original_run.plan_revision_ref,
            current_plan_revision=original_run.plan_revision_ref,
            affected_upstream_attempt_ids=tuple(seeds),
        )
        affected_attempts = seeds | {item.attempt.attempt_id for item in consumers}
        next_attempts = []
        for attempt_fact in before.attempts:
            if attempt_fact.attempt_id not in affected_attempts:
                next_attempts.append(attempt_fact)
                continue
            old = checkpoints[attempt_fact.attempt_id]
            attempt = replace(
                old.attempt,
                state=AttemptState.INVALIDATED,
                unknown_reason_ref="runtime_revision_basis_outdated",
                revision=old.attempt.revision + 1,
            )
            checkpoint = replace(old, attempt=attempt)
            self._validate_checkpoint_update(project_id, checkpoint)
            self._uow.stage_record(
                aggregate_kind="execution_checkpoint",
                record_id=attempt_fact.attempt_id,
                expected_revision=self._revision("execution_checkpoint", attempt_fact.attempt_id),
                payload={
                    **_json_payload(_CHECKPOINT_ADAPTER, checkpoint),
                    "project_id": project_id,
                },
            )
            next_attempts.append(
                project_attempt_update(attempt, attempt_fact, is_current=attempt_fact.is_current)
            )
        affected_steps = set(decision.invalidated_basis_step_ids) | {
            item.attempt.step_id
            for item in consumers
            if before.current_attempt_by_step.get(item.attempt.step_id) == item.attempt.attempt_id
        }
        self._stage_authorization_revocations(
            before=before,
            superseded_attempt_ids=tuple(sorted(affected_attempts)),
            changed_step_ids=tuple(
                sorted(affected_steps | {item.step_id for item in step_changes})
            ),
        )
        next_steps = [
            step.model_copy(
                update={
                    "state": StepStateFact.INVALIDATED,
                    "invalidated": True,
                    "invalidated_by": reference,
                }
            )
            if step.step_id in affected_steps
            else step
            for step in next_steps
        ]
        now = datetime.now(UTC)
        events = tuple(
            DependencyInvalidationFact(
                invalidation_id=f"{record_id}:{item.attempt.attempt_id}:{upstream}",
                run_id=run_id,
                affected_step_id=item.attempt.step_id,
                affected_attempt_id=item.attempt.attempt_id,
                upstream_attempt_id=upstream,
                reason="runtime_revision_basis_outdated",
                source_revision_ref=reference,
                transitive=upstream not in seeds,
                invalidated_at=now,
            )
            for item in consumers
            for upstream in item.upstream_attempt_ids
        )
        refs = (*before.runtime_revision_refs, reference)
        revision = before.run_revision + 1
        facts = before.model_copy(
            update={
                "facts_id": "runtime-revision:" + record_id,
                "committed_at": now,
                "run_revision": revision,
                "runtime_revision_refs": refs,
                "run": before.run.model_copy(
                    update={
                        "run_revision": revision,
                        "runtime_revision_refs": refs,
                        "driver": RunDriverFact(decision.effective_driver.value),
                        "control_state": (
                            RunControlStateFact.PAUSED
                            if decision.pause_required
                            else before.run.control_state
                        ),
                        "result_ref": None,
                        "evidence_level": None,
                        "coverage_summary": None,
                        "ended_at": None,
                    }
                ),
                "steps": tuple(next_steps),
                "attempts": tuple(next_attempts),
                "dependency_invalidations": before.dependency_invalidations + events,
                "coverage": before.coverage.model_copy(
                    update={
                        "executed_attempt_ids": tuple(
                            item
                            for item in before.coverage.executed_attempt_ids
                            if item not in affected_attempts
                        ),
                        "invalidated_step_ids": tuple(
                            step.step_id for step in next_steps if step.invalidated
                        ),
                        "blocked_step_ids": tuple(
                            step.step_id
                            for step in next_steps
                            if step.state is StepStateFact.BLOCKED
                        ),
                        "unknown_step_ids": tuple(
                            step.step_id
                            for step in next_steps
                            if step.state is StepStateFact.PENDING_VERIFICATION
                        ),
                    }
                ),
                "completeness": FactCompleteness.PARTIAL,
            }
        )
        _validate_current_facts(facts)
        _validate_publication_current(before, facts)
        # These are the only basis changes this guarded action derives.
        _validate_frozen_run_basis(
            before,
            facts.model_copy(
                update={
                    "run": facts.run.model_copy(
                        update={
                            "driver": before.run.driver,
                            "runtime_revision_refs": before.run.runtime_revision_refs,
                        }
                    )
                }
            ),
        )
        _validate_frozen_step_basis(
            before,
            facts.model_copy(
                update={
                    "steps": tuple(
                        step.model_copy(
                            update={
                                "step_revision_ref": original.step_revision_ref,
                                "step_revision": original.step_revision,
                                "level": original.level,
                            }
                        )
                        for step, original in zip(facts.steps, before.steps, strict=True)
                    )
                }
            ),
        )
        for content_id, body in materials.items():
            self._uow.stage_record(
                aggregate_kind="step_revision",
                record_id=content_id,
                expected_revision=0,
                payload=body.model_dump(mode="json"),
            )
        sequence = int(self._uow.next_commit_seq()) + 2
        facts = facts.model_copy(
            update={
                "snapshot_commit_id": f"commit-{sequence}",
                "snapshot_cursor": sequence,
                "snapshot_revision": 1,
            }
        )
        record = RunRevisionRecord(
            schema_version="aitest.run-plan-revision/1.0",
            record_id=record_id,
            project_id=project_id,
            origin_workspace_id=before.run.origin_workspace_id,
            run_id=run_id,
            intent_id=intent_id,
            input_digest=fingerprint,
            revision_no=decision.revision_no,
            previous_revision_ref=before.runtime_revision_refs[-1]
            if before.runtime_revision_refs
            else None,
            initial_plan_ref=PlanContentRef(**before.plan_revision.model_dump()),
            base_snapshot=SnapshotContentRef.of(before),
            result_snapshot=SnapshotContentRef.of(facts),
            request_payload=request_payload,
            confirmation_ids=confirmation_ids,
            effective_case_refs=tuple(
                CaseRevisionRef(
                    case_id=case.case_id,
                    revision=case.revision,
                    digest=case_content_digest(case, project_id=project_id),
                )
                for case in sorted(cases.values(), key=lambda case: case.case_id)
            ),
            step_changes=tuple(sorted(step_changes, key=lambda change: change.step_id)),
            invalidated_basis_step_ids=decision.invalidated_basis_step_ids,
            invalidated_consumer_attempt_ids=tuple(
                sorted(item.attempt.attempt_id for item in consumers)
            ),
            pause_required=decision.pause_required,
            effective_driver=RunDriverFact(decision.effective_driver.value),
            created_at=now,
        )
        self._uow.stage_record(
            aggregate_kind="run_plan_revision",
            record_id=record_id,
            expected_revision=0,
            payload=record.model_dump(mode="json"),
        )
        if int(self._uow.next_commit_seq()) + 1 != sequence:
            raise ValueError("runtime revision result does not match its reserved commit boundary")
        return self._stage_snapshot_records(
            facts,
            previous=before,
            pointer_id=_run_pointer_id(project_id, run_id),
            expected_revisions={},
        )[1], True

    def _stage_snapshot(
        self,
        facts: ExecutionFacts,
        *,
        expected_revisions: Mapping[str, int] | None = None,
        allow_current_change: bool = False,
    ) -> tuple[tuple[object, ...], ExecutionFacts]:
        _validate_current_facts(facts)
        expected_revisions = expected_revisions or {}
        previous = None
        pointer_id = None
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
        return self._stage_snapshot_records(
            facts, previous=previous, pointer_id=pointer_id, expected_revisions=expected_revisions
        )

    def _stage_snapshot_records(
        self,
        facts: ExecutionFacts,
        *,
        previous: ExecutionFacts | None,
        pointer_id: str | None,
        expected_revisions: Mapping[str, int],
    ) -> tuple[tuple[object, ...], ExecutionFacts]:
        """Shared serialization, called after the specific admission rules pass."""
        staged: list[object] = []
        if pointer_id is not None:
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
    return execution_payload_digest(payload)


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
    validate_frozen_step_basis(previous, facts)


def _validate_frozen_run_basis(previous: ExecutionFacts, facts: ExecutionFacts) -> None:
    validate_frozen_run_basis(previous, facts)


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
