import os
import subprocess
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
    ProcessTerminationReason,
    RegisteredEntryRef,
    SideEffectClass,
)
from aitest.infrastructure.adapters.execution.command import (
    CommandAdapter,
    CommandRegistration,
)
from aitest.infrastructure.file_store.spool import FileSpoolStore


def _request(
    entry_id: str,
    arguments: tuple[str, ...],
    *,
    timeout_ms: int | None = None,
) -> ExecutionRequest:
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
        timeout_ms=timeout_ms,
    )


def _adapter(spool_store: FileSpoolStore | None = None) -> CommandAdapter:
    adapter = CommandAdapter(
        lambda _scope: {"token": "secret-value"},
        spool_store=spool_store,
        stream_block_size=4,
        graceful_stop_timeout_seconds=0.5,
        force_kill_timeout_seconds=2,
    )
    adapter.register(
        CommandRegistration(
            entry_id="python",
            executable=sys.executable,
            cwd=Path.cwd(),
        )
    )
    return adapter


def _wait_for_terminal(adapter: CommandAdapter, handle: object) -> object:
    inspection = adapter.inspect(handle)  # type: ignore[arg-type]
    for _ in range(200):
        if inspection.state is not ExecutionInspectionState.RUNNING:
            return inspection
        time.sleep(0.01)
        inspection = adapter.inspect(handle)  # type: ignore[arg-type]
    return inspection


def test_command_adapter_captures_and_redacts_stdout_and_stderr() -> None:
    adapter = _adapter()
    script = (
        "import sys; "
        "print('token=' + 'secret-' + 'value'); "
        "print('Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234', file=sys.stderr)"
    )
    handle = adapter.start(_request("python", ("-c", script)))
    inspection = _wait_for_terminal(adapter, handle)
    assert inspection.state is ExecutionInspectionState.EXITED

    collected = adapter.collect(handle)
    assert collected.complete is True
    assert collected.exit_fact_ref is not None
    assert collected.exit_fact_ref.timed_out is False
    by_stream = {block.stream_name: block.content for block in collected.captured_blocks}
    assert b"secret-value" not in by_stream[OutputStreamName.STDOUT]
    assert b"secret-value" not in by_stream[OutputStreamName.STDERR]
    assert b"[REDACTED]" in by_stream[OutputStreamName.STDOUT]
    assert b"[REDACTED]" in by_stream[OutputStreamName.STDERR]
    assert {cursor.stream_name for cursor in collected.output_cursors} == {
        OutputStreamName.STDOUT,
        OutputStreamName.STDERR,
    }


def test_command_adapter_streams_verified_blocks_to_spool(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    adapter = _adapter(store)
    script = "import sys; print('stdout'); print('stderr', file=sys.stderr)"
    handle = adapter.start(_request("python", ("-c", script)))
    inspection = _wait_for_terminal(adapter, handle)
    assert inspection.state is ExecutionInspectionState.EXITED

    collected = adapter.collect(handle)
    manifest = store.read_manifest("attempt-1")
    assert collected.output_blocks == manifest.blocks
    assert {cursor.stream_name for cursor in manifest.cursors} == {
        OutputStreamName.STDOUT,
        OutputStreamName.STDERR,
    }
    assert (tmp_path / "spool" / "attempt-1" / "stdout.log").exists()
    assert (tmp_path / "spool" / "attempt-1" / "stderr.log").exists()


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


def test_command_adapter_timeout_kills_process_and_records_fact() -> None:
    adapter = _adapter()
    handle = adapter.start(_request("python", ("-c", "import time; time.sleep(30)"), timeout_ms=50))
    inspection = _wait_for_terminal(adapter, handle)
    assert inspection.state is ExecutionInspectionState.EXITED
    assert inspection.unknown_reason == "command_timeout"

    collected = adapter.collect(handle)
    assert collected.exit_fact_ref is not None
    assert collected.exit_fact_ref.timed_out is True
    assert collected.exit_fact_ref.termination_reason is ProcessTerminationReason.TIMEOUT
    assert collected.capture_completeness.value == "partial"


def test_command_adapter_reaps_child_process_group(tmp_path: Path) -> None:
    adapter = _adapter()
    script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "print(child.pid, flush=True); "
        "time.sleep(30)"
    )
    handle = adapter.start(_request("python", ("-c", script), timeout_ms=150))
    inspection = _wait_for_terminal(adapter, handle)
    assert inspection.state is ExecutionInspectionState.EXITED

    collected = adapter.collect(handle)
    stdout = b"".join(
        block.content
        for block in collected.captured_blocks
        if block.stream_name is OutputStreamName.STDOUT
    )
    child_pid = int(stdout.strip())
    assert _wait_until_not_alive(child_pid)


def _wait_until_not_alive(pid: int) -> bool:
    for _ in range(100):
        if not _process_is_alive(pid):
            return True
        time.sleep(0.02)
    return False


def _process_is_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
