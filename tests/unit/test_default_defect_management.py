"""默认装配下的 FR15 生产入口：doctor 广告、真实工作单元写入、issues.list 可见。

报告指出的缺口是「update_issue/record_fix/close_issue 未接默认入口」与「没有权威读取
新实际回归并持久关闭的生产用例」。本文件用**真实装配**（`assemble_workspace_core`）
证明前一半：动作真实可派发、写入落在真实工作单元、并写进 `issues.list` 索引入口。

后一半（关闭依据的权威派生）由 `tests/unit/test_saved_defect_management.py` 用已提交
事实驱动；真实 Trae 端到端关闭与真实业务对象核验仍待验收。
"""

from __future__ import annotations

from pathlib import Path

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session

_SESSION = Session(session_id="issue-probe", entry_kind=EntryKind.INTERACTIVE_CLI, interactive=True)
_PROJECT = "project-issues"


def _api(root: Path):
    return assemble_workspace_core(root, instance_id="core-issues").api


def _write(api, *, action: str, request_id: str, intent_id: str, expected: int, parameters):
    return api.dispatch(
        Command(
            request_id=request_id,
            action=action,
            project_id=_PROJECT,
            intent_id=intent_id,
            expected_revision=expected,
            parameters=parameters,
        ),
        _SESSION,
    )


def _query(api, *, request_id: str, **parameters):
    return api.dispatch(
        Command(
            request_id=request_id,
            action="query",
            project_id=_PROJECT,
            parameters=parameters,
        ),
        _SESSION,
    )


def _items(response) -> list[str]:
    assert response.error is None, response.error
    return [str(item["record_id"]) for item in response.result["items"]]


def test_default_assembly_advertises_the_issue_actions(tmp_path: Path) -> None:
    api = _api(tmp_path)
    assert {"update_issue", "record_fix", "close_issue"} <= set(api.handlers)
    assert {"create_report", "record_local_review", "export_report"} <= set(api.handlers)
    assert "copy_repair_brief" in set(api.handlers)
    doctor = api.dispatch(Command(request_id="req-doctor", action="doctor"), _SESSION)
    advertised = {str(item) for item in doctor.result["supported_actions"]}
    assert {"update_issue", "record_fix", "close_issue"} <= advertised
    assert {"create_report", "record_local_review", "export_report"} <= advertised
    assert "copy_repair_brief" in advertised


def test_repair_brief_and_export_refuse_structurally_without_saved_material(
    tmp_path: Path,
) -> None:
    """没有已保存材料时，只读简报与导出都必须结构化拒绝（不是 500、不是空成功）。"""

    api = _api(tmp_path)
    brief = api.dispatch(
        Command(
            request_id="req-brief",
            action="copy_repair_brief",
            project_id=_PROJECT,
            parameters={"issue_id": "issue-unknown"},
        ),
        _SESSION,
    )
    assert brief.error is not None and brief.error.code == "RECORD_NOT_FOUND"

    exported = api.dispatch(
        Command(
            request_id="req-export",
            action="export_report",
            project_id=_PROJECT,
            intent_id="intent-export",
            expected_revision=0,
            parameters={
                "report_id": "report-unknown",
                "kind": "markdown_summary",
                "review_ids": [],
            },
        ),
        _SESSION,
    )
    assert exported.error is not None and exported.error.code == "RECORD_NOT_FOUND"


def test_create_report_without_saved_material_refuses_structurally(tmp_path: Path) -> None:
    """没有已保存运行事实时，报告必须结构化拒绝，而不是 500/假报告。"""

    api = _api(tmp_path)
    response = api.dispatch(
        Command(
            request_id="req-report",
            action="create_report",
            project_id=_PROJECT,
            intent_id="intent-report",
            expected_revision=0,
            parameters={"report_id": "report-1", "run_id": "run-unknown", "issue_ids": []},
        ),
        _SESSION,
    )
    assert response.error is not None
    assert response.error.code != "INTERNAL_ERROR"
    assert response.error.code in {"INVALID_REQUEST", "RECORD_NOT_FOUND"}


