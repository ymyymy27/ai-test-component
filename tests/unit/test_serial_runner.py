import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    AuthorizationRef,
    CaptureCompleteness,
    DependencyEdge,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExecutionRequest,
    OutputCursor,
    PlanRevisionRef,
    RegisteredEntryRef,
    SideEffectClass,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
    StopRequestResult,
)


class FakeExecutionPort:
    def __init__(self) -> None:
        self.started: list[ExecutionRequest] = []
        self.stopped: list[ExecutionHandle] = []
        self.handle = ExecutionHandle(
            handle_id="handle-1",
            adapter_kind=AdapterKind.COMMAND,
            adapter_version="1.0",
            real_execution_id="proc-1",
            process_start_identity="start-1",
            workdir_ref="workspace-1",
        )

    def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self.started.append(request)
        return self.handle

    def inspect(self, handle: ExecutionHandle) -> ExecutionInspectionResult:
        return ExecutionInspectionResult(
            handle_id=handle.handle_id,
            state=ExecutionInspectionState.RUNNING,
            process_reachable=True,
            identity_matches=True,
        )

    def collect(
        self,
        handle: ExecutionHandle,
        cursor: OutputCursor | None = None,
    ) -> ExecutionCollectionResult:
        return ExecutionCollectionResult(
            attempt_id="attempt-1",
            output_cursors=(cursor,) if cursor else (),
            capture_completeness=CaptureCompleteness.PARTIAL,
        )

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult:
        self.stopped.append(handle)
        return StopRequestResult(
            handle_id=handle.handle_id,
            stop_confirmed=True,
            observed_state=ExecutionInspectionState.STOPPED,
        )


def _plan_revision() -> PlanRevisionRef:
    return PlanRevisionRef(
        revision_id="plan-1",
        revision_no=1,
        digest="sha256:plan-1",
    )


def _step_revision() -> StepRevisionRef:
    return StepRevisionRef(
        step_revision_id="step-revision-1",
        revision_no=1,
        digest="sha256:step-revision-1",
    )


def _attempt(state: AttemptState = AttemptState.INTENT_RECORDED) -> Attempt:
    return Attempt(
        attempt_id="attempt-1",
        run_id="run-1",
        step_id="step-1",
        attempt_index=1,
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        step_revision_ref=_step_revision(),
        source_binding_digest="sha256:source-1",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="1.0",
        state=state,
    )


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        registered_entry=RegisteredEntryRef(
            entry_id="entry-1",
            adapter_kind=AdapterKind.COMMAND,
            entrypoint="pytest",
        ),
        materialized_snapshot_ref="snapshot-1",
        environment_ref="environment-1",
        source_binding_digest="sha256:source-1",
        authorization_ref=AuthorizationRef(
            authorization_id="authorization-1",
            intent_id="intent-1",
            step_id="step-1",
            resolved_input_digest="sha256:input-1",
            target_ref="target-1",
            credential_scope_ref="credential-scope-1",
            plan_revision_ref=_plan_revision(),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def _step(step_id: str, state: StepState, dependencies: tuple[str, ...] = ()) -> Step:
    return Step(
        step_id=step_id,
        run_id="run-1",
        ordinal=int(step_id.removeprefix("step-")),
        case_id="case-1",
        level=StepLevel.L2,
        step_revision_ref=StepRevisionRef(
            step_revision_id=f"{step_id}-revision",
            revision_no=1,
            digest=f"sha256:{step_id}-revision",
        ),
        state=state,
        dependency_edges=tuple(
            DependencyEdge(upstream_step_id=upstream, downstream_step_id=step_id)
            for upstream in dependencies
        ),
    )


def test_start_attempt_records_real_handle() -> None:
    port = FakeExecutionPort()
    runner = SerialRunner(port)
    started = runner.start_attempt(_attempt(), _request())
    assert port.started == [_request()]
    assert started.state is AttemptState.RUNNING
    assert started.execution_handle_ref == port.handle


def test_runner_rejects_attempt_request_identity_mismatch() -> None:
    runner = SerialRunner(FakeExecutionPort())
    request = _request()
    mismatch = Attempt(
        attempt_id="attempt-2",
        run_id=request.run_id,
        step_id=request.step_id,
        attempt_index=1,
        resolved_input_digest=request.resolved_input_digest,
        step_revision_ref=_step_revision(),
        source_binding_digest=request.source_binding_digest,
        side_effect_class=request.side_effect_class,
        adapter_kind=request.registered_entry.adapter_kind,
        adapter_version="1.0",
    )
    with pytest.raises(ValueError, match="identity"):
        runner.start_attempt(mismatch, request)


def test_completed_dependency_makes_downstream_ready() -> None:
    plan = SerialRunner(FakeExecutionPort()).plan_dispatch(
        (
            _step("step-1", StepState.COMPLETED),
            _step("step-2", StepState.PENDING, ("step-1",)),
        )
    )
    assert plan.ready_step_ids == ("step-2",)
    assert plan.terminal_step_ids == ("step-1",)


def test_execution_error_blocks_only_dependents() -> None:
    plan = SerialRunner(FakeExecutionPort()).plan_dispatch(
        (
            _step("step-1", StepState.EXECUTION_ERROR),
            _step("step-2", StepState.PENDING, ("step-1",)),
            _step("step-3", StepState.PENDING),
        )
    )
    assert plan.blocked_step_ids == ("step-2",)
    assert plan.ready_step_ids == ("step-3",)
    assert plan.terminal_step_ids == ("step-1",)


def test_pending_verification_waits_instead_of_blocking() -> None:
    plan = SerialRunner(FakeExecutionPort()).plan_dispatch(
        (
            _step("step-1", StepState.PENDING_VERIFICATION),
            _step("step-2", StepState.PENDING, ("step-1",)),
        )
    )
    assert plan.waiting_step_ids == ("step-1", "step-2")
    assert not plan.blocked_step_ids


def test_missing_handle_cannot_be_inspected() -> None:
    runner = SerialRunner(FakeExecutionPort())
    with pytest.raises(ValueError, match="handle"):
        runner.inspect_attempt(_attempt())
