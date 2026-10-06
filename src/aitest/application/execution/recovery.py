"""Recovery decisions, spool salvage, and dependency invalidation."""

from __future__ import annotations

import hashlib
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
from aitest.domain.execution.output import require_saved_output_cursors
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
    SideEffectClass,
    SpoolManifest,
    has_reliable_terminal_fact,
    has_verified_exit,
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


def restore_checkpoint_attempt(checkpoint: RecoveryCheckpoint, attempt: Attempt) -> Attempt:
    """Validate the saved execution basis before inspecting or salvaging anything."""
    if (
        checkpoint.attempt_id != attempt.attempt_id
        or checkpoint.run_id != attempt.run_id
        or checkpoint.step_id != attempt.step_id
        or checkpoint.resolved_input_digest not in {"", attempt.resolved_input_digest}
        or (
            checkpoint.execution_handle_ref is not None
            and attempt.execution_handle_ref is not None
            and checkpoint.execution_handle_ref != attempt.execution_handle_ref
        )
    ):
        raise ValueError("checkpoint and attempt execution basis must match")
    return replace(
        attempt,
        execution_handle_ref=attempt.execution_handle_ref or checkpoint.execution_handle_ref,
    )


def recover_attempt(
    checkpoint: RecoveryCheckpoint,
    attempt: Attempt,
    spool_store: SpoolStore,
    *,
    inspection: ExecutionInspectionResult | None = None,
) -> RecoveryResult:
    """Recover one attempt without reclassifying already reliable terminal facts."""
    attempt = restore_checkpoint_attempt(checkpoint, attempt)
    if attempt.state is AttemptState.INVALIDATED:
        # Captured bytes and even a live handle cannot restore the superseded basis.
        blocks, cursors, _ = _recovery_material(checkpoint, attempt, spool_store)
        handle = attempt.execution_handle_ref
        uncertain = handle is not None and not has_verified_exit(attempt)
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

    if has_reliable_terminal_fact(attempt):
        blocks, cursors, gaps = _recovery_material(checkpoint, attempt, spool_store)
        return _result(
            RecoveryAction.TERMINAL_PRESERVED,
            attempt,
            blocks,
            cursors,
            gaps,
            None,
        )

    if inspection is not None and inspection.state in {
        ExecutionInspectionState.RUNNING,
        ExecutionInspectionState.EXITED,
        ExecutionInspectionState.STOPPED,
    }:
        handle = attempt.execution_handle_ref
        proven = (
            handle is not None
            and inspection.handle_id == handle.handle_id
            and inspection.identity_matches is True
            and (
                inspection.state is not ExecutionInspectionState.RUNNING
                or inspection.process_reachable is True
            )
        )
        if not proven:
            blocks, cursors, gaps = _recovery_material(checkpoint, attempt, spool_store)
            return _result(
                RecoveryAction.PENDING_VERIFICATION,
                replace(attempt, state=AttemptState.PENDING_VERIFICATION),
                blocks,
                cursors,
                [*gaps, "process_identity_mismatch"],
                "process_identity_mismatch",
            )
    if inspection is not None and inspection.state is ExecutionInspectionState.RUNNING:
        blocks, cursors, gaps = _recovery_material(checkpoint, attempt, spool_store)
        return _result(
            RecoveryAction.REATTACH,
            replace(attempt, state=AttemptState.RUNNING),
            blocks,
            cursors,
            gaps,
            None,
        )

    blocks, cursors, gaps = _recovery_material(checkpoint, attempt, spool_store, salvage=True)

    if inspection is not None and inspection.state in {
        ExecutionInspectionState.EXITED,
        ExecutionInspectionState.STOPPED,
    }:
        if (
            inspection.state is ExecutionInspectionState.STOPPED
            and inspection.stop_confirmed is not True
        ):
            return _result(
                RecoveryAction.PENDING_VERIFICATION,
                replace(attempt, state=AttemptState.PENDING_VERIFICATION),
                blocks,
                cursors,
                [*gaps, "stop_confirmation_unavailable"],
                "stop_confirmation_unavailable",
            )
        state = (
            AttemptState.CANCELLED
            if inspection.state is ExecutionInspectionState.STOPPED
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
    # A declared write category is not evidence of a business idempotency guarantee.
    # The current recovery input has no independent proof for retrying such writes.
    if attempt.side_effect_class is SideEffectClass.READ_ONLY:
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


def _recovery_material(
    checkpoint: RecoveryCheckpoint,
    attempt: Attempt,
    store: SpoolStore,
    *,
    salvage: bool = False,
) -> tuple[tuple[OutputBlockRef, ...], tuple[OutputCursor, ...], list[str]]:
    gaps: list[str] = []
    require_saved_output_cursors(
        attempt.attempt_id, attempt.output_cursors, attempt.output_block_refs
    )
    require_saved_output_cursors(
        attempt.attempt_id, checkpoint.output_cursors, checkpoint.output_block_refs
    )
    try:
        manifest = store.read_manifest(attempt.attempt_id)
    except FileNotFoundError:
        manifest = None
        if salvage:
            gaps.append("spool_manifest_missing")
    if manifest is not None:
        _validate_manifest(attempt, manifest)
        if salvage:
            # Validate before salvage, which can modify an unsealed tail.
            try:
                _verify_material(attempt, manifest.blocks, manifest.cursors, store)
            except FileNotFoundError:
                return (), (), ["spool_material_unavailable"]
            manifest = store.salvage_streams(attempt.attempt_id)
            _validate_manifest(attempt, manifest)
        blocks, cursors = manifest.blocks, manifest.cursors
    else:
        blocks, cursors = checkpoint.output_block_refs, checkpoint.output_cursors
    try:
        _verify_material(attempt, blocks, cursors, store)
    except FileNotFoundError:
        return (), (), ["spool_material_unavailable"]
    if any(block.complete is not True for block in blocks):
        gaps.append("partial_spool_block")
    return blocks, cursors, gaps


def _verify_material(
    attempt: Attempt,
    blocks: tuple[OutputBlockRef, ...],
    cursors: tuple[OutputCursor, ...],
    store: SpoolStore,
) -> None:
    if any(item.attempt_id != attempt.attempt_id for item in blocks) or any(
        item.attempt_id != attempt.attempt_id for item in cursors
    ):
        raise ValueError("recovery material has foreign attempt references")
    by_key = {(item.stream_name, item.block_index): item for item in blocks}
    if len(by_key) != len(blocks) or len({item.stream_name for item in cursors}) != len(cursors):
        raise ValueError("recovery material has duplicate block or cursor identities")
    require_saved_output_cursors(attempt.attempt_id, cursors, blocks)
    for block in blocks:
        content = store.read_block(block)
        if (
            type(content) is not bytes
            or len(content) != block.length
            or "sha256:" + hashlib.sha256(content).hexdigest() != block.digest
        ):
            raise ValueError("recovery block content failed verification")


def _validate_manifest(attempt: Attempt, manifest: SpoolManifest) -> None:
    if manifest.schema_version != "aitest.spool/1.0" or (
        manifest.run_id,
        manifest.step_id,
        manifest.attempt_id,
    ) != (attempt.run_id, attempt.step_id, attempt.attempt_id):
        raise ValueError("recovery manifest does not belong to the original execution")


def _result(
    action: RecoveryAction,
    attempt: Attempt,
    blocks: tuple[OutputBlockRef, ...],
    cursors: tuple[OutputCursor, ...],
    gaps: list[str],
    unknown_reason: str | None,
) -> RecoveryResult:
    # Sealed blocks prove their own bytes, not that every process output was captured.
    completeness = CaptureCompleteness.PARTIAL if gaps else attempt.capture_completeness
    if unknown_reason is not None and not attempt.unknown_reason_ref:
        attempt = replace(attempt, unknown_reason_ref=unknown_reason)
    if action is not RecoveryAction.TERMINAL_PRESERVED:
        # Missing temporary spool cannot erase previously saved material references.
        saved_blocks = {
            (item.stream_name, item.block_index): item for item in attempt.output_block_refs
        }
        for block in blocks:
            key = (block.stream_name, block.block_index)
            if key in saved_blocks and saved_blocks[key] != block:
                raise ValueError("recovery cannot rewrite a saved output block")
            saved_blocks[key] = block
        saved_cursors = {item.stream_name: item for item in attempt.output_cursors}
        for cursor in cursors:
            original = saved_cursors.get(cursor.stream_name)
            if original is not None and cursor.offset < original.offset:
                raise ValueError("recovery cannot move a saved output cursor backwards")
            saved_cursors[cursor.stream_name] = cursor
        attempt = replace(
            attempt,
            output_block_refs=tuple(saved_blocks.values()),
            output_cursors=tuple(saved_cursors.values()),
            capture_completeness=completeness,
        )
    return RecoveryResult(
        action=action,
        attempt=attempt,
        recovered_blocks=blocks,
        recovered_cursors=cursors,
        capture_completeness=completeness,
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
