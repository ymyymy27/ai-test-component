"""Frozen HTTP input must not be coerced to another request or another JSON path."""

from dataclasses import replace

import pytest

from aitest.infrastructure.adapters.execution.http import (
    HttpAssertion,
    HttpAssertionOperator,
    HttpRequestSpec,
)
from tests.unit.test_http_observation_integrity import observe


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_seconds": True},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": "1"},
        {"timeout_seconds": 10**1000},
        {"timeout_seconds": 1e100},
        {"url": "file:///C:/private.json"},
        {"url": "ftp://example.invalid/result"},
        {"url": "https:///result"},
        {"url": "https://user:password@example.invalid/result"},
        {"url": "https://example.invalid/result#different-target"},
        {"url": "https://example.invalid:99999/result"},
        {"url": "https://example.invalid/result\n"},
        {"method": "GET\r\nInjected: yes"},
        {"method": " GET"},
        {"headers": (("Authorization", "first"), ("authorization", "second"))},
        {"headers": (("Bad Name", "value"),)},
        {"headers": (("name", "value\r\nother: yes"),)},
        {"body": "ambiguous text instead of bytes"},
        {"extract_paths": (("value", "a"), ("value", "b"))},
        {
            "assertions": (
                HttpAssertion("same", "a", HttpAssertionOperator.EXISTS),
                HttpAssertion("same", "b", HttpAssertionOperator.EXISTS),
            )
        },
    ],
)
def test_invalid_http_request_is_rejected_before_any_io(changes):
    spec = HttpRequestSpec("request", "GET", "https://example.invalid/result")
    with pytest.raises(ValueError):
        replace(spec, **changes)


@pytest.mark.parametrize("path", ["value[0", "value[0]]]", "value[0]tail", "value..child"])
def test_invalid_paths_never_select_another_value_and_pass(path):
    body = b'{"value":[true]}' if "[" in path else b'{"value":{"child":true}}'
    result = observe(body, path=path, expected=True)
    assert result.assertion_results[0].matched is None
    assert not result.assertion_results[0].value_available
    assert result.missing_extractions == ("value",)


@pytest.mark.parametrize(
    "body,path,expected",
    [
        (b'{"value":[true]}', "value[0]", True),
        (b'{"value":{"child":null}}', "value.child", None),
        (b"[true]", "[0]", True),
        (b'{"value":true}', "", {"value": True}),
    ],
)
def test_complete_supported_path_and_root_keep_their_exact_values(body, path, expected):
    result = observe(body, path=path, expected=expected)
    assert result.assertion_results[0].matched is True
    assert result.assertion_results[0].value_available


@pytest.mark.parametrize(
    "path", ["value[-0]", "value[01]", "value[0][0]", "value[" + "9" * 5000 + "]"]
)
def test_unsupported_and_unbounded_array_index_remains_unknown(path):
    result = observe(b'{"value":[true]}', path=path, expected=True)
    assert result.assertion_results[0].matched is None


def test_valid_explicit_request_fields_are_preserved_without_io():
    spec = HttpRequestSpec(
        "request",
        "POST",
        "https://[::1]:443/result?object_id=order-1",
        headers=(("Content-Type", "application/json"), ("X-Request-Id", "request")),
        body=b'{"order_id":"order-1"}',
        timeout_seconds=1,
    )
    assert spec.url == "https://[::1]:443/result?object_id=order-1"
    assert spec.body == b'{"order_id":"order-1"}'
    assert spec.timeout_seconds == 1
