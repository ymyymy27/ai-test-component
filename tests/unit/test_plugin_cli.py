"""Behaviour tests for the lightweight CLI: project/module/task/delivery/rules."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main
from aitest.errors import AiTestError
from aitest.storage.files import read_json, write_json

Capsys = pytest.CaptureFixture[str]


def call(tmp_path: Path, command: str, payload: dict[str, Any], capsys: Capsys) -> dict[str, Any]:
    workspace = tmp_path / "ws"
    input_file = tmp_path / "req.json"
    request_id = payload.pop("request_id", f"{command}-{uuid.uuid4().hex[:8]}")
    envelope = {"command": command, "request_id": request_id, "payload": payload}
    input_file.write_text(json.dumps(envelope), encoding="utf-8")
    code = main([command, "--workspace", str(workspace), "--input", str(input_file)])
    captured = capsys.readouterr().out.strip()
    data = json.loads(captured)
    assert code in (0, 1, 2)
    return data


def test_doctor_reports_workspace_and_package(tmp_path: Path, capsys: Capsys) -> None:
    result = call(tmp_path, "doctor", {}, capsys)
    assert result["ok"] is True
    assert result["data"]["workspace"]["ok"] is True
    assert result["data"]["package"]["ok"] is True


def test_project_add_is_idempotent(tmp_path: Path, capsys: Capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = call(tmp_path, "project.add", {"request_id": "r1", "root": str(root)}, capsys)
    second = call(tmp_path, "project.add", {"request_id": "r1", "root": str(root)}, capsys)
    assert first["data"]["project_id"] == second["data"]["project_id"]
    listed = call(tmp_path, "project.list", {}, capsys)
    assert len(listed["data"]["projects"]) == 1


def test_project_add_rejects_overlapping_root(tmp_path: Path, capsys: Capsys) -> None:
    root = tmp_path / "repo"
    child = root / "apps" / "web"
    child.mkdir(parents=True)
    call(tmp_path, "project.add", {"root": str(root)}, capsys)
    result = call(tmp_path, "project.add", {"root": str(child)}, capsys)
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"


def test_task_add_marks_unobservable_item_pending(tmp_path: Path, capsys: Capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    result = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "下单验证",
            "acceptance_items": [
                {"text": "下单后列表出现订单", "observable": True},
                {"text": "系统要稳定", "observable": False},
            ],
        },
        capsys,
    )
    statuses = [item["status"] for item in result["data"]["acceptance_items"]]
    assert statuses == ["active", "pending"]


def test_delivery_incomplete_without_run_command_and_commit(tmp_path: Path, capsys: Capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    task = call(
        tmp_path,
        "task.add",
        {"project_id": project["project_id"], "title": "t"},
        capsys,
    )["data"]
    saved = call(
        tmp_path,
        "delivery.save",
        {"project_id": project["project_id"], "task_id": task["task_id"], "done": ["x"]},
        capsys,
    )
    assert saved["data"]["complete"] is False


def test_rules_bind_records_project_version(tmp_path: Path, capsys: Capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    rule = call(
        tmp_path,
        "rules.save",
        {"project_id": project["project_id"], "name": "接口检查", "version": "1"},
        capsys,
    )["data"]
    bound = call(
        tmp_path,
        "rules.bind",
        {"project_id": project["project_id"], "rules_version_id": rule["rules_version_id"]},
        capsys,
    )
    assert bound["data"]["rules_version_id"] == rule["rules_version_id"]


def test_read_model_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "project.json"
    write_json(path, {"schema_version": 99, "project_id": "p"})
    from aitest.models import Project

    with pytest.raises(AiTestError) as exc:
        from aitest.storage.files import read_model

        read_model(path, Project)
    assert exc.value.info.code == "SCHEMA_UNSUPPORTED"


def test_read_json_returns_object(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    write_json(path, {"b": 1, "a": 2})
    assert read_json(path) == {"a": 2, "b": 1}
