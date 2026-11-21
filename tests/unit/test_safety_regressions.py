"""Regression tests for false-pass, credential and confirmation boundaries."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main
from aitest.commands import _reference_problems
from aitest.core.expectations import ensure_publishable
from aitest.core.manual import aggregate_manual
from aitest.core.verdict import coverage, verdict
from aitest.errors import AiTestError
from aitest.integration_commands import cmd_link_assert
from aitest.models import (
    Case,
    ManualStep,
    Plan,
    Project,
    Run,
    RunCase,
    Scope,
)
from aitest.security import is_secret_field, redact_bytes
from aitest.storage.workspace import Workspace


def _plan(cases: list[Case], selected: list[str], applicable: int) -> Plan:
    return Plan(
        plan_id="plan_1",
        task_id="task_1",
        scope=Scope(preset="selected", acceptance_item_ids=selected),
        applicable_count=applicable,
        cases=cases,
    )


def _run(cases: list[RunCase]) -> Run:
    return Run(
        run_id="run_1",
        plan_id="plan_1",
        project_id="proj_1",
        scope=Scope(preset="selected", acceptance_item_ids=["a", "b"]),
        cases=cases,
    )


def _case(
    case_id: str,
    item: str,
    *,
    verified: bool = True,
    coverage_kind: str | None = None,
) -> Case:
    return Case(
        case_id=case_id,
        acceptance_item_id=item,
        layer="L2",
        expected="预期",
        expectation_source="验收项原文" if verified else "",
        expectation_verified=verified,
        coverage_kind=coverage_kind,
    )


def test_selected_item_without_case_is_not_a_pass() -> None:
    plan = _plan([_case("c1", "a")], ["a", "b"], 2)
    run = _run([RunCase(case_id="c1", result="passed")])

    assert verdict(plan, run) == "incomplete"
    counts = coverage(plan, run)
    assert counts["selected"] == 2
    assert counts["covered"] == 1
    assert counts["passed"] == 1
    assert counts["incomplete"] == 1


def test_unverified_expectation_cannot_pass() -> None:
    plan = _plan([_case("c1", "a", verified=False)], ["a"], 1)
    run = _run([RunCase(case_id="c1", result="passed")])

    assert verdict(plan, run) == "incomplete"
    assert coverage(plan, run)["passed"] == 0


def test_file_level_coverage_cannot_pass() -> None:
    plan = _plan([_case("c1", "a", coverage_kind="file")], ["a"], 1)
    run = _run([RunCase(case_id="c1", result="passed")])

    assert verdict(plan, run) == "incomplete"
    assert coverage(plan, run)["passed"] == 0


def test_shared_test_node_cannot_pass_each_item() -> None:
    plan = _plan(
        [
            _case("c1", "a", coverage_kind="shared"),
            _case("c2", "b", coverage_kind="shared"),
        ],
        ["a", "b"],
        2,
    )
    run = _run(
        [
            RunCase(case_id="c1", result="passed"),
            RunCase(case_id="c2", result="passed"),
        ]
    )

    assert verdict(plan, run) == "incomplete"
    assert coverage(plan, run)["passed"] == 0


def test_empty_scope_with_no_cases_is_not_applicable() -> None:
    plan = _plan([], [], 0)
    assert verdict(plan, _run([])) == "not_applicable"


def test_publish_requires_preconditions_but_allows_unverified_expectation() -> None:
    unverified = Case(
        case_id="c1",
        acceptance_item_id="a",
        layer="L2",
        expected="预期",
        expectation_source="与验收项无关",
        preconditions=["服务已启动"],
    )
    ensure_publishable([unverified])

    missing = Case(
        case_id="c2",
        acceptance_item_id="a",
        layer="L2",
        expected="预期",
        preconditions=[],
    )
    with pytest.raises(Exception) as excinfo:
        ensure_publishable([missing])
    assert "前置条件" in excinfo.value.info.next_step  # type: ignore[attr-defined]


def test_manual_steps_must_all_be_recorded() -> None:
    steps = [ManualStep(step="打开页面", result="passed")]
    assert aggregate_manual(steps, ["打开页面", "看到订单"]) == "manual_pending"
    assert aggregate_manual(steps, ["打开页面"]) == "passed"


def test_secret_field_matching_covers_camel_case_and_compounds() -> None:
    for name in ("model_key", "accessToken", "dbPassword", "openaiApiKey", "client_secret"):
        assert is_secret_field(name), name
    for name in ("monkey", "max_tokens", "keyboard", "model_name", "fields.key"):
        assert not is_secret_field(name), name


def test_redact_bytes_keeps_binary_and_redacts_text() -> None:
    secret = "sk-test-1234567890"
    assert secret not in redact_bytes(b"before sk-test-1234567890 after", [secret]).decode()
    binary = b"\x89PNG\x00\x00" + secret.encode()
    assert redact_bytes(binary, [secret]) == binary


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)


def test_confirmed_by_alone_does_not_authorize_a_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def call(command: str, payload: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
        request_id = f"{command}-{uuid.uuid4().hex[:8]}"
        request = tmp_path / "req.json"
        request.write_text(
            json.dumps(
                {"command": command, "request_id": request_id, "payload": payload},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        argv = [command, "--workspace", str(tmp_path / "ws"), "--input", str(request)]
        if confirm:
            argv.append("--confirm")
        main(argv)
        return json.loads(capsys.readouterr().out.strip())

    root = tmp_path / "repo"
    root.mkdir()
    project = call("project.add", {"root": str(root)})["data"]
    task = call(
        "task.add",
        {
            "project_id": project["project_id"],
            "title": "验证",
            "acceptance_items": [{"text": "订单出现", "observable": True}],
        },
    )["data"]
    item = task["acceptance_items"][0]["item_id"]
    plan = call(
        "plan.generate",
        {
            "project_id": project["project_id"],
            "task_id": task["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
            "cases": [
                {
                    "acceptance_item_id": item,
                    "layer": "L2",
                    "expected": "命令成功",
                    "expectation_source": "订单出现",
                    "preconditions": ["项目目录可访问"],
                    "verify_method": "command: echo ok",
                }
            ],
        },
    )["data"]
    result = call(
        "run.start",
        {
            "project_id": project["project_id"],
            "plan_id": plan["plan_id"],
            "confirmed_by": "someone",
        },
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_project_config_rejects_credential_fields_and_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def call(command: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = f"{command}-{uuid.uuid4().hex[:8]}"
        request = tmp_path / "req.json"
        request.write_text(
            json.dumps(
                {"command": command, "request_id": request_id, "payload": payload},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        main([command, "--workspace", str(tmp_path / "ws"), "--input", str(request)])
        return json.loads(capsys.readouterr().out.strip())

    root = tmp_path / "repo"
    root.mkdir()
    project = call("project.add", {"root": str(root)})["data"]
    for patch in (
        {"model": {"accessToken": "x"}},
        {"model": {"model_key": "x"}},
        {"link": {"db_query": "postgres://user:pass@localhost/db"}},
    ):
        result = call(
            "project.config",
            {"project_id": project["project_id"], "action": "update", "patch": patch},
        )
        assert result["ok"] is False, patch
        assert result["error"]["code"] == "INVALID_REQUEST"


def test_known_secret_is_redacted_before_task_and_plan_are_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-redaction-regression-123456"
    monkeypatch.setenv("AITEST_MODEL_KEY", secret)

    def call(command: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = f"{command}-{uuid.uuid4().hex[:8]}"
        request = tmp_path / "req.json"
        request.write_text(
            json.dumps(
                {"command": command, "request_id": request_id, "payload": payload},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        main([command, "--workspace", str(tmp_path / "ws"), "--input", str(request)])
        return json.loads(capsys.readouterr().out.strip())

    root = tmp_path / "repo"
    root.mkdir()
    project = call("project.add", {"root": str(root)})["data"]
    task = call(
        "task.add",
        {
            "project_id": project["project_id"],
            "title": f"标题 {secret}",
            "acceptance_items": [{"text": f"输出 {secret}", "observable": True}],
        },
    )["data"]
    item = task["acceptance_items"][0]["item_id"]
    plan = call(
        "plan.generate",
        {
            "project_id": project["project_id"],
            "task_id": task["task_id"],
            "scope": {"preset": "selected", "acceptance_item_ids": [item]},
            "cases": [
                {
                    "acceptance_item_id": item,
                    "layer": "L2",
                    "expected": f"输出 {secret}",
                    "expectation_source": f"输出 {secret}",
                    "preconditions": ["目录可访问"],
                    "verify_method": f"command: echo {secret}",
                }
            ],
        },
    )
    assert plan["ok"] is True, plan

    leaks: list[str] = []
    for path in (tmp_path / "ws").rglob("*"):
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if secret in text:
                leaks.append(str(path))
    assert leaks == []


def test_link_assert_requires_an_imported_node_and_persists_assertion(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.init()
    ws.save_project(Project(project_id="proj_1", name="p", root=str(tmp_path)))
    run_dir = ws.project_dir("proj_1") / "runs" / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "link.json").write_text(
        json.dumps(
            {"schema_version": 1, "nodes": [{"node_id": "log:1"}], "assertions": []}
        ),
        encoding="utf-8",
    )

    result = cmd_link_assert(
        ws,
        {
            "project_id": "proj_1",
            "run_id": "run_1",
            "node_id": "log:1",
            "expected": "a",
            "actual": "a",
        },
        False,
    )
    assert result == {"result": "passed"}
    stored = json.loads((run_dir / "link.json").read_text(encoding="utf-8"))
    assert stored["assertions"] == [
        {"node_id": "log:1", "expected": "a", "actual": "a", "result": "passed"}
    ]

    with pytest.raises(AiTestError):
        cmd_link_assert(
            ws,
            {
                "project_id": "proj_1",
                "run_id": "run_1",
                "node_id": "missing",
                "expected": "a",
                "actual": "a",
            },
            False,
        )


def test_doctor_reports_missing_evidence_references(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.init()
    ws.save_project(Project(project_id="proj_1", name="p", root=str(tmp_path)))
    run_dir = ws.project_dir("proj_1") / "runs" / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "run_1",
                "plan_id": "plan_1",
                "project_id": "proj_1",
                "scope": {"acceptance_item_ids": []},
                "evidence_refs": ["evidence/missing.log"],
                "cases": [],
            }
        ),
        encoding="utf-8",
    )
    assert _reference_problems(ws) == ["run_1:evidence/missing.log"]


def test_one_case_can_count_for_multiple_acceptance_items() -> None:
    case = Case(
        case_id="c1",
        acceptance_item_id="a",
        acceptance_item_ids=["b"],
        layer="L2",
        expected="预期",
        expectation_verified=True,
    )
    plan = _plan([case], ["a", "b"], 2)
    run = _run([RunCase(case_id="c1", result="passed")])

    assert verdict(plan, run) == "passed"
    counts = coverage(plan, run)
    assert counts["selected"] == 2
    assert counts["covered"] == 2
    assert counts["passed"] == 2


def test_l1_failure_cannot_be_hidden_by_passing_cases() -> None:
    plan = _plan([_case("c1", "a")], ["a"], 1)
    run = _run([RunCase(case_id="c1", result="passed")])
    run.l1 = {"build": {"ok": False}, "entries": [], "cancelled": False}
    assert verdict(plan, run) == "failed"


def test_cancelled_l1_is_incomplete() -> None:
    plan = _plan([_case("c1", "a")], ["a"], 1)
    run = _run([RunCase(case_id="c1", result="passed")])
    run.l1 = {"build": {"ok": True}, "entries": [], "cancelled": True}
    assert verdict(plan, run) == "incomplete"


def test_dead_worker_pid_is_stale_after_short_grace() -> None:
    from datetime import datetime, timedelta

    from aitest.runtime_commands import _heartbeat_stale

    run = _run([RunCase(case_id="c1", result="not_run")])
    run.status = "running"
    run.pid = 999_999_999
    run.pid_created_at = 1.0
    run.heartbeat_at = (datetime.now().astimezone() - timedelta(seconds=10)).isoformat()
    assert _heartbeat_stale(run) is True


def test_live_worker_is_not_stale_only_because_heartbeat_is_old() -> None:
    import os
    from datetime import datetime, timedelta

    from aitest.runtime_commands import _heartbeat_stale

    run = _run([RunCase(case_id="c1", result="not_run")])
    run.status = "running"
    run.pid = os.getpid()
    run.pid_created_at = None
    run.heartbeat_at = (datetime.now().astimezone() - timedelta(seconds=300)).isoformat()
    assert _heartbeat_stale(run) is False


def test_request_cache_key_is_bound_to_command_and_payload(tmp_path: Path) -> None:
    from aitest.commands import request_cache_path

    ws = Workspace(tmp_path / "ws")
    first = request_cache_path(ws, "same-id", "task.add", {"title": "a"})
    second = request_cache_path(ws, "same-id", "plan.generate", {"title": "a"})
    third = request_cache_path(ws, "same-id", "task.add", {"title": "b"})
    assert first != second
    assert first != third


def test_smoke_with_active_items_cannot_start_empty_scope() -> None:
    from aitest.models import AcceptanceItem, Task
    from aitest.runtime_commands import _ensure_plan_coverage

    task = Task(
        task_id="task_1",
        project_id="proj_1",
        title="快速检查",
        intent="smoke",
        acceptance_items=[
            AcceptanceItem(item_id="a", text="有验收项", status="active")
        ],
    )
    plan = _plan([], [], 1)
    with pytest.raises(AiTestError) as excinfo:
        _ensure_plan_coverage(plan, task)
    assert excinfo.value.info.code == "SCOPE_EMPTY"


def test_redact_bytes_handles_utf16_and_token_values() -> None:
    text = '{"access_token": "glpat-abcdefghijklmnop1234", "name": "测试"}'
    data = text.encode("utf-16")
    redacted = redact_bytes(data, [])
    assert "glpat-" not in redacted.decode("utf-16")
    assert "测试" in redacted.decode("utf-16")


def test_zip_restore_rejects_non_canonical_member_paths() -> None:
    from aitest.runtime_commands import _check_zip_member

    assert _check_zip_member("workspace.json") == "workspace.json"
    with pytest.raises(AiTestError):
        _check_zip_member("./workspace.json")
    with pytest.raises(AiTestError):
        _check_zip_member("../escape.json")


def test_run_env_strips_secret_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from aitest.worker import _run_env

    monkeypatch.setenv("AITEST_MODEL_KEY", "sk-secret-value-1234567890")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_value_1234567890")
    package_src = str(Path(__file__).resolve().parents[2] / "src")
    monkeypatch.setenv("PYTHONPATH", package_src + os.pathsep + str(tmp_path))
    env = _run_env(tmp_path, None, "run_1")
    assert "AITEST_MODEL_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert str(Path(__file__).resolve().parents[2] / "src") not in env.get("PYTHONPATH", "")


def test_run_env_adds_project_src_layout(tmp_path: Path) -> None:
    from aitest.worker import _run_env

    (tmp_path / "src").mkdir()
    env = _run_env(tmp_path, None, "run_src")
    paths = env.get("PYTHONPATH", "").split(os.pathsep)
    assert str((tmp_path / "src").resolve()) in paths
    assert str(tmp_path.resolve()) in paths


def test_worker_boot_failure_sets_execution_error(tmp_path: Path) -> None:
    from aitest.storage.files import write_model
    from aitest.worker import main as worker_main

    ws = Workspace(tmp_path / "ws")
    project_id = "proj_boot"
    run_id = "run_boot"
    run_dir = ws.project_dir(project_id) / "runs" / run_id
    run_dir.mkdir(parents=True)
    write_model(
        run_dir / "run.json",
        Run(
            run_id=run_id,
            plan_id="plan_missing",
            project_id=project_id,
            scope=Scope(),
            status="running",
        ),
    )
    code = worker_main(
        ["--workspace", str(ws.root), "--project", project_id, "--run", run_id]
    )
    assert code == 1
    failed = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert failed["status"] == "execution_error"
    assert (run_dir / "worker-error.txt").is_file()


def test_analysis_store_merges_current_run_and_rejects_outside_refs(tmp_path: Path) -> None:
    from aitest.core.analysis import _store
    from aitest.models import Analysis
    from aitest.storage.files import read_model, write_model

    ws = Workspace(tmp_path / "ws")
    project_id = "proj_analysis"
    run_dir = ws.project_dir(project_id) / "runs" / "run_analysis"
    run_dir.mkdir(parents=True)
    write_model(
        run_dir / "run.json",
        Run(
            run_id="run_analysis",
            plan_id="plan_1",
            project_id=project_id,
            scope=Scope(),
            status="completed",
            evidence_refs=["evidence/ok.log"],
        ),
    )
    # Another writer adds a manual result after the analysis snapshot was read.
    current = read_model(run_dir / "run.json", Run)
    current.evidence_refs.append("evidence/late.log")
    write_model(run_dir / "run.json", current)
    _store(
        ws,
        project_id,
        run_dir,
        Analysis(
            status="generated",
            observed="ok",
            evidence_refs=["evidence/ok.log", "evidence/late.log", "../escape.log"],
        ),
    )
    saved = read_model(run_dir / "run.json", Run)
    assert saved.evidence_refs == ["evidence/ok.log", "evidence/late.log"]
    assert saved.analysis[-1].evidence_refs == ["evidence/ok.log", "evidence/late.log"]


def test_run_env_keeps_connection_strings_needed_by_tests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from aitest.worker import _run_env

    dsn = "postgres://user:pass@localhost/db"
    monkeypatch.setenv("DATABASE_URL", dsn)
    env = _run_env(tmp_path, None, "run_1")
    assert env.get("DATABASE_URL") == dsn


def test_missing_task_snapshot_does_not_report_zero_uncovered() -> None:
    from aitest.core.scope import uncovered_items

    items = uncovered_items(None, Scope(preset="selected", acceptance_item_ids=["a"]), 3)
    assert len(items) == 2
    assert all("任务快照缺失" in item for item in items)
