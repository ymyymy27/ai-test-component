from aitest.domain.execution.runs import (
    AdapterKind,
    AuthorizationRef,
    CapturedOutputBlock,
    ExecutionInspectionState,
    ExecutionRequest,
    OutputStreamName,
    PlanRevisionRef,
    RegisteredEntryRef,
    SideEffectClass,
)
from aitest.infrastructure.adapters.execution.fake import (
    FakeExecutionPort,
    FakeExecutionSpec,
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
            entrypoint="fake",
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
            plan_revision_ref=PlanRevisionRef(
                revision_id="plan-1",
                revision_no=1,
                digest="sha256:plan-1",
            ),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def _capture() -> CapturedOutputBlock:
    return CapturedOutputBlock(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_index=0,
        offset=0,
        content=b"fake-output",
        capture_source="fake",
    )


def test_fake_adapter_progresses_from_running_to_exited() -> None:
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec(
            attempt_id="attempt-1",
            run_id="run-1",
            step_id="step-1",
            captures=(_capture(),),
        )
    )
    handle = port.start(_request())
    assert port.inspect(handle).state is ExecutionInspectionState.RUNNING
    assert port.inspect(handle).state is ExecutionInspectionState.EXITED
    collected = port.collect(handle)
    assert collected.captured_blocks == (_capture(),)
    assert collected.exit_fact_ref is not None
    assert collected.exit_fact_ref.real_exit_code == 0


def test_fake_adapter_confirms_requested_stop() -> None:
    port = FakeExecutionPort()
    port.register(FakeExecutionSpec(attempt_id="attempt-1", run_id="run-1", step_id="step-1"))
    handle = port.start(_request())
    stopped = port.request_stop(handle)
    assert stopped.stop_confirmed is True
    assert port.inspect(handle).state is ExecutionInspectionState.STOPPED
