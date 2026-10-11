import json

import pytest

from aitest.infrastructure.adapters.execution.http import (
    HttpAdapter,
    HttpAssertion,
    HttpAssertionOperator,
    HttpExchangeResult,
    HttpRequestSpec,
)


def observe(body, path="value", expected=None, operator=HttpAssertionOperator.EQUALS):
    spec = HttpRequestSpec(
        request_id="observation", method="GET", url="https://synthetic.invalid",
        extract_paths=(("value", path),),
        assertions=(HttpAssertion("assertion", path, operator, expected),),
    )
    return HttpAdapter._enrich(HttpExchangeResult(
        request_id=spec.request_id, method=spec.method, url=spec.url, status=200, body=body,
    ), spec)


@pytest.mark.parametrize("body,path", [
    (b"", "value"), (b"not JSON", ""), (b"{}", "value"),
    (b'{"value":[]}', "value[0]"), (b'{"value":null}', "value.child"),
])
def test_unavailable_http_value_never_becomes_a_null_match(body, path) -> None:
    result = observe(body, path)
    assert result.assertion_results[0].matched is None
    assert not result.assertion_results[0].value_available
    assert result.missing_extractions == ("value",)
    assert result.request_log_ref == "http-request:observation"


def test_explicit_json_null_remains_an_available_exact_value() -> None:
    result = observe(b'{"value":null}')
    assert result.assertion_results[0].matched is True
    assert result.assertion_results[0].value_available
    assert result.extracted == {"value": None}
    assert result.missing_extractions == ()


@pytest.mark.parametrize("actual,expected,operator", [
    (True, 1, HttpAssertionOperator.EQUALS),
    ({"paid": True}, {"paid": 1}, HttpAssertionOperator.EQUALS),
    ([True], 1, HttpAssertionOperator.CONTAINS),
    ({"key": "value"}, ["key"], HttpAssertionOperator.CONTAINS),
])
def test_boolean_number_and_unhashable_contains_cannot_forge_a_match(
    actual, expected, operator
) -> None:
    result = observe(json.dumps({"value": actual}).encode(), expected=expected, operator=operator)
    assert result.assertion_results[0].matched is False
    assert result.assertion_results[0].value_available
