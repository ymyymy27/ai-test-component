"""Pause, cancellation, rollback and evidence-supplement control facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aitest.application.execution.recovery import invalidate_downstream_attempts
from aitest.application.ports import ExecutionPort
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
    CaptureCompleteness,
    PlanRevisionRef,
    Run,
    RunControlState,
)

_ACTIVE_ATTEMPT_STATES = frozenset(
    {
        AttemptState.INTENT_RECORDED,
        AttemptState.STARTING,
        AttemptState.RUNNING,
        AttemptState.STOP_REQUESTED,
        AttemptState.COLLECTING,
    }
)


class RunControlAction(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    RETURN_FOR_REWORK = "return_for_rework"
    SUPPLEMENT_EVIDENCE = "supplement_evidence"


@dataclass(frozen=True, slots=True)
class RunControlDecision:
    action: RunControlAction
    run_state: RunControlState
    attempt_state: AttemptState | None = None
    stop_confirmed: bool | None = None
    requires_verification: bool = False
    invalidated_attempt_ids: tuple[str, ...] = ()
    rerun_step_ids: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()


class RunControlService:
    """Product-level control facts; it never fabricates a terminal state."""

    def __init__(self, execution_port: ExecutionPort) -> None:
        self._execution_port = execution_port

    def pause(self, run: Run, attempts: tuple[Attempt, ...]) -> RunControlDecision:
        if run.control_state is not RunControlState.RUNNING:
            raise ValueError("only a running run can be paused")
        state = (
            RunControlState.PAUSE_REQUESTED
            if any(attempt.state in _ACTIVE_ATTEMPT_STATES for attempt in attempts)
            else RunControlState.PAUSED
        )
        return RunControlDecision(action=RunControlAction.PAUSE, run_state=state)

    def resume(self, run: Run) -> RunControlDecision:
        if run.control_state not in {
            RunControlState.PAUSED,
            RunControlState.PAUSE_REQUESTED,
        }:
            raise ValueError("only a paused run can be resumed")
        return RunControlDecision(
            action=RunControlAction.RESUME,
            run_state=RunControlState.RUNNING,
        )

    def cancel(self, attempt: Attempt) -> RunControlDecision:
        if attempt.execution_handle_ref is None:
            return RunControlDecision(
                action=RunControlAction.CANCEL,
                run_state=RunControlState.PENDING_VERIFICATION,
                attempt_state=AttemptState.PENDING_VERIFICATION,
                stop_confirmed=False,
                requires_verification=True,
                gaps=("execution_handle_missing",),
            )
        stopped = self._execution_port.request_stop(attempt.execution_handle_ref)
        if stopped.stop_confirmed:
            return RunControlDecision(
                action=RunControlAction.CANCEL,
                run_state=RunControlState.CANCELLED,
                attempt_state=AttemptState.CANCELLED,
                stop_confirmed=True,
            )
        return RunControlDecision(
            action=RunControlAction.CANCEL,
            run_state=RunControlState.CANCELLING,
            attempt_state=AttemptState.PENDING_VERIFICATION,
            stop_confirmed=False,
            requires_verification=True,
            gaps=("stop_confirmation_unavailable",),
        )

    def return_for_rework(
        self,
        attempts: tuple[Attempt, ...],
        *,
        previous_plan_revision: PlanRevisionRef,
        current_plan_revision: PlanRevisionRef,
        affected_upstream_attempt_ids: tuple[str, ...],
    ) -> RunControlDecision:
        invalidations = invalidate_downstream_attempts(
            attempts,
            previous_plan_revision=previous_plan_revision,
            current_plan_revision=current_plan_revision,
            affected_upstream_attempt_ids=affected_upstream_attempt_ids,
        )
        return RunControlDecision(
            action=RunControlAction.RETURN_FOR_REWORK,
            run_state=RunControlState.PAUSED,
            invalidated_attempt_ids=tuple(
                item.attempt.attempt_id for item in invalidations
            ),
            rerun_step_ids=tuple(item.attempt.step_id for item in invalidations),
            requires_verification=bool(invalidations),
        )

    def supplement_evidence(
        self,
        *,
        evidence_refs: tuple[str, ...],
        verification_passed: bool,
    ) -> RunControlDecision:
        if not evidence_refs:
            return RunControlDecision(
                action=RunControlAction.SUPPLEMENT_EVIDENCE,
                run_state=RunControlState.PENDING_VERIFICATION,
                attempt_state=AttemptState.PENDING_VERIFICATION,
                requires_verification=True,
                gaps=("evidence_missing",),
            )
        if not verification_passed:
            return RunControlDecision(
                action=RunControlAction.SUPPLEMENT_EVIDENCE,
                run_state=RunControlState.PENDING_VERIFICATION,
                attempt_state=AttemptState.PENDING_VERIFICATION,
                requires_verification=True,
                gaps=("independent_verification_unconfirmed",),
            )
        return RunControlDecision(
            action=RunControlAction.SUPPLEMENT_EVIDENCE,
            run_state=RunControlState.RUNNING,
            requires_verification=False,
        )


@dataclass(frozen=True, slots=True)
class StepExecutionBasis:
    step_id: str
    attempt_id: str | None
    from_current_run: bool
    state: AttemptState
    capture_completeness: CaptureCompleteness = CaptureCompleteness.UNKNOWN
    verification_valid: bool = False


@dataclass(frozen=True, slots=True)
class CaseExecutionAggregate:
    case_id: str
    can_count_execution: bool
    can_count_reuse: bool
    can_count_verification: bool
    has_decisive_failure: bool
    mixed_inheritance: bool
    pending_step_ids: tuple[str, ...]
    invalidated_step_ids: tuple[str, ...]


def aggregate_case_execution(
    *,
    case_id: str,
    required_step_ids: tuple[str, ...],
    steps: tuple[StepExecutionBasis, ...],
    decisive_failure_step_ids: tuple[str, ...] = (),
) -> CaseExecutionAggregate:
    """Enforce E/R/V exclusivity across current-run and inherited steps."""
    by_step = {item.step_id: item for item in steps}
    required = tuple(required_step_ids)
    current = tuple(by_step.get(step_id) for step_id in required)
    missing = tuple(step_id for step_id, item in zip(required, current) if item is None)
    inherited = tuple(item for item in current if item is not None and not item.from_current_run)
    current_started = any(item is not None and item.from_current_run for item in current)
    decisive_failures = tuple(
        sorted(set(decisive_failure_step_ids) & set(required_step_ids))
    )

    pending: list[str] = list(missing)
    invalidated: list[str] = []
    for step_id, item in zip(required, current):
        if item is None:
            continue
        if not item.from_current_run:
            pending.append(step_id)
        if item.state is AttemptState.INVALIDATED:
            invalidated.append(step_id)
        if item.state is not AttemptState.COMPLETED:
            pending.append(step_id)

    can_execute = (
        not missing
        and not inherited
        and not decisive_failures
        and all(
            item is not None
            and item.from_current_run
            and item.state is AttemptState.COMPLETED
            and item.capture_completeness is CaptureCompleteness.COMPLETE
            for item in current
        )
    )
    can_reuse = (
        not current_started
        and bool(inherited)
        and not missing
        and not decisive_failures
        and all(
            item is not None
            and not item.from_current_run
            and item.state is AttemptState.COMPLETED
            and item.verification_valid
            for item in current
        )
    )
    can_verify = can_execute and not decisive_failures and all(
        item is not None and item.verification_valid for item in current
    )
    return CaseExecutionAggregate(
        case_id=case_id,
        can_count_execution=can_execute,
        can_count_reuse=can_reuse,
        can_count_verification=can_verify,
        has_decisive_failure=bool(decisive_failures),
        mixed_inheritance=current_started and bool(inherited),
        pending_step_ids=tuple(sorted(set(pending))),
        invalidated_step_ids=tuple(sorted(set(invalidated))),
    )


__all__ = [
    "CaseExecutionAggregate",
    "RunControlAction",
    "RunControlDecision",
    "RunControlService",
    "StepExecutionBasis",
    "aggregate_case_execution",
]
