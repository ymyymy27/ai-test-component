"""HTTP workflow capture outcome must follow actual captured material."""

from __future__ import annotations

import pytest

from aitest.application.execution.http_workflow_execution import (
    http_workflow_gaps,
    http_workflow_outcome,
)
from aitest.application.ports import HttpWorkflowCapture, HttpWorkflowExchange
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    TransportErrorClass,
)


def _exchange(
    *, body_complete: bool = True, error_class: str | None = None
) -> HttpWorkflowExchange:
    return HttpWorkflowExchange(
        request_id="read",
        method="GET",
        url="http://127.0.0.1/orders/1",
        status=200 if error_class is None else None,
        response_headers=(),
        body=b'{"data": {"status": "new"}}',
        body_complete=body_complete,
        error_class=error_class,
        error_detail=None,
        extracted={},
    )


def _capture(
    *exchanges: HttpWorkflowExchange, gaps: tuple[str, ...] = ()
) -> HttpWorkflowCapture:
    return HttpWorkflowCapture(
        exchanges=tuple(exchanges),
        variables={},
        observation="matched" if exchanges and not gaps else "no_result",
        gaps=gaps,
    )


def test_complete_capture_is_a_completed_complete_attempt() -> None:
    outcome = http_workflow_outcome("attempt-1", _capture(_exchange()))
    assert outcome.state is AttemptState.COMPLETED
    assert outcome.completeness is CaptureCompleteness.COMPLETE
    assert outcome.error_ref is None
    assert outcome.unknown_reason_ref is None


def test_partial_capture_stays_completed_but_never_counts_as_complete() -> None:
    capture = _capture(
        _exchange(body_complete=False),
        gaps=("http_response_incomplete:read",),
    )
    outcome = http_workflow_outcome("attempt-1", capture)
    assert outcome.state is AttemptState.COMPLETED
    assert outcome.completeness is CaptureCompleteness.PARTIAL
    assert outcome.error_ref is None
    assert outcome.unknown_reason_ref == "http_response_incomplete"


def test_a_gap_before_the_first_request_is_an_execution_error() -> None:
    outcome = http_workflow_outcome(
        "attempt-1", _capture(gaps=("http_secret_unavailable:token",))
    )
    assert outcome.state is AttemptState.EXECUTION_ERROR
    assert outcome.completeness is CaptureCompleteness.GAP
    assert outcome.error_ref is not None
    assert outcome.error_ref.error_class is TransportErrorClass.AUTHENTICATION
    assert outcome.error_ref.error_code == "http_secret_unavailable"
    assert outcome.unknown_reason_ref == "http_secret_unavailable"


def test_an_empty_capture_without_a_named_gap_still_stays_unknown() -> None:
    capture = _capture()
    assert http_workflow_gaps(capture) == ("http_workflow_no_exchange",)
    outcome = http_workflow_outcome("attempt-1", capture)
    assert outcome.state is AttemptState.EXECUTION_ERROR
    assert outcome.completeness is CaptureCompleteness.GAP
    assert outcome.unknown_reason_ref == "http_workflow_no_exchange"


@pytest.mark.parametrize(
    ("gap", "expected"),
    [
        ("http_request_invalid:read:missing", TransportErrorClass.SCHEMA_ERROR),
        ("http_request_failed:read:network", TransportErrorClass.NETWORK),
        ("http_extraction_missing:read:data.id", TransportErrorClass.SCHEMA_ERROR),
        ("http_assertions_missing", TransportErrorClass.SCHEMA_ERROR),
    ],
)
def test_gap_codes_keep_their_error_class(
    gap: str, expected: TransportErrorClass
) -> None:
    outcome = http_workflow_outcome("attempt-1", _capture(gaps=(gap,)))
    assert outcome.error_ref is not None
    assert outcome.error_ref.error_class is expected
