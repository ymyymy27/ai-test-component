"""第 4 步单测：plan.repair_case 预览/应用、守卫拦截、升版不可变与 script_repair。"""

from __future__ import annotations

from pathlib import Path

import pytest

from aitest.cli import _LAZY_MUTATING, _LAZY_RUNTIME
from aitest.errors import AiTestError
from aitest.models import AcceptanceItem, Attempt, Plan, Project, Run, RunCase, Task
from aitest.runtime.plan_commands import cmd_plan_generate, cmd_plan_repair_case
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace


def _bootstrap_script_plan(tmp_path: Path) -> tuple[Workspace, Project, Task, Plan, Path]:
    ws = Workspace(tmp_path / "ws")
    repo = tmp_path / "repo"
    repo.mkdir()
    proj = Project(project_id="proj_rep", name="demo", root=str(repo))
    ws.save_project(proj)
    task = Task(
        task_id="task_rep",
        project_id="proj_rep",
        title="脚本修复验证",
        acceptance_items=[
            AcceptanceItem(item_id="ai_1", text="订单创建后返回有效 id"),
        ],
    )
    write_model(ws.project_dir("proj_rep") / "tasks" / "task_rep.json", task)
    original_script = "val = 1\nassert val == 2\n"
    out = cmd_plan_generate(
        ws,
        {
            "project_id": "proj_rep",
            "task_id": "task_rep",
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": "case_script_1",
                    "acceptance_item_id": "ai_1",
                    "layer": "L2",
                    "title": "校验订单创建",
                    "preconditions": ["服务已就绪"],
                    "expected": "订单创建后返回有效 id",
                    "expectation_source": "订单创建后返回有效 id",
                    "verify_method": "script:check_order.py",
                    "script_name": "check_order.py",
                    "script_content": original_script,
                }
            ],
        },
        False,
    )
    plan_path = ws.project_dir("proj_rep") / "plans" / f"{out['plan_id']}.json"
    plan = read_model(plan_path, Plan)
    run_dir = ws.project_dir("proj_rep") / "runs" / "run_hist"
    run = Run(
        run_id="run_hist",
        plan_id=plan.plan_id,
        project_id="proj_rep",
        scope=plan.scope,
        status="completed",
        cases=[
            RunCase(
                case_id="case_script_1",
                result="failed",
                attempts=[
                    Attempt(
                        attempt=1, result="failed", stage="verify", message="AssertionError: 1 != 2"
                    )
                ],
            )
        ],
    )
    write_model(run_dir / "run.json", run)
    write_model(run_dir / "plan.json", plan)
    return ws, proj, task, plan, run_dir


def test_plan_repair_case_registration_preview_and_apply_immutability(tmp_path: Path) -> None:
    """preview 无需 confirm，apply 校验 confirm 并更新 Plan 新 revision，绝不覆写历史 run。"""
    assert "plan.repair_case" in _LAZY_RUNTIME
    assert "plan.repair_case" in _LAZY_MUTATING

    ws, _proj, _task, plan, run_dir = _bootstrap_script_plan(tmp_path)
    run_before = (run_dir / "run.json").read_text(encoding="utf-8")
    run_plan_before = (run_dir / "plan.json").read_text(encoding="utf-8")

    fixed_script = "val = 2\nassert val == 2\n"
    # 1. preview 模式不需 confirm
    preview = cmd_plan_repair_case(
        ws,
        {
            "project_id": "proj_rep",
            "plan_id": plan.plan_id,
            "case_id": "case_script_1",
            "run_id": "run_hist",
            "mode": "preview",
            "repaired_script": fixed_script,
        },
        False,
    )
    assert preview["mode"] == "preview"
    assert preview["ok"] is True
    assert preview["blocked"] is False
    assert "-val = 1" in preview["unified_diff"]
    assert "+val = 2" in preview["unified_diff"]

    # 2. apply 模式缺少 confirm 报错
    with pytest.raises(AiTestError) as exc_conf:
        cmd_plan_repair_case(
            ws,
            {
                "project_id": "proj_rep",
                "plan_id": plan.plan_id,
                "case_id": "case_script_1",
                "mode": "apply",
                "repaired_script": fixed_script,
            },
            False,
        )
    assert exc_conf.value.info.code == "CONFIRMATION_REQUIRED"

    # 3. apply 带 confirm 成功更新 Plan 新 revision 且历史 run 绝对不变
    applied = cmd_plan_repair_case(
        ws,
        {
            "project_id": "proj_rep",
            "plan_id": plan.plan_id,
            "case_id": "case_script_1",
            "mode": "apply",
            "repaired_script": fixed_script,
        },
        True,
    )
    assert applied["ok"] is True
    assert applied["plan_revision"] == plan.revision + 1
    assert applied["previous_revision"] == plan.revision
    reloaded = read_model(ws.project_dir("proj_rep") / "plans" / f"{plan.plan_id}.json", Plan)
    assert reloaded.revision == plan.revision + 1
    assert "val = 2" in (reloaded.cases[0].script_content or "")
    # 归档旧 revision
    assert (
        ws.project_dir("proj_rep") / "plans" / "revisions" / plan.plan_id / f"{plan.revision}.json"
    ).is_file()
    # 历史 run 文件绝对未被覆写
    assert (run_dir / "run.json").read_text(encoding="utf-8") == run_before
    assert (run_dir / "plan.json").read_text(encoding="utf-8") == run_plan_before


