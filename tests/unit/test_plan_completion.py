"""Behaviour tests for the 2026-11-16 completion checklist (A/B/D items)."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main
from aitest.errors import AiTestError
from aitest.runtime_commands import cmd_run_start
from aitest.storage.files import read_json
from aitest.storage.workspace import Workspace

Capsys = pytest.CaptureFixture[str]


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)
    monkeypatch.delenv("AITEST_GITHUB_TOKEN", raising=False)


def call(
    tmp_path: Path,
    command: str,
    payload: dict[str, Any],
    capsys: Capsys,
    confirm: bool = False,
) -> dict[str, Any]:
    workspace = tmp_path / "ws"
    request_id = payload.pop("request_id", f"{command}-{uuid.uuid4().hex[:8]}")
    envelope = {"command": command, "request_id": request_id, "payload": payload}
    input_file = tmp_path / "req.json"
    input_file.write_text(json.dumps(envelope), encoding="utf-8")
    argv = [command, "--workspace", str(workspace), "--input", str(input_file)]
    if confirm:
        argv.append("--confirm")
    main(argv)
    return json.loads(capsys.readouterr().out.strip())


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "ws")


def _setup(
    tmp_path: Path, capsys: Capsys, items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    task = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "下单验证",
            "acceptance_items": items
            or [{"text": "下单后列表出现订单", "observable": True}],
        },
        capsys,
    )["data"]
    return {"project": project, "task": task}


def _plan(
    tmp_path: Path,
    capsys: Capsys,
    setup: dict[str, Any],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    item = setup["task"]["acceptance_items"][0]["item_id"]
    for case in cases:
        case.setdefault("acceptance_item_id", item)
    return call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
            "cases": cases,
        },
        capsys,
    )["data"]


def _case(
    setup: dict[str, Any],
    *,
    layer: str = "L2",
    expected: str = "命令成功",
    source: str = "下单后列表出现订单",
    method: str = "command: echo ok",
    preconditions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "acceptance_item_id": setup["task"]["acceptance_items"][0]["item_id"],
        "layer": layer,
        "expected": expected,
        "expectation_source": source,
        "verify_method": method,
        "preconditions": preconditions or [],
    }


def _wait_completed(
    tmp_path: Path, capsys: Capsys, project_id: str, run_id: str, attempts: int = 800
) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for _ in range(attempts):
        status = call(
            tmp_path,
            "run.status",
            {"project_id": project_id, "run_id": run_id},
            capsys,
        )
        if "data" not in status:
            raise AssertionError(status)
        if status["data"]["run"]["status"] != "running":
            return status
        time.sleep(0.1)
    return status


def _wait_for(directory: Path, pattern: str, attempts: int = 600) -> list[Path]:
    for _ in range(attempts):
        found = sorted(directory.glob(pattern)) if directory.is_dir() else []
        if found:
            return found
        time.sleep(0.1)
    return []


def test_task_update_increments_revision_and_keeps_old_copy(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    task = setup["task"]
    item_id = task["acceptance_items"][0]["item_id"]
    updated = call(
        tmp_path,
        "task.update",
        {
            "project_id": project_id,
            "task_id": task["task_id"],
            "expected_revision": 1,
            "patch": {
                "title": "下单验证二期",
                "preconditions": ["服务已启动"],
                "acceptance_items": [{"item_id": item_id, "text": "列表出现两条订单"}],
            },
        },
        capsys,
    )
    assert updated["ok"] is True
    assert updated["data"]["revision"] == 2

    ws = _workspace(tmp_path)
    old = read_json(
        ws.project_dir(project_id) / "tasks" / "revisions" / task["task_id"] / "1.json"
    )
    assert old["title"] == "下单验证"
    assert old["revision"] == 1

    shown = call(
        tmp_path,
        "task.show",
        {"project_id": project_id, "task_id": task["task_id"]},
        capsys,
    )["data"]["task"]
    assert shown["title"] == "下单验证二期"
    assert shown["revision"] == 2
    assert shown["preconditions"] == ["服务已启动"]
    assert shown["acceptance_items"][0]["item_id"] == item_id

    conflict = call(
        tmp_path,
        "task.update",
        {
            "project_id": project_id,
            "task_id": task["task_id"],
            "expected_revision": 1,
            "patch": {"title": "不应生效"},
        },
        capsys,
    )
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "RUN_STATE_CONFLICT"


def test_run_manual_records_steps_and_aggregates_case_result(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(setup, layer="L3", method="manual:"),
            _case(setup, layer="L3", method="manual:", expected="人工确认可见"),
        ],
    )
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    status = _wait_completed(tmp_path, capsys, project_id, started["run_id"])
    assert status["data"]["run"]["status"] == "completed"
    assert [case["result"] for case in status["data"]["run"]["cases"]] == [
        "manual_pending",
        "manual_pending",
    ]
    assert status["data"]["outcome"] == "incomplete"

    cases = call(
        tmp_path, "plan.show", {"project_id": project_id, "plan_id": plan["plan_id"]}, capsys
    )["data"]["plan"]["cases"]
    first_case = cases[0]["case_id"]
    evidence = tmp_path / "shot.png"
    evidence.write_bytes(b"fake-image")
    recorded = call(
        tmp_path,
        "run.manual",
        {
            "project_id": project_id,
            "run_id": started["run_id"],
            "case_id": first_case,
            "step": "打开订单列表",
            "result": "passed",
            "evidence": [str(evidence)],
        },
        capsys,
    )
    assert recorded["ok"] is True
    assert recorded["data"]["result"] == "passed"

    ws = _workspace(tmp_path)
    run_dir = ws.project_dir(project_id) / "runs" / started["run_id"]
    log = read_json(run_dir / "manual" / f"{first_case}.json")
    assert log["steps"][0]["result"] == "passed"
    ref = log["steps"][0]["evidence_refs"][0]
    assert (run_dir / ref).is_file()

    failed = call(
        tmp_path,
        "run.manual",
        {
            "project_id": project_id,
            "run_id": started["run_id"],
            "case_id": first_case,
            "step": "检查订单详情",
            "result": "failed",
            "note": "金额错误",
        },
        capsys,
    )
    assert failed["data"]["result"] == "failed"
    status = _wait_completed(tmp_path, capsys, project_id, started["run_id"])
    assert status["data"]["run"]["cases"][0]["result"] == "failed"
    assert status["data"]["run"]["cases"][1]["result"] == "manual_pending"
    assert status["data"]["outcome"] == "failed"


def test_run_start_reuses_confirmation_and_requires_new_one_after_env_change(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = _plan(tmp_path, capsys, setup, [_case(setup)])

    first = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    assert first["confirmation"]["reused"] is False
    _wait_completed(tmp_path, capsys, project_id, first["run_id"])

    second = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
    )
    assert second["ok"] is True
    assert second["data"]["confirmation"]["reused"] is True
    assert (
        second["data"]["confirmation"]["confirmation_id"]
        == first["confirmation"]["confirmation_id"]
    )
    _wait_completed(tmp_path, capsys, project_id, second["data"]["run_id"])

    ws = _workspace(tmp_path)
    (ws.project_dir(project_id) / "project.config.json").write_text(
        json.dumps({"services": {"web": {"health": "", "start": ""}}}), encoding="utf-8"
    )
    stale = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "PLAN_STALE"

    plan = _plan(tmp_path, capsys, setup, [_case(setup)])
    changed = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
    )
    assert changed["ok"] is False
    assert changed["error"]["code"] == "CONFIRMATION_REQUIRED"

    third = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )
    assert third["ok"] is True
    assert third["data"]["confirmation"]["reused"] is False
    assert (
        third["data"]["confirmation"]["confirmation_id"]
        != first["confirmation"]["confirmation_id"]
    )
    _wait_completed(tmp_path, capsys, project_id, third["data"]["run_id"])


def test_concurrent_run_start_returns_run_active(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    payload = _plan(
        tmp_path,
        capsys,
        setup,
        [
            {
                **_case(setup, method="script:slow.py"),
                "script_name": "slow.py",
                "script_content": "import time\ntime.sleep(2)\n",
            }
        ],
    )
    ws = _workspace(tmp_path)
    results: list[tuple[str, Any]] = []

    def start() -> None:
        try:
            results.append(
                (
                    "ok",
                    cmd_run_start(
                        ws, {"project_id": project_id, "plan_id": payload["plan_id"]}, True
                    ),
                )
            )
        except AiTestError as exc:
            results.append(("error", exc.info.code))

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    outcomes = sorted(item[0] for item in results)
    assert outcomes == ["error", "ok"]
    errors = [item[1] for item in results if item[0] == "error"]
    assert errors == ["RUN_ACTIVE"]
    run_id = next(item[1]["run_id"] for item in results if item[0] == "ok")
    _wait_completed(tmp_path, capsys, project_id, run_id)


def test_report_contains_issues_analysis_layers_and_github_mapping(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    module = call(
        tmp_path,
        "module.add",
        {"project_id": project_id, "name": "orders"},
        capsys,
    )["data"]
    assert module["module_id"]
    call(
        tmp_path,
        "delivery.save",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "changed_modules": ["orders", "misc/helper.py"],
            "dependencies": ["orders"],
            "run_commands": ["python -m app"],
            "commit": "abc123",
        },
        capsys,
    )
    ws = _workspace(tmp_path)
    github_dir = ws.project_dir(project_id) / "github"
    github_dir.mkdir(parents=True, exist_ok=True)
    (github_dir / "abc123.json").write_text(
        json.dumps(
            {
                "repo": "o/r",
                "branch": "main",
                "commit": "abc123",
                "files": ["src/orders/app.py", "docs/readme.md"],
            }
        ),
        encoding="utf-8",
    )
    plan = _plan(tmp_path, capsys, setup, [_case(setup)])
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    _wait_completed(tmp_path, capsys, project_id, started["run_id"])

    generated = call(
        tmp_path,
        "report.generate",
        {"project_id": project_id, "run_id": started["run_id"]},
        capsys,
    )
    assert generated["ok"] is True
    report_id = generated["data"]["report_id"]
    report = read_json(ws.project_dir(project_id) / "reports" / f"{report_id}.json")
    assert report["issues"] == []
    assert report["analysis"] == []
    assert report["layers"] == {"L1": 0, "L2": 1, "L3": 0}
    assert report["rules_version_id"] is None
    assert report["source_commit"] == "abc123"
    assert report["environment"]["python"]
    assert report["environment"]["services"] == []
    assert report["github"]["files"] == ["src/orders/app.py", "docs/readme.md"]
    assert report["changed_modules"] == ["orders"]
    assert report["unmapped_files"] == ["misc/helper.py", "docs/readme.md"]
    assert report["uncovered_items"] == []
    assert report["case_details"][0]["result"] == "passed"
    assert any(item["exists"] for item in report["evidence_index"])

    export_path = tmp_path / "full-report.md"
    exported = call(
        tmp_path,
        "report.export",
        {"project_id": project_id, "report_id": report_id, "path": str(export_path)},
        capsys,
    )
    assert exported["ok"] is True
    text = export_path.read_text(encoding="utf-8")
    for section in (
        "## 范围与覆盖",
        "## 未覆盖清单",
        "## 分层计数",
        "## 用例明细",
        "## 失败证据",
        "## AI 分析",
        "## 问题",
        "## Mock 判定",
        "## 验收意见",
        "## 证据索引",
    ):
        assert section in text
    assert "abc123" in text


def test_worker_generates_report_and_issue_draft_after_failure(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = _plan(tmp_path, capsys, setup, [_case(setup, method="command: exit 1")])
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    status = _wait_completed(tmp_path, capsys, project_id, started["run_id"])
    assert status["data"]["outcome"] == "failed"

    ws = _workspace(tmp_path)
    reports = _wait_for(ws.project_dir(project_id) / "reports", "*.json")
    assert reports, "worker did not generate a report"
    report = read_json(reports[0])
    assert report["outcome"] == "failed"
    assert report["analysis"][0]["status"] == "not_generated"
    assert report["issues"] and report["issues"][0]["title"]

    issues = _wait_for(ws.project_dir(project_id) / "issues", "*.json")
    assert issues, "worker did not draft an issue"
    issue = read_json(issues[0])
    assert issue["status"] == "pending"
    assert issue["signature"].endswith(":unknown")

    cases = call(
        tmp_path, "plan.show", {"project_id": project_id, "plan_id": plan["plan_id"]}, capsys
    )["data"]["plan"]["cases"]
    added = call(
        tmp_path,
        "issue.add",
        {
            "project_id": project_id,
            "run_id": started["run_id"],
            "case_id": cases[0]["case_id"],
            "title": "重复草稿",
        },
        capsys,
    )
    assert added["ok"] is True
    assert added["data"]["existing"] is True
    assert added["data"]["issue_id"] == issue["issue_id"]


def test_plan_marks_unverified_expectation_and_warns_missing_preconditions(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = _plan(
        tmp_path,
        capsys,
        setup,
        [
            _case(setup, expected="应该成功", source=""),
            _case(
                setup,
                expected="列表出现订单",
                source="下单后列表出现订单",
                preconditions=["服务已启动"],
            ),
        ],
    )
    assert any("预期未证实" in warning for warning in plan["warnings"])
    assert any("缺少前置条件" in warning for warning in plan["warnings"])
    assert len(plan["unverified_cases"]) == 1

    shown = call(
        tmp_path, "plan.show", {"project_id": project_id, "plan_id": plan["plan_id"]}, capsys
    )["data"]["plan"]
    assert [case["expectation_verified"] for case in shown["cases"]] == [False, True]
    assert any("预期未证实" in warning for warning in shown["cases"][0]["warnings"])
    assert not any("缺少前置条件" in warning for warning in shown["cases"][1]["warnings"])

    updated = call(
        tmp_path,
        "plan.update",
        {
            "project_id": project_id,
            "plan_id": plan["plan_id"],
            "patch": {
                "cases": [
                    {
                        **_case(setup, source="与验收项无关的猜测"),
                    }
                ]
            },
        },
        capsys,
    )
    assert updated["ok"] is True
    assert updated["data"]["unverified_cases"]
    shown = call(
        tmp_path, "plan.show", {"project_id": project_id, "plan_id": plan["plan_id"]}, capsys
    )["data"]["plan"]
    assert shown["cases"][0]["expectation_verified"] is False


def test_report_lists_applicable_items_outside_selected_scope(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        items=[
            {"text": "下单成功", "observable": True},
            {"text": "库存扣减一次", "observable": True},
            {"text": "订单可以取消", "observable": True},
        ],
    )
    project_id = setup["project"]["project_id"]
    item_ids = [item["item_id"] for item in setup["task"]["acceptance_items"]]
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item_ids[0]]},
            "cases": [
                {
                    "acceptance_item_id": item_ids[0],
                    "layer": "L2",
                    "expected": "命令成功",
                    "expectation_source": "下单成功",
                    "verify_method": "command: echo ok",
                }
            ],
        },
        capsys,
    )["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    _wait_completed(tmp_path, capsys, project_id, started["run_id"])

    status = call(
        tmp_path,
        "run.status",
        {"project_id": project_id, "run_id": started["run_id"]},
        capsys,
    )["data"]
    expected = [
        f"{item_ids[1]}：库存扣减一次",
        f"{item_ids[2]}：订单可以取消",
    ]
    assert status["uncovered_items"] == expected

    generated = call(
        tmp_path,
        "report.generate",
        {"project_id": project_id, "run_id": started["run_id"]},
        capsys,
    )["data"]
    assert generated["coverage"] == {"selected": 1, "applicable": 3}
    assert generated["uncovered_items"] == expected


class _GeneratedCaseClient:
    def __init__(self, sources: list[str]) -> None:
        self.sources = sources

    def generate_cases(
        self,
        task: dict[str, Any],
        selected_items: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del task, context
        return [
            {
                "acceptance_item_id": item["item_id"],
                "layer": "L2",
                "expected": "命令成功",
                "expectation_source": source,
                "verify_method": "command: echo ok",
                "depends_on": [],
            }
            for item, source in zip(selected_items, self.sources, strict=False)
        ]


def test_generated_cases_align_expectation_source_with_item_text(
    tmp_path: Path, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        items=[
            {"text": "下单后列表出现订单", "observable": True},
            {"text": "库存扣减一次", "observable": True},
        ],
    )
    monkeypatch.setattr(
        "aitest.runtime_commands.build_client",
        lambda *args, **kwargs: _GeneratedCaseClient(["app/services.py:42", ""]),
    )
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "all_applicable"},
        },
        capsys,
    )["data"]

    assert plan["status"] == "published"
    assert plan["unverified_cases"] == []
    shown = call(
        tmp_path,
        "plan.show",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
    )["data"]["plan"]
    assert [case["expectation_source"] for case in shown["cases"]] == [
        "下单后列表出现订单",
        "库存扣减一次",
    ]
    assert all(case["expectation_verified"] for case in shown["cases"])
