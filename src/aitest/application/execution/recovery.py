"""Recovery decisions, spool salvage, and dependency invalidation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

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
)


class RecoveryAction(StrEnum):
    REATTACH = "reattach"
    RECOVER_FROM_SPOOL = "recover_from_spool"
    SAFE_RETRY = "safe_retry"
    PENDING_VERIFICATION = "pending_verification"


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


_LIVE_ATTEMPT_STATES = frozenset(
    {
        AttemptState.INTENT_RECORDED,
        AttemptState.STARTING,
        AttemptState.RUNNING,
        AttemptState.STOP_REQUESTED,
        AttemptState.COLLECTING,
    }
)


def recover_attempt(
    checkpoint: RecoveryCheckpoint,
    attempt: Attempt,
    spool_store: SpoolStore,
    *,
    inspection: ExecutionInspectionResult | None = None,
) -> RecoveryResult:
    """Recover one attempt without inferring success from missing facts."""
    if (
        checkpoint.attempt_id != attempt.attempt_id
        or checkpoint.run_id != attempt.run_id
        or checkpoint.step_id != attempt.step_id
    ):
        raise ValueError("checkpoint and attempt identity must match")

    manifest = None
    gaps: list[str] = []
    try:
        manifest = spool_store.salvage_streams(attempt.attempt_id)
    except FileNotFoundError:
        gaps.append("spool_manifest_missing")

    recovered_blocks = manifest.blocks if manifest is not None else checkpoint.output_block_refs
    recovered_cursors = manifest.cursors if manifest is not None else checkpoint.output_cursors
    if any(not block.complete for block in recovered_blocks):
        gaps.append("partial_spool_block")

    if inspection is not None and inspection.state is ExecutionInspectionState.RUNNING:
        if not inspection.identity_matches:
            gaps.append("process_identity_mismatch")
            return _result(
                RecoveryAction.PENDING_VERIFICATION,
                attempt,
                recovered_blocks,
                recovered_cursors,
                gaps,
                "process_identity_mismatch",
            )
        return _result(
            RecoveryAction.REATTACH,
            replace(attempt, state=AttemptState.RUNNING),
            recovered_blocks,
            recovered_cursors,
            gaps,
            None,
        )

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
            recovered_blocks,
            recovered_cursors,
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
            recovered_blocks,
            recovered_cursors,
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
        recovered_blocks,
        recovered_cursors,
        gaps,
        "execution_result_unknown",
    )


def invalidate_downstream_attempts(
    attempts: Sequence[Attempt],
    *,
    previous_plan_revision: PlanRevisionRef,
    current_plan_revision: PlanRevisionRef,
    affected_upstream_attempt_ids: Sequence[str],
) -> tuple[AttemptInvalidation, ...]:
    """Invalidate only live attempts with precise dependencies on changed facts."""
    if previous_plan_revision == current_plan_revision:
        return ()
    affected = frozenset(affected_upstream_attempt_ids)
    if not affected:
        return ()

    invalidations: list[AttemptInvalidation] = []
    for attempt in attempts:
        if attempt.state not in _LIVE_ATTEMPT_STATES:
            continue
        dependencies = {consumed.upstream_attempt_id for consumed in attempt.consumed_outputs} | {
            condition.upstream_attempt_id for condition in attempt.consumed_conditions
        }
        matched = tuple(sorted(dependencies & affected))
        if not matched:
            continue
        invalidations.append(
            AttemptInvalidation(
                attempt=replace(
                    attempt,
                    state=AttemptState.INVALIDATED,
                    unknown_reason_ref="upstream_plan_changed",
                ),
                upstream_attempt_ids=matched,
                reason="upstream_plan_changed",
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
    "RecoveryAction",
    "RecoveryResult",
    "invalidate_downstream_attempts",
    "recover_attempt",
]
