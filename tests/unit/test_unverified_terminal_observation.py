from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    StructuredExecutionError,
    TransportErrorClass,
)
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import fixture_coordinator
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_execution_authorization_origin import resolved as resolved
from tests.unit.test_serial_runner import _attempt, _request


@pytest.mark.parametrize(
    "state", [AttemptState.COMPLETED, AttemptState.CANCELLED, AttemptState.EXECUTION_ERROR]
)
def test_saved_terminal_label_without_exit_still_observes_original_process(tmp_path, state):
    unit = FileUnitOfWork(tmp_path)
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=0)
    )
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    runner = SerialRunner(port, FileSpoolStore(tmp_path), commit_coordinator=coordinator)
    started = runner.start_attempt(_attempt(), _request())
    old = replace(started, state=state, capture_completeness=CaptureCompleteness.GAP)
    coordinator.commit_checkpoint(
        project_id="project-1", checkpoint=runner._checkpoint_record(old, stage=state.value)
    )
    fresh_runner = SerialRunner(port, FileSpoolStore(tmp_path), commit_coordinator=coordinator)
    result = fresh_runner.execute_attempt(old, _request(), max_polls=1)
    assert result.exit_fact_ref is not None and result.exit_fact_ref.real_exit_code == 0
    assert result.state is AttemptState.COMPLETED
    assert (
        coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-1").attempt
        == result
    )
    assert port.execution_order == ["attempt-1"]


@pytest.mark.parametrize(
    "state", [AttemptState.COMPLETED, AttemptState.CANCELLED, AttemptState.EXECUTION_ERROR]
)
def test_running_observation_does_not_preserve_unverified_terminal_label(state):
    from tests.unit.test_execution_observation_identity import observation

    attempt, inspection, collection = observation()
    from aitest.domain.execution.runs import ExecutionInspectionState

    attempt = replace(attempt, state=state, exit_fact_ref=None)
    result = SerialRunner(Mock())._apply_collection(
        attempt,
        replace(inspection, state=ExecutionInspectionState.RUNNING, process_reachable=True),
        replace(
            collection,
            exit_fact_ref=None,
            complete=False,
            capture_completeness=CaptureCompleteness.GAP,
        ),
    )
    assert result.state is AttemptState.PENDING_VERIFICATION
    assert result.unknown_reason_ref == "exit_fact_unavailable"
    assert result.exit_fact_ref is None


@pytest.mark.parametrize(
    "state",
    [
        AttemptState.COMPLETED,
        AttemptState.CANCELLED,
        AttemptState.EXECUTION_ERROR,
        AttemptState.INVALIDATED,
    ],
)
def test_serial_progress_requires_actual_termination_even_for_terminal_labels(state):
    from tests.unit.test_execution_observation_identity import observation

    attempt, _, collection = observation()
    assert SerialRunner._blocks_serial_progress(replace(attempt, state=state, exit_fact_ref=None))
    assert not SerialRunner._blocks_serial_progress(
        replace(attempt, state=state, exit_fact_ref=collection.exit_fact_ref)
    )


def test_default_error_without_exit_continues_original_collection_and_keeps_gap(resolved):
    from tests.unit.test_default_execution_authorization import RELAY
    from tests.unit.test_default_step_execution import execution_command
    from tests.unit.test_execution_authorization_origin import review, save

    core, inputs, _, service, parameters, action = resolved
    actor, challenge = review(service, inputs.project_id, action, parameters)
    save(service, inputs.project_id, action, parameters, actor, challenge)
    error = StructuredExecutionError(
        "capture-error",
        TransportErrorClass.UNKNOWN,
        "READ_FAILED",
        "capture_failed",
        "采集读取失败",
    )
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec(
            action.attempt.attempt_id,
            action.request.run_id,
            action.request.step_id,
            running_observations_before_exit=1,
        )
    )
    original_collect = port.collect

    def collect(handle, cursors=None):
        return replace(
            original_collect(handle, cursors),
            error_ref=error,
            capture_completeness=CaptureCompleteness.GAP,
            complete=False,
        )

    port.collect = collect
    core.step_execution.execution_port = port
    value = execution_command(inputs.project_id, action, parameters)
    first = core.api.dispatch(value, RELAY)
    assert first.error is None, first.error
    assert first.result["attempt"]["state"] == "execution_error"
    assert first.result["attempt"]["exit_fact"] is None
    second = core.api.dispatch(value.model_copy(update={"request_id": "collect-error-exit"}), RELAY)
    assert second.error is None, second.error
    attempt = second.result["attempt"]
    assert attempt["state"] == "execution_error" and attempt["capture_completeness"] == "gap"
    assert attempt["exit_fact"]["real_exit_code"] == 0
    assert not second.result["execution_facts"]["coverage"]["executed_attempt_ids"]
    assert not second.result["execution_facts"]["verifications"]
    assert port.execution_order == [action.attempt.attempt_id]
    saved = core.execution_coordinator.read_checkpoint(
        project_id=inputs.project_id, attempt_id=action.attempt.attempt_id
    )
    assert saved.attempt.exit_fact_ref.real_exit_code == 0 and saved.attempt.error_ref == error
    before = core.unit_of_work.current_commit_sequence()
    core.step_execution.execution_port = None
    recalled = core.api.dispatch(value.model_copy(update={"request_id": "read-error-exit"}), RELAY)
    assert recalled.error is None and recalled.result == second.result
    assert core.unit_of_work.current_commit_sequence() == before


def test_serial_error_with_live_handle_blocks_and_next_slice_collects_original(tmp_path):
    from aitest.domain.execution.runs import StepState
    from tests.unit.test_serial_execution_slices import coordinator, items

    port = FakeExecutionPort()
    for item in items():
        port.register(
            FakeExecutionSpec(
                item.attempt.attempt_id,
                item.attempt.run_id,
                item.attempt.step_id,
                running_observations_before_exit=1,
            )
        )
    original_collect = port.collect
    observations = []
    error = StructuredExecutionError(
        "capture-error",
        TransportErrorClass.UNKNOWN,
        "READ_FAILED",
        "capture_failed",
        "采集读取失败",
    )

    def collect(handle, cursors=None):
        observations.append(handle.handle_id)
        return replace(
            original_collect(handle, cursors),
            error_ref=error,
            capture_completeness=CaptureCompleteness.GAP,
            complete=False,
        )

    port.collect = collect
    saved = coordinator(tmp_path)
    runner = SerialRunner(port, commit_coordinator=saved, poll_interval_seconds=0)
    first = runner.run_serial(items(), max_polls_per_slice=1)
    assert first.steps[0].state is StepState.EXECUTION_ERROR
    assert first.steps[1].state is StepState.PENDING
    assert first.attempts[0].exit_fact_ref is None
    current_items = (
        replace(items()[0], step=first.steps[0], attempt=first.attempts[0]),
        items()[1],
    )
    second = SerialRunner(port, commit_coordinator=saved, poll_interval_seconds=0).run_serial(
        current_items, max_polls_per_slice=1
    )
    assert len(observations) == 2
    assert second.attempts[0].exit_fact_ref is not None
    assert second.attempts[0].state is AttemptState.EXECUTION_ERROR
    assert second.steps[1].state is StepState.BLOCKED
    assert port.execution_order == ["attempt-1"]
    assert not saved.read_current_facts(
        project_id="project-1", run_id="run-1"
    ).coverage.executed_attempt_ids
