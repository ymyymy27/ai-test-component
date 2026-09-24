from pathlib import Path

import pytest

from aitest.application.execution.runner import SerialExecutionItem, SerialRunner
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    AuthorizationRef,
    CapturedOutputBlock,
    DependencyEdge,
    ExecutionRequest,
    OutputStreamName,
    PlanRevisionRef,
    RegisteredEntryRef,
    SideEffectClass,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
    StructuredExecutionError,
    TransportErrorClass,
)
from aitest.infrastructure.adapters.execution.fake import FakeExecutionPort, FakeExecutionSpec
from aitest.infrastructure.file_store.spool import FileSpoolStore


def _plan_revision() -> PlanRevisionRef:
    return PlanRevisionRef(revision_id="plan-1", revision_no=1, digest="sha256:plan-1")


def _step_revision(step_id: str) -> StepRevisionRef:
    return StepRevisionRef(
        step_revision_id=f"{step_id}-revision",
        revision_no=1,
        digest=f"sha256:{step_id}-revision",
    )


def _step(step_id: str, ordinal: int, dependency: str | None = None) -> Step:
    edges = (
        (DependencyEdge(upstream_step_id=dependency, downstream_step_id=step_id),)
        if dependency is not None
        else ()
    )
    return Step(
        step_id=step_id,
        run_id="run-1",
        ordinal=ordinal,
        case_id="case-1",
        level=StepLevel.L2,
        step_revision_ref=_step_revision(step_id),
        state=StepState.PENDING,
        dependency_edges=edges,
    )


def _attempt(step_id: str, attempt_id: str) -> Attempt:
    return Attempt(
        attempt_id=attempt_id,
        run_id="run-1",
        step_id=step_id,
        attempt_index=1,
        intent_id=f"intent:{attempt_id}",
        resolved_input_digest=f"sha256:input:{step_id}",
        step_revision_ref=_step_revision(step_id),
        source_binding_digest="sha256:source-1",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="fake/1.0",
    )


def _request(step_id: str, attempt_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id=step_id,
        attempt_id=attempt_id,
        intent_id=f"intent:{attempt_id}",
        resolved_input_digest=f"sha256:input:{step_id}",
        registered_entry=RegisteredEntryRef(
            entry_id=f"entry:{step_id}",
            adapter_kind=AdapterKind.COMMAND,
            entrypoint="fake",
        ),
        materialized_snapshot_ref="snapshot-1",
        environment_ref="environment-1",
        source_binding_digest="sha256:source-1",
        authorization_ref=AuthorizationRef(
            authorization_id=f"authorization:{attempt_id}",
            intent_id=f"intent:{attempt_id}",
            step_id=step_id,
            resolved_input_digest=f"sha256:input:{step_id}",
            target_ref="target-1",
            credential_scope_ref="credential-scope-1",
            plan_revision_ref=_plan_revision(),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def _capture(step_id: str, attempt_id: str, content: bytes) -> CapturedOutputBlock:
    return CapturedOutputBlock(
        run_id="run-1",
        step_id=step_id,
        attempt_id=attempt_id,
        stream_name=OutputStreamName.STDOUT,
        block_index=0,
        offset=0,
        content=content,
        capture_source="fake",
    )


def test_serial_execution_persists_spool_and_respects_dependencies(tmp_path: Path) -> None:
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec(
            attempt_id="attempt-1",
            run_id="run-1",
            step_id="step-1",
            captures=(_capture("step-1", "attempt-1", b"one"),),
        )
    )
    port.register(
        FakeExecutionSpec(
            attempt_id="attempt-2",
            run_id="run-1",
            step_id="step-2",
            captures=(_capture("step-2", "attempt-2", b"two"),),
        )
    )
    runner = SerialRunner(port, FileSpoolStore(tmp_path))
    result = runner.run_serial(
        (
            SerialExecutionItem(
                step=_step("step-1", 1),
                attempt=_attempt("step-1", "attempt-1"),
                request=_request("step-1", "attempt-1"),
            ),
            SerialExecutionItem(
                step=_step("step-2", 2, "step-1"),
                attempt=_attempt("step-2", "attempt-2"),
                request=_request("step-2", "attempt-2"),
            ),
        )
    )

    assert port.execution_order == ["attempt-1", "attempt-2"]
    assert [step.state for step in result.steps] == [StepState.COMPLETED, StepState.COMPLETED]
    assert [attempt.state for attempt in result.attempts] == [
        AttemptState.COMPLETED,
        AttemptState.COMPLETED,
    ]
    assert result.attempts[0].output_block_refs
    assert result.attempts[1].output_block_refs
    assert (tmp_path / "spool" / "attempt-1" / "manifest.json").exists()
    assert (tmp_path / "spool" / "attempt-2" / "manifest.json").exists()


def test_execution_error_blocks_dependent_step(tmp_path: Path) -> None:
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec(
            attempt_id="attempt-1",
            run_id="run-1",
            step_id="step-1",
            error_ref=StructuredExecutionError(
                error_id="error-1",
                error_class=TransportErrorClass.SCHEMA_ERROR,
                error_code="FAKE_EXECUTION_ERROR",
                message_code="fake.execution_error",
                safe_message="fake execution failed",
            ),
        )
    )
    runner = SerialRunner(port, FileSpoolStore(tmp_path))
    result = runner.run_serial(
        (
            SerialExecutionItem(
                step=_step("step-1", 1),
                attempt=_attempt("step-1", "attempt-1"),
                request=_request("step-1", "attempt-1"),
            ),
            SerialExecutionItem(
                step=_step("step-2", 2, "step-1"),
                attempt=_attempt("step-2", "attempt-2"),
                request=_request("step-2", "attempt-2"),
            ),
        )
    )

    assert result.attempts[0].state is AttemptState.EXECUTION_ERROR
    assert result.steps[0].state is StepState.EXECUTION_ERROR
    assert result.steps[1].state is StepState.BLOCKED


def test_serial_item_rejects_mismatched_identity(tmp_path: Path) -> None:
    runner = SerialRunner(FakeExecutionPort(), FileSpoolStore(tmp_path))
    with pytest.raises(ValueError, match="step_id"):
        runner.run_serial(
            (
                SerialExecutionItem(
                    step=_step("step-1", 1),
                    attempt=_attempt("step-2", "attempt-2"),
                    request=_request("step-2", "attempt-2"),
                ),
            )
        )
