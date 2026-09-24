"""Serial execution loop and dependency dispatch skeleton."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from aitest.application.ports import ExecutionPort, SpoolStore
from aitest.domain.execution.runs import (
    Attempt,
    AttemptState,
    CaptureCompleteness,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExecutionRequest,
    OutputBlockRef,
    OutputCursor,
    Step,
    StepState,
    StopRequestResult,
)

_TERMINAL_STEP_STATES = frozenset(
    {
        StepState.COMPLETED,
        StepState.CANCELLED,
        StepState.INVALIDATED,
        StepState.EXECUTION_ERROR,
    }
)
_BLOCKING_UPSTREAM_STATES = frozenset(
    {
        StepState.BLOCKED,
        StepState.CANCELLED,
        StepState.INVALIDATED,
        StepState.EXECUTION_ERROR,
    }
)


@dataclass(frozen=True, slots=True)
class DispatchPlan:
    ready_step_ids: tuple[str, ...] = ()
    blocked_step_ids: tuple[str, ...] = ()
    waiting_step_ids: tuple[str, ...] = ()
    terminal_step_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SerialExecutionItem:
    step: Step
    attempt: Attempt
    request: ExecutionRequest


@dataclass(frozen=True, slots=True)
class SerialExecutionResult:
    steps: tuple[Step, ...]
    attempts: tuple[Attempt, ...]


class SerialRunner:
    """Execute ready steps serially and persist sealed captured blocks."""

    def __init__(
        self,
        execution_port: ExecutionPort,
        spool_store: SpoolStore | None = None,
    ) -> None:
        self._execution_port = execution_port
        self._spool_store = spool_store

    def plan_dispatch(self, steps: Sequence[Step]) -> DispatchPlan:
        states = self._state_by_step_id(steps)
        ready: list[str] = []
        blocked: list[str] = []
        waiting: list[str] = []
        terminal: list[str] = []

        for step in steps:
            if step.state in _TERMINAL_STEP_STATES:
                terminal.append(step.step_id)
                continue
            if step.state is StepState.BLOCKED:
                blocked.append(step.step_id)
                continue
            if step.state not in {StepState.PENDING, StepState.READY}:
                waiting.append(step.step_id)
                continue

            dependencies = self._required_dependency_ids(step)
            dependency_states = [states.get(step_id) for step_id in dependencies]
            if any(state is None for state in dependency_states):
                waiting.append(step.step_id)
            elif any(state in _BLOCKING_UPSTREAM_STATES for state in dependency_states):
                blocked.append(step.step_id)
            elif all(state is StepState.COMPLETED for state in dependency_states):
                ready.append(step.step_id)
            else:
                waiting.append(step.step_id)

        return DispatchPlan(
            ready_step_ids=tuple(ready),
            blocked_step_ids=tuple(blocked),
            waiting_step_ids=tuple(waiting),
            terminal_step_ids=tuple(terminal),
        )

    def run_serial(
        self,
        items: Sequence[SerialExecutionItem],
        *,
        max_waves: int = 100,
    ) -> SerialExecutionResult:
        if max_waves < 1:
            raise ValueError("max_waves must be positive")
        steps = [item.step for item in items]
        self._state_by_step_id(steps)
        step_index = self._index_by_step_id(steps)
        items_by_step = self._items_by_step_id(items)
        attempts: list[Attempt] = []

        for _ in range(max_waves):
            plan = self.plan_dispatch(steps)
            for step_id in plan.blocked_step_ids:
                index = step_index[step_id]
                steps[index] = replace(steps[index], state=StepState.BLOCKED)
            if not plan.ready_step_ids:
                break
            for step_id in plan.ready_step_ids:
                item = items_by_step[step_id]
                attempt = self.execute_attempt(item.attempt, item.request)
                attempts.append(attempt)
                index = step_index[step_id]
                steps[index] = replace(
                    steps[index],
                    state=self._step_state_for_attempt(attempt.state),
                    current_attempt_id=attempt.attempt_id,
                )

        return SerialExecutionResult(steps=tuple(steps), attempts=tuple(attempts))

    def execute_attempt(
        self,
        attempt: Attempt,
        request: ExecutionRequest,
        *,
        max_polls: int = 8,
    ) -> Attempt:
        if max_polls < 1:
            raise ValueError("max_polls must be positive")
        current = self.start_attempt(attempt, request)
        for _ in range(max_polls):
            inspection = self.inspect_attempt(current)
            if inspection.state is ExecutionInspectionState.RUNNING:
                continue
            collection = self.collect_attempt(current, current.output_cursor_ref)
            return self._apply_collection(current, inspection, collection)
        return replace(
            current,
            state=AttemptState.PENDING_VERIFICATION,
            unknown_reason_ref="poll_limit_reached",
        )

    def start_attempt(self, attempt: Attempt, request: ExecutionRequest) -> Attempt:
        self._require_attempt_identity(attempt, request)
        handle = self._execution_port.start(request)
        return replace(
            attempt,
            state=AttemptState.RUNNING,
            execution_handle_ref=handle,
        )

    def inspect_attempt(self, attempt: Attempt) -> ExecutionInspectionResult:
        return self._execution_port.inspect(self._require_handle(attempt))

    def collect_attempt(
        self,
        attempt: Attempt,
        cursor: OutputCursor | None = None,
    ) -> ExecutionCollectionResult:
        return self._execution_port.collect(self._require_handle(attempt), cursor)

    def request_stop(self, attempt: Attempt) -> StopRequestResult:
        return self._execution_port.request_stop(self._require_handle(attempt))

    def _apply_collection(
        self,
        attempt: Attempt,
        inspection: ExecutionInspectionResult,
        collection: ExecutionCollectionResult,
    ) -> Attempt:
        output_blocks = self._merge_output_blocks(
            attempt.output_block_refs,
            collection.output_blocks,
        )
        if collection.captured_blocks:
            if self._spool_store is None:
                raise ValueError("captured blocks require a SpoolStore")
            manifest = self._spool_store.persist_blocks(collection.captured_blocks)
            output_blocks = self._merge_output_blocks(output_blocks, manifest.blocks)

        state = self._attempt_state_for(attempt, inspection, collection)
        capture_completeness = (
            attempt.capture_completeness
            if collection.capture_completeness is CaptureCompleteness.UNKNOWN
            else collection.capture_completeness
        )
        return replace(
            attempt,
            state=state,
            output_block_refs=output_blocks,
            output_cursor_ref=collection.output_cursor_ref or attempt.output_cursor_ref,
            structured_result_ref=collection.structured_result_ref,
            exit_fact_ref=collection.exit_fact_ref,
            capture_completeness=capture_completeness,
            error_ref=collection.error_ref,
            unknown_reason_ref=self._unknown_reason_for(inspection, collection, state),
        )

    @staticmethod
    def _attempt_state_for(
        attempt: Attempt,
        inspection: ExecutionInspectionResult,
        collection: ExecutionCollectionResult,
    ) -> AttemptState:
        if collection.error_ref is not None:
            return AttemptState.EXECUTION_ERROR
        if inspection.state in {
            ExecutionInspectionState.LOST,
            ExecutionInspectionState.UNKNOWN,
        }:
            return AttemptState.PENDING_VERIFICATION
        if inspection.state is ExecutionInspectionState.STOPPED:
            return (
                AttemptState.CANCELLED
                if inspection.stop_confirmed
                else AttemptState.PENDING_VERIFICATION
            )
        if inspection.state is ExecutionInspectionState.EXITED:
            if collection.exit_fact_ref is None or not collection.complete:
                return AttemptState.PENDING_VERIFICATION
            return AttemptState.COMPLETED
        return attempt.state

    @staticmethod
    def _unknown_reason_for(
        inspection: ExecutionInspectionResult,
        collection: ExecutionCollectionResult,
        state: AttemptState,
    ) -> str | None:
        if state is not AttemptState.PENDING_VERIFICATION:
            return None
        if inspection.unknown_reason is not None:
            return inspection.unknown_reason
        if inspection.state in {
            ExecutionInspectionState.LOST,
            ExecutionInspectionState.UNKNOWN,
        }:
            return "execution_state_unknown"
        if inspection.state is ExecutionInspectionState.STOPPED and not inspection.stop_confirmed:
            return "stop_confirmation_unavailable"
        if collection.exit_fact_ref is None:
            return "exit_fact_unavailable"
        return "verification_inconclusive"

    @staticmethod
    def _merge_output_blocks(
        existing: tuple[OutputBlockRef, ...],
        incoming: tuple[OutputBlockRef, ...],
    ) -> tuple[OutputBlockRef, ...]:
        merged = list(existing)
        keys = {(block.stream_name, block.block_index) for block in existing}
        for block in incoming:
            key = (block.stream_name, block.block_index)
            if key in keys:
                continue
            merged.append(block)
            keys.add(key)
        return tuple(merged)

    @staticmethod
    def _step_state_for_attempt(state: AttemptState) -> StepState:
        if state is AttemptState.COMPLETED:
            return StepState.COMPLETED
        if state is AttemptState.EXECUTION_ERROR:
            return StepState.EXECUTION_ERROR
        if state is AttemptState.CANCELLED:
            return StepState.CANCELLED
        if state is AttemptState.INVALIDATED:
            return StepState.INVALIDATED
        if state in {AttemptState.PENDING_VERIFICATION, AttemptState.UNKNOWN}:
            return StepState.PENDING_VERIFICATION
        return StepState.RUNNING

    @staticmethod
    def _index_by_step_id(steps: Sequence[Step]) -> dict[str, int]:
        return {step.step_id: index for index, step in enumerate(steps)}

    @staticmethod
    def _items_by_step_id(
        items: Sequence[SerialExecutionItem],
    ) -> dict[str, SerialExecutionItem]:
        by_step: dict[str, SerialExecutionItem] = {}
        for item in items:
            if (
                item.step.step_id != item.attempt.step_id
                or item.step.step_id != item.request.step_id
            ):
                raise ValueError("step, attempt and request step_id must match")
            if item.step.run_id != item.attempt.run_id or item.step.run_id != item.request.run_id:
                raise ValueError("step, attempt and request run_id must match")
            if item.step.step_id in by_step:
                raise ValueError(f"duplicate step_id: {item.step.step_id}")
            by_step[item.step.step_id] = item
        return by_step

    @staticmethod
    def _state_by_step_id(steps: Sequence[Step]) -> dict[str, StepState]:
        states: dict[str, StepState] = {}
        for step in steps:
            if step.step_id in states:
                raise ValueError(f"duplicate step_id: {step.step_id}")
            states[step.step_id] = step.state
        return states

    @staticmethod
    def _required_dependency_ids(step: Step) -> tuple[str, ...]:
        return tuple(
            edge.upstream_step_id
            for edge in step.dependency_edges
            if edge.downstream_step_id == step.step_id and edge.required
        )

    @staticmethod
    def _require_handle(attempt: Attempt) -> ExecutionHandle:
        if attempt.execution_handle_ref is None:
            raise ValueError("attempt has no execution handle")
        return attempt.execution_handle_ref

    @staticmethod
    def _require_attempt_identity(attempt: Attempt, request: ExecutionRequest) -> None:
        if (
            attempt.attempt_id != request.attempt_id
            or attempt.run_id != request.run_id
            or attempt.step_id != request.step_id
        ):
            raise ValueError("attempt and execution request identity must match")


__all__ = ["DispatchPlan", "SerialExecutionItem", "SerialExecutionResult", "SerialRunner"]
