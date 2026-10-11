from dataclasses import replace

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    ExecutionInspectionState,
    ProcessTerminationReason,
)
from tests.unit.test_execution_observation_identity import observation


@pytest.mark.parametrize("kind", ["natural", "stopped", "reader_error"])
def test_collection_does_not_revive_invalidated_history(kind):
    attempt, inspection, collection = observation()
    attempt = replace(attempt, state=AttemptState.INVALIDATED)
    if kind == "stopped":
        inspection = replace(
            inspection, state=ExecutionInspectionState.STOPPED, stop_confirmed=True
        )
        collection = replace(
            collection,
            exit_fact_ref=replace(
                collection.exit_fact_ref,
                termination_reason=ProcessTerminationReason.CONFIRMED_STOP,
                real_exit_code=None,
            ),
        )
    elif kind == "reader_error":
        inspection = replace(
            inspection, state=ExecutionInspectionState.RUNNING, process_reachable=True
        )
        collection = replace(
            collection,
            exit_fact_ref=None,
            error_ref="reader_failure",
            complete=False,
            capture_completeness=CaptureCompleteness.GAP,
        )
    result = SerialRunner(None)._apply_collection(attempt, inspection, collection)
    assert result.state is AttemptState.INVALIDATED
    assert result.exit_fact_ref == collection.exit_fact_ref
    assert result.error_ref == collection.error_ref


def test_original_execution_saves_late_exit_without_restoring_invalidated_basis(tmp_path):
    from aitest.infrastructure.file_store.spool import FileSpoolStore
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
    from tests.support.execution_authority import fixture_coordinator
    from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
    from tests.unit.test_serial_runner import _attempt, _request

    unit = FileUnitOfWork(tmp_path)
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=0)
    )
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    runner = SerialRunner(port, FileSpoolStore(tmp_path), commit_coordinator=coordinator)
    started = runner.start_attempt(_attempt(), _request())
    invalidated = replace(started, state=AttemptState.INVALIDATED)
    coordinator.commit_checkpoint(
        project_id="project-1",
        checkpoint=runner._checkpoint_record(invalidated, stage="invalidated"),
    )
    result = runner.execute_attempt(invalidated, _request(), max_polls=1)
    assert result.state is AttemptState.INVALIDATED
    assert result.exit_fact_ref is not None and result.exit_fact_ref.real_exit_code == 0
    assert (
        coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-1").attempt
        == result
    )
    assert port.execution_order == ["attempt-1"]
    current = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    assert current.attempts[0].state.value == "invalidated"
    assert not current.coverage.executed_attempt_ids
    assert current.verifications == ()
