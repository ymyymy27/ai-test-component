import sys
from pathlib import Path

from aitest.domain.execution.runs import (
    AdapterKind,
    AuthorizationRef,
    ExecutionInspectionState,
    ExecutionRequest,
    PlanRevisionRef,
    RegisteredEntryRef,
    SideEffectClass,
)
from aitest.infrastructure.adapters.execution.command import (
    CommandAdapter,
    CommandRegistration,
)
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore


def _request() -> ExecutionRequest:
    executable = str(Path(sys.executable).resolve())
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        registered_entry=RegisteredEntryRef(
            entry_id="python",
            adapter_kind=AdapterKind.COMMAND,
            entrypoint=executable,
            arguments=("-c", "import time; time.sleep(30)"),
        ),
        materialized_snapshot_ref=str(Path.cwd()),
        environment_ref="environment-1",
        source_binding_digest="sha256:source-1",
        authorization_ref=AuthorizationRef(
            authorization_id="authorization-1",
            intent_id="intent-1",
            step_id="step-1",
            resolved_input_digest="sha256:input-1",
            target_ref="target-1",
            credential_scope_ref="scope-1",
            plan_revision_ref=PlanRevisionRef(
                revision_id="plan-1",
                revision_no=1,
                digest="sha256:plan-1",
            ),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def test_persisted_command_handle_can_be_reattached_and_stopped(tmp_path: Path) -> None:
    handle_store = FileExecutionHandleStore(tmp_path)
    first = CommandAdapter(handle_store=handle_store)
    first.register(
        CommandRegistration(
            entry_id="python",
            executable=sys.executable,
            cwd=Path.cwd(),
        )
    )
    handle = first.start(_request())

    second = CommandAdapter(handle_store=handle_store)
    inspection = second.inspect(handle)
    assert inspection.state is ExecutionInspectionState.RUNNING
    assert inspection.identity_matches is True

    stopped = second.request_stop(handle)
    assert stopped.stop_confirmed is True
    assert stopped.observed_state is ExecutionInspectionState.STOPPED
