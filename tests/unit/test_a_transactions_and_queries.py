"""A 包事务、身份、有限查询与只追加存储单元测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from aitest.bootstrap import create_api
from aitest.contracts.commands import Command
from aitest.contracts.queries import QuerySpec
from aitest.infrastructure.file_store.index import FileQueryIndex, decode_cursor
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, Session


def test_workspace_allows_only_one_active_write_transaction(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    started = unit.begin("request-1", "project-1")
    assert started == {"request_id": "request-1", "state": "active"}

    with pytest.raises(RuntimeError, match="transaction already open"):
        unit.begin("request-2", "project-1")

    assert unit.rollback("request-1")["state"] == "rolled_back"


def test_begin_commit_and_rollback_flow(tmp_path: Path) -> None:
    unit = FileUnitOfWork(tmp_path)
    repository = FileRecordRepository(tmp_path)

    unit.begin("request-commit", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=0,
        payload={"project_id": "project-1", "summary": "committed"},
    )
    committed = unit.commit("request-commit")
    assert committed["state"] == "committed"
    assert repository.read(
        aggregate_kind="case", record_id="case-1", revision=1
    ).payload["summary"] == "committed"

    unit.begin("request-rollback", "project-1")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-2",
        expected_revision=0,
        payload={"project_id": "project-1", "summary": "discarded"},
    )
    rolled_back = unit.rollback("request-rollback")
    assert rolled_back["state"] == "rolled_back"
    assert repository.current_revision("case", "case-2") == 0


def test_request_id_is_deduplicated_and_cannot_replace_intent_id() -> None:
    calls = 0

    def handler(command: Command) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"request_id": command.request_id}

    api = create_api(handlers={"query": handler})
    session = Session("session-1", EntryKind.INTERACTIVE_CLI)
    command = Command(request_id="request-1", action="query")

    assert api.dispatch(command, session) == api.dispatch(command, session)
    assert calls == 1

    conflict = api.dispatch(
        Command(request_id="request-1", action="events"), session
    )
    assert conflict.error is not None
    assert conflict.error.code == "REQUEST_CONFLICT"

    with pytest.raises(ValidationError, match="request_id must not be used as intent_id"):
        Command(
            request_id="same-id",
            intent_id="same-id",
            action="start_run",
            project_id="project-1",
            expected_revision=0,
        )


def test_intent_retry_reuses_revision_and_changed_input_conflicts(tmp_path: Path) -> None:
    repository = FileRecordRepository(tmp_path)
    payload = {"project_id": "project-1", "summary": "stable"}

    first = repository.append_intent(
        intent_id="intent-1",
        kind="case",
        record_id="case-1",
        expected_revision=0,
        payload=payload,
    )
    repeated = repository.append_intent(
        intent_id="intent-1",
        kind="case",
        record_id="case-1",
        expected_revision=0,
        payload=payload,
    )
    assert (first, repeated) == (1, 1)
    assert repository.current_revision("case", "case-1") == 1

    with pytest.raises(ValueError, match="intent conflict"):
        repository.append_intent(
            intent_id="intent-1",
            kind="case",
            record_id="case-1",
            expected_revision=1,
            payload={"project_id": "project-1", "summary": "changed"},
        )


def test_query_spec_missing_index_never_scans_records(tmp_path: Path) -> None:
    (tmp_path / "records.json").write_text("not valid json", encoding="utf-8")
    index = FileQueryIndex(tmp_path)

    result = index.query_spec(QuerySpec(project_id="project-1", limit=20))

    assert result.status == "maintenance_required"
    assert result.items == ()
    assert result.next_cursor is None


def test_query_spec_invalid_cursor_is_explicit(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild(
        [
            {
                "project_id": "project-1",
                "aggregate_kind": "case",
                "record_id": "case-1",
                "revision": 1,
                "commit_sequence": 1,
            }
        ]
    )

    result = index.query_spec(
        QuerySpec(project_id="project-1", cursor="invalid", limit=20)
    )

    assert result.status == "invalid_cursor"
    assert result.items == ()


def test_query_spec_fixed_sort_and_bounded_cursor_page(tmp_path: Path) -> None:
    index = FileQueryIndex(tmp_path)
    index.rebuild(
        [
            {
                "project_id": "project-1",
                "aggregate_kind": "case",
                "record_id": f"case-{number}",
                "revision": 1,
                "commit_sequence": number,
            }
            for number in (3, 1, 2)
        ]
    )

    first = index.query_spec(
        QuerySpec(
            project_id="project-1",
            sort="commit_sequence",
            limit=2,
        )
    )
    second = index.query_spec(
        QuerySpec(
            project_id="project-1",
            sort="commit_sequence",
            limit=2,
            cursor=first.next_cursor,
        )
    )

    assert [item["commit_sequence"] for item in first.items] == [1, 2]
    # 游标为绑定末行排序键的不透明 token（A-05：不再是脆弱 offset）。
    assert first.next_cursor is not None
    assert decode_cursor(first.next_cursor)[0] == 2
    assert [item["commit_sequence"] for item in second.items] == [3]
    assert second.next_cursor is None

    # 分页间隙索引重建并插入新行，续读仍不重复上一页已见 ID。
    index.rebuild(
        [
            {
                "project_id": "project-1",
                "aggregate_kind": "case",
                "record_id": f"case-{number}",
                "revision": 1,
                "commit_sequence": number,
            }
            for number in (1, 2, 4)
        ]
    )
    continued = index.query_spec(
        QuerySpec(
            project_id="project-1",
            sort="commit_sequence",
            limit=2,
            cursor=first.next_cursor,
        )
    )
    assert [item["record_id"] for item in continued.items] == ["case-4"]


def test_permanent_records_are_append_only_and_have_no_delete_api(tmp_path: Path) -> None:
    repository = FileRecordRepository(tmp_path)
    repository.append(
        "case",
        "case-1",
        0,
        {"project_id": "project-1", "summary": "revision-1"},
    )
    repository.append(
        "case",
        "case-1",
        1,
        {"project_id": "project-1", "summary": "revision-2"},
    )

    assert repository.read(
        aggregate_kind="case", record_id="case-1", revision=1
    ).payload["summary"] == "revision-1"
    assert repository.read(
        aggregate_kind="case", record_id="case-1", revision=2
    ).payload["summary"] == "revision-2"
    assert not hasattr(repository, "delete")
    assert not hasattr(repository, "remove")

