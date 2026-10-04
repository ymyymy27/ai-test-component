"""Finite serial slices resume saved handles and then schedule dependencies."""

from dataclasses import replace

import pytest

from aitest.application.execution.runner import SerialExecutionItem, SerialRunner
from aitest.domain.execution.runs import AttemptState, StepState
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
from tests.unit.test_serial_execution_loop import _attempt, _request, _step


def items():
    return (
        SerialExecutionItem(
            _step("step-1", 1), _attempt("step-1", "attempt-1"), _request("step-1", "attempt-1")
        ),
        SerialExecutionItem(
            _step("step-2", 2, "step-1"),
            _attempt("step-2", "attempt-2"),
            _request("step-2", "attempt-2"),
        ),
    )


def test_slice_resumes_saved_running_attempt_and_then_dispatches_dependent(tmp_path):
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=2)
    )
    port.register(
        FakeExecutionSpec("attempt-2", "run-1", "step-2", running_observations_before_exit=0)
    )
    checkpoint = FileCheckpointStore(tmp_path)
    runner = SerialRunner(port, checkpoint_store=checkpoint, poll_interval_seconds=0)
    first = runner.execute_attempt(items()[0].attempt, items()[0].request, max_polls=1)
    assert first.state is AttemptState.RUNNING
    active_items = (
        replace(
            items()[0],
            step=replace(items()[0].step, state=StepState.RUNNING, current_attempt_id="attempt-1"),
            attempt=first,
        ),
        items()[1],
    )
    restarted = SerialRunner(port, checkpoint_store=checkpoint, poll_interval_seconds=0)
    result = restarted.run_serial(active_items)
    assert [step.state for step in result.steps] == [StepState.COMPLETED, StepState.COMPLETED]
    assert port.execution_order == ["attempt-1", "attempt-2"]


def test_default_slice_does_not_wait_for_all_long_running_observations(tmp_path):
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=500)
    )
    port.register(FakeExecutionSpec("attempt-2", "run-1", "step-2"))
    runner = SerialRunner(
        port, checkpoint_store=FileCheckpointStore(tmp_path), poll_interval_seconds=0
    )
    result = runner.run_serial(items())
    assert result.steps[0].state is StepState.RUNNING
    assert result.steps[1].state is StepState.PENDING
    assert result.attempts[0].state is AttemptState.RUNNING
    assert port.execution_order == ["attempt-1"]


def test_control_gate_stops_new_dispatch_without_consuming_intent(tmp_path):
    port = FakeExecutionPort()
    port.register(FakeExecutionSpec("attempt-1", "run-1", "step-1"))
    runner = SerialRunner(
        port,
        checkpoint_store=FileCheckpointStore(tmp_path),
        dispatch_allowed=lambda request: False,
        poll_interval_seconds=0,
    )
    result = runner.run_serial(items())
    assert not result.attempts
    assert all(step.state is StepState.PENDING for step in result.steps)
    assert port.execution_order == []


def test_saved_active_intent_can_be_collected_when_new_start_validation_is_unavailable(tmp_path):
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=1)
    )
    checkpoint = FileCheckpointStore(tmp_path)
    first = SerialRunner(port, checkpoint_store=checkpoint, poll_interval_seconds=0)
    running = first.execute_attempt(items()[0].attempt, items()[0].request, max_polls=1)

    class UnavailableNewStart:
        def validate(self, attempt, request):
            pytest.fail("original active intent must not require a new-start capability")

    restarted = SerialRunner(
        port,
        checkpoint_store=checkpoint,
        start_validator=UnavailableNewStart(),
        poll_interval_seconds=0,
    )
    complete = restarted.execute_attempt(running, items()[0].request, max_polls=2)
    assert complete.state is AttemptState.COMPLETED
    assert port.execution_order == ["attempt-1"]


def test_active_step_without_published_claim_cannot_be_started_again(tmp_path):
    port = FakeExecutionPort()
    port.register(FakeExecutionSpec("attempt-1", "run-1", "step-1"))
    active = replace(
        items()[0],
        step=replace(items()[0].step, state=StepState.RUNNING, current_attempt_id="attempt-1"),
    )
    runner = SerialRunner(port, checkpoint_store=FileCheckpointStore(tmp_path))
    with pytest.raises(ValueError, match="saved start claim"):
        runner.run_serial((active,))
    assert port.execution_order == []


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_slice_budget_is_a_positive_bounded_integer(budget):
    with pytest.raises(ValueError):
        SerialRunner(FakeExecutionPort()).run_serial((), max_polls_per_slice=budget)
