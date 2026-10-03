"""Recovery decisions, spool salvage, and dependency invalidation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from aitest.application.ports import SpoolStore
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
    CaptureCompleteness,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    OutputBlockRef,
    OutputCursor,
    PlanRevisionRef,
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


@dataclass(frozen=True, slots=True)
class AttemptInvalidation:
    attempt: Attempt
    upstream_attempt_ids: tuple[str, ...]
    reason: str


_NON_INVALIDATABLE_STATES = frozenset(
    {
        AttemptState.INVALIDATED,
        AttemptState.CANCELLED,
        AttemptState.EXECUTION_ERROR,
    }
)


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
            if inspection.state is ExecutionInspectionState.STOPPED
            and inspection.stop_confirmed
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


@dataclass(frozen=True, slots=True)
class CaseReuseBasis:
    case_id: str
    source_attempt_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CaseReuseInvalidation:
    case_id: str
    source_attempt_ids: tuple[str, ...]
    reason: str


def invalidate_downstream_attempts(
    attempts: Sequence[Attempt],
    *,
    previous_plan_revision: PlanRevisionRef,
    current_plan_revision: PlanRevisionRef,
    affected_upstream_attempt_ids: Sequence[str],
) -> tuple[AttemptInvalidation, ...]:
    """Invalidate direct and transitive consumers of changed upstream facts."""
    affected = set(affected_upstream_attempt_ids)
    if not affected:
        return ()

    invalidated: set[str] = set()
    changed = True
    while changed:
        changed = False
        for attempt in attempts:
            if attempt.attempt_id in invalidated:
                continue
            if attempt.state in _NON_INVALIDATABLE_STATES:
                continue
            dependencies = {
                consumed.upstream_attempt_id for consumed in attempt.consumed_outputs
            } | {
                condition.upstream_attempt_id for condition in attempt.consumed_conditions
            }
            if not dependencies & affected:
                continue
            invalidated.add(attempt.attempt_id)
            affected.add(attempt.attempt_id)
            changed = True

    results: list[AttemptInvalidation] = []
    for attempt in attempts:
        if attempt.attempt_id not in invalidated:
            continue
        dependencies = {
            consumed.upstream_attempt_id for consumed in attempt.consumed_outputs
        } | {
            condition.upstream_attempt_id for condition in attempt.consumed_conditions
        }
        results.append(
            AttemptInvalidation(
                attempt=replace(
                    attempt,
                    state=AttemptState.INVALIDATED,
                    unknown_reason_ref="upstream_dependency_invalidated",
                ),
                upstream_attempt_ids=tuple(sorted(dependencies & affected)),
                reason=(
                    "upstream_plan_changed"
                    if previous_plan_revision != current_plan_revision
                    else "upstream_attempt_replaced"
                ),
            )
        )
    return tuple(results)


def invalidate_reuse_bases(
    bases: Sequence[CaseReuseBasis],
    *,
    affected_upstream_attempt_ids: Sequence[str],
) -> tuple[CaseReuseInvalidation, ...]:
    """Revoke whole-case reuse when its source execution basis changed."""
    affected = frozenset(affected_upstream_attempt_ids)
    if not affected:
        return ()
    invalidations: list[CaseReuseInvalidation] = []
    for basis in bases:
        matched = tuple(sorted(set(basis.source_attempt_ids) & affected))
        if not matched:
            continue
        invalidations.append(
            CaseReuseInvalidation(
                case_id=basis.case_id,
                source_attempt_ids=matched,
                reason="reuse_basis_invalidated",
            )
        )
    return tuple(invalidations)


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
