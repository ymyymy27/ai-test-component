"""Business and imported observations must use the core JSON comparison rule."""

from unittest.mock import Mock

import pytest

from aitest.application.ports import VerificationRequest
from aitest.domain.evidence.evidence import VerificationObservation
from aitest.infrastructure.adapters.execution.external_result import (
    ExternalResultAdapter,
    ExternalResultPayload,
)
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter


def verify(mode, actual, expected):
    if mode == "business":
        query = Mock()
        query.read_business_object.return_value = actual
        return BusinessVerificationAdapter(query).verify(
            VerificationRequest(
                "test-object",
                "object-1",
                "read-only",
                "immediate",
                "deployment-1",
                expected_facts=expected,
            )
        )
    return (
        ExternalResultAdapter()
        .validate(
            ExternalResultPayload(
                "import-1", "fixture-result/1.0", "instance-1", "object-1", actual, actual
            ),
            expected_schema="fixture-result/1.0",
            expected_assertions=expected,
            verification_id="verify-1",
        )
        .verification
    )


@pytest.mark.parametrize("mode", ["business", "external"])
@pytest.mark.parametrize(
    "actual,expected,observation",
    [
        ({}, {"nullable": None}, VerificationObservation.NO_RESULT),
        ({"nullable": None}, {"nullable": None}, VerificationObservation.MATCHED),
        ({"enabled": True}, {"enabled": 1}, VerificationObservation.MISMATCHED),
        ({"enabled": 0}, {"enabled": False}, VerificationObservation.MISMATCHED),
        (
            {"nested": {"values": [True]}},
            {"nested": {"values": [1]}},
            VerificationObservation.MISMATCHED,
        ),
        ({"count": 1.0}, {"count": 1}, VerificationObservation.MATCHED),
        (
            {"status": "failed"},
            {"nullable": None, "status": "passed"},
            VerificationObservation.MISMATCHED,
        ),
    ],
)
def test_missing_null_boolean_and_nested_values_use_one_domain_rule(
    mode, actual, expected, observation
):
    result = verify(mode, actual, expected)
    assert result.observation is observation
    if "nullable" in expected and "nullable" not in actual:
        assert any("missing:nullable" in value for value in result.gap_ids)
    if "status" in expected:
        assert any("mismatch:status" in value for value in result.gap_ids)


@pytest.mark.parametrize("actual", [[], {"value": float("nan")}, {"value": object()}])
def test_unverifiable_business_material_is_structured_query_error(actual):
    result = verify("business", actual, {"value": None})
    assert result.observation is VerificationObservation.QUERY_ERROR
    assert result.actual_result_ref is None and result.gap_ids
