"""``module.suggest`` validation and its read-only contract."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

import aitest.commands as commands
from aitest.cli import main
from aitest.models import Project
from aitest.storage.workspace import Workspace

Capsys = pytest.CaptureFixture[str]


class FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.response

    def suggest_modules(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(("suggest_modules", json.dumps(context, ensure_ascii=False)))
        modules: list[dict[str, Any]] = self.response.get("modules", [])
        return modules


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
    (root / "order").mkdir(parents=True)
    (root / "stock").mkdir()
    (root / "README.md").write_text("订单服务\n", encoding="utf-8")
    with (root / "order" / "main.py").open("w", encoding="utf-8") as handle:
        handle.write("def create_order():\n    pass\n")
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    return {"root": root, "project": project}


def test_module_suggest_returns_suggestions_without_writing(
    tmp_path: Path,
    capsys: Capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _setup(tmp_path, capsys)
    client = FakeClient(
        {
            "modules": [
                {
                    "name": "order",
                    "responsibility": "下单",
                    "deps": [{"to": "stock", "type": "calls", "note": "扣减"}],
                },
                {"name": "stock", "responsibility": "库存"},
            ]
        }
    )
    monkeypatch.setattr(commands, "build_client", lambda ws, project_id: client)

    result = call(
        tmp_path,
        "module.suggest",
        {"project_id": setup["project"]["project_id"]},
        capsys,
    )

    assert result["ok"] is True
    assert [item["name"] for item in result["data"]["modules"]] == ["order", "stock"]
    assert client.calls
    assert "order/main.py" in client.calls[0][1]
    workspace = Workspace(tmp_path / "ws")
    project = workspace.load_project(setup["project"]["project_id"])
    assert project.modules == []


def test_module_suggest_accepts_dependency_on_registered_module(
    tmp_path: Path,
    capsys: Capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _setup(tmp_path, capsys)
    call(
        tmp_path,
        "module.add",
        {"project_id": setup["project"]["project_id"], "name": "payment"},
        capsys,
    )
    client = FakeClient(
        {"modules": [{"name": "order", "deps": [{"to": "payment"}]}]}
    )
    monkeypatch.setattr(commands, "build_client", lambda ws, project_id: client)

    result = call(
        tmp_path,
        "module.suggest",
        {"project_id": setup["project"]["project_id"]},
        capsys,
    )

    assert result["ok"] is True
    assert result["data"]["modules"][0]["deps"][0]["to"] == "payment"


def test_module_suggest_rejects_dependency_outside_suggestions_and_registry(
    tmp_path: Path,
    capsys: Capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup = _setup(tmp_path, capsys)
    client = FakeClient(
        {"modules": [{"name": "order", "deps": [{"to": "missing"}]}]}
    )
    monkeypatch.setattr(commands, "build_client", lambda ws, project_id: client)

    result = call(
        tmp_path,
        "module.suggest",
        {"project_id": setup["project"]["project_id"]},
        capsys,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "MODEL_OUTPUT_INVALID"
    workspace = Workspace(tmp_path / "ws")
    project: Project = workspace.load_project(setup["project"]["project_id"])
    assert project.modules == []


def test_module_suggest_without_model_key_is_unavailable(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    result = call(
        tmp_path,
        "module.suggest",
        {"project_id": setup["project"]["project_id"]},
        capsys,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "MODEL_UNAVAILABLE"
