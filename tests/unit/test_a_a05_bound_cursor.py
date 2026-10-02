"""A-05：查询游标完整键绑定、项目隔离、代次失效与摘要分片。

游标必须绑定完整查询条件（qid）、索引代次（generation）与提交根
（commit_id）：更换 project_id/筛选/排序复用游标一律 invalid_cursor；
维护重建晋升代次后旧游标失效；同代次内分页间隙的新提交不混入旧快照。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.application.planning.substrate import RecordQuery
from aitest.contracts.queries import QuerySpec
from aitest.infrastructure.file_store.index import (
    FileQueryIndex,
    decode_cursor,
    encode_cursor,
)
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork


def _row(
    project: str,
    number: int,
    *,
    kind: str = "case",
    record: str | None = None,
    revision: int = 1,
) -> dict[str, object]:
    return {
        "project_id": project,
        "aggregate_kind": kind,
        "record_id": record if record is not None else f"{kind}-{number}",
        "revision": revision,
        "commit_sequence": number,
    }


@pytest.fixture
def index(tmp_path: Path) -> FileQueryIndex:
    built = FileQueryIndex(tmp_path)
    built.rebuild(
        [
            _row("project-1", 1),
            _row("project-1", 2),
            _row("project-1", 3, kind="plan", record="plan-1"),
            _row("project-2", 4),
        ]
    )
    return built


def test_cursor_rejected_when_project_changes(index: FileQueryIndex) -> None:
    first = index.query_spec(QuerySpec(project_id="project-1", limit=1))
    assert first.next_cursor is not None

    foreign = index.query_spec(
        QuerySpec(project_id="project-2", limit=1, cursor=first.next_cursor)
    )
    assert foreign.status == "invalid_cursor"
    assert foreign.items == ()


@pytest.mark.parametrize(
    "mutated",
    [
        dict(aggregate_kind="plan"),
        dict(record_id="case-1"),
        dict(revision=2),
        dict(sort="commit_sequence"),
        dict(sort="aggregate_kind"),
        dict(descending=True),
    ],
)
def test_cursor_rejected_when_any_binding_condition_changes(
    index: FileQueryIndex, mutated: dict[str, object]
) -> None:
    base: dict[str, object] = {
        "project_id": "project-1",
        "sort": "record_id",
        "limit": 1,
    }
    first = index.query_spec(QuerySpec(**base))  # type: ignore[arg-type]
    assert first.next_cursor is not None

    changed = dict(base)
    changed.update(mutated)
    changed["cursor"] = first.next_cursor
    reused = index.query_spec(QuerySpec(**changed))  # type: ignore[arg-type]
    assert reused.status == "invalid_cursor"


def test_cursor_stays_valid_across_normal_commit_rebuild(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild([_row("project-1", 1), _row("project-1", 2)])
    first = index.query_spec(
        QuerySpec(project_id="project-1", sort="commit_sequence", limit=1)
    )
    assert first.next_cursor is not None
    generation = decode_cursor(first.next_cursor).generation

    # 正常业务提交发布索引：保留代次，旧游标仍可续读旧快照。
    index.rebuild([_row("project-1", 1), _row("project-1", 2), _row("project-1", 3)])
    second = index.query_spec(
        QuerySpec(
            project_id="project-1",
            sort="commit_sequence",
            limit=10,
            cursor=first.next_cursor,
        )
    )
    assert second.status == "ok"
    assert [item["commit_sequence"] for item in second.items] == [2]
    assert decode_cursor(first.next_cursor).generation == generation


def test_cursor_invalidated_by_maintenance_rebuild(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild([_row("project-1", 1), _row("project-1", 2)])
    first = index.query_spec(
        QuerySpec(project_id="project-1", sort="commit_sequence", limit=1)
    )
    assert first.next_cursor is not None
    old_generation = decode_cursor(first.next_cursor).generation

    new_generation = index.rebuild_for_maintenance(
        [_row("project-1", 1), _row("project-1", 2)]
    )
    assert new_generation == old_generation + 1

    stale = index.query_spec(
        QuerySpec(
            project_id="project-1",
            sort="commit_sequence",
            limit=10,
            cursor=first.next_cursor,
        )
    )
    assert stale.status == "invalid_cursor"


def test_new_commit_during_pagination_does_not_leak_into_snapshot(
    tmp_path: Path,
) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild([_row("project-1", 1), _row("project-1", 2)])
    first = index.query_spec(
        QuerySpec(project_id="project-1", sort="commit_sequence", limit=1)
    )
    assert [item["commit_sequence"] for item in first.items] == [1]
    assert decode_cursor(first.next_cursor).commit_id == 2

    # 分页间隙出现 seq 3：旧游标提交根仍为 2，不可见。
    index.rebuild(
        [
            _row("project-1", 1),
            _row("project-1", 2),
            _row("project-1", 3),
        ]
    )
    second = index.query_spec(
        QuerySpec(
            project_id="project-1",
            sort="commit_sequence",
            limit=10,
            cursor=first.next_cursor,
        )
    )
    assert [item["commit_sequence"] for item in second.items] == [2]

    refreshed = index.query_spec(
        QuerySpec(project_id="project-1", sort="commit_sequence", limit=10)
    )
    assert [item["commit_sequence"] for item in refreshed.items] == [1, 2, 3]


def test_same_commit_rows_paginate_without_duplicate_or_gap(tmp_path: Path) -> None:
    # 同一提交内三条记录：排序键在 commit_sequence 上相同，依赖稳定决胜列。
    rows = [
        {
            "project_id": "project-1",
            "aggregate_kind": "case",
            "record_id": f"case-{suffix}",
            "revision": 1,
            "commit_sequence": 1,
        }
        for suffix in ("a", "b", "c")
    ]
    index = FileQueryIndex(tmp_path)
    index.rebuild(rows)

    page_one = index.query_spec(QuerySpec(project_id="project-1", limit=2))
    page_two = index.query_spec(
        QuerySpec(project_id="project-1", limit=2, cursor=page_one.next_cursor)
    )
    seen = [item["record_id"] for item in (*page_one.items, *page_two.items)]
    assert seen == ["case-a", "case-b", "case-c"]
    assert page_two.next_cursor is None


def test_cursor_token_respects_frozen_length_limit_with_maximal_identifiers() -> None:
    long_id = "x" * 128
    spec = QuerySpec(
        project_id=long_id,
        aggregate_kind=long_id,
        record_id=long_id,
        sort="record_id",
        descending=True,
        limit=500,
    )
    token = encode_cursor(123, spec=spec, generation=2**31 - 1, commit_id=2**31 - 1)
    assert len(token) <= 256
    # 令牌可往返，且仍受 QuerySpec 冻结字段约束。
    decoded = decode_cursor(token)
    assert decoded.offset == 123
    QuerySpec(
        project_id=long_id,
        aggregate_kind=long_id,
        record_id=long_id,
        sort="record_id",
        descending=True,
        limit=500,
        cursor=token,
    )


def test_repository_pagination_is_project_scoped(tmp_path: Path) -> None:
    for project, record in (("project-a", "a-1"), ("project-a", "a-2"), ("project-b", "b-1")):
        unit = FileUnitOfWork(tmp_path)
        unit.begin(f"req-{record}", project)
        unit.stage_record(
            aggregate_kind="case",
            record_id=record,
            expected_revision=None,
            payload={"project_id": project},
        )
        unit.commit(f"req-{record}")

    repo = FileRecordRepository(tmp_path)
    first = repo.query(RecordQuery(project_id="project-a", limit=1))
    assert first.status == "ok"
    assert len(first.items) == 1
    assert first.next_cursor is not None

    second = repo.query(
        RecordQuery(project_id="project-a", limit=1, cursor=first.next_cursor)
    )
    assert {item.record_id for item in (*first.items, *second.items)} == {"a-1", "a-2"}

    # A 项目游标不得在 B 项目查询中复用。
    crossed = repo.query(
        RecordQuery(project_id="project-b", limit=10, cursor=first.next_cursor)
    )
    assert crossed.status == "invalid_cursor"
    only_b = repo.query(RecordQuery(project_id="project-b"))
    assert [item.record_id for item in only_b.items] == ["b-1"]


def test_query_reports_maintenance_when_index_row_missing_in_authority(
    tmp_path: Path,
) -> None:
    unit = FileUnitOfWork(tmp_path)
    unit.begin("req-1", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1"},
    )
    unit.commit("req-1")

    # 索引行指向权威边界中不存在的修订：索引已损坏，必须显式维护，
    # 绝不能静默跳过或整表扫描后返回 ok。
    import json

    index_path = tmp_path / "indexes.json"
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    raw["rows"][0]["revision"] = 99
    index_path.write_text(json.dumps(raw), encoding="utf-8")

    result = FileRecordRepository(tmp_path).query(RecordQuery(project_id="project-1"))
    assert result.status == "maintenance_required"
    assert result.items == ()
    assert result.next_cursor is None
