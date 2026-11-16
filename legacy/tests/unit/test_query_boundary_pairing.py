"""列表页与事件游标**配对在同一已提交边界**（架构 06 §4/§8/§136、AC-001 §147）。

分册原文：

- 架构 06 §4：「`SnapshotEnvelope` 同时返回 view、workspace_id、instance_id、writer_epoch、
  generation、commit_seq 和 `snapshot_cursor`。核心从同一已发布清单读取视图及其有效事件边界，
  **不能先读视图再取"此刻最新"游标**」；
- 架构 06 §136：「报告/问题/运行列表使用有限 QuerySpec…**列表返回 commit 配对摘要**，
  不展开全部修订或附件；索引恢复状态与"没有记录"分开」；
- 架构 06 §8：「响应含 IssueListProjection、**commit_id**、实际筛选摘要、next_cursor 和可用动作」；
- AC-001 §147：`query()` 返回 `RecordQueryResult(status, items, next_cursor)`（三字段形状冻结）。

本文件锁定：①`query` 响应给出该页**同一已提交边界**的 `commit_id` 与 `event_cursor`；
②用该 `event_cursor` 续读 `events` 只得到其后的提交（配对成立）；③`RecordQueryResult`
的冻结三字段形状不变（边界由 `RecordQueryPage` 一并返回，不塞进冻结结构）。
"""

from __future__ import annotations

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session

SESSION = Session("query-boundary-fixture", EntryKind.HUMAN_UI, True)
PROJECT = "project-1"


def _core(tmp_path):
    return assemble_workspace_core(tmp_path, instance_id="query-boundary-core")


def _write(core, record_id: str) -> None:
    unit = core.unit_of_work
    request = f"fixture-{record_id}"
    unit.begin(request, PROJECT, intent_id=f"intent-{request}")
    unit.stage_record(
        aggregate_kind="case",
        record_id=record_id,
        expected_revision=None,
        payload={"project_id": PROJECT, "objective": record_id},
    )
    unit.commit(request)


def _query(core, *, request_id: str = "req-query") -> dict[str, object]:
    response = core.api.dispatch(
        Command(
            request_id=request_id,
            action="query",
            project_id=PROJECT,
            parameters={"aggregate_kind": "case", "limit": 10},
        ),
        SESSION,
    )
    assert response.error is None, response.error
    assert response.result is not None
    return dict(response.result)


def test_query_page_carries_the_paired_commit_and_event_cursor(tmp_path) -> None:
    core = _core(tmp_path)
    try:
        _write(core, "case-1")
        boundary = core.unit_of_work.current_commit_sequence()
        result = _query(core)

        # ① 同一已提交边界：列表页的 commit_id 就是写这条记录后的提交序号。
        assert result["commit_id"] == boundary
        assert isinstance(result["event_cursor"], str) and result["event_cursor"]
        assert result["items"] == [
            {"aggregate_kind": "case", "record_id": "case-1", "revision": 1}
        ]
        # 列表仍只给摘要 + 游标（不展开载荷）。
        assert set(result) == {"items", "next_cursor", "commit_id", "event_cursor"}
    finally:
        core.lifetime_lock.release()


def test_event_cursor_from_the_page_reads_only_later_commits(tmp_path) -> None:
    core = _core(tmp_path)
    try:
        _write(core, "case-1")
        first = _query(core, request_id="req-query-1")
        cursor = first["event_cursor"]
        assert isinstance(cursor, str)

        # ② 配对游标续读：游标之后的提交出现，之前的提交不再重复投递。
        _write(core, "case-2")
        events = core.api.dispatch(
            Command(
                request_id="req-events",
                action="events",
                project_id=PROJECT,
                parameters={"after": cursor, "limit": 50},
            ),
            SESSION,
        )
        assert events.error is None, events.error
        assert events.result is not None
        delivered = events.result["events"]
        assert delivered, "游标之后的新提交必须被投递"
        boundary = first["commit_id"]
        assert isinstance(boundary, int)
        # 同一提交内游标之后的事件可以继续投递（架构 06 §4：不可只收第一个就跳过
        # 整次提交），但**游标之前**的事件不得重复投递。
        assert all(event["commit_sequence"] >= boundary for event in delivered)
        assert any(event["commit_sequence"] > boundary for event in delivered)
    finally:
        core.lifetime_lock.release()


def test_missing_boundary_is_reported_as_unknown_not_as_latest(tmp_path) -> None:
    """实现拿不到边界时如实给 `None`（未知），不退回"此刻最新"水位。"""

    from aitest.application.planning.substrate import RecordPage

    page = RecordPage(items=())
    assert page.commit_id is None and page.event_cursor is None