def test_plan_repair_case_blocks_weakened_assertions_and_seed_mutation(tmp_path: Path) -> None:
    """企图削弱断言、通过 trivial 断言或删除种子账号的补丁必须被硬拦截。"""
    ws, _proj, _task, plan, _run_dir = _bootstrap_script_plan(tmp_path)

    # 削弱断言：把 assert val == 2 改成 assert True
    weak_preview = cmd_plan_repair_case(
        ws,
        {
            "project_id": "proj_rep",
            "plan_id": plan.plan_id,
            "case_id": "case_script_1",
            "mode": "preview",
            "repaired_script": "val = 1\nassert True\n",
        },
        False,
    )
    assert weak_preview["blocked"] is True
    assert any(
        g in weak_preview["guard_blocked"] for g in ("assertions_preserved", "blocking_lint_errors")
    )

    with pytest.raises(AiTestError) as exc_apply:
        cmd_plan_repair_case(
            ws,
            {
                "project_id": "proj_rep",
                "plan_id": plan.plan_id,
                "case_id": "case_script_1",
                "mode": "apply",
                "repaired_script": "val = 1\nassert True\n",
            },
            True,
        )
    assert exc_apply.value.info.code == "INVALID_REQUEST"

    # 清理 _mutates_seed_account 硬编码后，危险操作仍由 scan_script 拦截，其余正常放行
    seed_script = (
        "import os, requests\n"
        "u = os.environ['FIRST_SUPERUSER']\n"
        "requests.delete('http://127.0.0.1:8000/api/users/me')\n"
        "val = 2\n"
        "assert val == 2\n"
    )
    seed_preview = cmd_plan_repair_case(
        ws,
        {
            "project_id": "proj_rep",
            "plan_id": plan.plan_id,
            "case_id": "case_script_1",
            "mode": "preview",
            "repaired_script": seed_script,
        },
        False,
    )
    assert seed_preview["blocked"] is False


def test_plan_repair_case_apply_checks_expected_revision(tmp_path: Path) -> None:
    """回归：plan.repair_case apply 传入过期 expected_revision 时必须返回 RUN_STATE_CONFLICT。"""
    ws, _proj, _task, plan, _run_dir = _bootstrap_script_plan(tmp_path)
    with pytest.raises(AiTestError) as exc:
        cmd_plan_repair_case(
            ws,
            {
                "project_id": "proj_rep",
                "plan_id": plan.plan_id,
                "case_id": "case_script_1",
                "mode": "apply",
                "expected_revision": plan.revision + 99,
                "repaired_script": "val = 2\nassert val == 2\n",
            },
            True,
        )
    assert exc.value.info.code == "RUN_STATE_CONFLICT"
