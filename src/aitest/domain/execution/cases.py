"""Pure whole-case execution/reuse aggregation, separate from business outcome."""

from dataclasses import dataclass

from aitest.domain.execution.reuse import WholeCaseReuseBasis
from aitest.domain.execution.runs import AttemptState, CaptureCompleteness
from aitest.domain.json_material import require_json_text


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
    reuse_basis: WholeCaseReuseBasis | None = None,
) -> CaseExecutionAggregate:
    """Enforce E/R/V exclusivity across current-run and inherited steps."""
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case identity must be nonempty text")
    require_json_text(case_id)
    for identities in (required_step_ids, decisive_failure_step_ids):
        if not isinstance(identities, tuple) or any(
            not isinstance(identity, str) or not identity.strip() for identity in identities
        ):
            raise ValueError("case step identities must be immutable nonempty text")
        for identity in identities:
            require_json_text(identity)
    if not isinstance(steps, tuple):
        raise ValueError("case step facts require an immutable sequence")
    if reuse_basis is not None and not isinstance(reuse_basis, WholeCaseReuseBasis):
        raise ValueError("whole-case reuse requires a typed complete basis")
    for fact in steps:
        if (
            not isinstance(fact, StepExecutionBasis)
            or not isinstance(fact.step_id, str)
            or not fact.step_id.strip()
            or (fact.attempt_id is not None and not isinstance(fact.attempt_id, str))
            or type(fact.from_current_run) is not bool
            or type(fact.verification_valid) is not bool
            or not isinstance(fact.state, AttemptState)
            or not isinstance(fact.capture_completeness, CaptureCompleteness)
        ):
            raise ValueError("case step fact identity, type or enum cannot be verified")
        require_json_text(fact.step_id)
        if fact.attempt_id is not None:
            require_json_text(fact.attempt_id)
    if len(set(required_step_ids)) != len(required_step_ids):
        raise ValueError("required steps must be unique")
    if len({item.step_id for item in steps}) != len(steps):
        raise ValueError("current step facts must be unique")
    attempt_ids = [item.attempt_id for item in steps if _has_attempt(item)]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise ValueError("one attempt cannot belong to multiple steps")
    by_step = {item.step_id: item for item in steps}
    required = tuple(required_step_ids)
    current = tuple(by_step.get(step_id) for step_id in required)
    missing = tuple(
        step_id for step_id, item in zip(required, current, strict=True) if item is None
    )
    inherited = tuple(item for item in current if item is not None and not item.from_current_run)
    # Any new Attempt of this case cancels whole-case reuse, including optional steps.
    current_started = any(item.from_current_run and _has_attempt(item) for item in steps)

    pending: list[str] = list(missing)
    invalidated: list[str] = []
    for step_id, item in zip(required, current, strict=True):
        if item is None:
            continue
        if not item.from_current_run:
            pending.append(step_id)
        if item.state is AttemptState.INVALIDATED:
            invalidated.append(step_id)
        if (
            item.state is not AttemptState.COMPLETED
            or not _has_attempt(item)
            or item.capture_completeness is not CaptureCompleteness.COMPLETE
        ):
            pending.append(step_id)

    can_execute = (
        bool(required)
        and not missing
        and not inherited
        and all(
            item is not None
            and item.from_current_run
            and _has_attempt(item)
            and item.state is AttemptState.COMPLETED
            and item.capture_completeness is CaptureCompleteness.COMPLETE
            for item in current
        )
    )
    can_reuse = (
        reuse_basis is not None
        and not reuse_basis.denial_reasons
        and reuse_basis.target.case_id == case_id
        and dict(reuse_basis.source_attempt_by_step) == {
            item.step_id: item.attempt_id for item in current if item is not None
        }
        and not current_started
        and bool(inherited)
        and not missing
        and all(
            item is not None
            and not item.from_current_run
            and item.capture_completeness is CaptureCompleteness.COMPLETE
            and _has_attempt(item)
            and item.state is AttemptState.COMPLETED
            and item.verification_valid
            for item in current
        )
    )
    can_verify = (can_execute or can_reuse) and all(
        item is not None and item.verification_valid for item in current
    )
    decisive_failures = tuple(
        step_id
        for step_id in sorted(set(decisive_failure_step_ids) & set(required_step_ids))
        if (item := by_step.get(step_id)) is not None
        and _has_attempt(item)
        and item.state is AttemptState.COMPLETED
        and item.verification_valid
        and (item.from_current_run or can_reuse)
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


def _has_attempt(item: StepExecutionBasis) -> bool:
    return isinstance(item.attempt_id, str) and bool(item.attempt_id.strip())
