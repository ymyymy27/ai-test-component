"""Pause, cancellation, rollback and evidence-supplement control facts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aitest.application.execution.recovery import invalidate_downstream_attempts
from aitest.application.ports import ExecutionPort
from aitest.domain.execution.cases import (
    CaseExecutionAggregate as CaseExecutionAggregate,
)
from aitest.domain.execution.cases import (
    StepExecutionBasis as StepExecutionBasis,
)
from aitest.domain.execution.cases import (
    aggregate_case_execution as aggregate_case_execution,
)
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
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


__all__ = [
    "CaseExecutionAggregate",
    "RunControlAction",
    "RunControlDecision",
    "RunControlService",
    "StepExecutionBasis",
    "aggregate_case_execution",
]