def test_issue_written_through_the_core_is_visible_in_issues_list(tmp_path: Path) -> None:
    api = _api(tmp_path)

    created = _write(
        api,
        action="update_issue",
        request_id="req-create",
        intent_id="intent-create",
        expected=0,
        parameters={
            "issue_id": "issue-live-1",
            "title": "checkout fails on retry",
            "links": {"module_ids": ["module-checkout"], "layers": ["L2"]},
        },
    )
    assert created.error is None, created.error
    assert created.result["record_id"] == "issue-live-1"

    # 默认 OPEN 视图应能看到新草稿。
    opened = _query(api, request_id="req-open", view="OPEN")
    assert _items(opened) == ["issue-live-1"]

    # 模块 facet 也要命中（索引为每个已知模块建键，且不重复返回同一 problem）。
    by_module = _query(
        api, request_id="req-module", view="OPEN", facet="module", facet_value="module-checkout"
    )
    assert _items(by_module) == ["issue-live-1"]

    # 未知模块走独立 UNKNOWN 分支，不是空串通配。
    unknown_module = _query(
        api, request_id="req-unknown", view="OPEN", facet="module", facet_value="unknown"
    )
    assert _items(unknown_module) == []

    # 草稿 → 待确认 → 已确认；确认级别后成为阻塞项，severity 叠加查询可见。
    assert (
        _write(
            api,
            action="update_issue",
            request_id="req-await",
            intent_id="intent-await",
            expected=1,
            parameters={"issue_id": "issue-live-1", "awaiting_confirmation": True,
                        "evidence_refs": ["evidence-1"]},
        ).error
        is None
    )
    assert (
        _write(
            api,
            action="update_issue",
            request_id="req-confirm",
            intent_id="intent-confirm",
            expected=2,
            parameters={
                "issue_id": "issue-live-1",
                "confirm": {"severity": "P1", "owner": "local-owner"},
            },
        ).error
        is None
    )
    blocking = _query(
        api, request_id="req-blocking", view="OPEN", facet="blocking", facet_value="blocking"
    )
    assert _items(blocking) == ["issue-live-1"]
    severity = _query(api, request_id="req-severity", view="OPEN", severity="P1")
    assert _items(severity) == ["issue-live-1"]

    # 核实非缺陷：不再出现在 OPEN，但 ALL 与 disposition facet 仍可查（历史保留）。
    disposed = _write(
        api,
        action="update_issue",
        request_id="req-non-defect",
        intent_id="intent-non-defect",
        expected=3,
        parameters={
            "issue_id": "issue-live-1",
            "disposition": {
                "kind": "non_defect",
                "reason": "environment misconfiguration",
                "evidence_refs": ["evidence-1"],
            },
        },
    )
    assert disposed.error is None, disposed.error
    assert _items(_query(api, request_id="req-open-2", view="OPEN")) == []
    assert _items(_query(api, request_id="req-all", view="ALL")) == ["issue-live-1"]
    assert _items(
        _query(
            api, request_id="req-disposition", view="ALL",
            facet="disposition", facet_value="non_defect",
        )
    ) == ["issue-live-1"]


def test_issue_write_requires_the_command_identity(tmp_path: Path) -> None:
    api = _api(tmp_path)
    # 写动作必须带 project/intent/expected_revision（Command 层强制）。
    import pytest

    with pytest.raises(ValueError):
        Command(request_id="req-x", action="update_issue")

    # AI 中继不得执行人工动作。
    relay = Session(session_id="relay-1", entry_kind=EntryKind.AGENT_RELAY, interactive=False)
    denied = api.dispatch(
        Command(
            request_id="req-relay",
            action="update_issue",
            project_id=_PROJECT,
            intent_id="intent-relay",
            expected_revision=0,
            parameters={"issue_id": "issue-x", "title": "t"},
        ),
        relay,
    )
    assert denied.error is not None and denied.error.code == "AWAITING_USER_CONFIRMATION"
