from dataclasses import replace

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.recovery import CaseReuseBasis, RecoveryRecord
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
    RecoveryCheckpoint,
    RegisteredEntryRef,
    SideEffectClass,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
    StopRequestResult,
)
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
from tests.support.fake_execution import (
    FakeExecutionPort as DeterministicFakeExecutionPort,
)
from tests.support.fake_execution import FakeExecutionSpec


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
        cursors: tuple[OutputCursor, ...] | None = None,
    ) -> ExecutionCollectionResult:
        return ExecutionCollectionResult(
            attempt_id="attempt-1",
            output_cursors=cursors or (),
            capture_completeness=CaptureCompleteness.PARTIAL,
        )

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult:
        self.stopped.append(handle)
        return StopRequestResult(
            handle_id=handle.handle_id,
            stop_confirmed=True,
            observed_state=ExecutionInspectionState.STOPPED,
        )


class _RecordingUnitOfWork:
    def __init__(self) -> None:
        self.staged: list[tuple[str, str, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0

    def open(self, project_id: str) -> None:
        return None

    def begin(self, request_id: str, project_id: str) -> object:
        return {"request_id": request_id, "project_id": project_id}

    def stage_record(
        self,
        *,
        aggregate_kind: str,
        record_id: str,
        expected_revision: int | None,
        payload: dict[str, object],
    ) -> str:
        self.staged.append((aggregate_kind, record_id, payload))
        return f"{aggregate_kind}:{record_id}:{expected_revision}"

    def commit(self) -> str:
        self.commits += 1
        return "commit-1"

    def rollback(self) -> None:
        self.rollbacks += 1


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


def test_execute_attempt_persists_intent_checkpoint_before_start(tmp_path) -> None:
    port = DeterministicFakeExecutionPort()
    port.register(
        FakeExecutionSpec(
            attempt_id="attempt-1",
            run_id="run-1",
            step_id="step-1",
            running_observations_before_exit=0,
        )
    )
    checkpoint_path = tmp_path / "checkpoints" / "attempt-1.json"
    port_started_after_checkpoint = False

    original_start = port.start

    def start(request: ExecutionRequest) -> ExecutionHandle:
        nonlocal port_started_after_checkpoint
        port_started_after_checkpoint = checkpoint_path.exists()
        return original_start(request)

    port.start = start  # type: ignore[method-assign]
    runner = SerialRunner(
        port,
        checkpoint_store=FileCheckpointStore(tmp_path),
    )

    result = runner.execute_attempt(_attempt(), _request())

    assert result.state is AttemptState.COMPLETED
    assert port_started_after_checkpoint is True


def test_execute_attempt_rejects_mismatched_authorization_before_start() -> None:
    port = FakeExecutionPort()
    runner = SerialRunner(port)
    request = _request()
    request = replace(
        request,
        authorization_ref=replace(
            request.authorization_ref,
            resolved_input_digest="sha256:other-input",
        ),
    )

    with pytest.raises(ValueError, match="authorization input digest"):
        runner.execute_attempt(_attempt(), request)

    assert port.started == []


def test_execute_attempt_rejects_frozen_input_mismatch_before_start() -> None:
    port = FakeExecutionPort()
    runner = SerialRunner(port)
    mismatch = replace(
        _attempt(),
        resolved_input_digest="sha256:other-input",
    )

    with pytest.raises(ValueError, match="resolved input digest"):
        runner.execute_attempt(mismatch, _request())

    assert port.started == []


def test_repeated_same_intent_does_not_start_second_execution(tmp_path) -> None:
    port = DeterministicFakeExecutionPort()
    port.register(
        FakeExecutionSpec(
            attempt_id="attempt-1",
            run_id="run-1",
            step_id="step-1",
            running_observations_before_exit=0,
        )
    )
    runner = SerialRunner(
        port,
        checkpoint_store=FileCheckpointStore(tmp_path),
    )

    first = runner.execute_attempt(_attempt(), _request())
    second = runner.execute_attempt(_attempt(), _request())

    assert first.state is AttemptState.COMPLETED
    assert second.state is AttemptState.COMPLETED
    assert port.execution_order == ["attempt-1"]


def test_start_intent_without_confirmed_handle_stays_pending(tmp_path) -> None:
    store = FileCheckpointStore(tmp_path)
    attempt = _attempt()
    store.persist(
        RecoveryRecord(
            checkpoint=RecoveryCheckpoint(
                run_id=attempt.run_id,
                step_id=attempt.step_id,
                attempt_id=attempt.attempt_id,
                last_committed_stage="intent_recorded",
            ),
            attempt=attempt,
        )
    )
    port = DeterministicFakeExecutionPort()
    runner = SerialRunner(port, checkpoint_store=store)

    result = runner.execute_attempt(attempt, _request())

    assert result.state is AttemptState.PENDING_VERIFICATION
    assert result.unknown_reason_ref == "start_intent_without_confirmed_handle"
    assert port.execution_order == []


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


def test_start_intent_is_committed_to_uow_before_execution_port_start() -> None:
    port = DeterministicFakeExecutionPort()
    port.register(
        FakeExecutionSpec(
            attempt_id="attempt-1",
            run_id="run-1",
            step_id="step-1",
            running_observations_before_exit=0,
        )
    )
    unit = _RecordingUnitOfWork()
    coordinator = ExecutionCommitCoordinator(unit)
    commits_at_start: list[int] = []
    original_start = port.start

    def start(request: ExecutionRequest) -> ExecutionHandle:
        commits_at_start.append(unit.commits)
        return original_start(request)

    port.start = start  # type: ignore[method-assign]
    runner = SerialRunner(port, commit_coordinator=coordinator)

    result = runner.execute_attempt(_attempt(), _request())

    assert result.state is AttemptState.COMPLETED
    assert commits_at_start and commits_at_start[0] >= 1
    assert any(kind == "execution_checkpoint" for kind, _, _ in unit.staged)


def test_new_attempt_revokes_old_case_reuse_basis_in_runner_path() -> None:
    port = DeterministicFakeExecutionPort()
    port.register(
        FakeExecutionSpec(
            attempt_id="attempt-1",
            run_id="run-1",
            step_id="step-1",
            running_observations_before_exit=0,
        )
    )
    runner = SerialRunner(
        port,
        reuse_bases=(
            CaseReuseBasis(
                case_id="case-1",
                source_attempt_ids=("old-attempt-1",),
            ),
        ),
        previous_attempt_ids_by_step={"step-1": ("old-attempt-1",)},
    )

    runner.execute_attempt(_attempt(), _request())
    runner.execute_attempt(_attempt(), _request())

    assert len(runner.reuse_invalidations) == 1
    assert runner.reuse_invalidations[0].case_id == "case-1"
