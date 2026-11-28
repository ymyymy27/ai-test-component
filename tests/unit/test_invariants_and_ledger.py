"""断言强度评级与无伪不变量规划的行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from aitest.core.reports import build_report
from aitest.core.script_linter import assess_assertion_strength
from aitest.errors import AiTestError
from aitest.models import AcceptanceItem, Attempt, Case, Plan, Project, Run, RunCase, Task
from aitest.runtime.plan_commands import cmd_plan_generate
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace


def test_plan_generate_without_model_requires_model_unless_explicit_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """阶段 1B 禁用伪确定性用例注入；无模型且未传 cases 时报 MODEL_UNAVAILABLE。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)
    monkeypatch.setenv("AITEST_AI_MODE", "off")

    ws = Workspace(tmp_path / "ws")
    fixture_root = Path("tests/fixtures/zoo/static/fastapi_router").resolve()
    project = Project(project_id="proj_off", name="fastapi-demo", root=str(fixture_root))
    ws.save_project(project)
    task = Task(
        task_id="task_off",
        project_id="proj_off",
        title="无模型规划检查",
        acceptance_items=[
            AcceptanceItem(item_id="ai_1", text="任务支持创建、查询列表、更新与删除持久化"),
        ],
    )
    write_model(ws.project_dir("proj_off") / "tasks" / "task_off.json", task)

    with pytest.raises(AiTestError) as exc_info:
        cmd_plan_generate(
            ws,
            {
                "project_id": "proj_off",
                "task_id": "task_off",
                "scope": {"preset": "all_applicable"},
            },
            False,
        )
    assert exc_info.value.info.code == "MODEL_UNAVAILABLE"

    out = cmd_plan_generate(
        ws,
        {
            "project_id": "proj_off",
            "task_id": "task_off",
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": "c_exp_1",
                    "acceptance_item_id": "ai_1",
                    "layer": "L2",
                    "title": "显式接口验证",
                    "expected": "任务支持创建、查询列表、更新与删除持久化",
                    "expectation_source": "任务支持创建、查询列表、更新与删除持久化",
                    "preconditions": ["服务已启动"],
                    "verify_method": (
                        'http:[{"method":"GET","url":"http://127.0.0.1:8000/items",'
                        '"expect":[{"path":"status","operator":"equals","value":200},'
                        '{"path":"body","operator":"exists","value":true}]}]'
                    ),
                }
            ],
        },
        False,
    )
    assert out["ai_generated"] is False
    assert out["case_count"] == 1
    plan = read_model(ws.project_dir("proj_off") / "plans" / f"{out['plan_id']}.json", Plan)
    assert plan.recipes == []
    assert plan.coverage_ledger == []
    assert plan.capabilities is not None and plan.capabilities["level"] == "T1"
    assert plan.cases[0].assertion_strength == "strong"

    run_dir = ws.project_dir("proj_off") / "runs" / "run_off"
    run = Run(
        run_id="run_off",
        plan_id=plan.plan_id,
        project_id="proj_off",
        scope=plan.scope,
        status="completed",
        cases=[
            RunCase(
                case_id=c.case_id,
                result="passed",
                attempts=[Attempt(attempt=1, result="passed", stage="verify")],
            )
            for c in plan.cases
        ],
    )
    write_model(run_dir / "run.json", run)
    write_model(run_dir / "plan.json", plan)
    write_model(run_dir / "task.json", task)

    report = build_report(ws, project, run_dir, use_model=False)
    assert report.capabilities is not None
    assert report.coverage_ledger == []
    assert report.assertion_strength["strong"] == 1


def test_assertion_strength_distinguishes_strong_weak_and_unverified() -> None:
    """验证强/弱/未证实断言评级。"""
    weak_http = Case(
        case_id="c_weak",
        acceptance_item_id="ai_1",
        layer="L2",
        expected="返回 200",
        expectation_source="返回 200",
        expectation_verified=True,
        verify_method='http:[{"method":"GET","url":"http://127.0.0.1:8000/items","expect":[{"path":"status","operator":"equals","value":200}]}]',
    )
    assert assess_assertion_strength(weak_http) == "weak"

    unverified_case = Case(
        case_id="c_unv",
        acceptance_item_id="ai_1",
        layer="L2",
        expected="返回 200",
        expectation_source="",
        expectation_verified=False,
        verify_method='http:[{"method":"GET","url":"http://127.0.0.1:8000/items","expect":[{"path":"status","operator":"equals","value":200},{"path":"id","operator":"exists","value":true}]}]',
    )
    assert assess_assertion_strength(unverified_case) == "unverified"
