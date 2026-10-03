"""A-05：分片查询目录、键集游标绑定、报告/问题固定目录与有界访问。

游标 v3 绑定完整查询条件（qid）、索引代次（generation）、提交根
（commit_id）与服务端保存的最后完整排序键：更换 project/筛选/排序复用
游标一律 invalid_cursor；维护重建晋升代次后旧游标失效；同代次内分页
间隙的新提交不混入旧快照；同并列键不漏不重。查询只读取前缀区间落点
的 1—2 个有界分片，无关历史增长不增加固定范围分页的访问量。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.application.planning.substrate import RecordQuery
from aitest.contracts.queries import QuerySpec
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.index import (
    FileQueryIndex,
    _issue_mask,
    _ShardDirectory,
    _ShardInfo,
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


# --------------------------------------------------------------- 游标绑定


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
    index: FileQueryIndex,
    mutated: dict[str, object],
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
    cursor_id = "c" * 32
    token = encode_cursor(
        cursor_id, spec=spec, generation=2**31 - 1, commit_id=2**31 - 1
    )
    assert len(token) <= 256
    # 令牌可往返，且仍受 QuerySpec 冻结字段约束。
    decoded = decode_cursor(token)
    assert decoded.cursor_id == cursor_id
    QuerySpec(
        project_id=long_id,
        aggregate_kind=long_id,
        record_id=long_id,
        sort="record_id",
        descending=True,
        limit=500,
        cursor=token,
    )


def test_foreign_cursor_file_is_rejected(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild([_row("project-1", 1), _row("project-1", 2)])
    first = index.query_spec(
        QuerySpec(project_id="project-1", sort="commit_sequence", limit=1)
    )
    # 游标服务端键文件被删除（清理工具误删/迁移遗漏）：明确 invalid_cursor，
    # 绝不回退起点重读制造重复页。
    stored = decode_cursor(first.next_cursor)
    index._cursor_path(stored.cursor_id).unlink()
    continued = index.query_spec(
        QuerySpec(
            project_id="project-1",
            sort="commit_sequence",
            limit=10,
            cursor=first.next_cursor,
        )
    )
    assert continued.status == "invalid_cursor"


# --------------------------------------------------------------- 仓库侧


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


def test_query_reports_maintenance_when_index_points_to_missing_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    # 分片内索引行被篡改指向权威边界不存在的修订：水合失败必须显式维护，
    # 不能静默跳过，更不能整表扫描后返回 ok。
    shard = next(
        path
        for path in (tmp_path / "indexes" / "records-kind").glob("*.json")
        if path.name != "meta.json"
    )
    raw = json.loads(shard.read_text(encoding="utf-8"))
    raw["entries"][0]["v"]["revision"] = 99
    shard.write_text(json.dumps(raw), encoding="utf-8")

    result = FileRecordRepository(tmp_path).query(RecordQuery(project_id="project-1"))
    assert result.status == "maintenance_required"
    assert result.items == ()
    assert result.next_cursor is None


# --------------------------------------------------------------- 有界访问


def test_fixed_range_page_reads_only_landing_shards_across_history_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_files: list[str] = []
    original_read = _ShardDirectory._read_shard

    def _counting_read(self: _ShardDirectory, info: _ShardInfo) -> list[dict[str, object]]:
        if self.name == "records-kind":
            read_files.append(info.file)
        return original_read(self, info)

    index = FileQueryIndex(tmp_path, shard_size=8)
    history = [
        _row("project-z", number, record=f"case-z-{number}")
        for number in range(1, 201)
    ]
    mine = [_row("project-1", number, record=f"case-{number}") for number in range(1, 5)]
    index.rebuild([*mine, *history])

    monkeypatch.setattr(_ShardDirectory, "_read_shard", _counting_read)
    first = index.query_spec(QuerySpec(project_id="project-1", limit=2))
    assert [item["record_id"] for item in first.items] == ["case-1", "case-2"]
    assert first.next_cursor is not None
    first_reads = len(read_files)
    assert first_reads <= 2

    # 无关历史翻倍：project-1 的固定范围分页仍只读同样数量的落点分片。
    monkeypatch.undo()
    more_history = [
        _row("project-z", number, record=f"case-z-{number}")
        for number in range(201, 401)
    ]
    index.rebuild([*mine, *history, *more_history])
    read_files.clear()
    monkeypatch.setattr(_ShardDirectory, "_read_shard", _counting_read)
    grown = index.query_spec(QuerySpec(project_id="project-1", limit=2))
    assert [item["record_id"] for item in grown.items] == ["case-1", "case-2"]
    assert len(read_files) == first_reads


def test_corrupt_shard_returns_maintenance_required_without_full_scan(
    tmp_path: Path,
) -> None:
    index = FileQueryIndex(tmp_path, shard_size=8)
    index.rebuild([_row("project-1", number) for number in range(1, 33)])
    # 损坏首页必然读取的链首分片（meta.json 保持可读，验证的是分片
    # 负载损坏而非目录缺失）；有界扫描必须立即报维护而非跳过。
    family_meta = json.loads(
        (tmp_path / "indexes" / "records-seq" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    first_shard = tmp_path / "indexes" / "records-seq" / family_meta["shards"][0]["file"]
    first_shard.write_text("{not json", encoding="utf-8")

    result = index.query_spec(
        QuerySpec(project_id="project-1", sort="commit_sequence", limit=5)
    )
    assert result.status == "maintenance_required"
    assert result.items == ()
    assert result.next_cursor is None


def test_incremental_publish_keeps_generic_listing_queryable(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild([_row("project-1", 1)])
    # 增量发布只插落点分片，不重建全量目录。
    index.publish([_row("project-1", 2)], commit_sequence=2)

    page = index.query_spec(
        QuerySpec(project_id="project-1", sort="commit_sequence", limit=10)
    )
    assert [item["commit_sequence"] for item in page.items] == [1, 2]


# --------------------------------------------------------------- 报告目录


def _report_row(
    report_id: str,
    revision: int,
    outcome: str,
    sequence: int,
    *,
    run_id: str = "run-1",
) -> dict[str, object]:
    return {
        "project_id": "project-1",
        "aggregate_kind": "report",
        "record_id": f"report-record-{report_id}",
        "revision": revision,
        "commit_sequence": sequence,
        "report_id": report_id,
        "run_id": run_id,
        "business_outcome": outcome,
        "published_sequence": sequence,
        "content_revision": revision,
    }


def test_reports_latest_collapses_to_current_revision(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild(
        [
            _report_row("r1", 1, "passed", 1),
            _report_row("r1", 2, "failed", 2),
            _report_row("r2", 1, "passed", 3),
        ]
    )

    failed = index.query_spec(
        QuerySpec(project_id="project-1", business_outcome="failed", limit=10)
    )
    assert [item["report_id"] for item in failed.items] == ["r1"]
    assert failed.items[0]["content_revision"] == 2

    passed = index.query_spec(
        QuerySpec(project_id="project-1", business_outcome="passed", limit=10)
    )
    assert [item["report_id"] for item in passed.items] == ["r2"]

    all_reports = index.query_spec(
        QuerySpec(project_id="project-1", aggregate_kind="report", limit=10)
    )
    assert sorted(item["report_id"] for item in all_reports.items) == ["r1", "r2"]
    r1 = next(item for item in all_reports.items if item["report_id"] == "r1")
    assert r1["business_outcome"] == "failed"


def test_reports_by_id_direct_lookup_and_outcome_filter(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild([_report_row("r1", 2, "failed", 2)])

    by_report = index.query_spec(
        QuerySpec(project_id="project-1", report_id="r1", limit=10)
    )
    assert [item["report_id"] for item in by_report.items] == ["r1"]

    by_run = index.query_spec(
        QuerySpec(project_id="project-1", run_id="run-1", limit=10)
    )
    assert [item["report_id"] for item in by_run.items] == ["r1"]

    mismatched = index.query_spec(
        QuerySpec(
            project_id="project-1",
            report_id="r1",
            business_outcome="passed",
            limit=10,
        )
    )
    assert mismatched.items == ()

    unknown = index.query_spec(
        QuerySpec(project_id="project-1", report_id="nope", limit=10)
    )
    assert unknown.status == "ok"
    assert unknown.items == ()


def test_incremental_report_revision_replaces_old_state_keys(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild([_report_row("r1", 1, "passed", 1)])
    index.publish([_report_row("r1", 2, "failed", 2)], commit_sequence=2)

    assert index.query_spec(
        QuerySpec(project_id="project-1", business_outcome="passed", limit=10)
    ).items == ()
    failed = index.query_spec(
        QuerySpec(project_id="project-1", business_outcome="failed", limit=10)
    )
    assert [item["content_revision"] for item in failed.items] == [2]
    # 通用记录目录保留全部历史修订（业务事实不折叠）。
    history = index.query_spec(
        QuerySpec(
            project_id="project-1",
            aggregate_kind="report",
            record_id="report-record-r1",
            sort="revision",
            limit=10,
        )
    )
    assert [item["revision"] for item in history.items] == [1, 2]


# --------------------------------------------------------------- 问题目录


def _issue_entries(
    *,
    views: tuple[str, ...] = ("OPEN", "ALL"),
    modules: tuple[str, ...] = ("m1",),
    severity: str = "high",
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for view in views:
        entries.append({"view": view, "mask": 0})
        entries.append({"view": view, "mask": 1, "severity": severity})
        for module in modules:
            entries.append({"view": view, "mask": 2, "facet_value": module})
            entries.append(
                {
                    "view": view,
                    "mask": 3,
                    "facet_value": module,
                    "severity": severity,
                }
            )
    return entries


def _issue_row(
    issue_id: str,
    updated_sequence: int,
    *,
    entries: list[dict[str, object]] | None = None,
    commit_sequence: int | None = None,
) -> dict[str, object]:
    return {
        "project_id": "project-1",
        "aggregate_kind": "issue",
        "record_id": issue_id,
        "revision": 1,
        "commit_sequence": commit_sequence or updated_sequence,
        "updated_sequence": updated_sequence,
        "severity": "high",
        "issue_index_entries": entries
        if entries is not None
        else _issue_entries(),
    }


def test_issue_mask_codes_match_fixed_14_contract() -> None:
    expected = {
        ("NONE", False): 0,
        ("NONE", True): 1,
        ("module", False): 2,
        ("module", True): 3,
        ("layer", False): 4,
        ("layer", True): 5,
        ("review_state", False): 6,
        ("review_state", True): 7,
        ("workflow_state", False): 8,
        ("workflow_state", True): 9,
        ("disposition", False): 10,
        ("disposition", True): 11,
        ("blocking", False): 12,
        ("blocking", True): 13,
    }
    for (facet, with_severity), mask in expected.items():
        assert _issue_mask(facet, "high" if with_severity else None) == mask


def test_issue_list_facet_mask_and_severity_routing(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild(
        [
            _issue_row(
                "i1",
                2,
                entries=_issue_entries(modules=("m1", "m2")),
            ),
            _issue_row(
                "i2",
                1,
                entries=_issue_entries(
                    views=("ALL",),
                    modules=("m1",),
                ),
            ),
        ]
    )

    open_module = index.query_spec(
        QuerySpec(
            project_id="project-1",
            view="OPEN",
            facet="module",
            facet_value="m1",
            sort="updated_sequence",
            limit=10,
        )
    )
    # 多模块问题按准确模块各建一键，但同一模块查询不得重复返回该问题。
    assert [item["record_id"] for item in open_module.items] == ["i1"]

    module_severity = index.query_spec(
        QuerySpec(
            project_id="project-1",
            facet="module",
            facet_value="m2",
            severity="high",
            sort="updated_sequence",
            limit=10,
        )
    )
    assert [item["record_id"] for item in module_severity.items] == ["i1"]

    severity_only = index.query_spec(
        QuerySpec(
            project_id="project-1",
            severity="high",
            sort="updated_sequence",
            limit=10,
        )
    )
    # severity 单独给出时 view 缺省 OPEN：i2 只有 ALL 入口，不出现。
    assert [item["record_id"] for item in severity_only.items] == ["i1"]

    all_module = index.query_spec(
        QuerySpec(
            project_id="project-1",
            view="ALL",
            facet="module",
            facet_value="m1",
            sort="updated_sequence",
            limit=10,
        )
    )
    assert sorted(item["record_id"] for item in all_module.items) == ["i1", "i2"]

    unknown_module = index.query_spec(
        QuerySpec(
            project_id="project-1",
            facet="module",
            facet_value="missing",
            sort="updated_sequence",
            limit=10,
        )
    )
    assert unknown_module.status == "ok"
    assert unknown_module.items == ()


def test_issue_updates_replace_old_projection_keys(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild(
        [_issue_row("i1", 1, entries=_issue_entries(modules=("m1",)))]
    )
    moved = _issue_row(
        "i1",
        5,
        commit_sequence=6,
        entries=_issue_entries(modules=("m9",)),
    )
    index.publish([moved], commit_sequence=6)

    old_key = index.query_spec(
        QuerySpec(
            project_id="project-1",
            facet="module",
            facet_value="m1",
            sort="updated_sequence",
            limit=10,
        )
    )
    assert old_key.items == ()
    new_key = index.query_spec(
        QuerySpec(
            project_id="project-1",
            facet="module",
            facet_value="m9",
            sort="updated_sequence",
            limit=10,
        )
    )
    assert [item["record_id"] for item in new_key.items] == ["i1"]
    assert new_key.items[0]["updated_sequence"] == 5


def test_facet_value_requires_facet_and_view_defaults_open() -> None:
    with pytest.raises(ValidationError):
        QuerySpec(project_id="p", facet_value="m1")
    spec = QuerySpec(project_id="p", severity="high")
    assert spec.view == "OPEN"
    spec2 = QuerySpec(project_id="p", facet="module", facet_value="m1")
    assert spec2.view == "OPEN"


# --------------------------------------------------------------- 事件边界


def test_list_page_carries_event_cursor_at_same_commit_boundary(
    tmp_path: Path,
) -> None:
    journal = FileEventJournal(tmp_path, instance_id="core")
    unit = FileUnitOfWork(tmp_path, journal=journal)
    unit.begin("req-1", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-1"},
    )
    unit.commit("req-1")

    index = FileQueryIndex(tmp_path, journal=journal)
    page = index.query_spec(QuerySpec(project_id="project-1", limit=10))
    assert page.status == "ok"
    assert page.commit_id == 1
    assert page.event_cursor == journal.snapshot_cursor(commit_sequence=1)
