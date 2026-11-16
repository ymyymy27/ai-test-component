"""有限查询的命令层到 `QuerySpec` 桥接。

背景（2026-10-11）：`contracts.queries.QuerySpec` 与 `infrastructure/file_store/index.py`
早已实现报告与问题的固定目录（`business_outcome`／`report_id`／`run_id`／`view`／`facet`／
`severity`／`sort`／`descending`／`revision`），但命令层的 `RecordQuery` 只带 5 个字段，
这些选择器**没有任何调用点能到达索引层**——MCP relay 也因此只敢广告 4 个字段。

本文件锁定两件事：
1. `RecordQuery` 的每个有限选择器都被送进 `QuerySpec`（不是被桥接处丢掉）；
2. 固定目录的**组合合法性**仍由 `QuerySpec.ensure_finite_route()` 唯一判定，
   非法组合如实抛出 `QUERY_UNSUPPORTED_FILTER`，不静默忽略、也不变成 INTERNAL_ERROR。
"""

from __future__ import annotations

from typing import Any

import pytest

from aitest.application.planning.substrate import RecordQuery
from aitest.contracts.queries import QueryUnsupportedFilter
from aitest.infrastructure.file_store.index import FileQueryIndex, IndexQueryResult
from aitest.infrastructure.file_store.records import FileRecordRepository


def test_record_query_forwards_every_finite_selector_into_the_query_spec(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def capture(self: FileQueryIndex, spec: Any) -> IndexQueryResult:
        captured["spec"] = spec
        return IndexQueryResult(status="maintenance_required")

    monkeypatch.setattr(FileQueryIndex, "query_spec", capture)
    repository = FileRecordRepository(tmp_path)
    result = repository.query(
        RecordQuery(
            project_id="project-1",
            aggregate_kind="report",
            limit=25,
            sort="published_sequence",
            descending=True,
            business_outcome="passed",
            run_id="run-1",
        )
    )

    assert result.status == "maintenance_required"
    spec = captured["spec"]
    assert spec.business_outcome == "passed"
    assert spec.run_id == "run-1"
    assert spec.sort == "published_sequence"
    assert spec.descending is True
    assert spec.limit == 25
    assert spec.project_id == "project-1"


def test_issue_facet_selector_reaches_the_query_spec(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def capture(self: FileQueryIndex, spec: Any) -> IndexQueryResult:
        captured["spec"] = spec
        return IndexQueryResult(status="maintenance_required")

    monkeypatch.setattr(FileQueryIndex, "query_spec", capture)
    FileRecordRepository(tmp_path).query(
        RecordQuery(
            project_id="project-1",
            aggregate_kind="issue",
            view="ALL",
            facet="blocking",
            facet_value="P1",
            severity="P1",
        )
    )

    spec = captured["spec"]
    assert spec.view == "ALL"
    assert spec.facet == "blocking"
    assert spec.facet_value == "P1"
    assert spec.severity == "P1"


def test_illegal_fixed_directory_combination_stays_unsupported(tmp_path) -> None:
    """`view` 与精确记录身份不能混用；组合判定只有 `QuerySpec` 一处。"""

    repository = FileRecordRepository(tmp_path)
    with pytest.raises(QueryUnsupportedFilter):
        repository.query(
            RecordQuery(
                project_id="project-1",
                aggregate_kind="issue",
                record_id="case-1",
                view="OPEN",
            )
        )


def test_issue_and_report_selectors_cannot_be_mixed(tmp_path) -> None:
    """问题目录与报告目录的选择器不得混用（同一条路由判定）。"""

    repository = FileRecordRepository(tmp_path)
    with pytest.raises(QueryUnsupportedFilter):
        repository.query(
            RecordQuery(project_id="project-1", aggregate_kind="report", view="OPEN")
        )


def test_relay_advertises_exactly_the_selectors_the_command_layer_accepts() -> None:
    """MCP relay 的 `_QUERY_FIELDS` 必须与实际能处理的选器集合一致。

    两边漂移过一次：relay 只广告 4 个字段，因为命令层当时只支持这 4 个
    （relay 是诚实的，缺的是命令层）。本断言锁死二者不得再分叉。
    """

    from aitest.contracts.queries import QuerySpec
    from aitest.interfaces.tools.agent_relay import _QUERY_FIELDS

    assert set(QuerySpec.model_fields) >= _QUERY_FIELDS
    assert {
        "revision",
        "sort",
        "descending",
        "business_outcome",
        "run_id",
        "report_id",
        "view",
        "facet",
        "facet_value",
        "severity",
    } <= _QUERY_FIELDS
