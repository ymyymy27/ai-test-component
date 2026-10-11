"""Single dependency rule for serial dispatch; eligibility is not authorization."""

from collections.abc import Sequence
from dataclasses import dataclass

from aitest.domain.execution.runs import Step, StepState

_TERMINAL = frozenset(
    {StepState.COMPLETED, StepState.CANCELLED, StepState.INVALIDATED, StepState.EXECUTION_ERROR}
)
_BLOCKING = frozenset(
    {StepState.BLOCKED, StepState.CANCELLED, StepState.INVALIDATED, StepState.EXECUTION_ERROR}
)


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    ready_step_ids: tuple[str, ...] = ()
    blocked_step_ids: tuple[str, ...] = ()
    waiting_step_ids: tuple[str, ...] = ()
    terminal_step_ids: tuple[str, ...] = ()


def plan_serial_dispatch(steps: Sequence[Step]) -> DispatchPlan:
    states = {step.step_id: step.state for step in steps}
    if len(states) != len(steps):
        raise ValueError("duplicate step_id in serial dispatch")
    ready, blocked, waiting, terminal = [], [], [], []
    for step in steps:
        if step.state in _TERMINAL:
            terminal.append(step.step_id)
        elif step.state is StepState.BLOCKED:
            blocked.append(step.step_id)
        elif step.state not in {StepState.PENDING, StepState.READY}:
            waiting.append(step.step_id)
        else:
            dependencies = [
                states.get(edge.upstream_step_id)
                for edge in step.dependency_edges
                if edge.downstream_step_id == step.step_id and edge.required
            ]
            if any(state is None for state in dependencies):
                waiting.append(step.step_id)
            elif any(state in _BLOCKING for state in dependencies):
                blocked.append(step.step_id)
            elif all(state is StepState.COMPLETED for state in dependencies):
                ready.append(step.step_id)
            else:
                waiting.append(step.step_id)
    return DispatchPlan(tuple(ready), tuple(blocked), tuple(waiting), tuple(terminal))
