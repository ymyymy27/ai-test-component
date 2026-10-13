"""CORE-001 1.81 合同检查：`query` 响应含**同一已提交边界**的配对信息。

合同正文（`docs/接口对接/进行中/CORE-001-A包公共接口/contract.md`）1.81：

> 按一期架构 06 §4/§8/§136「核心从同一已发布清单读取视图及其有效事件边界，**不能先读视图再取
> “此刻最新”游标**」「列表返回 **commit 配对摘要**」「响应含 … **commit_id** …」，`query` 成功响应
> 在既有 `items`/`next_cursor` 之外**追加** `commit_id` 与 `event_cursor`…取不到时如实返回 `null`，
> **不退回“此刻最新”水位**。既有字段语义、协议版本、生成 Schema/夹具与已保存记录语义不变。

本文件只做**合同层面**的检查（键集合、类型、边界一致性、既有字段未变）；行为与续读语义的用例在
`tests/unit/test_query_boundary_pairing.py`。
"""

from __future__ import annotations

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session

SESSION = Session("core-001-fixture", EntryKind.HUMAN_UI, True)
PROJECT = "project-1"


def test_query_response_shape_matches_core_001_1_81(tmp_path) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="core-001-query")
    try:
        unit = core.unit_of_work
        unit.begin("req-write", PROJECT, intent_id="intent-write")
        unit.stage_record(
            aggregate_kind="case",
            record_id="case-1",
            expected_revision=None,
            payload={"project_id": PROJECT, "objective": "case-1"},
        )
        unit.commit("req-write")

        response = core.api.dispatch(
            Command(
                request_id="req-query",
                action="query",
                project_id=PROJECT,
                parameters={"aggregate_kind": "case", "limit": 10},
            ),
            SESSION,
        )
        assert response.error is None, response.error
        result = response.result
        assert result is not None

        # 既有字段语义不变（1.81 只追加）。
        assert result["items"] == [
            {"aggregate_kind": "case", "record_id": "case-1", "revision": 1}
        ]
        assert "next_cursor" in result
        # 追加字段：配对边界，类型严格；取不到时是 None（未知），不是"最新水位"。
        assert set(result) == {"items", "next_cursor", "commit_id", "event_cursor"}
        commit_id = result["commit_id"]
        event_cursor = result["event_cursor"]
        assert commit_id is None or type(commit_id) is int
        assert event_cursor is None or (isinstance(event_cursor, str) and event_cursor)
        # 本例有已提交记录 ⇒ 两者都必须给出，且 commit_id 与写后的提交序号一致。
        assert commit_id == core.unit_of_work.current_commit_sequence()
        assert isinstance(event_cursor, str)
    finally:
        core.lifetime_lock.release()
