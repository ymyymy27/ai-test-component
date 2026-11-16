"""Template loading, model disposition and its report projection."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

import aitest.commands as commands
from aitest.cli import main
from aitest.core.templates import apply_template, list_templates, load_template
from aitest.errors import AiTestError
from aitest.models import Plan, Project, Report, Run, Scope, Task
from aitest.runtime_commands import cmd_report_generate
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace

Capsys = pytest.CaptureFixture[str]


class FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.response


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)


def call(
    tmp_path: Path, command: str, payload: dict[str, Any], capsys: Capsys
) -> dict[str, Any]:
    input_file = tmp_path / "req.json"
    envelope = {
        "command": command,
        "request_id": f"{command}-{uuid.uuid4().hex[:8]}",
        "payload": payload,
    }
    input_file.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    main([command, "--workspace", str(tmp_path / "ws"), "--input", str(input_file)])
    return json.loads(capsys.readouterr().out.strip())


def _setup(tmp_path: Path, capsys: Capsys) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    task = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "服务启动验证",
            "goal": "验证服务可以启动",
            "acceptance_items": [{"text": "服务健康检查通过", "observable": True}],
        },
        capsys,
    )["data"]
    workspace = Workspace(tmp_path / "ws")
    plan = Plan(
        plan_id="plan_1",
        task_id=task["task_id"],
        scope=Scope(
            preset="selected",
            acceptance_item_ids=[task["acceptance_items"][0]["item_id"]],
        ),
        applicable_count=1,
    )
    project_dir = workspace.projects_dir / project["project_id"]
    write_model(project_dir / "plans" / "plan_1.json", plan)
    return {"project": project, "task": task, "workspace": workspace}


def test_list_templates_includes_builtin_presets() -> None:
    templates = list_templates()
    ids = [item["template_id"] for item in templates]
    assert "python-service" in ids
    assert all(item["item_count"] > 0 for item in templates)
    service = next(item for item in templates if item["template_id"] == "python-service")
    assert service["name"]


def test_load_template_rejects_path_like_names() -> None:
    with pytest.raises(AiTestError) as exc:
        load_template("../escape")
    assert exc.value.info.code == "INVALID_REQUEST"


def test_apply_template_keeps_missing_reason_explicit() -> None:
    template = load_template("python-service")
    item_ids = [item["item_id"] for item in template["items"]]
    client = FakeClient(
        {
            "items": [
                {"item_id": item_ids[0], "disposition": "applicable", "reason": "有启动命令"},
                {"item_id": item_ids[1], "disposition": "not_applicable"},
                {"item_id": item_ids[2], "disposition": "unsupported", "reason": "缺少采集能力"},
            ]
        }
    )
    project = Project(project_id="proj_1", name="订单", root=".")
    task = Task(task_id="task_1", project_id="proj_1", title="启动验证")

    result = apply_template(client, template, project, task)

    by_id = {item["item_id"]: item for item in result["items"]}
    assert by_id[item_ids[0]]["disposition"] == "applicable"
    assert by_id[item_ids[1]]["disposition"] == "unknown"
    assert by_id[item_ids[1]]["reason"] == "模型未提供理由"
    assert by_id[item_ids[2]]["disposition"] == "unsupported"
    assert by_id[item_ids[3]]["disposition"] == "unknown"
    assert result["summary"]["applicable"] == 1
    assert result["summary"]["unknown"] == 2


def test_template_apply_writes_task_file_and_plan_templates(
    tmp_path: Path,
    capsys: Capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _setup(tmp_path, capsys)
    template = load_template("python-service")
    response = {
        "items": [
            {"item_id": item["item_id"], "disposition": "applicable", "reason": "已确认"}
            for item in template["items"]
        ]
    }
    monkeypatch.setattr(
        commands, "build_client", lambda ws, project_id: FakeClient(response)
    )

    result = call(
        tmp_path,
        "template.apply",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "template_name": "python-service",
            "plan_id": "plan_1",
        },
        capsys,
    )

    assert result["ok"] is True
    assert result["data"]["template"]["summary"]["applicable"] == len(template["items"])
    assert result["data"]["plan_revision"] == 2
    project_dir = setup["workspace"].projects_dir / setup["project"]["project_id"]
    stored = project_dir / "tasks" / setup["task"]["task_id"] / "templates"
    assert (stored / "python-service.json").is_file()
    plan = read_model(project_dir / "plans" / "plan_1.json", Plan)
    assert [entry["template_id"] for entry in plan.templates] == ["python-service"]


def test_report_includes_template_disposition(
    tmp_path: Path,
    capsys: Capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _setup(tmp_path, capsys)
    template = load_template("python-service")
    response = {
        "items": [
            {"item_id": item["item_id"], "disposition": "applicable", "reason": "已确认"}
            for item in template["items"]
        ]
    }
    monkeypatch.setattr(
        commands, "build_client", lambda ws, project_id: FakeClient(response)
    )
    call(
        tmp_path,
        "template.apply",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "template_name": "python-service",
            "plan_id": "plan_1",
        },
        capsys,
    )
    workspace: Workspace = setup["workspace"]
    project_dir = workspace.projects_dir / setup["project"]["project_id"]
    plan = read_model(project_dir / "plans" / "plan_1.json", Plan)
    run_dir = project_dir / "runs" / "run_1"
    run = Run(
        run_id="run_1",
        plan_id="plan_1",
        project_id=setup["project"]["project_id"],
        scope=plan.scope,
        status="completed",
        started_at="2026-11-16T00:00:00+00:00",
        cases=[],
    )
    write_model(run_dir / "run.json", run)
    write_model(run_dir / "plan.json", plan)

    generated = cmd_report_generate(
        workspace,
        {"project_id": setup["project"]["project_id"], "run_id": "run_1"},
        False,
    )

    assert generated["templates"] == [
        {
            "template_id": "python-service",
            "name": "Python服务",
            "summary": {
                "applicable": len(template["items"]),
                "not_applicable": 0,
                "unsupported": 0,
                "unknown": 0,
            },
        }
    ]
    report = read_model(project_dir / "reports" / f"{generated['report_id']}.json", Report)
    assert report.templates[0]["items"][0]["reason"] == "已确认"


def test_template_apply_rejects_unknown_template(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    result = call(
        tmp_path,
        "template.apply",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "template_name": "missing-template",
        },
        capsys,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"
