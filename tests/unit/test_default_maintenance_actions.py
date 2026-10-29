"""默认装配的「查询/维护」只读动作（架构 05 第 3 节）。

背景（2026-10-11）：`storage_usage` 与 `events` 的实现早已存在
（`FileMaintenanceService.diagnose()` / `FileEventJournal.read()`），但从未注册，
因此默认装配里 doctor 之外没有可用的查询/维护面。本文件锁定它们**真实可派发**、
返回结构化结果，且参数问题按 `B_INVALID_PARAMETER` 回执而不是 INTERNAL_ERROR。
"""

from __future__ import annotations

from pathlib import Path

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session

_SESSION = Session(session_id="maintenance-probe", entry_kind=EntryKind.INTERACTIVE_CLI)


def _assembled_api(root: Path):
    return assemble_workspace_core(root, instance_id="core-maintenance").api


def test_default_assembly_registers_the_read_only_maintenance_actions(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    assert {"storage_usage", "events"} <= set(api.handlers)


def test_storage_usage_reports_the_workspace_space_and_integrity(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    response = api.dispatch(
        Command(request_id="req-usage", action="storage_usage"), _SESSION
    )
    assert response.error is None, response.error
    result = response.result
    assert result is not None
    for field in (
        "total_bytes",
        "total_files",
        "business_bytes",
        "temp_bytes",
        "backup_bytes",
        "other_bytes",
        "reclaimable_bytes",
        "reclaimable_files",
        "integrity_ok",
        "integrity_errors",
    ):
        assert field in result, field


def test_events_reads_the_committed_journal_with_a_bounded_page(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    response = api.dispatch(
        Command(request_id="req-events", action="events", parameters={"limit": 5}), _SESSION
    )
    assert response.error is None, response.error
    result = response.result
    assert result is not None
    assert set(result) >= {"status", "events", "next_cursor"}


def test_events_rejects_a_non_finite_limit_as_a_parameter_problem(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    response = api.dispatch(
        Command(request_id="req-events-bad", action="events", parameters={"limit": 0}), _SESSION
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"


def test_events_result_shape_is_the_panel_contract(tmp_path: Path) -> None:
    """`events` 的结果形状是**共享面板**的消费契约（`event-stream.spec.ts` 逐字解析）。

    - 成功：`status == "ok"`，`events` 为数组，`next_cursor` 为字符串或 null；
    - 游标非法：`status == "snapshot_required"`（**在 result 里**，不是宿主层错误），
      面板据此保持原游标并显示代码，绝不静默重置成"从头开始"。
    """

    api = _assembled_api(tmp_path)
    first = api.dispatch(
        Command(request_id="req-events-shape", action="events", parameters={"limit": 5}), _SESSION
    )
    assert first.error is None, first.error
    result = first.result
    assert result is not None
    assert result["status"] == "ok"
    assert isinstance(result["events"], list)
    assert result["next_cursor"] is None or isinstance(result["next_cursor"], str)

    bad = api.dispatch(
        Command(
            request_id="req-events-bad-cursor",
            action="events",
            parameters={"limit": 5, "cursor": "not-a-cursor"},
        ),
        _SESSION,
    )
    assert bad.error is None, bad.error
    assert bad.result is not None
    assert bad.result["status"] == "snapshot_required"


def test_doctor_advertises_the_maintenance_actions(tmp_path: Path) -> None:
    api = _assembled_api(tmp_path)
    response = api.dispatch(Command(request_id="req-doctor", action="doctor"), _SESSION)
    assert response.error is None
    assert response.result is not None
    advertised = response.result["supported_actions"]
    assert "storage_usage" in advertised
    assert "events" in advertised
