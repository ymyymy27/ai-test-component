"""Non-process HTTP/Agent attempts terminate by structured result, not a fake exit."""

from __future__ import annotations

from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    CaptureCompleteness,
    SideEffectClass,
    StepRevisionRef,
    execution_boundary_pending,
    has_complete_capture,
    has_reliable_terminal_fact,
)


def _attempt(adapter: AdapterKind, *, state: AttemptState, complete: bool = True) -> Attempt:
    return Attempt(
        attempt_id="attempt-http",
        run_id="run-1",
        step_id="step-1",
        attempt_index=1,
        resolved_input_digest="sha256:input",
        step_revision_ref=StepRevisionRef(
            step_revision_id="step-rev", revision_no=1, digest="sha256:step"
        ),
        source_binding_digest="sha256:source",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=adapter,
        adapter_version="http-workflow/1.0",
        state=state,
        structured_result_ref="http-workflow-result:attempt-http" if complete else None,
        capture_completeness=(
            CaptureCompleteness.COMPLETE if complete else CaptureCompleteness.GAP
        ),
    )


def test_http_attempt_completes_without_a_process_exit() -> None:
    attempt = _attempt(AdapterKind.HTTP, state=AttemptState.COMPLETED)
    assert has_complete_capture(attempt)
    assert has_reliable_terminal_fact(attempt)
    assert not execution_boundary_pending(attempt)


def test_http_attempt_without_structured_result_is_not_complete() -> None:
    attempt = _attempt(AdapterKind.HTTP, state=AttemptState.COMPLETED, complete=False)
    assert not has_complete_capture(attempt)
    assert not has_reliable_terminal_fact(attempt)


def test_http_attempt_in_progress_keeps_the_boundary_pending() -> None:
    attempt = _attempt(AdapterKind.HTTP, state=AttemptState.RUNNING, complete=False)
    assert execution_boundary_pending(attempt)


def test_process_attempt_still_requires_a_real_exit() -> None:
    attempt = _attempt(AdapterKind.COMMAND, state=AttemptState.COMPLETED)
    assert not has_complete_capture(attempt)
    assert not has_reliable_terminal_fact(attempt)
