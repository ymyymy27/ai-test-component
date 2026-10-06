"""Untrusted adapter acknowledgements do not prove the current execution stopped."""

from dataclasses import replace

import pytest

from aitest.application.execution.control import RunControlService
from aitest.domain.execution.runs import (
    AttemptState,
    ExecutionInspectionState,
    RunControlState,
    StopRequestResult,
)
from tests.unit.test_execution_control import _attempt, _run


class StopPort:
    def __init__(self, result):
        self.result = result

    def request_stop(self, handle):
        return self.result


@pytest.mark.parametrize(
    "identity,state,confirmed",
    [
        ("foreign-handle", ExecutionInspectionState.STOPPED, True),
        ("handle-1", ExecutionInspectionState.RUNNING, True),
        ("handle-1", ExecutionInspectionState.UNKNOWN, True),
        ("handle-1", ExecutionInspectionState.LOST, True),
        ("handle-1", ExecutionInspectionState.EXITED, True),
        ("handle-1", ExecutionInspectionState.STOPPED, 1),
    ],
)
def test_cancel_requires_exact_stopped_handle(identity, state, confirmed):
    decision = RunControlService(StopPort(StopRequestResult(identity, confirmed, state))).cancel(
        _attempt(AttemptState.RUNNING)
    )
    assert decision.run_state is RunControlState.CANCELLING
    assert decision.attempt_state is AttemptState.PENDING_VERIFICATION
    assert decision.stop_confirmed is False and decision.requires_verification


def test_exact_stopped_handle_can_be_cancelled():
    result = StopRequestResult("handle-1", True, ExecutionInspectionState.STOPPED)
    decision = RunControlService(StopPort(result)).cancel(_attempt(AttemptState.RUNNING))
    assert decision.run_state is RunControlState.CANCELLED
    assert decision.attempt_state is AttemptState.CANCELLED and decision.stop_confirmed is True


@pytest.mark.parametrize("state", [AttemptState.UNKNOWN, AttemptState.PENDING_VERIFICATION])
def test_pause_waits_for_unknown_execution_boundary(state):
    run = replace(_run(), control_state=RunControlState.RUNNING)
    decision = RunControlService(StopPort(None)).pause(run, (_attempt(state),))
    assert decision.run_state is RunControlState.PAUSE_REQUESTED
    assert decision.requires_verification


def test_pause_does_not_trust_terminal_label_without_exit_fact():
    run = replace(_run(), control_state=RunControlState.RUNNING)
    decision = RunControlService(StopPort(None)).pause(run, (_attempt(AttemptState.COMPLETED),))
    assert decision.run_state is RunControlState.PAUSE_REQUESTED
    assert decision.requires_verification
