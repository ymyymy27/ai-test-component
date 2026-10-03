"""Atomic C-package publication through the A workspace unit of work.

The coordinator does not create a second transaction system. It serializes the
checkpoint, evidence references and ExecutionFacts, stages them together in the
caller-owned A UOW, and commits once.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from uuid import uuid4

from pydantic import TypeAdapter

from aitest.application.evidence.publication import (
    EvidencePublicationContext,
    EvidencePublisher,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.evidence.evidence import EvidenceRef
from aitest.domain.execution.runs import RecoveryRecord

_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryRecord)
_EVIDENCE_ADAPTER = TypeAdapter(EvidenceRef)


class StageableWorkspaceUnitOfWork(Protocol):
    def open(self, project_id: str) -> None: ...

    def begin(self, request_id: str, project_id: str) -> object: ...

    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: Mapping[str, object],
    ) -> object: ...

    def commit(self) -> object: ...

    def rollback(self) -> object: ...


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


class ExecutionCommitCoordinator:
    """Stage C facts and A records in the same short transaction."""

    def __init__(
        self,
        unit_of_work: StageableWorkspaceUnitOfWork,
        *,
        checkpoint_store: CheckpointPayloadCodec | None = None,
    ) -> None:
        self._uow = unit_of_work
        self._checkpoint_store = checkpoint_store

    def stage(
        self,
        batch: ExecutionCommitBatch,
    ) -> tuple[object, ...]:
        facts = batch.facts
        if batch.checkpoint.attempt.run_id != facts.run_id:
            raise ValueError("checkpoint and ExecutionFacts must share run_id")
        if batch.checkpoint.attempt.attempt_id == "":
            raise ValueError("checkpoint requires an attempt_id")

        staged: list[object] = []
        staged.append(
            self._uow.stage_record(
                aggregate_kind="execution_checkpoint",
                record_id=batch.checkpoint.attempt.attempt_id,
                expected_revision=batch.expected_revisions.get(
                    f"execution_checkpoint:{batch.checkpoint.attempt.attempt_id}"
                ),
                payload=(
                    self._checkpoint_store.to_payload(batch.checkpoint)
                    if self._checkpoint_store is not None
                    else _json_payload(_CHECKPOINT_ADAPTER, batch.checkpoint)
                ),
            )
        )
        for evidence in batch.evidence_refs:
            if evidence.run_id != facts.run_id:
                raise ValueError("evidence and ExecutionFacts must share run_id")
            staged.append(
                self._uow.stage_record(
                    aggregate_kind="evidence_ref",
                    record_id=evidence.evidence_id,
                    expected_revision=batch.expected_revisions.get(
                        f"evidence_ref:{evidence.evidence_id}"
                    ),
                    payload=_json_payload(_EVIDENCE_ADAPTER, evidence),
                )
            )
        staged.append(
            self._uow.stage_record(
                aggregate_kind="execution_facts",
                record_id=facts.snapshot_commit_id,
                expected_revision=batch.expected_revisions.get(
                    f"execution_facts:{facts.snapshot_commit_id}"
                ),
                payload=facts.model_dump(mode="json"),
            )
        )
        return tuple(staged)

    def stage_and_commit(
        self,
        batch: ExecutionCommitBatch,
    ) -> ExecutionCommitResult:
        staged = self.stage(batch)
        return ExecutionCommitResult(staged=staged, committed=self._uow.commit())

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
            staged = self._uow.stage_record(
                aggregate_kind="execution_checkpoint",
                record_id=checkpoint.attempt.attempt_id,
                expected_revision=expected_revision,
                payload=(
                    self._checkpoint_store.to_payload(checkpoint)
                    if self._checkpoint_store is not None
                    else _json_payload(_CHECKPOINT_ADAPTER, checkpoint)
                ),
            )
            committed = self._uow.commit()
        except BaseException:
            self._uow.rollback()
            raise
        return ExecutionCommitResult(staged=(staged,), committed=committed)

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


__all__ = [
    "ExecutionCommitBatch",
    "ExecutionCommitCoordinator",
    "ExecutionCommitResult",
    "CheckpointPayloadCodec",
    "StageableWorkspaceUnitOfWork",
]
