"""Deterministic in-memory ExecutionPort used for local integration slices."""

from __future__ import annotations

from dataclasses import dataclass

from aitest.domain.execution.runs import (
    AdapterKind,
    CaptureCompleteness,
    CapturedOutputBlock,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExecutionRequest,
    ExitFact,
    OutputCursor,
    OutputStreamName,
    ProcessTerminationReason,
    StopRequestResult,
    StructuredExecutionError,
)


@dataclass(frozen=True, slots=True)
class FakeExecutionSpec:
    attempt_id: str
    run_id: str
    step_id: str
    captures: tuple[CapturedOutputBlock, ...] = ()
    running_observations_before_exit: int = 1
    exit_code: int = 0
    stop_exit_code: int = -1
    capture_completeness: CaptureCompleteness = CaptureCompleteness.COMPLETE
    error_ref: StructuredExecutionError | None = None

    def __post_init__(self) -> None:
        if not self.attempt_id.strip() or not self.run_id.strip() or not self.step_id.strip():
            raise ValueError("fake execution spec requires run, step and attempt identity")
        if self.running_observations_before_exit < 0:
            raise ValueError("running_observations_before_exit must be non-negative")
        if any(
            (block.run_id, block.step_id, block.attempt_id)
            != (self.run_id, self.step_id, self.attempt_id)
            for block in self.captures
        ):
            raise ValueError("captured block identity must match fake execution spec")


@dataclass(slots=True)
class _FakeRuntime:
    spec: FakeExecutionSpec
    handle: ExecutionHandle
    inspection_count: int = 0
    stop_requested: bool = False


class FakeExecutionPort:
    """ExecutionPort with deterministic inspect and collect transitions."""

    adapter_version = "fake/1.0"

    def __init__(self) -> None:
        self._specs: dict[str, FakeExecutionSpec] = {}
        self._runtimes: dict[str, _FakeRuntime] = {}
        self.execution_order: list[str] = []

    def register(self, spec: FakeExecutionSpec) -> None:
        if spec.attempt_id in self._specs:
            raise ValueError(f"fake execution already registered: {spec.attempt_id}")
        self._specs[spec.attempt_id] = spec

    def start(self, request: ExecutionRequest) -> ExecutionHandle:
        spec = self._require_spec(request)
        existing = self._runtimes.get(request.attempt_id)
        if existing is not None:
            return existing.handle
        handle = ExecutionHandle(
            handle_id=f"fake-handle:{request.attempt_id}",
            adapter_kind=AdapterKind.COMMAND,
            adapter_version=self.adapter_version,
            real_execution_id=f"fake-process:{request.attempt_id}",
            process_start_identity=f"fake-start:{request.attempt_id}",
            workdir_ref=request.materialized_snapshot_ref,
        )
        self._runtimes[request.attempt_id] = _FakeRuntime(spec=spec, handle=handle)
        self.execution_order.append(request.attempt_id)
        return handle

    def inspect(self, handle: ExecutionHandle) -> ExecutionInspectionResult:
        runtime = self._runtime_for_handle(handle)
        if runtime.stop_requested:
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.STOPPED,
                process_reachable=False,
                identity_matches=True,
                stop_confirmed=True,
            )
        runtime.inspection_count += 1
        state = self._observation_state(runtime)
        return ExecutionInspectionResult(
            handle_id=handle.handle_id,
            state=state,
            process_reachable=state is ExecutionInspectionState.RUNNING,
            identity_matches=True,
        )

    def collect(
        self,
        handle: ExecutionHandle,
        cursor: OutputCursor | None = None,
    ) -> ExecutionCollectionResult:
        runtime = self._runtime_for_handle(handle)
        state = self._observation_state(runtime)
        if state is ExecutionInspectionState.RUNNING:
            return ExecutionCollectionResult(
                attempt_id=runtime.spec.attempt_id,
                output_cursor_ref=cursor,
                capture_completeness=CaptureCompleteness.GAP,
                complete=False,
            )
        stopped = state is ExecutionInspectionState.STOPPED
        captures = runtime.spec.captures
        last_capture = captures[-1] if captures else None
        output_cursor = None
        if last_capture is not None:
            output_cursor = OutputCursor(
                attempt_id=runtime.spec.attempt_id,
                stream_name=last_capture.stream_name,
                offset=last_capture.offset + last_capture.length,
                last_block_index=last_capture.block_index,
                last_committed_digest=last_capture.digest,
                durable=True,
            )
        last_block_index_by_stream: tuple[tuple[OutputStreamName, int], ...] = ()
        saved_bytes_by_stream: tuple[tuple[OutputStreamName, int], ...] = ()
        if last_capture is not None:
            last_block_index_by_stream = ((last_capture.stream_name, last_capture.block_index),)
            saved_bytes_by_stream = ((last_capture.stream_name, last_capture.length),)
        exit_fact = ExitFact(
            attempt_id=runtime.spec.attempt_id,
            startup_token=runtime.handle.process_start_identity,
            process_start_identity=runtime.handle.process_start_identity,
            real_exit_code=(runtime.spec.stop_exit_code if stopped else runtime.spec.exit_code),
            last_block_index_by_stream=last_block_index_by_stream,
            saved_bytes_by_stream=saved_bytes_by_stream,
            capture_completeness=runtime.spec.capture_completeness,
            termination_reason=(
                ProcessTerminationReason.CONFIRMED_STOP
                if stopped
                else ProcessTerminationReason.NATURAL_EXIT
            ),
        )
        return ExecutionCollectionResult(
            attempt_id=runtime.spec.attempt_id,
            captured_blocks=captures,
            output_cursor_ref=output_cursor or cursor,
            exit_fact_ref=exit_fact,
            structured_result_ref=f"fake-result:{runtime.spec.attempt_id}",
            capture_completeness=runtime.spec.capture_completeness,
            error_ref=runtime.spec.error_ref,
            complete=True,
        )

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult:
        runtime = self._runtime_for_handle(handle)
        if runtime.stop_requested:
            return StopRequestResult(
                handle_id=handle.handle_id,
                stop_confirmed=True,
                observed_state=ExecutionInspectionState.STOPPED,
            )
        runtime.stop_requested = True
        return StopRequestResult(
            handle_id=handle.handle_id,
            stop_confirmed=True,
            observed_state=ExecutionInspectionState.STOPPED,
        )

    @staticmethod
    def _observation_state(runtime: _FakeRuntime) -> ExecutionInspectionState:
        if runtime.stop_requested:
            return ExecutionInspectionState.STOPPED
        if runtime.inspection_count > runtime.spec.running_observations_before_exit:
            return ExecutionInspectionState.EXITED
        return ExecutionInspectionState.RUNNING

    def _require_spec(self, request: ExecutionRequest) -> FakeExecutionSpec:
        spec = self._specs.get(request.attempt_id)
        if spec is None:
            raise KeyError(f"fake execution is not registered: {request.attempt_id}")
        if (spec.run_id, spec.step_id) != (request.run_id, request.step_id):
            raise ValueError("fake execution identity does not match request")
        return spec

    def _runtime_for_handle(self, handle: ExecutionHandle) -> _FakeRuntime:
        for runtime in self._runtimes.values():
            if runtime.handle == handle:
                return runtime
        raise KeyError(f"unknown fake execution handle: {handle.handle_id}")


__all__ = ["FakeExecutionPort", "FakeExecutionSpec"]
