"""Regression tests for false-pass, credential and confirmation boundaries."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from aitest.cli import main
from aitest.core.expectations import ensure_publishable
from aitest.core.manual import aggregate_manual
from aitest.core.verdict import coverage, verdict
from aitest.models import (
    Case,
    ManualStep,
    Plan,
    Run,
    RunCase,
    Scope,
)
from aitest.security import is_secret_field, redact_bytes


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


def _case(case_id: str, item: str, *, verified: bool = True) -> Case:
    return Case(
        case_id=case_id,
        acceptance_item_id=item,
        layer="L2",
        expected="预期",
        expectation_source="验收项原文" if verified else "",
        expectation_verified=verified,
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
    for name in ("monkey", "max_tokens", "keyboard", "model_name"):
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
