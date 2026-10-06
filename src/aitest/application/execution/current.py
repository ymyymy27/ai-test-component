"""Project committed current-attempt changes without recalculating business results."""

from datetime import datetime

from aitest.application.execution.facts import project_attempt_fact, project_attempt_update
from aitest.contracts.execution_facts import (
    AttemptStateFact,
    DependencyInvalidationFact,
    ExecutionFacts,
    FactCompleteness,
    RunControlStateFact,
    StepStateFact,
)
from aitest.domain.execution.dependencies import AttemptInvalidation
from aitest.domain.execution.runs import Attempt


def project_current_update(
    previous: ExecutionFacts,
    attempt: Attempt,
    *,
    invalidations: tuple[AttemptInvalidation, ...] = (),
    committed_at: datetime,
) -> ExecutionFacts:
    """Use saved facts plus authoritative checkpoint changes; retain all history."""
    changes = {item.attempt.attempt_id: item.attempt for item in invalidations}
    changes[attempt.attempt_id] = attempt
    current = {**previous.current_attempt_by_step, attempt.step_id: attempt.attempt_id}
    attempts = tuple(
        project_attempt_update(
            changes[item.attempt_id], item, is_current=current[item.step_id] == item.attempt_id
        )
        if item.attempt_id in changes
        else item.model_copy(update={"is_current": current[item.step_id] == item.attempt_id})
        for item in previous.attempts
    )
    if all(item.attempt_id != attempt.attempt_id for item in attempts):
        attempts += (project_attempt_fact(attempt, is_current=True),)
    by_id = {item.attempt_id: item for item in attempts}
    affected_steps = {
        item.attempt.step_id: item
        for item in invalidations
        if current.get(item.attempt.step_id) == item.attempt.attempt_id
    }
    steps = []
    for step in previous.steps:
        if step.step_id == attempt.step_id:
            fact = by_id[attempt.attempt_id]
            state = _step_state(fact.state)
            steps.append(
                step.model_copy(
                    update={
                        "current_attempt_id": attempt.attempt_id,
                        "state": state,
                        "invalidated": state is StepStateFact.INVALIDATED,
                        "invalidated_by": step.invalidated_by
                        if state is StepStateFact.INVALIDATED
                        else None,
                    }
                )
            )
        elif step.step_id in affected_steps:
            steps.append(
                step.model_copy(
                    update={
                        "state": StepStateFact.INVALIDATED,
                        "invalidated": True,
                        "invalidated_by": attempt.attempt_id,
                    }
                )
            )
        else:
            steps.append(step)
    events = tuple(
        DependencyInvalidationFact(
            invalidation_id=f"{attempt.attempt_id}:{item.attempt.attempt_id}",
            run_id=previous.run_id,
            affected_step_id=item.attempt.step_id,
            affected_attempt_id=item.attempt.attempt_id,
            upstream_attempt_id=upstream,
            reason=item.reason,
            source_revision_ref=previous.snapshot_commit_id,
            transitive=upstream != previous.current_attempt_by_step[attempt.step_id],
            invalidated_at=committed_at,
        )
        for item in invalidations
        for upstream in item.upstream_attempt_ids
    )
    eligible = {
        item.attempt_id
        for item in attempts
        if item.is_current
        and item.state is AttemptStateFact.COMPLETED
        and item.capture_completeness.value == "complete"
    }
    executed = set(previous.coverage.executed_attempt_ids) & eligible
    if attempt.attempt_id in eligible:
        executed.add(attempt.attempt_id)
    revision = previous.run_revision + 1
    run_update: dict[str, object] = {
        "run_revision": revision,
        "result_ref": None,
        "evidence_level": None,
        "coverage_summary": None,
        "ended_at": None,
    }
    # A checkpoint is an execution observation, not permission to resume. Saved
    # pause/cancel/recovery decisions and terminal states need an explicit control
    # transition; one active or completed Attempt cannot confirm a run-wide boundary.
    if previous.run.control_state in {
        RunControlStateFact.NOT_STARTED,
        RunControlStateFact.RUNNING,
    }:
        if attempt.state.value in {"intent_recorded", "starting", "running", "collecting"}:
            run_update["control_state"] = RunControlStateFact.RUNNING
        if attempt.state.value in {"pending_verification", "unknown"}:
            run_update["control_state"] = RunControlStateFact.PENDING_VERIFICATION
    return previous.model_copy(
        update={
            "facts_id": f"execution-update:{attempt.attempt_id}:{revision}",
            "committed_at": committed_at,
            "run_revision": revision,
            "run": previous.run.model_copy(update=run_update),
            "steps": tuple(steps),
            "attempts": attempts,
            "current_attempt_by_step": current,
            "dependency_invalidations": previous.dependency_invalidations + events,
            "coverage": previous.coverage.model_copy(
                update={
                    "executed_attempt_ids": tuple(sorted(executed)),
                    "blocked_step_ids": tuple(
                        step.step_id for step in steps if step.state is StepStateFact.BLOCKED
                    ),
                    "invalidated_step_ids": tuple(
                        step.step_id for step in steps if step.invalidated
                    ),
                    "unknown_step_ids": tuple(
                        step.step_id
                        for step in steps
                        if step.state is StepStateFact.PENDING_VERIFICATION
                    ),
                }
            ),
            "completeness": FactCompleteness.PARTIAL,
        }
    )


def _step_state(state: AttemptStateFact) -> StepStateFact:
    if state is AttemptStateFact.INTENT_RECORDED:
        return StepStateFact.PENDING
    if state in {
        AttemptStateFact.STARTING,
        AttemptStateFact.RUNNING,
        AttemptStateFact.STOP_REQUESTED,
        AttemptStateFact.COLLECTING,
    }:
        return StepStateFact.RUNNING
    if state is AttemptStateFact.UNKNOWN:
        return StepStateFact.PENDING_VERIFICATION
    return StepStateFact(state.value)
