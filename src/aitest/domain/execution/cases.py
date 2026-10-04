"""Pure whole-case execution/reuse aggregation, separate from business outcome."""

from dataclasses import dataclass

from aitest.domain.execution.runs import AttemptState, CaptureCompleteness


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
    if len(set(required_step_ids)) != len(required_step_ids):
        raise ValueError("required steps must be unique")
    if len({item.step_id for item in steps}) != len(steps):
        raise ValueError("current step facts must be unique")
    by_step = {item.step_id: item for item in steps}
    required = tuple(required_step_ids)
    current = tuple(by_step.get(step_id) for step_id in required)
    missing = tuple(
        step_id
        for step_id, item in zip(required, current, strict=True)
        if item is None
    )
    inherited = tuple(item for item in current if item is not None and not item.from_current_run)
    # Any new Attempt of this case cancels whole-case reuse, including optional steps.
    current_started = any(item.from_current_run and bool(item.attempt_id) for item in steps)
    decisive_failures = tuple(
        sorted(set(decisive_failure_step_ids) & set(required_step_ids))
    )

    pending: list[str] = list(missing)
    invalidated: list[str] = []
    for step_id, item in zip(required, current, strict=True):
        if item is None:
            continue
        if not item.from_current_run:
            pending.append(step_id)
        if item.state is AttemptState.INVALIDATED:
            invalidated.append(step_id)
        if item.state is not AttemptState.COMPLETED:
            pending.append(step_id)

    can_execute = (
        bool(required)
        and not missing
        and not inherited
        and all(
            item is not None
            and item.from_current_run
            and bool(item.attempt_id)
            and item.state is AttemptState.COMPLETED
            and item.capture_completeness is CaptureCompleteness.COMPLETE
            for item in current
        )
    )
    can_reuse = (
        not current_started
        and bool(inherited)
        and not missing
        and all(
            item is not None
            and not item.from_current_run
            and item.capture_completeness is CaptureCompleteness.COMPLETE
            and bool(item.attempt_id)
            and item.state is AttemptState.COMPLETED
            and item.verification_valid
            for item in current
        )
    )
    can_verify = (can_execute or can_reuse) and all(
        item is not None and item.verification_valid for item in current
    )
    if can_reuse:
        pending.clear()
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

