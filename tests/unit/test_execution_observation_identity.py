"""Do not attach another execution's terminal facts or bytes to this Attempt."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    CapturedOutputBlock,
    ExecutionCollectionResult,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExitFact,
    OutputCursor,
    OutputStreamName,
    ProcessTerminationReason,
)
from tests.unit.test_execution_control import _attempt


def observation():
    attempt = _attempt(AttemptState.RUNNING)
    inspection = ExecutionInspectionResult("handle-1", ExecutionInspectionState.EXITED, False, True)
    collection = ExecutionCollectionResult(
        attempt.attempt_id,
        exit_fact_ref=ExitFact(
            attempt.attempt_id,
            "startup-token",
            "start-1",
            0,
            capture_completeness=CaptureCompleteness.COMPLETE,
            termination_reason=ProcessTerminationReason.NATURAL_EXIT,
        ),
        capture_completeness=CaptureCompleteness.COMPLETE,
        complete=True,
    )
    return attempt, inspection, collection


@pytest.mark.parametrize(
    "change",
    ["handle", "identity", "attempt", "exit_attempt", "process", "cursor", "run", "step", "block"],
)
def test_foreign_observation_is_rejected_before_spool_or_completion(change):
    attempt, inspection, collection = observation()
    if change == "handle":
        inspection = replace(inspection, handle_id="foreign-handle")
    elif change == "identity":
        inspection = replace(inspection, identity_matches=False)
    elif change == "attempt":
        collection = replace(collection, attempt_id="foreign-attempt")
    elif change == "exit_attempt":
        collection = replace(
            collection,
            exit_fact_ref=replace(collection.exit_fact_ref, attempt_id="foreign-attempt"),
        )
    elif change == "process":
        collection = replace(
            collection,
            exit_fact_ref=replace(
                collection.exit_fact_ref, process_start_identity="another-process-start"
            ),
        )
    elif change == "cursor":
        collection = replace(
            collection,
            output_cursors=(
                OutputCursor(
                    "foreign-attempt", OutputStreamName.STDOUT, 0, 0, "sha256:foreign", True
                ),
            ),
        )
    else:
        block = CapturedOutputBlock(
            attempt.run_id,
            attempt.step_id,
            attempt.attempt_id,
            OutputStreamName.STDOUT,
            0,
            0,
            b"foreign",
        )
        field = {"run": "run_id", "step": "step_id", "block": "attempt_id"}[change]
        collection = replace(collection, captured_blocks=(replace(block, **{field: "foreign"}),))
    spool = Mock()
    runner = SerialRunner(Mock(), spool)
    with pytest.raises(ValueError, match="execution observation"):
        runner._apply_collection(attempt, inspection, collection)
    spool.persist_blocks.assert_not_called()


def test_verified_matching_terminal_fact_remains_usable():
    attempt, inspection, collection = observation()
    result = SerialRunner(Mock())._apply_collection(attempt, inspection, collection)
    assert result.state is AttemptState.COMPLETED
    assert result.exit_fact_ref == collection.exit_fact_ref


def test_unverified_running_identity_does_not_keep_polling_as_running():
    port = Mock()
    port.inspect.return_value = ExecutionInspectionResult(
        "foreign-handle", ExecutionInspectionState.RUNNING, True, True
    )
    result = SerialRunner(port).inspect_attempt(_attempt(AttemptState.RUNNING))
    assert result.handle_id == "handle-1"
    assert result.state is ExecutionInspectionState.UNKNOWN and not result.identity_matches


def test_unknown_exit_reason_cannot_be_completed():
    attempt, inspection, collection = observation()
    collection = replace(
        collection,
        exit_fact_ref=replace(
            collection.exit_fact_ref, termination_reason=ProcessTerminationReason.UNKNOWN
        ),
    )
    result = SerialRunner(Mock())._apply_collection(attempt, inspection, collection)
    assert result.state is AttemptState.PENDING_VERIFICATION


@pytest.mark.parametrize("change", ["inspection", "collection"])
def test_bad_adapter_observation_is_saved_as_pending_without_restarting(tmp_path, change):
    from aitest.infrastructure.file_store.spool import FileSpoolStore
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
    from tests.support.execution_authority import fixture_coordinator
    from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
    from tests.unit.test_serial_runner import _attempt, _request

    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=0)
    )
    if change == "inspection":
        original = port.inspect
        port.inspect = lambda handle: replace(original(handle), handle_id="foreign-handle")
    else:
        original = port.collect
        port.collect = lambda handle, cursors=None: replace(
            original(handle, cursors), attempt_id="foreign-attempt"
        )
    unit = FileUnitOfWork(tmp_path)
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    runner = SerialRunner(port, FileSpoolStore(tmp_path), commit_coordinator=coordinator)
    for _ in range(2):
        result = runner.execute_attempt(_attempt(), _request())
        assert result.state is AttemptState.PENDING_VERIFICATION
        assert result.capture_completeness is CaptureCompleteness.GAP
        assert result.exit_fact_ref is None and not result.output_block_refs
        assert (
            coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-1").attempt
            == result
        )
    assert port.execution_order == ["attempt-1"]
    facts = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    assert "attempt-1" not in facts.coverage.executed_attempt_ids
