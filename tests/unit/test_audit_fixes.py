"""Regression tests for the 2026-11-20 audit's test/contract/document items."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main
from aitest.errors import AiTestError
from aitest.models import Run, Scope
from aitest.runners.package_install import InstallOutcome
from aitest.storage.files import read_json, write_json, write_model
from aitest.storage.workspace import Workspace, iso_now

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
    code = main(argv)
    data = json.loads(capsys.readouterr().out.strip())
    expected = 0 if data.get("ok") else (2 if data.get("error", {}).get("code") == "USAGE" else 1)
    assert code == expected, data
    return data


def _project(tmp_path: Path, capsys: Capsys) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    project = call(tmp_path, "project.add", {"root": str(root)}, capsys)["data"]
    return project, root


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path / "ws")


def _manual_run(
    tmp_path: Path,
    project_id: str,
    *,
    status: str = "completed",
    suggestions: list[dict[str, str]] | None = None,
) -> tuple[Path, Run]:
    ws = _workspace(tmp_path)
    run_dir = ws.project_dir(project_id) / "runs" / "run_audit"
    run_dir.mkdir(parents=True)
    run = Run(
        run_id="run_audit",
        plan_id="plan_x",
        project_id=project_id,
        scope=Scope(),
        status=status,
        started_at="2026-01-01T00:00:00+00:00",
        heartbeat_at=iso_now(),
        environment={"python": sys.executable, "python_version": "3.11.0", "cwd": "x"},
        dependency_suggestions=suggestions or [],
    )
    write_model(run_dir / "run.json", run)
    return run_dir, run


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


def _setup_task(tmp_path: Path, capsys: Capsys) -> dict[str, Any]:
    project, root = _project(tmp_path, capsys)
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
    return {
        "project": project,
        "root": root,
        "task": task,
        "item": task["acceptance_items"][0]["item_id"],
    }


def _wait_terminal(
    tmp_path: Path, capsys: Capsys, project_id: str, run_id: str, attempts: int = 900
) -> dict[str, Any]:
    status: dict[str, Any] = {}
    deadline = time.monotonic() + 120
    for _ in range(attempts):
        status = call(
            tmp_path,
            "run.status",
            {"project_id": project_id, "run_id": run_id},
            capsys,
        )
        run = status["data"]["run"]
        if run["status"] != "running":
            return status
        if time.monotonic() > deadline:
            raise AssertionError(f"运行未在截止时间内结束：{run}")
        time.sleep(0.1)
    raise AssertionError(f"运行未结束：{status}")


# -- T-02 / T-06 run.repair -------------------------------------------------


def test_run_repair_requires_confirmation_and_confirmer(
    tmp_path: Path, capsys: Capsys
) -> None:
    project, _root = _project(tmp_path, capsys)
    _manual_run(
        tmp_path,
        project["project_id"],
        suggestions=[{"module": "factory", "package": "factory-boy>=3.2"}],
    )
    payload = {
        "project_id": project["project_id"],
        "run_id": "run_audit",
        "packages": ["factory-boy>=3.2"],
        "confirmed_by": "tester",
    }
    denied = call(tmp_path, "run.repair", dict(payload), capsys)
    assert denied["error"]["code"] == "CONFIRMATION_REQUIRED"
    no_by = {key: value for key, value in payload.items() if key != "confirmed_by"}
    missing_person = call(tmp_path, "run.repair", no_by, capsys, confirm=True)
    assert missing_person["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_run_repair_rejects_bad_or_unsuggested_packages(
    tmp_path: Path, capsys: Capsys
) -> None:
    project, _root = _project(tmp_path, capsys)
    _manual_run(
        tmp_path,
        project["project_id"],
        suggestions=[{"module": "factory", "package": "factory-boy>=3.2"}],
    )
    base = {
        "project_id": project["project_id"],
        "run_id": "run_audit",
        "confirmed_by": "tester",
    }
    bad = call(
        tmp_path,
        "run.repair",
        {**base, "packages": ["--index-url=http://evil"]},
        capsys,
        confirm=True,
    )
    assert bad["error"]["code"] == "INVALID_REQUEST"
    other = call(
        tmp_path,
        "run.repair",
        {**base, "packages": ["requests>=2"]},
        capsys,
        confirm=True,
    )
    assert other["error"]["code"] == "INVALID_REQUEST"


def test_run_repair_rejects_running_and_already_installed(
    tmp_path: Path, capsys: Capsys
) -> None:
    project, _root = _project(tmp_path, capsys)
    _manual_run(
        tmp_path,
        project["project_id"],
        status="running",
        suggestions=[{"module": "factory", "package": "factory-boy>=3.2"}],
    )
    running = call(
        tmp_path,
        "run.repair",
        {
            "project_id": project["project_id"],
            "run_id": "run_audit",
            "packages": ["factory-boy>=3.2"],
            "confirmed_by": "tester",
        },
        capsys,
        confirm=True,
    )
    assert running["error"]["code"] == "RUN_ACTIVE"

    run_dir = _workspace(tmp_path).project_dir(project["project_id"]) / "runs" / "run_audit"
    run = Run.model_validate(read_json(run_dir / "run.json"))
    run.status = "completed"
    run.dependency_suggestions = [
        {"module": "factory", "package": "factory-boy>=3.2", "installed": "true"}
    ]
    write_model(run_dir / "run.json", run)
    already = call(
        tmp_path,
        "run.repair",
        {
            "project_id": project["project_id"],
            "run_id": "run_audit",
            "packages": ["factory-boy>=3.2"],
            "confirmed_by": "tester",
        },
        capsys,
        confirm=True,
    )
    assert already["error"]["code"] == "DEPENDENCY_ALREADY_INSTALLED"


def test_run_repair_records_installation_and_failure(
    tmp_path: Path, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _root = _project(tmp_path, capsys)
    run_dir, _run = _manual_run(
        tmp_path,
        project["project_id"],
        suggestions=[
            {"module": "factory", "package": "factory-boy>=3.2", "source": "declared"},
        ],
    )

    outcomes = [
        InstallOutcome(
            ok=True,
            exit_code=0,
            output=b"Successfully installed factory-boy-3.2.0\n",
            installer="pip",
            attempts=["pip"],
        )
    ]

    def fake_install(*_args, **_kwargs):
        return outcomes.pop(0)

    monkeypatch.setattr("aitest.runtime_commands.install_packages", fake_install)
    monkeypatch.setattr(
        "aitest.runtime_commands._installed_versions",
        lambda python, packages, env: {spec: "3.2.0" for spec in packages},
    )
    monkeypatch.setattr(
        "aitest.worker._environment_fingerprint",
        lambda python, root: {"python": str(python), "python_version": "3.11.0", "cwd": str(root)},
    )
    repaired = call(
        tmp_path,
        "run.repair",
        {
            "project_id": project["project_id"],
            "run_id": "run_audit",
            "packages": ["factory-boy>=3.2"],
            "confirmed_by": "tester",
        },
        capsys,
        confirm=True,
    )
    assert repaired["ok"] is True, repaired
    stored = Run.model_validate(read_json(run_dir / "run.json"))
    assert stored.dependency_repairs[0]["confirmed_by"] == "tester"
    assert stored.dependency_repairs[0]["ok"] is True
    assert stored.environment_history
    assert stored.environment_history[0]["before"]["python"] == sys.executable
    assert stored.dependency_suggestions[0]["installed"] == "true"

    run_dir2 = _workspace(tmp_path).project_dir(project["project_id"]) / "runs" / "run_audit2"
    run_dir2.mkdir(parents=True)
    run2 = Run(
        run_id="run_audit2",
        plan_id="plan_x",
        project_id=project["project_id"],
        scope=Scope(),
        status="completed",
        started_at=iso_now(),
        heartbeat_at=iso_now(),
        dependency_suggestions=[
            {"module": "factory", "package": "factory-boy>=3.2", "source": "declared"}
        ],
    )
    write_model(run_dir2 / "run.json", run2)
    outcomes.append(
        InstallOutcome(
            ok=False,
            exit_code=1,
            output=b"ERROR: no matching distribution\n",
            installer="pip",
            attempts=["pip"],
        )
    )
    failed = call(
        tmp_path,
        "run.repair",
        {
            "project_id": project["project_id"],
            "run_id": "run_audit2",
            "packages": ["factory-boy>=3.2"],
            "confirmed_by": "tester",
        },
        capsys,
        confirm=True,
    )
    assert failed["error"]["code"] == "DEPENDENCY_INSTALL_FAILED"
    written = Run.model_validate(read_json(run_dir2 / "run.json"))
    assert written.dependency_repairs[0]["ok"] is False
    assert (run_dir2 / "evidence" / "repair-deps.log").is_file()


# -- T-03 worker-level mixed failure ---------------------------------------


def _worker_case(tmp_path: Path, capsys: Capsys, script: str) -> dict[str, Any]:
    setup = _setup_task(tmp_path, capsys)
    (setup["root"] / "check.py").write_text(script, encoding="utf-8")
    plan = call(
        tmp_path,
        "plan.generate",
        _plan_payload(setup, "command:python check.py"),
        capsys,
    )["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    status = _wait_terminal(
        tmp_path, capsys, setup["project"]["project_id"], started["run_id"]
    )
    return status["data"]


def test_worker_keeps_mixed_assertion_and_missing_dependency_as_failed(
    tmp_path: Path, capsys: Capsys
) -> None:
    data = _worker_case(
        tmp_path,
        capsys,
        "print(\"ModuleNotFoundError: No module named 'factory'\")\n"
        "print('FAIL: test_total (shop.tests.test_cart)')\n"
        "raise SystemExit(1)\n",
    )
    case = data["plan"]["cases"][0]
    run_case = next(item for item in data["run"]["cases"] if item["case_id"] == case["case_id"])
    assert run_case["result"] == "failed", run_case
    assert data["run"]["dependency_suggestions"]
    assert data["outcome"] in {"failed", "incomplete"}


def test_worker_keeps_project_local_missing_module_as_failed(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup_task(tmp_path, capsys)
    (setup["root"] / "shop").mkdir()
    (setup["root"] / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (setup["root"] / "check.py").write_text(
        "print(\"ModuleNotFoundError: No module named 'shop.missing'\")\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    plan = call(
        tmp_path,
        "plan.generate",
        _plan_payload(setup, "command:python check.py"),
        capsys,
    )["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    data = _wait_terminal(
        tmp_path, capsys, setup["project"]["project_id"], started["run_id"]
    )["data"]
    case_id = data["plan"]["cases"][0]["case_id"]
    run_case = next(item for item in data["run"]["cases"] if item["case_id"] == case_id)
    assert run_case["result"] == "failed", run_case


# -- T-07 origin -----------------------------------------------------------


def test_task_add_rejects_manual_origin(tmp_path: Path, capsys: Capsys) -> None:
    project, _root = _project(tmp_path, capsys)
    rejected = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "旧来源",
            "origin": "manual",
            "acceptance_items": [{"text": "可见结果", "observable": True}],
        },
        capsys,
    )
    assert rejected["error"]["code"] == "INVALID_REQUEST"
    default = call(
        tmp_path,
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "默认来源",
            "acceptance_items": [{"text": "可见结果", "observable": True}],
        },
        capsys,
    )["data"]
    assert default["origin"] == "goal"


# -- T-08 / T-13 report environment fingerprint ----------------------------


def test_report_and_export_carry_run_environment_fingerprint(
    tmp_path: Path, capsys: Capsys
) -> None:
    data = _worker_case(tmp_path, capsys, "print('ok')\n")
    project_id = data["run"]["project_id"]
    run_id = data["run"]["run_id"]
    generated = call(
        tmp_path, "report.generate", {"project_id": project_id, "run_id": run_id}, capsys
    )["data"]
    report = call(
        tmp_path,
        "report.show",
        {"project_id": project_id, "report_id": generated["report_id"]},
        capsys,
    )["data"]["report"]
    assert report["environment"]["cwd"] == str(tmp_path / "repo")
    assert report["environment"]["python_version"]
    target = tmp_path / "report.md"
    exported = call(
        tmp_path,
        "report.export",
        {"project_id": project_id, "report_id": generated["report_id"], "path": str(target)},
        capsys,
    )
    assert exported["ok"] is True
    text = target.read_text(encoding="utf-8")
    assert "环境 Python" in text
    assert "环境 工作目录" in text
    assert str(tmp_path / "repo") in text


# -- 补-T05 real worker kill ----------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="taskkill 仅 Windows")
def test_killed_worker_is_marked_interrupted(
    tmp_path: Path, capsys: Capsys
) -> None:
    setup = _setup_task(tmp_path, capsys)
    (setup["root"] / "sleepy.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    plan = call(
        tmp_path,
        "plan.generate",
        _plan_payload(setup, "command:python sleepy.py"),
        capsys,
    )["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": setup["project"]["project_id"], "plan_id": plan["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    run_id = started["run_id"]
    pid = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = call(
            tmp_path,
            "run.status",
            {"project_id": setup["project"]["project_id"], "run_id": run_id},
            capsys,
        )["data"]
        pid = status["run"].get("pid")
        if pid:
            break
        time.sleep(0.2)
    assert pid, "worker 没有记录 pid"
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        check=False,
    )
    status = None
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        status = call(
            tmp_path,
            "run.status",
            {"project_id": setup["project"]["project_id"], "run_id": run_id},
            capsys,
        )["data"]
        if status["run"]["status"] == "interrupted":
            break
        time.sleep(0.5)
    assert status is not None
    assert status["run"]["status"] == "interrupted", status["run"]
    run_dir = (
        _workspace(tmp_path).root
        / "projects"
        / setup["project"]["project_id"]
        / "runs"
        / run_id
    )
    assert (run_dir / "run.json").is_file()
    assert (run_dir / "plan.json").is_file()


# -- T-17 docs/contract consistency ---------------------------------------


def test_docs_command_counts_and_panel_paths_match_code() -> None:
    import aitest.integration_commands  # noqa: F401
    import aitest.runtime_commands  # noqa: F401
    from aitest.commands import REGISTRY

    root = Path(__file__).resolve().parents[2]
    assert len(REGISTRY) == 60
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "**60** 个命令" in readme
    assert "**59** 个命令" not in readme
    architecture = (
        root / "docs/项目文档/一期/04-架构/02-CLI与扩展.md"
    ).read_text(encoding="utf-8")
    assert "注册 60 条命令" in architecture
    assert "panel/src/host/commands.ts" in architecture
    assert "panel/src/api/commands.ts" not in architecture
    contract = (
        root / "docs/项目文档/一期/03-设计/03-CLI与数据契约.md"
    ).read_text(encoding="utf-8")
    assert "当前注册 60 条命令" in contract


# -- T-18 old data visibility ---------------------------------------------


def test_run_list_reports_old_schema_instead_of_hiding_it(
    tmp_path: Path, capsys: Capsys
) -> None:
    project, _root = _project(tmp_path, capsys)
    run_dir = _workspace(tmp_path).project_dir(project["project_id"]) / "runs" / "run_old"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"schema_version": 0, "run_id": "run_old"}), encoding="utf-8"
    )
    listed = call(tmp_path, "run.list", {"project_id": project["project_id"]}, capsys)
    assert listed["data"]["runs"] == []
    assert listed["data"]["skipped"][0]["run_id"] == "run_old"
    assert listed["data"]["skipped"][0]["code"] == "SCHEMA_UNSUPPORTED"


def test_old_project_config_is_rejected(tmp_path: Path, capsys: Capsys) -> None:
    project, _root = _project(tmp_path, capsys)
    path = _workspace(tmp_path).project_dir(project["project_id"]) / "project.config.json"
    write_json(path, {"schema_version": 0, "model": {"name": "old"}})
    result = call(
        tmp_path,
        "project.config",
        {"project_id": project["project_id"], "action": "get"},
        capsys,
    )
    assert result["error"]["code"] == "SCHEMA_UNSUPPORTED"


# -- 补-T04 cross-process workspace lock ----------------------------------


def test_workspace_lock_blocks_another_process(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.init()
    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from aitest.storage.workspace import Workspace\n"
        "ws = Workspace(Path(sys.argv[1]))\n"
        "with ws.lock(timeout=5):\n"
        "    print('locked', flush=True)\n"
        "    time.sleep(3)\n"
    )
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(ws.root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(AiTestError) as excinfo, ws.lock(timeout=0.2):
            pass
        assert excinfo.value.info.code == "WORKSPACE_LOCKED"
    finally:
        child.kill()
        child.wait(timeout=10)


# -- 补-T09 command-level coverage ----------------------------------------


def test_template_rules_module_and_evidence_commands(
    tmp_path: Path, capsys: Capsys
) -> None:
    project, _root = _project(tmp_path, capsys)
    project_id = project["project_id"]
    templates = call(tmp_path, "template.list", {"project_id": project_id}, capsys)
    assert templates["data"]["templates"]

    saved = call(
        tmp_path,
        "rules.save",
        {"project_id": project_id, "name": "规则", "version": "1", "steps_md": "# 步骤"},
        capsys,
    )["data"]
    shown = call(
        tmp_path,
        "rules.show",
        {"project_id": project_id, "rules_version_id": saved["rules_version_id"]},
        capsys,
    )["data"]["rule"]
    assert shown["name"] == "规则"
    target = tmp_path / "rule.md"
    exported = call(
        tmp_path,
        "rules.export",
        {
            "project_id": project_id,
            "rules_version_id": saved["rules_version_id"],
            "path": str(target),
        },
        capsys,
    )
    assert exported["ok"] is True
    source = tmp_path / "import.md"
    source.write_text("# 导入规则", encoding="utf-8")
    imported = call(
        tmp_path,
        "rules.import",
        {"project_id": project_id, "name": "导入", "version": "2", "path": str(source)},
        capsys,
    )["data"]
    assert imported["version"] == "2"

    module = call(
        tmp_path,
        "module.add",
        {"project_id": project_id, "name": "订单", "responsibility": "下单"},
        capsys,
    )["data"]
    listed = call(tmp_path, "module.list", {"project_id": project_id}, capsys)["data"]
    assert any(item["module_id"] == module["module_id"] for item in listed["modules"])
    updated = call(
        tmp_path,
        "module.update",
        {"project_id": project_id, "module_id": module["module_id"], "patch": {"owner": "张三"}},
        capsys,
    )["data"]
    assert updated["module_id"] == module["module_id"]

    add_run = _manual_run(tmp_path, project_id)
    run_dir = add_run[0]
    evidence_file = tmp_path / "shot.log"
    evidence_file.write_text("ok", encoding="utf-8")
    added = call(
        tmp_path,
        "evidence.add",
        {"project_id": project_id, "run_id": "run_audit", "path": str(evidence_file)},
        capsys,
    )["data"]
    assert (run_dir / added["evidence_ref"]).is_file()
    evidence = call(
        tmp_path,
        "evidence.list",
        {"project_id": project_id, "run_id": "run_audit"},
        capsys,
    )["data"]
    assert evidence["evidence"][0]["exists"] is True
    analysis = call(
        tmp_path,
        "analysis.run",
        {"project_id": project_id, "run_id": "run_audit"},
        capsys,
    )["data"]
    assert analysis["status"] in {"not_generated", "invalid"}
    assert analysis["reason"] in {
        "no_failures",
        "model_missing",
        "model_invalid",
        "model_error",
        "",
    }


def test_issue_update_and_close_paths(tmp_path: Path, capsys: Capsys) -> None:
    setup = _setup_task(tmp_path, capsys)
    project_id = setup["project"]["project_id"]
    failing = call(
        tmp_path,
        "plan.generate",
        _plan_payload(setup, "command:python -c \"raise SystemExit(1)\""),
        capsys,
    )["data"]
    started = call(
        tmp_path,
        "run.start",
        {"project_id": project_id, "plan_id": failing["plan_id"]},
        capsys,
        confirm=True,
    )["data"]
    status = _wait_terminal(tmp_path, capsys, project_id, started["run_id"])
    case_id = status["data"]["plan"]["cases"][0]["case_id"]
    issue = call(
        tmp_path,
        "issue.add",
        {
            "project_id": project_id,
            "run_id": started["run_id"],
            "case_id": case_id,
            "title": "失败",
            "error_type": "assertion",
            "stack_key": "stack-audit",
        },
        capsys,
    )["data"]
    updated = call(
        tmp_path,
        "issue.update",
        {"project_id": project_id, "issue_id": issue["issue_id"], "status": "in_progress"},
        capsys,
    )["data"]
    assert updated["status"] == "in_progress"
    rejected = call(
        tmp_path,
        "issue.update",
        {"project_id": project_id, "issue_id": issue["issue_id"], "status": "closed"},
        capsys,
    )
    assert rejected["error"]["code"] == "INVALID_REQUEST"
    cannot_close = call(
        tmp_path,
        "issue.close",
        {"project_id": project_id, "issue_id": issue["issue_id"], "run_id": started["run_id"]},
        capsys,
    )
    assert cannot_close["error"]["code"] == "RUN_STATE_CONFLICT"


# -- 补-T10 backup boundaries ---------------------------------------------


def _write_zip(path: Path, entries: list[tuple[str, bytes, int]], manifest: dict[str, Any]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data, attr in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = attr
            archive.writestr(info, data)
        archive.writestr("manifest.json", json.dumps(manifest))


def _manifest(files: dict[str, bytes]) -> dict[str, Any]:
    import hashlib

    return {
        "schema_version": 1,
        "file_count": len(files),
        "files": sorted(files),
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }


def test_backup_restore_rejects_duplicate_symlink_and_schema(
    tmp_path: Path, capsys: Capsys
) -> None:
    _project(tmp_path, capsys)
    files = {"a.txt": b"a"}
    manifest = _manifest(files)
    duplicate = tmp_path / "dup.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("a.txt", b"a")
        archive.writestr("a.txt", b"a")
        archive.writestr("manifest.json", json.dumps(manifest))
    result = call(
        tmp_path,
        "backup.restore",
        {"path": str(duplicate), "target": str(tmp_path / "out-dup")},
        capsys,
    )
    assert result["error"]["code"] == "BACKUP_INVALID"

    symlink = tmp_path / "symlink.zip"
    _write_zip(symlink, [("link", b"", (0o120777 << 16))], _manifest({}))
    result = call(
        tmp_path,
        "backup.restore",
        {"path": str(symlink), "target": str(tmp_path / "out-link")},
        capsys,
    )
    assert result["error"]["code"] == "BACKUP_INVALID"

    old = tmp_path / "old.zip"
    _write_zip(old, [("a.txt", b"a", 0)], {**manifest, "schema_version": 2})
    result = call(
        tmp_path,
        "backup.restore",
        {"path": str(old), "target": str(tmp_path / "out-old")},
        capsys,
    )
    assert result["error"]["code"] == "SCHEMA_UNSUPPORTED"


def test_backup_restore_enforces_limits(
    tmp_path: Path, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aitest import runtime_commands

    _project(tmp_path, capsys)
    files = {"a.txt": b"1234"}
    archive_path = tmp_path / "limit.zip"
    _write_zip(archive_path, [("a.txt", b"1234", 0)], _manifest(files))
    monkeypatch.setattr(runtime_commands, "_MAX_BACKUP_FILES", 0)
    too_many = call(
        tmp_path,
        "backup.restore",
        {"path": str(archive_path), "target": str(tmp_path / "out-many")},
        capsys,
    )
    assert too_many["error"]["code"] == "BACKUP_INVALID"
    monkeypatch.setattr(runtime_commands, "_MAX_BACKUP_FILES", 100)
    monkeypatch.setattr(runtime_commands, "_MAX_BACKUP_UNCOMPRESSED", 1)
    too_big = call(
        tmp_path,
        "backup.restore",
        {"path": str(archive_path), "target": str(tmp_path / "out-big")},
        capsys,
    )
    assert too_big["error"]["code"] == "BACKUP_INVALID"


# -- 补-T11 storage write failure / retries --------------------------------


def _raiser(exc: BaseException):
    def raise_it(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return raise_it


def test_write_json_maps_permission_and_disk_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aitest.storage import files

    target = tmp_path / "x.json"
    real_mkstemp = files.tempfile.mkstemp
    monkeypatch.setattr(
        files.tempfile,
        "mkstemp",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "denied")),
    )
    with pytest.raises(AiTestError) as excinfo:
        write_json(target, {"schema_version": 1})
    assert excinfo.value.info.code == "PERMISSION_DENIED"
    monkeypatch.setattr(files.tempfile, "mkstemp", real_mkstemp)
    monkeypatch.setattr(
        files.os, "replace", _raiser(PermissionError(13, "busy", None, 5))
    )
    with pytest.raises(AiTestError) as excinfo:
        write_json(target, {"schema_version": 1})
    assert excinfo.value.info.code == "FILE_BUSY"

    monkeypatch.setattr(files.os, "replace", _raiser(OSError(28, "full")))
    with pytest.raises(AiTestError) as excinfo:
        write_json(target, {"schema_version": 1})
    assert excinfo.value.info.code == "DISK_FULL"

    monkeypatch.setattr(files.os, "replace", _raiser(OSError(36, "long")))
    with pytest.raises(AiTestError) as excinfo:
        write_json(target, {"schema_version": 1})
    assert excinfo.value.info.code == "PATH_TOO_LONG"

    monkeypatch.setattr(files.os, "replace", _raiser(OSError(12345, "io")))
    with pytest.raises(AiTestError) as excinfo:
        write_json(target, {"schema_version": 1})
    assert excinfo.value.info.code == "WRITE_FAILED"


# -- 补-T22 cleanup environment -------------------------------------------


def test_data_cleanup_subprocess_drops_credentials(
    tmp_path: Path, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, root = _project(tmp_path, capsys)
    config_path = (
        _workspace(tmp_path).root
        / "projects"
        / project["project_id"]
        / "project.config.json"
    )
    write_json(
        config_path,
        {"schema_version": 1, "data": {"cleanup": "echo cleaned {run_id}"}},
    )
    monkeypatch.setenv("AITEST_MODEL_KEY", "sk-should-not-leak-0000000000")
    monkeypatch.setenv("AITEST_GITHUB_TOKEN", "ghp_shouldnotleak0000000000000")
    seen: dict[str, Any] = {}

    from aitest.runners.base import RunnerResult

    def fake_run(*args: Any, **kwargs: Any) -> RunnerResult:
        seen.update(kwargs.get("env") or {})
        return RunnerResult(status="passed", exit_code=0, stdout=b"done")

    monkeypatch.setattr("aitest.runners.command_runner.run_command", fake_run)
    result = call(
        tmp_path,
        "data.cleanup",
        {"project_id": project["project_id"], "run_id": "run_clean", "dry_run": False},
        capsys,
        confirm=True,
    )
    assert result["ok"] is True, result
    assert "AITEST_MODEL_KEY" not in seen
    assert "AITEST_GITHUB_TOKEN" not in seen
    assert seen["AITEST_RUN_ID"] == "run_clean"
