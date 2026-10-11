"""Selectors must use an explicit finite route, never residual full-history filters."""

import pytest
from pydantic import ValidationError

from aitest.contracts.queries import QuerySpec


@pytest.mark.parametrize("selectors", [
    {"revision": 2},
    {"record_id": "x"},
    {"aggregate_kind": "case", "revision": 2},
    {"view": "ALL", "report_id": "x"},
    {"view": "OPEN", "run_id": "x"},
    {"view": "ALL", "business_outcome": "failed"},
    {"view": "ALL", "aggregate_kind": "case"},
    {"view": "ALL", "record_id": "x", "aggregate_kind": "issue"},
    {"view": "ALL", "revision": 1},
    {"facet": "module", "facet_value": "m", "report_id": "x"},
    {"severity": "high", "report_id": "x"},
    {"report_id": "x", "run_id": "y"},
    {"report_id": "x", "aggregate_kind": "case"},
    {"report_id": "x", "record_id": "y", "aggregate_kind": "report"},
    {"run_id": "x", "revision": 1},
    {"aggregate_kind": "case", "business_outcome": "passed"},
    {"aggregate_kind": "report", "record_id": "x", "business_outcome": "passed"},
])
def test_illegal_selector_combinations_are_rejected(selectors):
    with pytest.raises(ValidationError, match="QUERY_UNSUPPORTED_FILTER"):
        QuerySpec(project_id="p", **selectors)
