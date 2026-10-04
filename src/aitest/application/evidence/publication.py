"""Publish verified spool blocks into immutable evidence references."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from aitest.application.ports import EvidenceObjectStore, SpoolStore
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.evidence.evidence import (
    CodeIdentity,
    EvidenceCaptureSource,
    EvidenceIntegrity,
    EvidenceKind,
    EvidenceLevel,
    EvidenceRef,
    ProjectionState,
    RedactionState,
)
from aitest.domain.execution.runs import OutputBlockRef, RecoveryRecord


@dataclass(frozen=True, slots=True)
class EvidencePublicationContext:
    project_id: str
    source_instance_id: str
    run_id: str
    step_id: str
    attempt_id: str
    code_identity: CodeIdentity
    capture_source: EvidenceCaptureSource = EvidenceCaptureSource.PLUGIN_RUNTIME
    projection_state: ProjectionState = ProjectionState.DISPLAYABLE
    media_type: str = "application/octet-stream"


class TransactionalCommitPort(Protocol):
    def publish_and_stage(
        self,
        *,
        checkpoint: RecoveryRecord,
        evidence_publisher: EvidencePublisher,
        evidence_context: EvidencePublicationContext,
        facts: ExecutionFacts,
        expected_revisions: Mapping[str, int] | None = None,
    ) -> object: ...


class EvidencePublisher:
    """Read spool bytes, publish content objects, and create EvidenceRef facts."""

    def __init__(
        self,
        spool_store: SpoolStore,
        object_store: EvidenceObjectStore,
    ) -> None:
        self._spool_store = spool_store
        self._object_store = object_store

    def publish_attempt(
        self,
        context: EvidencePublicationContext,
    ) -> tuple[EvidenceRef, ...]:
        manifest = self._spool_store.read_manifest(context.attempt_id)
        return self.publish_blocks(context, manifest.blocks)

    def publish_attempt_transactional(
        self,
        context: EvidencePublicationContext,
        *,
        coordinator: TransactionalCommitPort,
        checkpoint: RecoveryRecord,
        facts: ExecutionFacts,
        expected_revisions: Mapping[str, int] | None = None,
    ) -> object:
        """Formal path: publish evidence refs and stage them with facts/UOW."""
        return coordinator.publish_and_stage(
            checkpoint=checkpoint,
            evidence_publisher=self,
            evidence_context=context,
            facts=facts,
            expected_revisions=expected_revisions,
        )

    def publish_blocks(
        self,
        context: EvidencePublicationContext,
        blocks: Sequence[OutputBlockRef],
    ) -> tuple[EvidenceRef, ...]:
        return tuple(self._publish_block(context, block) for block in blocks)

    def _publish_block(
        self,
        context: EvidencePublicationContext,
        block: OutputBlockRef,
    ) -> EvidenceRef:
        if block.attempt_id != context.attempt_id:
            raise ValueError("spool block and publication context attempt_id must match")
        content = self._spool_store.read_block(block)
        if (
            len(content) != block.length
            or "sha256:" + hashlib.sha256(content).hexdigest() != block.digest
        ):
            raise ValueError("spool publication bytes do not match the frozen output block")
        stored = self._object_store.publish_bytes(
            context.project_id,
            content,
            media_type=context.media_type,
        )
        if (
            stored.project_id != context.project_id
            or stored.digest != block.digest
            or stored.size != block.length
            or self._object_store.read_bytes(stored) != content
        ):
            raise ValueError("published evidence does not preserve the verified output bytes")
        integrity = EvidenceIntegrity.COMPLETE if block.complete else EvidenceIntegrity.PARTIAL
        return EvidenceRef(
            evidence_id=f"evidence:{context.attempt_id}:{block.stream_name.value}:{block.block_index}",
            project_id=context.project_id,
            source_instance_id=context.source_instance_id,
            run_id=context.run_id,
            step_id=context.step_id,
            attempt_id=context.attempt_id,
            evidence_kind=EvidenceKind.COMMAND_OUTPUT,
            capture_source=context.capture_source,
            object_digest=stored.digest,
            object_size=stored.size,
            code_identity=context.code_identity,
            integrity=integrity,
            redaction_state=(
                RedactionState.REDACTED
                if block.redaction_summary_id is not None
                else RedactionState.NOT_REQUIRED
            ),
            projection_state=context.projection_state,
            redaction_summary_ref=block.redaction_summary_id,
            evidence_level=EvidenceLevel.UNKNOWN,
            gap_ids=() if block.complete else ("partial_spool_block",),
            media_type=context.media_type,
        )


__all__ = [
    "EvidencePublicationContext",
    "EvidencePublisher",
    "TransactionalCommitPort",
]
