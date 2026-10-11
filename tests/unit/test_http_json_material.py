"""Ambiguous or non-JSON response material cannot support HTTP assertions."""

import pytest

from aitest.infrastructure.adapters.execution.http import HttpAssertionOperator
from tests.unit.test_http_observation_integrity import observe


@pytest.mark.parametrize(
    "body",
    [
        b'{"value":false,"value":true}',
        b'{"value":{"paid":false,"paid":true}}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e400}',
        b'{"value":[{"amount":1e400}]}',
    ],
)
def test_ambiguous_json_response_is_unavailable_instead_of_verified(body):
    result = observe(body, operator=HttpAssertionOperator.EXISTS)
    assert result.status == 200 and result.body == body
    assert result.assertion_results[0].matched is None
    assert result.assertion_results[0].value_available is False
    assert result.missing_extractions == ("value",)


def test_excessive_json_depth_is_preserved_as_unknown_instead_of_crashing():
    body = b'{"value":' + b"[" * 1200 + b"0" + b"]" * 1200 + b"}"
    result = observe(body, operator=HttpAssertionOperator.EXISTS)
    assert result.body == body
    assert result.assertion_results[0].matched is None
    assert result.missing_extractions == ("value",)


def test_equal_keys_in_different_objects_are_valid_json():
    result = observe(
        b'{"value":[{"paid":true},{"paid":false}]}', expected=[{"paid": True}, {"paid": False}]
    )
    assert result.assertion_results[0].matched is True
