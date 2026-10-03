"""Serial execution loop and dependency dispatch skeleton."""

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from aitest.application.execution.recovery import (
    CaseReuseBasis,
    CaseReuseInvalidation,
    CheckpointStore,
    RecoveryRecord,
    RecoveryResult,
    invalidate_downstream_attempts,
    invalidate_reuse_bases,
    recover_attempt,
)
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
    PlanRevisionRef,
    RecoveryCheckpoint,
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


class StartValidationPort(Protocol):
    """Optional boundary for authorization/input/source checks owned by a caller."""

    def validate(self, attempt: Attempt, request: ExecutionRequest) -> None: ...


class SerialRunner:
    """Execute ready steps serially and persist sealed captured blocks."""

    def __init__(
        self,
        execution_port: ExecutionPort,
        spool_store: SpoolStore | None = None,
        *,
        checkpoint_store: CheckpointStore | None = None,
        poll_interval_seconds: float = 0.01,
        start_validator: StartValidationPort | None = None,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be non-negative")
        self._execution_port = execution_port
        self._spool_store = spool_store
        self._checkpoint_store = checkpoint_store
        self._poll_interval_seconds = poll_interval_seconds
        self._start_validator = start_validator
        self._intent_claims: dict[str, Attempt] = {}

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
        max_polls: int | None = None,
    ) -> Attempt:
        prepared = self._prepare_attempt(attempt, request)
        existing = self._find_intent_claim(request.intent_id)
        if existing is not None:
            if existing.attempt_id != prepared.attempt_id:
                raise ValueError("intent_id is already claimed by another attempt")
            if existing.execution_handle_ref is not None:
                return existing
            return replace(
                existing,
                state=AttemptState.PENDING_VERIFICATION,
                unknown_reason_ref="start_intent_without_confirmed_handle",
            )

        self._validate_start(prepared, request)
        self._intent_claims[request.intent_id] = prepared
        self._persist_checkpoint(prepared, stage="intent_recorded")
        current = self.start_attempt(prepared, request)
        self._intent_claims[request.intent_id] = current
        self._persist_checkpoint(current, stage="started")
        polls = 0
        while max_polls is None or polls < max_polls:
            polls += 1
            inspection = self.inspect_attempt(current)
            if inspection.state is ExecutionInspectionState.RUNNING:
                if self._checkpoint_store is not None and polls % 100 == 0:
                    self._persist_checkpoint(current, stage="running")
                if self._poll_interval_seconds:
                    time.sleep(self._poll_interval_seconds)
                continue
            collection = self.collect_attempt(current, current.output_cursors or None)
            completed = self._apply_collection(current, inspection, collection)
            self._persist_checkpoint(completed, stage=completed.state.value)
            self._intent_claims[request.intent_id] = completed
            return completed
        pending = replace(
            current,
            state=AttemptState.PENDING_VERIFICATION,
            unknown_reason_ref="poll_limit_reached",
        )
        self._persist_checkpoint(pending, stage="poll_limit_reached")
        self._intent_claims[request.intent_id] = pending
        return pending

    def _prepare_attempt(self, attempt: Attempt, request: ExecutionRequest) -> Attempt:
        self._require_attempt_identity(attempt, request)
        if attempt.intent_id and attempt.intent_id != request.intent_id:
            raise ValueError("attempt intent_id does not match execution request")
        if attempt.resolved_input_digest != request.resolved_input_digest:
            raise ValueError("attempt resolved input digest does not match request")
        if attempt.source_binding_digest != request.source_binding_digest:
            raise ValueError("attempt source binding digest does not match request")
        if (
            attempt.authorization_ref is not None
            and attempt.authorization_ref != request.authorization_ref
        ):
            raise ValueError("attempt authorization does not match execution request")
        if (
            attempt.expected_plan_revision_ref is not None
            and request.expected_plan_revision_ref is not None
            and attempt.expected_plan_revision_ref != request.expected_plan_revision_ref
        ):
            raise ValueError("attempt expected plan revision does not match request")
        return replace(
            attempt,
            intent_id=request.intent_id,
            resolved_input_digest=request.resolved_input_digest,
            source_binding_digest=request.source_binding_digest,
            authorization_ref=request.authorization_ref,
            expected_plan_revision_ref=(
                request.expected_plan_revision_ref
                if request.expected_plan_revision_ref is not None
                else attempt.expected_plan_revision_ref
            ),
        )

    def _validate_start(self, attempt: Attempt, request: ExecutionRequest) -> None:
        authorization = request.authorization_ref
        if attempt.intent_id and attempt.intent_id != request.intent_id:
            raise ValueError("attempt intent_id does not match request")
        if authorization.intent_id != request.intent_id:
            raise ValueError("authorization intent_id does not match request")
        if authorization.step_id != request.step_id:
            raise ValueError("authorization step_id does not match request")
        if authorization.resolved_input_digest != request.resolved_input_digest:
            raise ValueError("authorization input digest does not match request")
        if (
            authorization.consumed_by_attempt_id is not None
            and authorization.consumed_by_attempt_id != request.attempt_id
        ):
            raise ValueError("authorization is already consumed by another attempt")
        expected_plan_revision = (
            request.expected_plan_revision_ref or attempt.expected_plan_revision_ref
        )
        if (
            expected_plan_revision is not None
            and authorization.plan_revision_ref != expected_plan_revision
        ):
            raise ValueError("authorization plan revision does not match frozen plan")
        if (
            attempt.expected_plan_revision_ref is not None
            and request.expected_plan_revision_ref is not None
            and attempt.expected_plan_revision_ref != request.expected_plan_revision_ref
        ):
            raise ValueError("attempt expected plan revision does not match request")
        if attempt.resolved_input_digest != request.resolved_input_digest:
            raise ValueError("attempt resolved input digest does not match request")
        if attempt.source_binding_digest != request.source_binding_digest:
            raise ValueError("attempt source binding digest does not match request")
        if self._start_validator is not None:
            self._start_validator.validate(attempt, request)

    def _find_intent_claim(self, intent_id: str) -> Attempt | None:
        claims: list[Attempt] = []
        in_memory = self._intent_claims.get(intent_id)
        if in_memory is not None:
            claims.append(in_memory)
        if self._checkpoint_store is not None:
            claims.extend(
                record.attempt
                for record in self._checkpoint_store.scan()
                if record.attempt.intent_id == intent_id
            )
        unique = {claim.attempt_id: claim for claim in claims}
        if len(unique) > 1:
            raise ValueError("intent_id has multiple persisted attempts")
        return next(iter(unique.values()), None)

    def start_attempt(self, attempt: Attempt, request: ExecutionRequest) -> Attempt:
        self._require_attempt_identity(attempt, request)
        self._validate_start(attempt, request)
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
        cursors: tuple[OutputCursor, ...] | None = None,
    ) -> ExecutionCollectionResult:
        return self._execution_port.collect(self._require_handle(attempt), cursors)

    def request_stop(self, attempt: Attempt) -> StopRequestResult:
        return self._execution_port.request_stop(self._require_handle(attempt))

    def recover_pending(self) -> tuple[RecoveryResult, ...]:
        if self._checkpoint_store is None or self._spool_store is None:
            return ()
        results: list[RecoveryResult] = []
        for record in self._checkpoint_store.scan():
            reliable_terminal = (
                record.attempt.state in {AttemptState.COMPLETED, AttemptState.CANCELLED}
                and record.attempt.exit_fact_ref is not None
            )
            inspection = None
            if not reliable_terminal and record.attempt.execution_handle_ref is not None:
                try:
                    inspection = self.inspect_attempt(record.attempt)
                except (KeyError, ValueError):
                    inspection = None
            result = recover_attempt(
                record.checkpoint,
                record.attempt,
                self._spool_store,
                inspection=inspection,
            )
            results.append(result)
            self._persist_checkpoint(result.attempt, stage=result.action.value)
        return tuple(results)

    def invalidate_dependencies(
        self,
        attempts: Sequence[Attempt],
        *,
        previous_plan_revision: PlanRevisionRef,
        current_plan_revision: PlanRevisionRef,
        affected_upstream_attempt_ids: Sequence[str],
    ) -> tuple[Attempt, ...]:
        invalidations = invalidate_downstream_attempts(
            attempts,
            previous_plan_revision=previous_plan_revision,
            current_plan_revision=current_plan_revision,
            affected_upstream_attempt_ids=affected_upstream_attempt_ids,
        )
        by_id = {item.attempt.attempt_id: item.attempt for item in invalidations}
        for attempt in by_id.values():
            self._persist_checkpoint(attempt, stage=attempt.state.value)
        return tuple(by_id.get(attempt.attempt_id, attempt) for attempt in attempts)

    def invalidate_reuse(
        self,
        bases: Sequence[CaseReuseBasis],
        *,
        affected_upstream_attempt_ids: Sequence[str],
    ) -> tuple[CaseReuseInvalidation, ...]:
        return invalidate_reuse_bases(
            bases,
            affected_upstream_attempt_ids=affected_upstream_attempt_ids,
        )

    def _persist_checkpoint(self, attempt: Attempt, *, stage: str) -> None:
        if self._checkpoint_store is None:
            return
        checkpoint = RecoveryCheckpoint(
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            last_committed_stage=stage,
            output_cursors=attempt.output_cursors,
            output_block_refs=attempt.output_block_refs,
            resolved_input_digest=attempt.resolved_input_digest,
            side_effect_class=attempt.side_effect_class,
            execution_handle_ref=attempt.execution_handle_ref,
        )
        self._checkpoint_store.persist(RecoveryRecord(checkpoint=checkpoint, attempt=attempt))

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
            output_cursors=self._merge_output_cursors(
                attempt.output_cursors,
                collection.output_cursors,
            ),
            structured_result_ref=collection.structured_result_ref,
            exit_fact_ref=collection.exit_fact_ref,
            capture_completeness=capture_completeness,
            timed_out=bool(collection.exit_fact_ref and collection.exit_fact_ref.timed_out),
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
            if collection.exit_fact_ref.timed_out:
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
        if collection.exit_fact_ref is not None and collection.exit_fact_ref.timed_out:
            return "command_timeout"
        if collection.exit_fact_ref is None:
            return "exit_fact_unavailable"
        return "verification_inconclusive"

    @staticmethod
    def _merge_output_cursors(
        existing: tuple[OutputCursor, ...],
        incoming: tuple[OutputCursor, ...],
    ) -> tuple[OutputCursor, ...]:
        merged = {cursor.stream_name: cursor for cursor in existing}
        for cursor in incoming:
            merged[cursor.stream_name] = cursor
        return tuple(merged[stream] for stream in sorted(merged, key=lambda item: item.value))

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


__all__ = [
    "DispatchPlan",
    "SerialExecutionItem",
    "SerialExecutionResult",
    "SerialRunner",
    "StartValidationPort",
]
