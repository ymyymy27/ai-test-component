"""第 4 步单测：无锁只读 plan.review 双盲冲突检出、权限补漏、去重签名与 plan.update 合并。"""

from __future__ import annotations

from pathlib import Path

import pytest

from aitest.cli import _LAZY_MUTATING, _LAZY_RUNTIME
from aitest.commands import MUTATING
from aitest.models import AcceptanceItem, Plan, Project, Task
from aitest.runtime.plan_commands import cmd_plan_generate, cmd_plan_review, cmd_plan_update
from aitest.runtime.plan_validation import (
    dedupe_patch_cases,
    infer_case_signature,
    merge_and_filter_reviews,
)
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace


def test_plan_review_is_read_only_lockless_and_detects_weak_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """plan.review 仅入 _LAZY_RUNTIME 不入 _LAZY_MUTATING，只读提示弱断言并缓存。"""
    assert "plan.review" in _LAZY_RUNTIME
    assert "plan.review" not in _LAZY_MUTATING
    assert "plan.review" not in MUTATING

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)
    monkeypatch.setenv("AITEST_AI_MODE", "off")

    ws = Workspace(tmp_path / "ws")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname="conflict-app"\ndependencies=["fastapi"]\n', encoding="utf-8"
    )
    (repo / "app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
        '@app.post("/api/items")\n'
        "def create_item(name: str):\n"
        "    return {'id': '1', 'name': name}\n",
        encoding="utf-8",
    )
    proj = Project(project_id="proj_rev", name="conflict", root=str(repo))
    ws.save_project(proj)
    task = Task(
        task_id="task_rev",
        project_id="proj_rev",
        title="弱断言只读评审测试",
        acceptance_items=[
            AcceptanceItem(item_id="ai_create", text="创建条目提交 name 字段并返回"),
        ],
    )
    write_model(ws.project_dir("proj_rev") / "tasks" / "task_rev.json", task)

    gen = cmd_plan_generate(
        ws,
        {
            "project_id": "proj_rev",
            "task_id": "task_rev",
            "scope": {"preset": "all_applicable"},
            "cases": [
                {
                    "case_id": "c_weak",
                    "acceptance_item_id": "ai_create",
                    "layer": "L2",
                    "title": "创建条目",
                    "preconditions": ["服务就绪"],
                    "expected": "创建条目提交 name 字段并返回",
                    "expectation_source": "创建条目提交 name 字段并返回",
                    "verify_method": 'http:[{"method":"POST","url":"http://127.0.0.1:8000/api/items","json":{"name":"a"},"expect":[{"path":"status","operator":"equals","value":200}]}]',
                },
            ],
        },
        False,
    )
    plan_id = gen["plan_id"]
    plan_before = (ws.project_dir("proj_rev") / "plans" / f"{plan_id}.json").read_text(
        encoding="utf-8"
    )

    review1 = cmd_plan_review(ws, {"project_id": "proj_rev", "plan_id": plan_id}, False)
    assert review1["cached"] is False
    kinds = {r["kind"] for r in review1["reviews"]}
    assert kinds == {"weak_assertion"}
    assert all(r["patch_cases"] == [] for r in review1["reviews"])
    assert (ws.project_dir("proj_rev") / "plans" / f"{plan_id}.json").read_text(
        encoding="utf-8"
    ) == plan_before

    review2 = cmd_plan_review(ws, {"project_id": "proj_rev", "plan_id": plan_id}, False)
    assert review2["cached"] is True
    assert review2["input_hash"] == review1["input_hash"]

    plan_obj = read_model(ws.project_dir("proj_rev") / "plans" / f"{plan_id}.json", Plan)
    updated = cmd_plan_update(
        ws,
        {
            "project_id": "proj_rev",
            "plan_id": plan_id,
            "expected_revision": plan_obj.revision,
            "patch": {"ai_reviews": review1["reviews"]},
        },
        False,
    )
    assert updated["revision"] == plan_obj.revision + 1
    reloaded = read_model(ws.project_dir("proj_rev") / "plans" / f"{plan_id}.json", Plan)
    assert len(reloaded.ai_reviews) == len(review1["reviews"])


