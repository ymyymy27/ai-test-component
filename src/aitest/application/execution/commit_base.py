"""协调器共享状态与快照暂存原语。"""
# mypy: disable-error-code="misc,attr-defined"


from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from aitest.application.execution.checkpoint_refs import (
    KIND as CHECKPOINT_REFS_KIND,
)
from aitest.application.execution.checkpoint_refs import (
    StagedCheckpoints,
    build_checkpoint_map,
    checkpoint_map_id,
)
from aitest.application.execution.commit_guards import (
    _payload_digest,
    _require_record_envelope,
    _run_pointer_id,
    _validate_current_facts,
    _validate_frozen_run_basis,
    _validate_frozen_step_basis,
    _validate_publication_current,
)
from aitest.application.execution.commit_types import (
    CheckpointPayloadCodec,
)
from aitest.application.ports import (
    BasisConfirmationProof,
    ControlledWriteProof,
    EvidenceObjectStore,
    ExecutionAuthorizationProof,
    ExecutionEvidenceCollector,
    RecordRepository,
)
from aitest.application.ports import StageableWorkspaceUnitOfWork as StageableWorkspaceUnitOfWork
from aitest.contracts.execution_facts import (
    ExecutionFacts,
)
from aitest.domain.evidence.evidence import (
    EvidenceRef,
)
from aitest.domain.execution.runs import (
    AttemptState,
    RecoveryRecord,
    attempt_start_basis,
)

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


if TYPE_CHECKING:
    from aitest.application.execution.commit import ExecutionCommitCoordinator


class CommitCoordinatorBase:
    def __init__(
        self: ExecutionCommitCoordinator,
        unit_of_work: StageableWorkspaceUnitOfWork,
        *,
        checkpoint_store: CheckpointPayloadCodec | None = None,
        records: RecordRepository | None = None,
        approvals: BasisConfirmationProof | None = None,
        controlled_writes: ControlledWriteProof | None = None,
        execution_authorizations: ExecutionAuthorizationProof | None = None,
        serial_execution: bool = False,
        evidence_collector: ExecutionEvidenceCollector | None = None,
        source_objects: EvidenceObjectStore | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._checkpoint_store = checkpoint_store
        self._records = records
        self._approvals, self._controlled_writes = approvals, controlled_writes
        self._execution_authorizations = execution_authorizations
        self._serial_execution = serial_execution
        self._evidence_collector = evidence_collector
        self._source_objects = source_objects

    def _revision(
        self: ExecutionCommitCoordinator, kind: str, record_id: str, expected: int | None = None
    ) -> int | None:
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

    def _read_payload(
        self: ExecutionCommitCoordinator, kind: str, record_id: str
    ) -> Mapping[str, object] | None:
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

    def _stage_checkpoint_payload(
        self: ExecutionCommitCoordinator,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
        checkpoint_refs: dict[str, tuple[object, Mapping[str, object]]],
    ) -> object:
        revision = self._uow.stage_record(
            aggregate_kind=aggregate_kind,
            record_id=record_id,
            expected_revision=expected_revision,
            payload=payload,
        )
        checkpoint_refs[record_id] = (revision, payload)
        return revision

    def _stage_snapshot(
        self: ExecutionCommitCoordinator,
        facts: ExecutionFacts,
        *,
        expected_revisions: Mapping[str, int] | None = None,
        allow_current_change: bool = False,
        checkpoint_refs: StagedCheckpoints | None = None,
    ) -> tuple[tuple[object, ...], ExecutionFacts]:
        _validate_current_facts(facts)
        expected_revisions = expected_revisions or {}
        previous = None
        pointer_id = None
        next_sequence = getattr(self._uow, "next_commit_seq", None)
        if callable(next_sequence):
            # A nonempty snapshot also freezes exact checkpoint warehouse references.
            sequence = int(next_sequence()) + 1 + bool(facts.attempts)
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
            facts,
            previous=previous,
            pointer_id=pointer_id,
            expected_revisions=expected_revisions,
            checkpoint_refs=checkpoint_refs,
        )

    def _stage_snapshot_records(
        self: ExecutionCommitCoordinator,
        facts: ExecutionFacts,
        *,
        previous: ExecutionFacts | None,
        pointer_id: str | None,
        expected_revisions: Mapping[str, int],
        checkpoint_refs: StagedCheckpoints | None = None,
    ) -> tuple[tuple[object, ...], ExecutionFacts]:
        """Shared serialization, called after the specific admission rules pass."""
        staged: list[object] = []
        if pointer_id is not None and facts.attempts:
            staged.append(
                self._uow.stage_record(
                    aggregate_kind=CHECKPOINT_REFS_KIND,
                    record_id=checkpoint_map_id(facts),
                    expected_revision=0,
                    payload=build_checkpoint_map(
                        cast(RecordRepository, self._records or self._uow),
                        facts,
                        previous,
                        checkpoint_refs or {},
                    ),
                )
            )
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
        if (previous_attempt.runtime_source is not None
                and previous_attempt.runtime_source != attempt.runtime_source):
            raise ValueError("checkpoint cannot rewrite an actual process observation")
        if (
            previous_attempt.state is AttemptState.INVALIDATED
            and attempt.state is not AttemptState.INVALIDATED
        ):
            raise ValueError("an outdated attempt cannot become valid without a new attempt")
