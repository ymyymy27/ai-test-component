import time
from dataclasses import replace

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import AttemptState, CaptureCompleteness, OutputStreamName
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_command_adapter import _adapter, _request
from tests.unit.test_execution_observation_identity import observation


@pytest.mark.parametrize("reader_error", [False, True])
def test_live_capture_returns_material_before_process_exit(tmp_path, reader_error):
    spool = FileSpoolStore(tmp_path)
    port = _adapter(spool, block_size=32)
    script = (
        "import sys,time; print('alpha '*12000,flush=True); "
        "print('beta '*12000,file=sys.stderr,flush=True); time.sleep(300)"
    )
    handle = port.start(_request("python", ("-c", script), timeout_ms=10000))
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            manifest = spool.read_manifest("attempt-1")
            if {c.stream_name for c in manifest.cursors} == {
                OutputStreamName.STDOUT,
                OutputStreamName.STDERR,
            }:
                break
            time.sleep(0.01)
        assert manifest.blocks and len(manifest.cursors) == 2
        runtime = port._runtimes[handle.handle_id]
        # A delayed or wrong runtime cache must not override saved bytes.
        runtime.output_cursors = {
            OutputStreamName.STDOUT: replace(manifest.cursors[0], offset=999999)
        }
        if reader_error:
            runtime.read_errors.append("OSError")
        result = port.collect(handle)
        assert result.output_blocks
        assert result.output_cursors
        assert not result.complete and result.exit_fact_ref is None
        attempt, _, _ = observation()
        updated = SerialRunner(port, spool)._apply_collection(
            replace(attempt, execution_handle_ref=handle), port.inspect(handle), result
        )
        assert updated.state is AttemptState.RUNNING
        assert updated.capture_completeness is (
            CaptureCompleteness.GAP if reader_error else CaptureCompleteness.PARTIAL
        )
        assert updated.output_block_refs == result.output_blocks
        assert all(spool.read_block(ref) for ref in updated.output_block_refs)
    finally:
        port.request_stop(handle)
        port.collect(handle)


def test_live_memory_capture_does_not_echo_callers_persistent_position():
    from aitest.domain.execution.runs import OutputCursor

    port = _adapter()
    handle = port.start(
        _request("python", ("-c", "import time; time.sleep(300)"), timeout_ms=10000)
    )
    caller = OutputCursor("attempt-1", OutputStreamName.STDOUT, 999, 9, "sha256:fake", True)
    try:
        result = port.collect(handle, (caller,))
        assert not result.output_blocks and not result.output_cursors and not result.captured_blocks
        assert not result.complete and result.exit_fact_ref is None
        assert result.capture_completeness is CaptureCompleteness.GAP
    finally:
        port.request_stop(handle)
        port.collect(handle)


@pytest.mark.parametrize("mode", ["no_spool", "missing", "empty", "prefix"])
def test_recovered_collection_uses_saved_positions_and_never_echoes_the_caller(tmp_path, mode):
    from aitest.domain.execution.runs import CapturedOutputBlock, OutputCursor
    from aitest.infrastructure.adapters.execution.command import CommandAdapter
    from tests.unit.test_persisted_execution_receipts import material

    handles, handle, attempt, _, _ = material(tmp_path)
    spool = None if mode == "no_spool" else FileSpoolStore(tmp_path)
    if mode == "prefix":
        spool.persist_blocks(
            (
                CapturedOutputBlock(
                    attempt.run_id,
                    attempt.step_id,
                    attempt.attempt_id,
                    OutputStreamName.STDOUT,
                    0,
                    0,
                    b"kept",
                ),
            )
        )
    elif mode == "empty":
        spool.open_stream(
            run_id=attempt.run_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            stream_name=OutputStreamName.STDOUT,
        ).close()
    caller = OutputCursor(attempt.attempt_id, OutputStreamName.STDOUT, 999, 9, "sha256:fake", True)
    result = CommandAdapter(handle_store=handles, spool_store=spool).collect(handle, (caller,))
    assert not result.complete and result.exit_fact_ref is None
    if mode == "prefix":
        assert result.output_cursors == spool.read_manifest(attempt.attempt_id).cursors
        assert result.output_blocks == spool.read_manifest(attempt.attempt_id).blocks
    else:
        assert not result.output_blocks and not result.output_cursors
