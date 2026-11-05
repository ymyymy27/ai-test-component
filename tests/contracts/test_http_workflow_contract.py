"""DEC-022 HTTP workflow input contract (`aitest.http-workflow/1.0`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aitest.contracts.http_workflow import (
    HttpWorkflowAssertion,
    HttpWorkflowRequest,
    HttpWorkflowSpec,
    parse_http_workflow,
)


def _request(**overrides: object) -> HttpWorkflowRequest:
    values: dict[str, object] = {
        "request_id": "create",
        "method": "POST",
        "url": "https://example.test/orders",
        "headers": (("Content-Type", "application/json"),),
        "body": '{"name":"demo"}',
        "extract": {"order_id": "data.id"},
        "assertions": (
            HttpWorkflowAssertion(
                assertion_id="created",
                json_path="data.id",
                operator="exists",
            ),
        ),
    }
    values.update(overrides)
    return HttpWorkflowRequest(**values)  # type: ignore[arg-type]


def test_a_chain_can_use_an_extracted_identifier_in_the_next_request() -> None:
    spec = HttpWorkflowSpec(
        requests=(
            _request(),
            _request(
                request_id="read",
                method="GET",
                url="https://example.test/orders/{{order_id}}",
                body=None,
                extract={},
                assertions=(),
            ),
        )
    )
    assert spec.requests[1].url.endswith("{{order_id}}")


def test_forward_reference_is_rejected() -> None:
    with pytest.raises(ValidationError, match="before extraction"):
        HttpWorkflowSpec(
            requests=(
                _request(
                    request_id="read",
                    method="GET",
                    url="https://example.test/orders/{{order_id}}",
                    extract={},
                ),
            )
        )


def test_duplicate_extraction_name_across_the_chain_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unique across the chain"):
        HttpWorkflowSpec(
            requests=(
                _request(),
                _request(
                    request_id="read",
                    method="GET",
                    url="https://example.test/orders",
                    extract={"order_id": "data.id"},
                ),
            )
        )


def test_incomplete_json_path_is_rejected() -> None:
    with pytest.raises(ValidationError, match="complete dotted JSON path"):
        HttpWorkflowAssertion(
            assertion_id="bad",
            json_path="data..id",
            operator="exists",
        )


def test_null_expected_value_is_a_real_assertion() -> None:
    assertion = HttpWorkflowAssertion(
        assertion_id="null", json_path="data.value", operator="equals", expected=None
    )
    assert assertion.expected is None


def test_missing_expected_value_is_rejected_for_value_operators() -> None:
    with pytest.raises(ValidationError, match="expected value"):
        HttpWorkflowAssertion(
            assertion_id="missing", json_path="data.value", operator="equals"
        )


def test_bad_header_and_method_are_rejected() -> None:
    with pytest.raises(ValidationError, match="header"):
        _request(headers=(("Bad Header", "x"),))
    with pytest.raises(ValidationError, match="method"):
        _request(method="GET POST")


def test_parse_requires_exactly_one_frozen_document() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        parse_http_workflow(())
    with pytest.raises(ValueError, match="exactly one"):
        parse_http_workflow(("{}", "{}"))