def test_dedupe_signature_guarantees_zero_exact_duplicates() -> None:
    """去重签名确保 exact duplicate = 0。"""
    c1 = {
        "acceptance_item_id": "ai_1",
        "title": "校验未认证 401",
        "expected": "返回 401",
        "verify_method": 'http:[{"method":"GET","url":"http://127.0.0.1:8000/api/orders/12","expect":[{"path":"status","operator":"equals","value":401}]}]',
    }
    c2_dup = {
        "acceptance_item_id": "ai_1",
        "title": "另一个标题但同样目标",
        "expected": "返回 401",
        "verify_method": 'http:[{"method":"GET","url":"http://127.0.0.1:8000/api/orders/99","expect":[{"path":"status","operator":"equals","value":401}]}]',
    }
    c3_distinct = {
        "acceptance_item_id": "ai_1",
        "title": "越权 403",
        "expected": "返回 403",
        "verify_method": 'http:[{"method":"DELETE","url":"http://127.0.0.1:8000/api/orders/12","expect":[{"path":"status","operator":"equals","value":403}]}]',
    }
    assert infer_case_signature(c1) == infer_case_signature(c2_dup)
    filtered = dedupe_patch_cases([c1], [c2_dup, c3_distinct, c3_distinct])
    assert len(filtered) == 1
    assert filtered[0]["title"] == "越权 403"


def test_merge_and_plan_review_cases_carry_valid_case_ids_into_plan_update(
    tmp_path: Path,
) -> None:
    """回归：plan.review 返回的补丁即使缺 case_id，合并进 plan.update 也必须过校验。"""
    from aitest.core.scope import task_contract
    from aitest.models import Case, Scope, new_id

    ws = Workspace(tmp_path / "ws")
    root = tmp_path / "app_patch_id"
    root.mkdir()
    (root / "app.py").write_text(
        "@app.delete('/api/admin/users')\ndef delete_user():\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    proj = Project(project_id="proj_pid", name="pid", root=str(root))
    ws.save_project(proj)
    task = Task(
        task_id="t_pid",
        project_id="proj_pid",
        title="权限验证",
        acceptance_items=[AcceptanceItem(item_id="ai_1", text="未认证访问删除接口应返回 401")],
    )
    plan_id = new_id("plan")
    plan_obj = Plan(
        plan_id=plan_id,
        task_id="t_pid",
        plan_no=1,
        task_revision=1,
        task_contract=task_contract(task),
        scope=Scope(preset="all_applicable", acceptance_item_ids=["ai_1"]),
        applicable_count=1,
        cases=[
            Case(
                case_id="c_ok",
                acceptance_item_id="ai_1",
                layer="L2",
                title="正常访问列表",
                preconditions=["服务已启动"],
                expected="200",
                expectation_source="未认证访问删除接口应返回 401",
                expectation_verified=True,
                verify_method='http:[{"method":"GET","url":"http://127.0.0.1:8000/api/items","stage":"assert","expect":[{"path":"status","operator":"equals","value":200},{"path":"body","operator":"exists","value":true}]}]',
            )
        ],
        item_texts={"ai_1": "未认证访问删除接口应返回 401"},
        status="published",
        revision=1,
    )
    write_model(ws.project_dir("proj_pid") / "tasks" / "t_pid.json", task)
    write_model(ws.project_dir("proj_pid") / "plans" / f"{plan_id}.json", plan_obj)

    model_raw = [
        {
            "review_id": "rev_model_no_id",
            "kind": "permission_gap",
            "severity": "high",
            "statement": "缺少越权 403 检查用例",
            "evidence": ["app.py:1"],
            "patch_cases": [
                {
                    "acceptance_item_id": "ai_1",
                    "layer": "L2",
                    "title": "无 case_id 模型补丁",
                    "preconditions": ["服务已启动"],
                    "steps": ["无权限删除"],
                    "expected": "403",
                    "expectation_source": "未认证访问删除接口应返回 401",
                    "expectation_verified": True,
                    "verify_method": 'http:[{"method":"DELETE","url":"http://127.0.0.1:8000/api/admin/users","stage":"assert","expect":[{"path":"status","operator":"equals","value":403},{"path":"body","operator":"exists","value":true}]}]',
                }
            ],
        }
    ]
    base_cases = [c.model_dump(mode="json") for c in plan_obj.cases]
    merged = merge_and_filter_reviews([], model_raw, base_cases)
    assert len(merged) == 1
    assert merged[0]["patch_cases"][0]["case_id"].startswith("case_rev_")

    merged_cases = base_cases + merged[0]["patch_cases"]
    updated = cmd_plan_update(
        ws,
        {
            "project_id": "proj_pid",
            "plan_id": plan_id,
            "expected_revision": plan_obj.revision,
            "patch": {
                "cases": merged_cases,
                "ai_reviews": merged,
            },
        },
        False,
    )
    assert updated["revision"] == 2
