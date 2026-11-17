"""Behaviour tests for plan context, smoke intent, signatures and analyses."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main
from aitest.core.analysis import analyze_run
from aitest.errors import AiTestError
from aitest.models import Run, RunCase, Scope
from aitest.storage.files import read_json, read_model, write_json, write_model
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
    tmp_path: Path,
    capsys: Capsys,
    *,
    goal: str = "验证下单流程",
    items: list[dict[str, Any]] | None = None,
    intent: str | None = None,
) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    payload: dict[str, Any] = {
        "project_id": project["project_id"],
        "title": "下单验证",
        "goal": goal,
        "acceptance_items": items or [{"text": "下单后列表出现订单", "observable": True}],
    }
    if intent is not None:
        payload["intent"] = intent
    task = call(tmp_path, "task.add", payload, capsys)["data"]
    return {"project": project, "task": task, "root": root}


def _plan_payload(
    setup: dict[str, Any], method: str, *, case_id: str | None = None
) -> dict[str, Any]:
    item = setup["task"]["acceptance_items"][0]["item_id"]
    case: dict[str, Any] = {
        "acceptance_item_id": item,
        "layer": "L2",
        "expected": "命令成功",
        "expectation_source": "下单后列表出现订单",
        "preconditions": ["被测项目目录可访问"],
        "verify_method": method,
    }
    if case_id is not None:
        case["case_id"] = case_id
    return {
        "project_id": setup["project"]["project_id"],
        "task_id": setup["task"]["task_id"],
        "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        "cases": [case],
    }


def _wait_completed(
    tmp_path: Path, capsys: Capsys, project_id: str, run_id: str, attempts: int = 600
) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for _ in range(attempts):
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


class _CapturingClient:
    def __init__(self) -> None:
        self.context: dict[str, Any] | None = None

    def generate_cases(
        self,
        task: dict[str, Any],
        selected_items: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del task
        self.context = context
        return [
            {
                "acceptance_item_id": item["item_id"],
                "layer": "L2",
                "expected": "命令成功",
                "expectation_source": item["text"],
                "preconditions": ["被测项目目录可访问"],
                "verify_method": "command: echo ok",
                "depends_on": [],
            }
            for item in selected_items
        ]


def test_project_context_reports_commands_frameworks_and_capabilities(
    tmp_path: Path, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup = _setup(tmp_path, capsys)
    root = setup["root"]
    (root / "tests").mkdir()
    (root / "test").mkdir()
    (root / "src" / "app" / "tests").mkdir(parents=True)
    (root / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                'dependencies = ["pytest>=8", "fastapi", "sqlalchemy"]',
                "",
                "[tool.pytest.ini_options]",
                'addopts = "-q"',
            ]
        ),
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"test": "vitest run", "dev": "vite"},
                "dependencies": {"react": "18"},
                "devDependencies": {"vitest": "1"},
            }
        ),
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Demo\n\n```bash\nnpm test\npython -m pytest\nuvicorn app.main:app\n```\n",
        encoding="utf-8",
    )
    project_id = setup["project"]["project_id"]
    call(
        tmp_path,
        "project.config",
        {
            "project_id": project_id,
            "action": "update",
            "patch": {
                "services": {
                    "api": {
                        "health": "http://127.0.0.1:9000/health",
                        "start": "uvicorn app.main:app",
                    }
                }
            },
        },
        capsys,
    )
    client = _CapturingClient()
    monkeypatch.setattr("aitest.runtime_commands.build_client", lambda *a, **k: client)
    item = setup["task"]["acceptance_items"][0]["item_id"]
    result = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        },
        capsys,
    )
    assert result["ok"] is True, result
    context = client.context
    assert context is not None
    assert "python -m pytest" in context["test_commands"]
    assert "npm test" in context["test_commands"]
    assert context["services"]["api"]["health_url"] == "http://127.0.0.1:9000/health"
    assert "tests/" in context["test_dirs"]
    assert "test/" in context["test_dirs"]
    assert "src/app/tests/" in context["test_dirs"]
    assert {"pytest", "vitest", "fastapi"} <= set(context["framework"])
    assert context["has_frontend"] is True
    assert context["has_database"] is True
    assert 0 < len(context["existing_commands"]) <= 5
    assert "npm test" in context["existing_commands"]


def test_task_intent_inference_and_suggest_fallback(tmp_path: Path, capsys: Capsys) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    smoke = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "冒烟",
            "goal": "验证项目能否启动并跑通主流程",
        },
        capsys,
    )["data"]
    assert smoke["intent"] == "smoke"
    assert smoke["acceptance_items"] == []

    business = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "下单",
            "goal": "验证下单后订单列表出现",
        },
        capsys,
    )["data"]
    assert business["intent"] == "business"

    explicit = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "显式冒烟",
            "goal": "验证下单流程",
            "intent": "smoke",
        },
        capsys,
    )["data"]
    assert explicit["intent"] == "smoke"

    suggested = call(tmp_path, "task.suggest", {"project_id": project["project_id"]}, capsys)[
        "data"
    ]
    assert suggested["intent"] == "smoke"
    assert suggested["fallback_intent"] == "smoke"


def test_smoke_plan_uses_existing_test_command_without_a_model(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        goal="验证项目能否启动并跑通主流程",
        intent="smoke",
    )
    root = setup["root"]
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8"
    )
    item = setup["task"]["acceptance_items"][0]["item_id"]
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        },
        capsys,
    )
    assert plan["ok"] is True, plan
    assert plan["data"]["intent"] == "smoke"
    assert plan["data"]["cases"] == 1
    shown = call(
        tmp_path,
        "plan.show",
        {
            "project_id": setup["project"]["project_id"],
            "plan_id": plan["data"]["plan_id"],
        },
        capsys,
    )["data"]["plan"]
    assert [case["layer"] for case in shown["cases"]] == ["L1"]
    assert shown["cases"][0]["verify_method"].startswith("command: python -m pytest")


def test_smoke_plan_rejects_http_and_uses_l1_import_when_no_command(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        goal="验证项目能否启动并跑通主流程",
        intent="smoke",
    )
    rejected = call(tmp_path, "plan.generate", _plan_payload(setup, "http:[]"), capsys)
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INVALID_REQUEST"
    assert "验证方式" in rejected["error"]["message"]

    (setup["root"] / "src" / "app").mkdir(parents=True)
    (setup["root"] / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    item = setup["task"]["acceptance_items"][0]["item_id"]
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        },
        capsys,
    )
    assert plan["ok"] is True, plan
    shown = call(
        tmp_path,
        "plan.show",
        {
            "project_id": setup["project"]["project_id"],
            "plan_id": plan["data"]["plan_id"],
        },
        capsys,
    )["data"]["plan"]
    assert shown["cases"][0]["layer"] == "L1"
    assert "import app" in shown["cases"][0]["verify_method"]


def test_smoke_plan_uses_discovery_and_smallest_test_module(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        goal="验证项目能否启动并跑通主流程",
        intent="smoke",
    )
    root = setup["root"]
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "-q"\n', encoding="utf-8"
    )
    (root / "tests" / "test_big.py").write_text(
        "def test_big():\n    assert True\n" * 20, encoding="utf-8"
    )
    (root / "tests" / "test_small.py").write_text(
        "def test_small():\n    assert True\n", encoding="utf-8"
    )
    item = setup["task"]["acceptance_items"][0]["item_id"]
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        },
        capsys,
    )
    assert plan["ok"] is True, plan
    shown = call(
        tmp_path,
        "plan.show",
        {
            "project_id": setup["project"]["project_id"],
            "plan_id": plan["data"]["plan_id"],
        },
        capsys,
    )["data"]["plan"]
    methods = [case["verify_method"] for case in shown["cases"]]
    assert len(methods) == 2
    assert "command: python -m pytest --collect-only -q" in methods
    smallest = next(item for item in methods if "test_small.py" in item)
    assert "test_big.py" not in smallest


def test_smoke_command_override_wins_over_inferred_checks(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        goal="验证项目能否启动并跑通主流程",
        intent="smoke",
    )
    item = setup["task"]["acceptance_items"][0]["item_id"]
    call(
        tmp_path,
        "project.config",
        {
            "project_id": setup["project"]["project_id"],
            "action": "update",
            "patch": {"l1": {"smoke_command": "python -m pytest tests/test_small.py -q"}},
        },
        capsys,
    )
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        },
        capsys,
    )
    assert plan["ok"] is True, plan
    shown = call(
        tmp_path,
        "plan.show",
        {
            "project_id": setup["project"]["project_id"],
            "plan_id": plan["data"]["plan_id"],
        },
        capsys,
    )["data"]["plan"]
    assert len(shown["cases"]) == 1
    assert shown["cases"][0]["verify_method"] == "command: python -m pytest tests/test_small.py -q"


def test_smoke_l1_build_is_not_run_twice(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        goal="验证项目能否启动并跑通主流程",
        intent="smoke",
    )
    project_id = setup["project"]["project_id"]
    call(
        tmp_path,
        "project.config",
        {
            "project_id": project_id,
            "action": "update",
            "patch": {"l1": {"build": "python -m compileall -q src"}},
        },
        capsys,
    )
    item = setup["task"]["acceptance_items"][0]["item_id"]
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        },
        capsys,
    )
    assert plan["ok"] is True, plan
    shown = call(
        tmp_path,
        "plan.show",
        {"project_id": project_id, "plan_id": plan["data"]["plan_id"]},
        capsys,
    )["data"]["plan"]
    assert len(shown["cases"]) == 1
    assert shown["cases"][0]["verify_method"] == "command: python -m compileall -q src"


def test_smoke_plan_without_any_check_is_rejected(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        goal="验证项目能否启动并跑通主流程",
        intent="smoke",
    )
    item = setup["task"]["acceptance_items"][0]["item_id"]
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": setup["project"]["project_id"],
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        },
        capsys,
    )
    assert plan["ok"] is False
    assert plan["error"]["code"] == "INVALID_REQUEST"
    assert "缺少可执行检查" in plan["error"]["message"]


def test_plan_signature_detects_smoke_command_change(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(
        tmp_path,
        capsys,
        goal="验证项目能否启动并跑通主流程",
        intent="smoke",
    )
    project_id = setup["project"]["project_id"]
    call(
        tmp_path,
        "project.config",
        {
            "project_id": project_id,
            "action": "update",
            "patch": {"l1": {"smoke_command": "python -m pytest -q"}},
        },
        capsys,
    )
    item = setup["task"]["acceptance_items"][0]["item_id"]
    plan = call(
        tmp_path,
        "plan.generate",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
        },
        capsys,
    )["data"]
    call(
        tmp_path,
        "project.config",
        {
            "project_id": project_id,
            "action": "update",
            "patch": {"l1": {"smoke_command": "python -m pytest tests/test_small.py"}},
        },
        capsys,
    )
    stale = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "PLAN_STALE"


def test_business_http_rejects_external_example_and_allows_service_host(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    call(
        tmp_path,
        "project.config",
        {
            "project_id": project_id,
            "action": "update",
            "patch": {"services": {"api": {"health": "http://127.0.0.1:9000/health"}}},
        },
        capsys,
    )
    external = _plan_payload(
        setup,
        "http:" + json.dumps([{"method": "GET", "url": "https://api.example.com/orders"}]),
        case_id="case_external",
    )
    rejected = call(tmp_path, "plan.generate", external, capsys)
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "INVALID_REQUEST"
    assert "case_external" in rejected["error"]["message"]

    internal = _plan_payload(
        setup,
        "http:" + json.dumps([{"method": "GET", "url": "http://127.0.0.1:9000/orders"}]),
    )
    allowed = call(tmp_path, "plan.generate", internal, capsys)
    assert allowed["ok"] is True, allowed

    script = _plan_payload(setup, "script:probe.py", case_id="case_script")
    script["cases"][0]["script_name"] = "probe.py"
    script["cases"][0]["script_content"] = 'url = "https://api.example.com"\n'
    script_rejected = call(tmp_path, "plan.generate", script, capsys)
    assert script_rejected["ok"] is False
    assert "case_script" in script_rejected["error"]["message"]


@pytest.mark.parametrize(
    ("method", "extra"),
    [
        ("command:   ", {}),
        ("script:probe.py", {"script_name": "probe.py"}),
        ("http:not-json", {}),
        ("http:{}", {}),
        ('http:[{"method": "GET"}]', {}),
        ("playwright:   ", {}),
        ("sql:   ", {}),
        ("junit:   ", {}),
        ("pytest:   ", {}),
    ],
)
def test_plan_generate_rejects_invalid_verify_methods(
    tmp_path: Path, capsys: Capsys, method: str, extra: dict[str, Any]
) -> None:
    setup = _setup(tmp_path, capsys)
    payload = _plan_payload(setup, method, case_id="case_bad")
    payload["cases"][0].update(extra)
    result = call(tmp_path, "plan.generate", payload, capsys)
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_REQUEST"
    assert "case_bad" in result["error"]["message"]


def test_plan_update_and_publish_reject_invalid_verify_methods(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)["data"]
    bad_case = _plan_payload(setup, "command:   ")["cases"][0]
    bad_case["case_id"] = "case_update"
    updated = call(
        tmp_path,
        "plan.update",
        {"project_id": project_id, "plan_id": plan["plan_id"], "patch": {"cases": [bad_case]}},
        capsys,
    )
    assert updated["ok"] is False
    assert updated["error"]["code"] == "INVALID_REQUEST"
    assert "case_update" in updated["error"]["message"]

    ws = _workspace(tmp_path)
    path = ws.project_dir(project_id) / "plans" / f"{plan['plan_id']}.json"
    stored = read_json(path)
    stored["status"] = "draft"
    stored["cases"][0]["case_id"] = "case_publish"
    stored["cases"][0]["verify_method"] = "command:   "
    write_json(path, stored)
    published = call(
        tmp_path,
        "plan.publish",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
    )
    assert published["ok"] is False
    assert published["error"]["code"] == "INVALID_REQUEST"
    assert "case_publish" in published["error"]["message"]


def test_plan_signature_detects_delivery_commit_change(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    call(
        tmp_path,
        "delivery.save",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "run_commands": ["python -m app"],
            "commit": "abc123",
        },
        capsys,
    )
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)["data"]
    assert plan["source_signature"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    _wait_completed(tmp_path, capsys, project_id, started["run_id"])

    call(
        tmp_path,
        "delivery.save",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "run_commands": ["python -m app"],
            "commit": "def456",
        },
        capsys,
    )
    stale = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "PLAN_STALE"
    assert "重新生成计划" in stale["error"]["next_step"]


def test_plan_signature_detects_services_change_and_accepts_legacy_empty_signature(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    _wait_completed(tmp_path, capsys, project_id, started["run_id"])

    ws = _workspace(tmp_path)
    (ws.project_dir(project_id) / "project.config.json").write_text(
        json.dumps({"services": {"web": {"health": "http://127.0.0.1:8000/health"}}}),
        encoding="utf-8",
    )
    stale = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "PLAN_STALE"

    path = ws.project_dir(project_id) / "plans" / f"{plan['plan_id']}.json"
    stored = read_json(path)
    stored["source_signature"] = ""
    write_json(path, stored)
    legacy = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )
    assert legacy["ok"] is False, legacy
    assert legacy["error"]["code"] == "PLAN_STALE"


def test_plan_update_recomputes_source_signature(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    call(
        tmp_path,
        "delivery.save",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "run_commands": ["python -m app"],
            "commit": "abc123",
        },
        capsys,
    )
    plan = call(tmp_path, "plan.generate", _plan_payload(setup, "command: echo ok"), capsys)["data"]
    call(
        tmp_path,
        "delivery.save",
        {
            "project_id": project_id,
            "task_id": setup["task"]["task_id"],
            "run_commands": ["python -m app"],
            "commit": "def456",
        },
        capsys,
    )
    updated = call(
        tmp_path,
        "plan.update",
        {
            "project_id": project_id,
            "plan_id": plan["plan_id"],
            "patch": {"cases": _plan_payload(setup, "command: echo ok")["cases"]},
        },
        capsys,
    )
    assert updated["ok"] is True, updated
    assert updated["data"]["source_signature"] != plan["source_signature"]
    run = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )
    assert run["ok"] is True, run
    _wait_completed(tmp_path, capsys, project_id, run["data"]["run_id"])


class _ExtractionClient:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def extract_acceptance_items(self, requirement: str) -> list[dict[str, Any]]:
        del requirement
        return self.items


def test_generic_ai_acceptance_items_become_pending_and_no_fallback_item(
    tmp_path: Path, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    client = _ExtractionClient(
        [
            {"text": "项目能够启动", "observable": True, "source": "需求"},
            {"text": "主流程能够跑通", "observable": True, "source": "需求"},
            {"text": "下单后列表出现订单", "observable": True, "source": "需求"},
        ]
    )
    monkeypatch.setattr("aitest.commands.build_client", lambda *a, **k: client)
    task = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "提取",
            "goal": "验证下单流程",
            "requirement": "项目要能启动并跑通主流程，下单后列表出现订单",
        },
        capsys,
    )["data"]
    assert [item["status"] for item in task["acceptance_items"]] == [
        "pending",
        "pending",
        "active",
    ]
    assert [item["observable"] for item in task["acceptance_items"]] == [
        False,
        False,
        True,
    ]

    monkeypatch.setattr("aitest.commands.build_client", lambda *a, **k: None)
    empty = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "空任务",
            "goal": "验证下单流程",
            "requirement": "项目要能启动",
        },
        capsys,
    )["data"]
    assert empty["acceptance_items"] == []


def _write_run(ws: Workspace, project_id: str, run_id: str, results: list[str]) -> Path:
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    run_dir.mkdir(parents=True)
    run = Run(
        run_id=run_id,
        plan_id="plan_x",
        project_id=project_id,
        scope=Scope(),
        cases=[
            RunCase(case_id=f"case_{index}", result=result)  # type: ignore[arg-type]
            for index, result in enumerate(results)
        ],
    )
    write_model(run_dir / "run.json", run)
    return run_dir


class _AnalyzeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def analyze_failure(self, run: dict[str, Any], evidence: list[str]) -> dict[str, Any]:
        del run, evidence
        if self.error is not None:
            raise self.error
        return {
            "observed": "接口返回 500",
            "evidence_refs": [],
            "candidate_causes": [{"cause": "下游超时", "confidence": "unknown"}],
            "unverified": ["待确认"],
            "next_steps": ["查看日志"],
        }


def test_analyze_run_sets_reason_for_each_missing_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _workspace(tmp_path)
    project_id = "proj_analysis"
    monkeypatch.setattr("aitest.core.analysis.build_client", lambda *a, **k: None)

    no_failure = _write_run(ws, project_id, "run_ok", ["passed"])
    result = analyze_run(ws, project_id, no_failure)
    assert result["reason"] == "no_failures"
    assert read_model(no_failure / "run.json", Run).analysis[0].reason == "no_failures"

    missing = _write_run(ws, project_id, "run_missing", ["failed"])
    result = analyze_run(ws, project_id, missing)
    assert result["reason"] == "model_missing"
    assert read_model(missing / "run.json", Run).analysis[0].reason == "model_missing"

    invalid = _write_run(ws, project_id, "run_invalid", ["failed"])
    monkeypatch.setattr(
        "aitest.core.analysis.build_client",
        lambda *a, **k: _AnalyzeClient(AiTestError("MODEL_OUTPUT_INVALID", "bad output", "retry")),
    )
    result = analyze_run(ws, project_id, invalid)
    assert result["reason"] == "model_invalid"
    assert read_model(invalid / "run.json", Run).analysis[0].reason == "model_invalid"

    error = _write_run(ws, project_id, "run_error", ["failed"])
    monkeypatch.setattr(
        "aitest.core.analysis.build_client",
        lambda *a, **k: _AnalyzeClient(AiTestError("MODEL_UNAVAILABLE", "down", "retry")),
    )
    result = analyze_run(ws, project_id, error)
    assert result["reason"] == "model_error"
    assert read_model(error / "run.json", Run).analysis[0].reason == "model_error"

    generated = _write_run(ws, project_id, "run_generated", ["failed"])
    monkeypatch.setattr("aitest.core.analysis.build_client", lambda *a, **k: _AnalyzeClient())
    result = analyze_run(ws, project_id, generated)
    assert result["status"] == "generated"
    assert result["analysis"]["reason"] == ""
