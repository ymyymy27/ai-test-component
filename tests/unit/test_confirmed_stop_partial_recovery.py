"""Confirmed original stop facts recover control without invented output/exit codes."""

import pytest

from aitest.domain.execution.runs import (
    CaptureCompleteness,
    CapturedOutputBlock,
    ExecutionInspectionState,
    OutputStreamName,
    StopRequestResult,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_persisted_execution_receipts import material


@pytest.mark.parametrize("prefix", [False, True])
def test_original_stop_fact_preserves_unknown_exit_code_and_partial_capture(tmp_path, prefix):
    store, handle, attempt, _, _ = material(tmp_path)
    store.save_stop(
        handle, StopRequestResult(handle.handle_id, True, ExecutionInspectionState.STOPPED)
    )
    spool = FileSpoolStore(tmp_path)
    if prefix:
        spool.persist_blocks(
            (
                CapturedOutputBlock(
                    attempt.run_id,
                    attempt.step_id,
                    attempt.attempt_id,
                    OutputStreamName.STDOUT,
                    0,
                    0,
                    b"kept-output",
                ),
            )
        )
    adapter = CommandAdapter(handle_store=store, spool_store=spool)
    result = adapter.collect(handle)
    assert result.exit_fact_ref is not None
    assert result.exit_fact_ref.real_exit_code is None
    assert result.exit_fact_ref.termination_reason.value == "confirmed_stop"
    assert result.exit_fact_ref.startup_token == store.load(handle.handle_id).startup_token
    assert result.exit_fact_ref.process_start_identity == handle.process_start_identity
    assert result.complete is False
    assert result.capture_completeness is (
        CaptureCompleteness.PARTIAL if prefix else CaptureCompleteness.GAP
    )
    if prefix:
        assert b"".join(spool.read_block(b) for b in result.output_blocks) == b"kept-output"


def test_without_original_stop_fact_no_exit_is_inferred(tmp_path):
    store, handle, _, _, _ = material(tmp_path)
    result = CommandAdapter(handle_store=store, spool_store=FileSpoolStore(tmp_path)).collect(
        handle
    )
    assert result.exit_fact_ref is None and result.complete is False
    assert result.capture_completeness is CaptureCompleteness.GAP
