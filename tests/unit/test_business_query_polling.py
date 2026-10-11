"""Frozen deadlines and decisive observations, without actual remote deployments."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from aitest.application.ports import VerificationRequest
from aitest.domain.evidence.evidence import VerificationObservation
from aitest.domain.evidence.polling import (
    BusinessQueryObservation,
    QueryPollingPolicy,
    derive_polled_query,
    query_polling_policy,
)
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter


class Time:
    def __init__(self):
        self.ns = 0
        self.waits = []

    def clock(self):
        return self.ns

    def wait(self, seconds):
        self.waits.append(seconds)
        self.ns += round(seconds * 1_000_000_000)


def request(**changes):
    return replace(
        VerificationRequest(
            "attempt-1",
            "order-1",
            "read-payment",
            "poll_deadline_ms:50",
            "deployment-1",
            query_interval="poll_interval_ms:10",
            expected_facts={"paid": True},
        ),
        **changes,
    )


def capture(values, *, query_ms=0, after_query=None):
    time = Time()
    calls = []
    remaining = iter(values)

    def read(**kw):
        calls.append(kw)
        time.ns += query_ms * 1_000_000
        value = next(remaining)
        if isinstance(value, BaseException):
            raise value
        if after_query is not None:
            after_query(value)
        return value

    adapter = BusinessVerificationAdapter(
        SimpleNamespace(read_business_object_before=read), clock_ns=time.clock, wait=time.wait
    )
    return adapter.capture(request()), calls, time


def test_actual_missing_and_partial_fields_are_requeried_until_visible():
    result, calls, time = capture([None, {"other": 1}, {"paid": True}], query_ms=2)
    assert result.verification.observation is VerificationObservation.MATCHED
    assert result.actual_fields == {"paid": True}
    assert [
        (item.started_after_ms, item.completed_after_ms) for item in result.query_observations
    ] == [
        (0, 2),
        (12, 14),
        (24, 26),
    ]
    assert result.query_elapsed_ms == 26 and time.waits == [0.01, 0.01]
    assert len(calls) == 3 and all(
        kw
        == {
            "business_object_id": "order-1",
            "target_deployment_ref": "deployment-1",
            "deadline_monotonic": 0.05,
        }
        for kw in calls
    )


def test_missing_result_reaches_one_total_deadline_without_sending_at_cutoff():
    result, calls, time = capture([None] * 8)
    assert result.verification.observation is VerificationObservation.DEADLINE_REACHED
    assert len(calls) == 5 and result.query_elapsed_ms == 50 and time.ns == 50_000_000
    assert [item.started_after_ms for item in result.query_observations] == [0, 10, 20, 30, 40]
    assert result.verification.gap_ids == ("independent_query_deadline",)


@pytest.mark.parametrize("query_ms", [50, 51])
def test_late_matching_bytes_do_not_become_timely_validation(query_ms):
    result, calls, _ = capture([{"paid": True}], query_ms=query_ms)
    assert result.actual_fields == {"paid": True} and len(calls) == 1
    assert result.verification.observation is VerificationObservation.DEADLINE_REACHED


@pytest.mark.parametrize(
    "actual,observation",
    [
        ({"paid": False}, VerificationObservation.MISMATCHED),
        (OSError("opaque sensitive failure"), VerificationObservation.QUERY_ERROR),
        ({"paid": object()}, VerificationObservation.QUERY_ERROR),
        (TimeoutError(), VerificationObservation.QUERY_ERROR),
    ],
)
def test_decisive_failure_or_error_is_not_retried_or_hidden_by_later_success(actual, observation):
    result, calls, _ = capture([actual, {"paid": True}])
    assert result.verification.observation is observation and len(calls) == 1
    assert "opaque" not in repr(result) and "sensitive" not in repr(result)


def test_deadline_without_cooperating_reader_refuses_unbounded_legacy_query():
    def unbounded(**kw):
        pytest.fail("must not fall back to the old unbounded reader")

    result = BusinessVerificationAdapter(SimpleNamespace(read_business_object=unbounded)).capture(
        request()
    )
    assert result.actual_fields is None and not result.query_observations
    assert result.verification.gap_ids == ("independent_query_deadline_capability_missing",)


@pytest.mark.parametrize(
    "interval,deadline",
    [
        ("configured", "eventually"),
        ("poll_interval_ms:1", "immediate"),
        ("poll_interval_ms:01", "poll_deadline_ms:50"),
        ("poll_interval_ms:0", "poll_deadline_ms:50"),
        ("poll_interval_ms:1", "poll_deadline_ms:65"),
        ("poll_interval_ms:5", "poll_deadline_ms:4"),
        ("poll_interval_ms:1000", "poll_deadline_ms:60001"),
        ("poll_interval_ms:10 ", "poll_deadline_ms:50"),
        ("poll_interval_ms:10", "poll_deadline_ms:True"),
    ],
)
def test_unknown_or_excessive_policy_does_not_silently_query_once(interval, deadline):
    with pytest.raises(ValueError):
        query_polling_policy(interval, deadline)
    result = BusinessVerificationAdapter(SimpleNamespace()).capture(
        request(
            query_interval=interval,
            deadline_condition=deadline,
        )
    )
    assert result.verification.gap_ids == ("query_policy_unsupported",)


def test_trace_detaches_returned_mapping_before_next_query():
    earlier = {"other": [1]}

    def mutate(value):
        if value.get("paid"):
            earlier["other"][0] = 2

    result, _, _ = capture([earlier, {"paid": True}], after_query=mutate)
    assert result.query_observations[0].actual_fields == {"other": [1]}


@pytest.mark.parametrize("timing", [(True, 5), (0, 5.0), (2, 1), (-1, 3)])
def test_trace_timing_requires_actual_integers(timing):
    with pytest.raises(ValueError):
        BusinessQueryObservation(*timing)


@pytest.mark.parametrize(
    "damage", ["early_stop", "early_retry", "after_mismatch", "extra_after_match", "late_start"]
)
def test_saved_trace_rejects_timing_or_terminal_rewrites(damage):
    policy = QueryPollingPolicy(10, 50)
    values = (BusinessQueryObservation(0, 0), BusinessQueryObservation(10, 10, {"paid": True}))
    elapsed = 10
    if damage == "early_stop":
        values = (values[0],)
    elif damage == "early_retry":
        values = (values[0], replace(values[1], started_after_ms=9))
    elif damage in ("after_mismatch", "extra_after_match"):
        values = (
            replace(values[0], actual_fields={"paid": damage == "extra_after_match"}),
            values[1],
        )
    else:
        values = (BusinessQueryObservation(50, 50, {"paid": True}),)
        elapsed = 50
    with pytest.raises(ValueError):
        derive_polled_query(policy, values, elapsed, {"paid": True})


def test_observation_total_material_budget_prevents_unbounded_capture():
    result, calls, _ = capture([{"paid": True, "body": "x" * (1024 * 1024 + 1)}])
    assert result.actual_fields is None and len(calls) == 1
    assert result.verification.gap_ids == ("independent_query_material_invalid",)
