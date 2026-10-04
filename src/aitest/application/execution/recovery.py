"""Recovery decisions, spool salvage, and dependency invalidation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from aitest.application.ports import SpoolStore
from aitest.domain.execution.dependencies import (
    AttemptInvalidation,
    CaseReuseBasis,
    CaseReuseInvalidation,
    invalidate_downstream_attempts,
    invalidate_reuse_bases,
)
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
    CaptureCompleteness,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    OutputBlockRef,
    OutputCursor,
    RecoveryCheckpoint,
    RecoveryRecord,
    SpoolManifest,
)


class RecoveryAction(StrEnum):
    TERMINAL_PRESERVED = "terminal_preserved"
    REATTACH = "reattach"
    RECOVER_FROM_SPOOL = "recover_from_spool"
    SAFE_RETRY = "safe_retry"
    PENDING_VERIFICATION = "pending_verification"


class CheckpointStore(Protocol):
    def persist(self, record: RecoveryRecord) -> Path: ...

    def scan(self) -> tuple[RecoveryRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    action: RecoveryAction
    attempt: Attempt
    recovered_blocks: tuple[OutputBlockRef, ...]
    recovered_cursors: tuple[OutputCursor, ...]
    capture_completeness: CaptureCompleteness
    gaps: tuple[str, ...] = ()


def recover_attempt(
    checkpoint: RecoveryCheckpoint,
    attempt: Attempt,
    spool_store: SpoolStore,
    *,
    inspection: ExecutionInspectionResult | None = None,
) -> RecoveryResult:
    """Recover one attempt without reclassifying already reliable terminal facts."""
    manifest: SpoolManifest | None
    if (
        checkpoint.attempt_id != attempt.attempt_id
        or checkpoint.run_id != attempt.run_id
        or checkpoint.step_id != attempt.step_id
    ):
        raise ValueError("checkpoint and attempt identity must match")

    if attempt.state is AttemptState.INVALIDATED:
        # Captured bytes and even a live handle cannot restore the superseded basis.
        try:
            manifest = spool_store.read_manifest(attempt.attempt_id)
            blocks, cursors = manifest.blocks, manifest.cursors
        except FileNotFoundError:
            blocks, cursors = checkpoint.output_block_refs, checkpoint.output_cursors
        handle = attempt.execution_handle_ref
        exit_fact = attempt.exit_fact_ref
        uncertain = handle is not None and (
            exit_fact is None
            or exit_fact.attempt_id != attempt.attempt_id
            or exit_fact.process_start_identity != handle.process_start_identity
        )
        return RecoveryResult(
            action=RecoveryAction.PENDING_VERIFICATION
            if uncertain
            else RecoveryAction.TERMINAL_PRESERVED,
            attempt=attempt,
            recovered_blocks=blocks,
            recovered_cursors=cursors,
            capture_completeness=attempt.capture_completeness,
            gaps=("outdated_execution_termination_unverified",) if uncertain else (),
        )

    if (
        attempt.state in {AttemptState.COMPLETED, AttemptState.CANCELLED}
        and attempt.exit_fact_ref is not None
    ):
        try:
            manifest = spool_store.read_manifest(attempt.attempt_id)
            blocks = manifest.blocks
            cursors = manifest.cursors
        except FileNotFoundError:
            blocks = checkpoint.output_block_refs
            cursors = checkpoint.output_cursors
        return _result(
            RecoveryAction.TERMINAL_PRESERVED,
            attempt,
            blocks,
            cursors,
            [],
            None,
        )

    if inspection is not None and inspection.state is ExecutionInspectionState.RUNNING:
        try:
            manifest = spool_store.read_manifest(attempt.attempt_id)
            blocks = manifest.blocks
            cursors = manifest.cursors
        except FileNotFoundError:
            blocks = checkpoint.output_block_refs
            cursors = checkpoint.output_cursors
        if not inspection.identity_matches:
            return _result(
                RecoveryAction.PENDING_VERIFICATION,
                replace(attempt, state=AttemptState.PENDING_VERIFICATION),
                blocks,
                cursors,
                ["process_identity_mismatch"],
                "process_identity_mismatch",
            )
        return _result(
            RecoveryAction.REATTACH,
            replace(attempt, state=AttemptState.RUNNING),
            blocks,
            cursors,
            [],
            None,
        )

    gaps: list[str] = []
    try:
        manifest = spool_store.salvage_streams(attempt.attempt_id)
    except FileNotFoundError:
        manifest = None
        gaps.append("spool_manifest_missing")

    blocks = manifest.blocks if manifest is not None else checkpoint.output_block_refs
    cursors = manifest.cursors if manifest is not None else checkpoint.output_cursors
    if any(not block.complete for block in blocks):
        gaps.append("partial_spool_block")

    if inspection is not None and inspection.state in {
        ExecutionInspectionState.EXITED,
        ExecutionInspectionState.STOPPED,
    }:
        state = (
            AttemptState.CANCELLED
            if inspection.state is ExecutionInspectionState.STOPPED and inspection.stop_confirmed
            else AttemptState.COLLECTING
        )
        return _result(
            RecoveryAction.RECOVER_FROM_SPOOL,
            replace(attempt, state=state),
            blocks,
            cursors,
            gaps,
            None,
        )

    gaps.append("execution_result_unknown")
    if attempt.side_effect_class.value in {"read_only", "idempotent_write"}:
        return _result(
            RecoveryAction.SAFE_RETRY,
            replace(
                attempt,
                state=AttemptState.INVALIDATED,
                unknown_reason_ref="safe_retry_requires_new_attempt",
            ),
            blocks,
            cursors,
            gaps,
            "safe_retry_requires_new_attempt",
        )
    return _result(
        RecoveryAction.PENDING_VERIFICATION,
        replace(
            attempt,
            state=AttemptState.PENDING_VERIFICATION,
            unknown_reason_ref="execution_result_unknown",
        ),
        blocks,
        cursors,
        gaps,
        "execution_result_unknown",
    )


def _result(
    action: RecoveryAction,
    attempt: Attempt,
    blocks: tuple[OutputBlockRef, ...],
    cursors: tuple[OutputCursor, ...],
    gaps: list[str],
    unknown_reason: str | None,
) -> RecoveryResult:
    complete = not gaps and all(block.complete for block in blocks)
    if unknown_reason is not None and not attempt.unknown_reason_ref:
        attempt = replace(attempt, unknown_reason_ref=unknown_reason)
    return RecoveryResult(
        action=action,
        attempt=attempt,
        recovered_blocks=blocks,
        recovered_cursors=cursors,
        capture_completeness=(
            CaptureCompleteness.COMPLETE if complete else CaptureCompleteness.PARTIAL
        ),
        gaps=tuple(gaps),
    )


__all__ = [
    "AttemptInvalidation",
    "CaseReuseBasis",
    "CaseReuseInvalidation",
    "CheckpointStore",
    "RecoveryAction",
    "RecoveryRecord",
    "RecoveryResult",
    "invalidate_downstream_attempts",
    "invalidate_reuse_bases",
    "recover_attempt",
]
