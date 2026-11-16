from dataclasses import replace

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import AttemptState
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import fixture_coordinator
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_serial_runner import _attempt, _request


@pytest.mark.parametrize("entry", ["execute", "observe"])
def test_invalidated_observation_mismatch_saves_gap_without_reviving_or_restarting(tmp_path, entry):
    unit = FileUnitOfWork(tmp_path)
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=0)
    )
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    runner = SerialRunner(port, FileSpoolStore(tmp_path), commit_coordinator=coordinator)
    started = runner.start_attempt(_attempt(), _request())
    old = replace(started, state=AttemptState.INVALIDATED)
    coordinator.commit_checkpoint(
        project_id="project-1", checkpoint=runner._checkpoint_record(old, stage="invalidated")
    )
    collect = port.collect
    port.collect = lambda handle, cursors=None: replace(
        collect(handle, cursors), attempt_id="foreign"
    )
    result = (
        runner.execute_attempt(old, _request(), max_polls=1)
        if entry == "execute"
        else runner.observe_saved_attempt(old, project_id="project-1")
    )
    assert result.state is AttemptState.INVALIDATED
    assert result.capture_completeness.value == "gap"
    assert result.unknown_reason_ref == "collection_identity_unverified"
    assert result.exit_fact_ref is None and not result.output_block_refs
    assert (
        coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-1").attempt
        == result
    )
    assert port.execution_order == ["attempt-1"]
