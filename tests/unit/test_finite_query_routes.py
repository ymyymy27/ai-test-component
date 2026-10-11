"""Selectors must use an explicit finite route, never residual full-history filters."""

import pytest
from pydantic import ValidationError

from aitest.contracts.queries import QuerySpec


@pytest.mark.parametrize(
    "selectors",
    [
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
    ],
)
def test_illegal_selector_combinations_are_rejected(selectors):
    with pytest.raises(ValidationError, match="QUERY_UNSUPPORTED_FILTER"):
        QuerySpec(project_id="p", **selectors)


@pytest.mark.parametrize(
    "selectors",
    [
        {},
        {"aggregate_kind": "case"},
        {"aggregate_kind": "case", "record_id": "x"},
        {"aggregate_kind": "case", "record_id": "x", "revision": 2},
        {"aggregate_kind": "report"},
        {"business_outcome": "passed"},
        {"report_id": "x", "business_outcome": "failed"},
        {"run_id": "x", "business_outcome": "incomplete"},
        {"report_id": "x", "aggregate_kind": "report"},
        {"view": "ALL", "aggregate_kind": "issue"},
        {"view": "OPEN", "facet": "module", "facet_value": "m", "severity": "high"},
        {"facet": "layer", "facet_value": "L1"},
        {"severity": "high"},
    ],
)
def test_existing_finite_routes_remain_valid(selectors):
    spec = QuerySpec(project_id="p", **selectors)
    spec.ensure_finite_route()


@pytest.mark.parametrize("method", ["model_copy", "model_construct"])
@pytest.mark.parametrize(
    "selectors",
    [
        {"revision": 2},
        {"record_id": "x"},
        {"view": "ALL", "report_id": "x"},
        {"sort": "arbitrary"},
        {"limit": True},
        {"facet": "module"},
    ],
)
def test_unvalidated_model_objects_cannot_bypass_route_guard(
    tmp_path, monkeypatch, selectors, method
):
    from pathlib import Path

    from aitest.infrastructure.file_store.index import FileQueryIndex

    def read_forbidden(*args, **kwargs):
        raise AssertionError("unsupported query must be rejected before reading any files")

    monkeypatch.setattr(Path, "read_text", read_forbidden)
    monkeypatch.setattr(Path, "read_bytes", read_forbidden)
    spec = (
        QuerySpec(project_id="p").model_copy(update=selectors)
        if method == "model_copy"
        else QuerySpec.model_construct(project_id="p", **selectors)
    )
    result = FileQueryIndex(tmp_path).query_spec(spec)
    assert result.status == "unsupported_filter" and result.items == ()


def test_application_record_query_reports_the_contract_error():
    from aitest.application.planning.substrate import RecordQuery
    from aitest.contracts.queries import QueryUnsupportedFilter

    with pytest.raises(QueryUnsupportedFilter) as raised:
        RecordQuery(project_id="p", record_id="x")
    assert raised.value.code == "QUERY_UNSUPPORTED_FILTER"


def test_exact_revision_query_seeks_only_the_target_identity(tmp_path, monkeypatch):
    from aitest.infrastructure.file_store.index import FileQueryIndex, _ShardDirectory

    index = FileQueryIndex(tmp_path, shard_size=8)
    rows = [
        dict(
            project_id="p",
            aggregate_kind="case",
            record_id=f"c{number:03}",
            revision=1,
            commit_sequence=number + 1,
        )
        for number in range(240)
    ]
    rows.append(
        dict(
            project_id="p",
            aggregate_kind="case",
            record_id="target",
            revision=2,
            commit_sequence=241,
        )
    )
    index.rebuild(rows)
    reads = []
    original = _ShardDirectory._read_shard

    def read(self, info):
        reads.append(info["file"])
        return original(self, info)

    monkeypatch.setattr(_ShardDirectory, "_read_shard", read)
    spec = QuerySpec(project_id="p", aggregate_kind="case", record_id="target", revision=2)
    result = index.query_spec(spec)
    assert result.status == "ok" and result.items == (rows[-1],)
    assert len(reads) == 1


def test_local_api_preserves_unsupported_query_error_code():
    from aitest.application.planning.substrate import RecordQuery
    from aitest.bootstrap import create_api
    from aitest.contracts.commands import Command
    from aitest.interfaces.local.api import EntryKind, Session

    def handler(command):
        return RecordQuery(project_id="p", record_id="x")

    api = create_api(handlers={"query": handler})
    response = api.dispatch(
        Command(request_id="query-invalid", action="query"), Session("s", EntryKind.INTERACTIVE_CLI)
    )
    assert response.error is not None and response.error.code == "QUERY_UNSUPPORTED_FILTER"
