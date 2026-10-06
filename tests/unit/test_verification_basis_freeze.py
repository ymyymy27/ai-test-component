"""I/O callbacks cannot replace the comparison basis after a request starts."""

from io import BytesIO
from unittest.mock import Mock

import pytest

from aitest.application.ports import VerificationRequest
from aitest.domain.evidence.evidence import VerificationObservation
from aitest.domain.execution.assertions import HttpAssertion, HttpAssertionOperator
from aitest.infrastructure.adapters.execution.http import HttpAdapter, HttpRequestSpec
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter


def request(expected):
    return VerificationRequest(
        "check", "object", "readonly", "immediate", "deployment", expected_facts=expected
    )


def test_business_query_cannot_mutate_the_frozen_expected_value():
    expected = {"payment": {"paid": True}}
    query = Mock()

    def read(**kwargs):
        expected["payment"]["paid"] = False
        return {"payment": {"paid": False}}

    query.read_business_object.side_effect = read
    result = BusinessVerificationAdapter(query).verify(request(expected))
    assert result.observation is VerificationObservation.MISMATCHED


def test_override_cannot_replace_the_request_expected_facts():
    query = Mock()
    query.read_business_object.return_value = {"paid": False}
    result = BusinessVerificationAdapter(query).verify(
        request({"paid": True}), expected_facts={"paid": False}
    )
    assert result.observation is VerificationObservation.NO_RESULT
    assert result.gap_ids == ("expected_facts_conflict",)
    query.read_business_object.assert_not_called()


@pytest.mark.parametrize(
    "expected", [{"value": float("nan")}, {"value": object()}, {"value": {1: "unsafe key"}}]
)
def test_invalid_expected_material_stops_before_business_query(expected):
    query = Mock()
    result = BusinessVerificationAdapter(query).verify(request(expected))
    assert result.observation is VerificationObservation.NO_RESULT
    assert result.gap_ids == ("expected_facts_invalid",)
    query.read_business_object.assert_not_called()


def test_equal_override_keeps_the_request_basis():
    query = Mock()
    query.read_business_object.return_value = {"paid": True}
    result = BusinessVerificationAdapter(query).verify(
        request({"paid": True}), expected_facts={"paid": True}
    )
    assert result.observation is VerificationObservation.MATCHED
    query.read_business_object.assert_called_once()


def test_http_response_cannot_replace_the_original_nested_assertion(monkeypatch):
    from aitest.infrastructure.adapters.execution import http

    expected = {"paid": True}

    class Response(BytesIO):
        status = 200
        headers = {}

    def fetch(*args, **kwargs):
        expected["paid"] = False
        return Response(b'{"payment":{"paid":false}}')

    monkeypatch.setattr(http, "urlopen", fetch)
    result = HttpAdapter().execute(
        HttpRequestSpec(
            "frozen-http",
            "GET",
            "https://synthetic.invalid",
            assertions=(HttpAssertion("paid", "payment", HttpAssertionOperator.EQUALS, expected),),
        )
    )
    assert result.assertion_results[0].matched is False


def test_invalid_http_assertion_stops_before_transport(monkeypatch):
    from aitest.infrastructure.adapters.execution import http

    fetch = Mock()
    monkeypatch.setattr(http, "urlopen", fetch)
    result = HttpAdapter().execute(
        HttpRequestSpec(
            "invalid-http",
            "GET",
            "https://synthetic.invalid",
            assertions=(HttpAssertion("paid", "payment", HttpAssertionOperator.EQUALS, object()),),
        )
    )
    assert result.status is None and result.error_class == "assertion_input_invalid"
    assert result.assertion_results == ()
    fetch.assert_not_called()
