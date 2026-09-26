import sys
import time
from pathlib import Path

import pytest

from aitest.domain.execution.runs import (
    AdapterKind,
    AuthorizationRef,
    ExecutionInspectionState,
    ExecutionRequest,
    OutputStreamName,
    PlanRevisionRef,
    RegisteredEntryRef,
    SideEffectClass,
)
from aitest.infrastructure.adapters.execution.command import (
    CommandAdapter,
    CommandRegistration,
)


def _request(entry_id: str, arguments: tuple[str, ...]) -> ExecutionRequest:
    executable = str(Path(sys.executable).resolve())
    return ExecutionRequest(
        project_id="project-1",
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        intent_id="intent-1",
        resolved_input_digest="sha256:input-1",
        registered_entry=RegisteredEntryRef(
            entry_id=entry_id,
            adapter_kind=AdapterKind.COMMAND,
            entrypoint=executable,
            arguments=arguments,
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
            credential_scope_ref="scope-1",
            plan_revision_ref=PlanRevisionRef(
                revision_id="plan-1",
                revision_no=1,
                digest="sha256:plan-1",
            ),
        ),
        side_effect_class=SideEffectClass.READ_ONLY,
    )


def _adapter() -> CommandAdapter:
    adapter = CommandAdapter(lambda _scope: {"token": "secret-value"})
    adapter.register(
        CommandRegistration(
            entry_id="python",
            executable=sys.executable,
            cwd=Path.cwd(),
        )
    )
    return adapter


def test_command_adapter_captures_and_redacts_stdout_and_stderr() -> None:
    adapter = _adapter()
    script = (
        "import sys; "
        "print('token=' + 'secret-' + 'value'); "
        "print('Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234', file=sys.stderr)"
    )
    handle = adapter.start(_request("python", ("-c", script)))

    for _ in range(100):
        inspection = adapter.inspect(handle)
        if inspection.state is not ExecutionInspectionState.RUNNING:
            break
        time.sleep(0.01)
    assert inspection.state is ExecutionInspectionState.EXITED

    collected = adapter.collect(handle)
    assert collected.complete is True
    assert collected.exit_fact_ref is not None
    assert collected.exit_fact_ref.real_exit_code == 0
    by_stream = {block.stream_name: block.content for block in collected.captured_blocks}
    assert b"secret-value" not in by_stream[OutputStreamName.STDOUT]
    assert b"secret-value" not in by_stream[OutputStreamName.STDERR]
    assert b"[REDACTED]" in by_stream[OutputStreamName.STDOUT]
    assert b"[REDACTED]" in by_stream[OutputStreamName.STDERR]


def test_command_adapter_rejects_resolved_secret_in_arguments() -> None:
    adapter = _adapter()
    request = _request("python", ("-c", "print('secret-value')"))
    with pytest.raises(ValueError, match="secret value"):
        adapter.start(request)


def test_command_adapter_stops_running_process() -> None:
    adapter = _adapter()
    handle = adapter.start(_request("python", ("-c", "import time; time.sleep(30)")))
    stopped = adapter.request_stop(handle)
    assert stopped.stop_confirmed is True
    assert stopped.observed_state is ExecutionInspectionState.STOPPED
