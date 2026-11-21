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


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Doctor must never see this machine's real credentials or gh login."""

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)
    monkeypatch.delenv("AITEST_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))


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


def test_project_config_get_returns_whitelist_without_credentials(
    tmp_path: Path, capsys: Capsys
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    path = tmp_path / "ws" / "projects" / project["project_id"] / "project.config.json"
    write_json(
        path,
        {
            "schema_version": 1,
            "model": {"base_url": "https://example", "api_key": "sk-secret", "max_tokens": 200},
            "services": {"api": {"start": "uvicorn app:main", "password": "db-pass"}},
            "link": {"log_paths": ["logs/app.log"]},
            "runners": {"http": {"timeout_seconds": 30}},
            "evidence": {"media_budget_mb": 100},
            "l1": {"entry": ["app.main"]},
            "data": {"cleanup": "rm -rf data-{run_id}"},
            "custom": {"api_key": "sk-other"},
        },
    )
    result = call(
        tmp_path,
        "project.config",
        {"project_id": project["project_id"], "action": "get"},
        capsys,
    )
    assert result["ok"] is True
    config = result["data"]["config"]
    assert set(config) == {"model", "services", "link", "runners", "evidence", "l1", "data"}
    assert config["model"] == {"base_url": "https://example", "max_tokens": 200}
    assert config["services"] == {"api": {"start": "uvicorn app:main"}}
    assert "api_key" not in json.dumps(config)
    assert "db-pass" not in json.dumps(config)


def test_project_config_update_merges_and_persists(tmp_path: Path, capsys: Capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    path = tmp_path / "ws" / "projects" / project["project_id"] / "project.config.json"
    write_json(
        path,
        {
            "schema_version": 1,
            "services": {"api": {"start": "uvicorn app:main", "health": "http://localhost/health"}},
            "link": {"log_paths": ["logs/app.log"], "log_format": "jsonl"},
        },
    )
    result = call(
        tmp_path,
        "project.config",
        {
            "project_id": project["project_id"],
            "action": "update",
            "patch": {
                "services": {"web": {"start": "npm run dev"}},
                "link": {"log_format": "regex"},
                "evidence": {"media_budget_mb": 100, "video": False},
            },
        },
        capsys,
    )
    assert result["ok"] is True
    config = result["data"]["config"]
    assert config["services"]["api"]["start"] == "uvicorn app:main"
    assert config["services"]["web"]["start"] == "npm run dev"
    assert config["link"] == {"log_paths": ["logs/app.log"], "log_format": "regex"}
    assert config["evidence"] == {"media_budget_mb": 100, "video": False}
    assert read_json(path)["services"]["web"]["start"] == "npm run dev"

    reread = call(
        tmp_path,
        "project.config",
        {"project_id": project["project_id"], "action": "get"},
        capsys,
    )
    assert reread["data"]["config"]["evidence"] == {"media_budget_mb": 100, "video": False}


def test_project_config_update_rejects_secret_and_unknown_fields(
    tmp_path: Path, capsys: Capsys
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    path = tmp_path / "ws" / "projects" / project["project_id"] / "project.config.json"
    write_json(
        path, {"schema_version": 1, "services": {"api": {"start": "uvicorn app:main"}}}
    )

    for patch in (
        {"model": {"api_key": "sk-secret"}},
        {"services": {"db": {"password": "db-pass"}}},
        {"model": {"token": "t"}},
    ):
        rejected = call(
            tmp_path,
            "project.config",
            {"project_id": project["project_id"], "action": "update", "patch": patch},
            capsys,
        )
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "INVALID_REQUEST"
        assert "密钥" in rejected["error"]["message"]
        assert read_json(path) == {
            "schema_version": 1,
            "services": {"api": {"start": "uvicorn app:main"}},
        }

    unknown = call(
        tmp_path,
        "project.config",
        {
            "project_id": project["project_id"],
            "action": "update",
            "patch": {"unknown": {"value": 1}},
        },
        capsys,
    )
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == "INVALID_REQUEST"
    assert "unknown" in unknown["error"]["message"]


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


def test_corrupt_json_reports_invalid_request(tmp_path: Path) -> None:
    from aitest.storage.files import read_json

    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(AiTestError) as exc:
        read_json(path)
    assert exc.value.info.code == "INVALID_REQUEST"


def test_missing_file_reports_invalid_request(tmp_path: Path) -> None:
    from aitest.storage.files import read_json

    with pytest.raises(AiTestError) as exc:
        read_json(tmp_path / "missing.json")
    assert exc.value.info.code == "INVALID_REQUEST"
