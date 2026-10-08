"""Frozen independent-query timing and observations; no clock or I/O here."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from aitest.domain.evidence.evidence import VerificationObservation
from aitest.domain.evidence.verification import compare_business_fields
from aitest.domain.execution.assertions import freeze_json_value


@dataclass(frozen=True, slots=True)
class QueryPollingPolicy:
    interval_ms: int
    deadline_ms: int

    def __post_init__(self) -> None:
        if (
            type(self.interval_ms) is not int
            or type(self.deadline_ms) is not int
            or not 0 < self.interval_ms <= self.deadline_ms <= 60_000
            or (self.deadline_ms - 1) // self.interval_ms + 1 > 64
        ):
            raise ValueError("query polling requires bounded integer timing")


def query_polling_policy(interval: str, deadline: str) -> QueryPollingPolicy | None:
    if deadline == "immediate" and interval == "configured":
        return None
    values = []
    for value, prefix in ((interval, "poll_interval_ms:"), (deadline, "poll_deadline_ms:")):
        if not isinstance(value, str) or not re.fullmatch(prefix + r"[1-9][0-9]{0,4}", value):
            raise ValueError("query timing policy is unsupported")
        values.append(int(value.removeprefix(prefix)))
    return QueryPollingPolicy(*values)


@dataclass(frozen=True, slots=True)
class BusinessQueryObservation:
    started_after_ms: int
    completed_after_ms: int
    actual_fields: Mapping[str, object] | None = None
    error_code: str | None = None
    unavailable_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.started_after_ms) is not int
            or type(self.completed_after_ms) is not int
            or not 0 <= self.started_after_ms <= self.completed_after_ms
            or self.error_code
            not in (
                None,
                "independent_query_error",
                "independent_query_material_invalid",
                "independent_query_deadline",
            )
            or (self.error_code is not None and self.actual_fields is not None)
            or type(self.unavailable_fields) is not tuple
            or any(not isinstance(key, str) or not key for key in self.unavailable_fields)
            or tuple(sorted(set(self.unavailable_fields))) != self.unavailable_fields
            or (self.unavailable_fields and self.actual_fields is None)
        ):
            raise ValueError("query observation timing/material is invalid")
        if self.actual_fields is not None and not isinstance(
            freeze_json_value(self.actual_fields), dict
        ):
            raise ValueError("query observation requires JSON fields")


def derive_polled_query(
    policy: QueryPollingPolicy,
    observations: tuple[BusinessQueryObservation, ...],
    elapsed_ms: int,
    expected: Mapping[str, object],
) -> tuple[VerificationObservation, tuple[str, ...]]:
    """Keep decisive observations; deadline alone cannot prove an observed failure."""
    if (
        not isinstance(policy, QueryPollingPolicy)
        or type(observations) is not tuple
        or not 0 < len(observations) <= 64
        or type(elapsed_ms) is not int
        or elapsed_ms < 0
        or not isinstance(expected, Mapping)
        or not expected
    ):
        raise ValueError("query polling needs complete actual observations")
    previous = None
    last = None
    for index, item in enumerate(observations):
        if not isinstance(item, BusinessQueryObservation) or (
            item.completed_after_ms > elapsed_ms
            or item.started_after_ms >= policy.deadline_ms
            or (
                previous is not None
                and item.started_after_ms < (previous.completed_after_ms + policy.interval_ms)
            )
        ):
            raise ValueError("query observation differs from its frozen timing")
        result = query_observation_result(policy, item, expected)
        if index < len(observations) - 1 and (result[0] is not VerificationObservation.NO_RESULT):
            raise ValueError("query polling continued after a terminal observation")
        previous, last = item, result
    assert last is not None and previous is not None
    if last[0] is VerificationObservation.NO_RESULT and not previous.unavailable_fields:
        if elapsed_ms < policy.deadline_ms:
            raise ValueError("query polling stopped before its deadline without a result")
        return VerificationObservation.DEADLINE_REACHED, ("independent_query_deadline",)
    return last


def query_observation_result(
    policy: QueryPollingPolicy,
    item: BusinessQueryObservation,
    expected: Mapping[str, object],
) -> tuple[VerificationObservation, tuple[str, ...]]:
    if item.completed_after_ms >= policy.deadline_ms:
        return VerificationObservation.DEADLINE_REACHED, ("independent_query_deadline",)
    if item.error_code is not None:
        return VerificationObservation.QUERY_ERROR, (item.error_code,)
    if item.actual_fields is None:
        return VerificationObservation.NO_RESULT, ("business_object_not_found",)
    return compare_business_fields(
        item.actual_fields,
        expected,
        unavailable_fields=item.unavailable_fields,
    )
