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
from aitest.domain.execution.runs import ArtifactRef, OutputBlockRef, RecoveryRecord


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
        blocks = self.publish_blocks(context, manifest.blocks)
        artifacts = self.publish_artifacts(
            context, self._spool_store.read_artifacts(context.attempt_id)
        )
        return (*blocks, *artifacts)

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

    def publish_artifacts(
        self,
        context: EvidencePublicationContext,
        artifacts: Sequence[ArtifactRef],
    ) -> tuple[EvidenceRef, ...]:
        return tuple(self._publish_artifact(context, artifact) for artifact in artifacts)

    def _publish_artifact(
        self,
        context: EvidencePublicationContext,
        artifact: ArtifactRef,
    ) -> EvidenceRef:
        if artifact.attempt_id != context.attempt_id:
            raise ValueError("spool artifact and publication context attempt_id must match")
        content = self._spool_store.read_artifact(artifact)
        if (
            type(content) is not bytes
            or len(content) != artifact.length
            or "sha256:" + hashlib.sha256(content).hexdigest() != artifact.digest
        ):
            raise ValueError("spool publication bytes do not match the frozen artifact")
        stored = self._object_store.publish_bytes(
            context.project_id,
            content,
            media_type=artifact.media_type,
        )
        if (
            type(stored.size) is not int
            or stored.project_id != context.project_id
            or stored.digest != artifact.digest
            or stored.size != artifact.length
            or self._object_store.read_bytes(stored) != content
        ):
            raise ValueError("published artifact does not preserve the verified bytes")
        return self.reference_for_artifact(context, artifact)

    @staticmethod
    def reference_for_artifact(
        context: EvidencePublicationContext,
        artifact: ArtifactRef,
    ) -> EvidenceRef:
        if artifact.attempt_id != context.attempt_id:
            raise ValueError("artifact evidence metadata belongs to another attempt")
        integrity = (
            EvidenceIntegrity.COMPLETE
            if artifact.complete and not artifact.truncated
            else EvidenceIntegrity.MISSING_TAIL
        )
        return EvidenceRef(
            evidence_id=artifact.artifact_id,
            project_id=context.project_id,
            source_instance_id=context.source_instance_id,
            run_id=context.run_id,
            step_id=context.step_id,
            attempt_id=context.attempt_id,
            evidence_kind=EvidenceKind.ARTIFACT,
            capture_source=EvidenceCaptureSource.PLUGIN_RUNTIME,
            object_digest=artifact.digest,
            object_size=artifact.length,
            code_identity=context.code_identity,
            integrity=integrity,
            redaction_state=RedactionState.NOT_REQUIRED,
            projection_state=context.projection_state,
            evidence_level=EvidenceLevel.UNKNOWN,
            gap_ids=artifact.gap_ids,
            media_type=artifact.media_type,
        )

    def _publish_block(
        self,
        context: EvidencePublicationContext,
        block: OutputBlockRef,
    ) -> EvidenceRef:
        if block.attempt_id != context.attempt_id:
            raise ValueError("spool block and publication context attempt_id must match")
        content = self._spool_store.read_block(block)
        if (
            type(content) is not bytes or len(content) != block.length
            or "sha256:" + hashlib.sha256(content).hexdigest() != block.digest
        ):
            raise ValueError("spool publication bytes do not match the frozen output block")
        stored = self._object_store.publish_bytes(
            context.project_id,
            content,
            media_type=context.media_type,
        )
        if (
            type(stored.size) is not int or stored.project_id != context.project_id
            or stored.digest != block.digest
            or stored.size != block.length
            or self._object_store.read_bytes(stored) != content
        ):
            raise ValueError("published evidence does not preserve the verified output bytes")
        return self.reference_for_block(context, block)

    @staticmethod
    def reference_for_block(
        context: EvidencePublicationContext, block: OutputBlockRef,
    ) -> EvidenceRef:
        """Metadata only; callers must independently verify material availability."""
        if block.attempt_id != context.attempt_id:
            raise ValueError("output evidence metadata belongs to another attempt")
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
            object_digest=block.digest,
            object_size=block.length,
            code_identity=context.code_identity,
            integrity=integrity,
            redaction_state=(
                RedactionState.REDACTED
                if block.redaction_summary_id is not None
                else RedactionState.UNKNOWN
            ),
            projection_state=context.projection_state,
            redaction_summary_ref=block.redaction_summary_id,
            evidence_level=EvidenceLevel.UNKNOWN,
            gap_ids=(
                (() if block.complete else ("partial_spool_block",))
                + (
                    () if block.redaction_summary_id is not None
                    else ("redaction_summary_unavailable",)
                )
            ),
            media_type=context.media_type,
        )


__all__ = [
    "EvidencePublicationContext",
    "EvidencePublisher",
    "TransactionalCommitPort",
]
