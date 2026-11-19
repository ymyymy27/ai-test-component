"""Behaviour tests for plan, run, report, evidence and backup."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main
from aitest.worker import _run_env

Capsys = pytest.CaptureFixture[str]


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)


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


def _setup_task(tmp_path: Path, capsys: Capsys) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    task = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "下单验证",
            "acceptance_items": [{"text": "下单后列表出现订单", "observable": True}],
        },
        capsys,
    )["data"]
    return {"project": project, "task": task, "item": task["acceptance_items"][0]["item_id"]}


def _plan_payload(setup: dict[str, Any], command: str) -> dict[str, Any]:
    return {
        "project_id": setup["project"]["project_id"],
        "task_id": setup["task"]["task_id"],
        "scope": {"preset": "selected", "acceptance_item_ids": [setup["item"]]},
        "cases": [
            {
                "acceptance_item_id": setup["item"],
                "layer": "L2",
                "expected": "命令成功",
                "expectation_source": "下单后列表出现订单",
                "preconditions": ["被测项目目录可访问"],
                "verify_method": command,
            }
        ],
    }


def test_plan_generate_requires_cases(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    payload = _plan_payload(setup, "command: echo ok")
    payload.pop("cases")
    result = call(tmp_path, "plan.generate", payload, capsys)
    assert result["ok"] is False
    assert result["error"]["code"] == "MODEL_UNAVAILABLE"


def test_plan_generate_covers_selected_items(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    result = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)
    assert result["ok"] is True
    assert result["data"]["coverage"] == {"selected": 1, "applicable": 1}
    assert result["data"]["status"] == "published"


def test_run_requires_confirmation(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)["data"]
    result = call(
        tmp_path,
        "run.start",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_run_completes_and_reports_passed(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )
    assert started["ok"] is True
    run_id = started["data"]["run_id"]
    time.sleep(0.5)
    status: dict[str, Any] = {}
    for _ in range(600):
        status = call(
            tmp_path,
            "run.status",
            {"project_id": setup["project"]["project_id"], "run_id": run_id},
            capsys,
        )
        if status["data"]["run"]["status"] != "running":
            break
        time.sleep(0.1)
    assert status["data"]["run"]["status"] == "completed"
    assert status["data"]["outcome"] == "passed"

    report = call(
        tmp_path,
        "report.generate",
        {"project_id": setup["project"]["project_id"], "run_id": run_id},
        capsys,
    )
    assert report["data"]["outcome"] == "passed"
    report_id = report["data"]["report_id"]
    export_path = tmp_path / "report.md"
    exported = call(
        tmp_path,
        "report.export",
        {
            "project_id": setup["project"]["project_id"],
            "report_id": report_id,
            "path": str(export_path),
        },
        capsys,
    )
    assert exported["ok"] is True
    assert "未填写验收意见" in export_path.read_text(encoding="utf-8")


def test_failed_command_reports_failed(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: exit 1"), capsys)["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )
    run_id = started["data"]["run_id"]
    for _ in range(600):
        status = call(
            tmp_path,
            "run.status",
            {"project_id": setup["project"]["project_id"], "run_id": run_id},
            capsys,
        )
        if status["data"]["run"]["status"] != "running":
            break
        time.sleep(0.1)
    assert status["data"]["outcome"] == "failed"


def test_backup_create_excludes_evidence_and_restores(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    del setup
    archive = tmp_path / "backup.zip"
    created = call(tmp_path, "backup.create", {"path": str(archive)}, capsys)
    assert created["ok"] is True
    assert created["data"]["includes_evidence"] is False
    target = tmp_path / "restored"
    restored = call(
        tmp_path, "backup.restore", {"path": str(archive), "target": str(target)}, capsys
    )
    assert restored["ok"] is True
    assert (target / "workspace.json").is_file()


def test_stale_running_run_is_marked_interrupted(tmp_path: Path, capsys: Capsys) -> None:
    from aitest.models import Run, Scope
    from aitest.storage.files import write_model
    from aitest.storage.workspace import Workspace

    setup = _setup_task(tmp_path, capsys)
    ws = Workspace(tmp_path / "ws")
    run_id = "run_stale"
    run_dir = ws.project_dir(setup["project"]["project_id"]) / "runs" / run_id
    run_dir.mkdir(parents=True)
    write_model(
        run_dir / "run.json",
        Run(
            run_id=run_id,
            plan_id="plan_x",
            project_id=setup["project"]["project_id"],
            scope=Scope(acceptance_item_ids=[setup["item"]]),
            status="running",
            pid=999999,
            started_at="2000-01-01T00:00:00+08:00",
            heartbeat_at="2000-01-01T00:00:00+08:00",
        ),
    )
    status = call(
        tmp_path,
        "run.status",
        {"project_id": setup["project"]["project_id"], "run_id": run_id},
        capsys,
    )
    assert status["data"]["run"]["status"] == "interrupted"


def test_backup_restore_rejects_non_empty_target(tmp_path: Path, capsys: Capsys) -> None:
    _setup_task(tmp_path, capsys)
    archive = tmp_path / "backup.zip"
    assert call(tmp_path, "backup.create", {"path": str(archive)}, capsys)["ok"] is True
    target = tmp_path / "restored"
    target.mkdir()
    (target / "occupied.txt").write_text("x", encoding="utf-8")
    result = call(tmp_path, "backup.restore", {"path": str(archive), "target": str(target)}, capsys)
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_PATH"


def test_generated_script_runs_and_dangerous_script_is_rejected(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup_task(tmp_path, capsys)
    payload = _plan_payload(setup, "script:case.py")
    payload["cases"][0]["script_name"] = "case.py"
    payload["cases"][0]["script_content"] = "print('ok')\n"
    plan = call(tmp_path, "plan.generate", payload, capsys)["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )
    run_id = started["data"]["run_id"]
    for _ in range(600):
        status = call(
            tmp_path,
            "run.status",
            {"project_id": setup["project"]["project_id"], "run_id": run_id},
            capsys,
        )
        if status["data"]["run"]["status"] != "running":
            break
        time.sleep(0.1)
    assert status["data"]["outcome"] == "passed"

    dangerous = _plan_payload(setup, "script:bad.py")
    dangerous["cases"][0]["script_name"] = "bad.py"
    dangerous["cases"][0]["script_content"] = "rm -rf /\n"
    rejected = call(tmp_path, "plan.generate", dangerous, capsys)
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INVALID_REQUEST"
    assert "危险操作" in rejected["error"]["message"]

    traversing = _plan_payload(setup, "script:bad.py")
    traversing["cases"][0]["script_name"] = "../../bad.py"
    traversing["cases"][0]["script_content"] = "print('ok')\n"
    escaped = call(tmp_path, "plan.generate", traversing, capsys)
    assert escaped["ok"] is False
    assert escaped["error"]["code"] == "INVALID_REQUEST"


def _wait_terminal(
    tmp_path: Path, capsys: Capsys, project_id: str, run_id: str
) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for _ in range(600):
        status = call(
            tmp_path,
            "run.status",
            {"project_id": project_id, "run_id": run_id},
            capsys,
        )
        if status["data"]["run"]["status"] != "running":
            return status
        time.sleep(0.1)
    return status


def test_plan_number_contract_and_task_change_invalidates_plan(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup_task(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    task_id = setup["task"]["task_id"]
    first = call(
        tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys
    )["data"]
    second = call(
        tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys
    )["data"]
    assert first["plan_no"] == 1
    assert second["plan_no"] == 2

    shown = call(
        tmp_path,
        "plan.show",
        {"project_id": project_id, "plan_id": first["plan_id"]},
        capsys,
    )
    assert shown["data"]["stale"] is False

    updated_task = call(
        tmp_path,
        "task.update",
        {
            "project_id": project_id,
            "task_id": task_id,
            "patch": {
                "acceptance_items": [
                    {
                        "item_id": setup["item"],
                        "text": "下单后列表必须只出现一条订单",
                        "observable": True,
                    }
                ]
            },
        },
        capsys,
    )
    assert updated_task["ok"] is True

    shown = call(
        tmp_path,
        "plan.show",
        {"project_id": project_id, "plan_id": first["plan_id"]},
        capsys,
    )
    assert shown["data"]["stale"] is True
    assert shown["data"]["task_updated"] is True

    blocked_update = call(
        tmp_path,
        "plan.update",
        {"project_id": project_id, "plan_id": first["plan_id"], "patch": {"cases": []}},
        capsys,
    )
    assert blocked_update["ok"] is False
    assert blocked_update["error"]["code"] == "PLAN_STALE"

    blocked_run = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": first["plan_id"]},
        capsys,
        confirm=True,
    )
    assert blocked_run["ok"] is False
    assert blocked_run["error"]["code"] == "PLAN_STALE"


def test_run_status_and_list_return_snapshots(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    status = _wait_terminal(tmp_path, capsys, project_id, started["run_id"])
    assert status["data"]["run"]["status"] == "completed"
    assert status["data"]["run"]["current_case_id"] == ""
    assert status["data"]["task_snapshot"] is True
    assert status["data"]["task"]["title"] == "下单验证"
    assert status["data"]["plan"]["plan_id"] == plan["plan_id"]

    call(
        tmp_path,
        "task.update",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "patch": {"acceptance_items": [
                {"item_id": setup["item"], "text": "运行后改过的验收项", "observable": True}
            ]},
        },
        capsys,
    )
    after = call(
        tmp_path,
        "run.status",
        {"project_id": project_id, "run_id": started["run_id"]},
        capsys,
    )
    assert after["data"]["task"]["acceptance_items"][0]["text"] == "下单后列表出现订单"

    listing = call(tmp_path, "run.list", {"project_id": project_id, "limit": 5}, capsys)
    row = listing["data"]["runs"][0]
    assert row["task_title"] == "下单验证"
    assert row["plan_no"] == 1
    assert row["outcome"] == "passed"
    assert row["counts"]["selected"] == 1
    assert row["latest_report_id"]
    assert row["task_snapshot"] is True

    snapshot = (
        tmp_path
        / "ws"
        / "projects"
        / project_id
        / "runs"
        / started["run_id"]
        / "task.json"
    )
    snapshot.unlink()
    without = call(tmp_path, "run.list", {"project_id": project_id, "limit": 5}, capsys)
    assert without["data"]["runs"][0]["task_snapshot"] is False


def test_report_show_and_list_expose_full_report(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    _wait_terminal(tmp_path, capsys, project_id, started["run_id"])

    generated = call(
        tmp_path,
        "report.generate",
        {"project_id": project_id, "run_id": started["run_id"]},
        capsys,
    )["data"]
    assert generated["generated_at"]
    shown = call(
        tmp_path,
        "report.show",
        {"project_id": project_id, "report_id": generated["report_id"]},
        capsys,
    )
    assert shown["ok"] is True
    report = shown["data"]["report"]
    assert report["report_id"] == generated["report_id"]
    assert "mock_judgement" in report
    assert "evidence_index" in report
    assert "opinion" in report

    versions = call(
        tmp_path,
        "report.list",
        {"project_id": project_id, "run_id": started["run_id"]},
        capsys,
    )
    assert any(item["report_id"] == generated["report_id"] for item in versions["data"]["reports"])
    refreshed = call(
        tmp_path,
        "run.status",
        {"project_id": project_id, "run_id": started["run_id"]},
        capsys,
    )
    assert refreshed["data"]["run"]["latest_report_id"] == generated["report_id"]


def test_issue_show_and_list_summary(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: exit 1"), capsys)["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    status = _wait_terminal(tmp_path, capsys, project_id, started["run_id"])
    case_id = status["data"]["plan"]["cases"][0]["case_id"]
    added = call(
        tmp_path,
        "issue.add",
        {
            "project_id": project_id,
            "run_id": started["run_id"],
            "case_id": case_id,
            "title": "列表数量不对",
            "severity": "P1",
            "error_type": "manual-check",
            "stack_key": "unique-stack-1",
        },
        capsys,
    )["data"]
    issue_id = added["issue_id"]
    listing = call(tmp_path, "issue.list", {"project_id": project_id}, capsys)["data"]["issues"]
    row = next(item for item in listing if item["issue_id"] == issue_id)
    assert row["last_at"]
    assert row["last_result"] == "failed"
    assert row["last_case_id"] == case_id
    assert row["last_run_id"] == started["run_id"]

    shown = call(
        tmp_path, "issue.show", {"project_id": project_id, "issue_id": issue_id}, capsys
    )
    assert shown["ok"] is True
    issue = shown["data"]["issue"]
    assert issue["issue_id"] == issue_id
    assert len(issue["occurrences"]) == 1
    assert issue["occurrences"][0]["case_id"] == case_id


def test_run_env_drops_inherited_pythonhome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONHOME", "C:/other/python")
    python = tmp_path / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    env = _run_env(tmp_path, python, "run_envcheck")
    assert "PYTHONHOME" not in env
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")
    assert env["PATH"].startswith(str(python.parent))
    assert env["AITEST_RUN_ID"] == "run_envcheck"


def test_project_snapshot_returns_read_model(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    plan = call(
        tmp_path,
        "plan.generate",
        _plan_payload(setup, "command: echo ok"),
        capsys,
    )["data"]
    snapshot = call(
        tmp_path,
        "project.snapshot",
        {"project_id": setup["project"]["project_id"]},
        capsys,
    )["data"]
    assert snapshot["project"]["project_id"] == setup["project"]["project_id"]
    assert len(snapshot["tasks"]) == 1
    assert snapshot["tasks"][0]["task"]["task_id"] == setup["task"]["task_id"]
    assert snapshot["tasks"][0]["plans"][0]["plan"]["plan_id"] == plan["plan_id"]
    assert snapshot["runs"] == []
    assert snapshot["issues"] == []
