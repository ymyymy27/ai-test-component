"""Pure invalidation of actual output/condition consumers and whole-case reuse."""

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, replace

from aitest.domain.execution.runs import Attempt, AttemptState, PlanRevisionRef


@dataclass(frozen=True, slots=True)
class AttemptInvalidation:
    attempt: Attempt
    upstream_attempt_ids: tuple[str, ...]
    reason: str


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
    """All actual consumers become outdated, regardless of execution state.

    A cancelled/error/already-invalidated middle node still carries an input
    dependency. Skipping that node would leave its successful downstream valid.
    Historical input objects remain unchanged; returned values describe the new basis.
    """
    seeds = set(affected_upstream_attempt_ids)
    by_id = {attempt.attempt_id: attempt for attempt in attempts}
    if len(by_id) != len(attempts):
        raise ValueError("attempt facts must have unique identities")
    dependencies: dict[str, set[str]] = {}
    consumers: dict[str, set[str]] = defaultdict(set)
    for attempt in attempts:
        used = {item.upstream_attempt_id for item in attempt.consumed_outputs} | {
            item.upstream_attempt_id for item in attempt.consumed_conditions
        }
        if attempt.attempt_id in used:
            raise ValueError("an attempt cannot consume its own result")
        dependencies[attempt.attempt_id] = used
        for upstream in used:
            consumers[upstream].add(attempt.attempt_id)
    affected = set(seeds)
    pending = deque(seeds)
    while pending:
        upstream = pending.popleft()
        for consumer in consumers.get(upstream, ()):
            if consumer not in affected:
                affected.add(consumer)
                pending.append(consumer)
    reason = (
        "upstream_plan_changed"
        if previous_plan_revision != current_plan_revision
        else "upstream_attempt_replaced"
    )
    return tuple(
        AttemptInvalidation(
            attempt=replace(
                attempt,
                state=AttemptState.INVALIDATED,
                unknown_reason_ref="upstream_dependency_invalidated",
            ),
            upstream_attempt_ids=tuple(sorted(dependencies[attempt.attempt_id] & affected)),
            reason=reason,
        )
        for attempt in attempts
        if attempt.attempt_id in affected - seeds
    )


def invalidate_reuse_bases(
    bases: Sequence[CaseReuseBasis],
    *,
    affected_upstream_attempt_ids: Sequence[str],
) -> tuple[CaseReuseInvalidation, ...]:
    """Revoke whole-case reuse when its precise source execution basis changes."""
    if len({basis.case_id for basis in bases}) != len(bases):
        raise ValueError("reuse bases must have unique case identities")
    affected = frozenset(affected_upstream_attempt_ids)
    return tuple(
        CaseReuseInvalidation(
            case_id=basis.case_id,
            source_attempt_ids=tuple(sorted(set(basis.source_attempt_ids) & affected)),
            reason="reuse_basis_invalidated",
        )
        for basis in bases
        if set(basis.source_attempt_ids) & affected
    )
