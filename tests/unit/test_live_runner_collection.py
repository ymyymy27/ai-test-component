from dataclasses import replace

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    CaptureCompleteness,
    CapturedOutputBlock,
    ExecutionCollectionResult,
    OutputStreamName,
)
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import fixture_coordinator
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_serial_runner import _attempt, _request


@pytest.mark.parametrize("invalid", [False, True])
def test_running_collection_is_saved_and_invalid_progress_does_not_revert_to_running(
    tmp_path, invalid
):
    unit = FileUnitOfWork(tmp_path)
    spool = FileSpoolStore(tmp_path)
    manifest = spool.persist_blocks(
        (
            CapturedOutputBlock(
                "run-1", "step-1", "attempt-1", OutputStreamName.STDOUT, 0, 0, b"prefix"
            ),
        )
    )
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=100000)
    )

    def collect(handle, cursors=None):
        current = spool.read_manifest("attempt-1")
        return ExecutionCollectionResult(
            "attempt-1",
            output_blocks=current.blocks,
            output_cursors=(replace(current.cursors[0], offset=99),)
            if invalid
            else current.cursors,
            capture_completeness=CaptureCompleteness.PARTIAL,
            complete=False,
        )

    port.collect = collect
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    runner = SerialRunner(port, spool, commit_coordinator=coordinator)
    result = runner.execute_attempt(_attempt(), _request(), max_polls=1)
    if invalid:
        assert result.state.value == "pending_verification"
        assert result.unknown_reason_ref == "output_cursor_material_unverified"
    else:
        assert result.output_block_refs == manifest.blocks
        assert result.state.value == "running" and result.exit_fact_ref is None
        spool.persist_blocks(
            (
                CapturedOutputBlock(
                    "run-1", "step-1", "attempt-1", OutputStreamName.STDOUT, 1, 6, b"tail"
                ),
            )
        )
        updated = runner.observe_saved_attempt(result, project_id="project-1")
        assert len(updated.output_block_refs) == 2 and updated.output_cursors[0].offset == 10
        assert (
            coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-1").attempt
            == updated
        )
    assert port.execution_order == ["attempt-1"]
    assert not coordinator.read_current_facts(
        project_id="project-1", run_id="run-1"
    ).coverage.executed_attempt_ids
