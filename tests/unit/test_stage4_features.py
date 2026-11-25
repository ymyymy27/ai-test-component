"""阶段四新增 CLI 可选参数与实用能力回归测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aitest.cli import main
from aitest.commands import REGISTRY
from aitest.models import Case, Plan, Project, Run, RunCase, Scope
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace


def _cli(
    tmp_path: Path, capsys: Any, command: str, payload: dict[str, Any], *, confirm: bool = False
) -> tuple[int, dict[str, Any]]:
    req = tmp_path / "req.json"
    req.write_text(
        json.dumps({"command": command, "request_id": f"req_{command}", "payload": payload}),
        encoding="utf-8",
    )
    argv = [command, "--workspace", str(tmp_path / "ws"), "--input", str(req)]
    if confirm:
        argv.append("--confirm")
    code = main(argv)
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return code, json.loads(out)


def test_registry_still_exact_60_commands() -> None:
    assert len(REGISTRY) == 60


def test_project_config_detect_is_readonly_and_extracts_draft(
    tmp_path: Path, capsys: Any
) -> None:
    proj_root = tmp_path / "sample_app"
    proj_root.mkdir(parents=True)
    (proj_root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["pytest"]\n',
        encoding="utf-8",
    )
    (proj_root / "tests").mkdir()
    (proj_root / "tests" / "test_basic.py").write_text(
        "def test_ok():\n    assert 1 == 1\n",
        encoding="utf-8",
    )
    ws = Workspace(tmp_path / "ws")
    ws.init()
    ws.save_project(Project(project_id="proj_det", name="det", root=str(proj_root)))

    code, res = _cli(
        tmp_path, capsys, "project.config", {"project_id": "proj_det", "action": "detect"}
    )
    assert code == 0 and res["ok"] is True
    draft = res["data"]["draft"]
    assert draft["runners"]["test"]
    assert "test_basic.py" in draft["l1"]["smoke_command"]
    assert not (ws.project_dir("proj_det") / "project.config.json").exists()


def test_evidence_preview_run_tail_and_manual_batch(
    tmp_path: Path, capsys: Any
) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.init()
    proj_root = tmp_path / "repo"
    proj_root.mkdir()
    ws.save_project(Project(project_id="proj_s4", name="s4", root=str(proj_root)))
    run_dir = ws.project_dir("proj_s4") / "runs" / "run_s4_1"
    (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
    log_file = run_dir / "evidence" / "case1-0.log"
    log_file.write_text("Bearer tokensecret123456\nline two error", encoding="utf-8")
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    (run_dir / "evidence" / "snap.png").write_bytes(png_bytes)
    (run_dir / "worker-stderr.log").write_text("worker line tail\n", encoding="utf-8")

    scope = Scope(preset="selected", acceptance_item_ids=["ai_1"])
    plan = Plan(
        plan_id="plan_s4",
        task_id="task_s4",
        scope=scope,
        applicable_count=1,
        cases=[
            Case(
                case_id="case_m",
                acceptance_item_id="ai_1",
                layer="L3",
                title="人工检查",
                expected="展示结果",
                expectation_source="展示结果",
                preconditions=["打开页面"],
                steps=["步骤1", "步骤2"],
                verify_method="manual",
            )
        ],
    )
    run = Run(
        run_id="run_s4_1",
        plan_id="plan_s4",
        project_id="proj_s4",
        scope=scope,
        status="completed",
        evidence_refs=["evidence/case1-0.log", "evidence/snap.png"],
        cases=[RunCase(case_id="case_m", result="manual_pending")],
    )
    write_model(run_dir / "plan.json", plan)
    write_model(run_dir / "run.json", run)

    # 1) evidence.list 文本与图片安全预览
    code, res_txt = _cli(
        tmp_path,
        capsys,
        "evidence.list",
        {
            "project_id": "proj_s4",
            "run_id": "run_s4_1",
            "include_hash": False,
            "preview_ref": "evidence/case1-0.log",
        },
    )
    assert code == 0 and res_txt["data"]["preview"]["kind"] == "text"
    assert "tokensecret123456" not in res_txt["data"]["preview"]["text"]
    assert "line two error" in res_txt["data"]["preview"]["text"]

    code, res_img = _cli(
        tmp_path,
        capsys,
        "evidence.list",
        {
            "project_id": "proj_s4",
            "run_id": "run_s4_1",
            "include_hash": False,
            "preview_ref": "evidence/snap.png",
        },
    )
    assert code == 0 and res_img["data"]["preview"]["kind"] == "image"
    assert res_img["data"]["preview"]["data_url"].startswith("data:image/png;base64,")

    code, res_bad = _cli(
        tmp_path,
        capsys,
        "evidence.list",
        {
            "project_id": "proj_s4",
            "run_id": "run_s4_1",
            "include_hash": False,
            "preview_ref": "../run.json",
        },
    )
    assert code == 0 and res_bad["data"]["preview"]["kind"] == "missing"

    # 2) run.status include_tail
    code, res_status = _cli(
        tmp_path,
        capsys,
        "run.status",
        {"project_id": "proj_s4", "run_id": "run_s4_1", "include_tail": True},
    )
    assert code == 0
    names = [item["name"] for item in res_status["data"]["log_tail"]]
    assert "worker-stderr.log" in names
    assert "evidence/case1-0.log" in names

    # 3) run.manual 批量 steps
    code, res_manual = _cli(
        tmp_path,
        capsys,
        "run.manual",
        {
            "project_id": "proj_s4",
            "run_id": "run_s4_1",
            "case_id": "case_m",
            "steps": [
                {"step": "步骤1", "result": "passed", "note": "ok1"},
                {"step": "步骤2", "result": "passed", "note": "ok2"},
            ],
        },
    )
    assert code == 0 and res_manual["data"]["result"] == "passed"
    assert len(res_manual["data"]["steps"]) == 2
    saved_run = read_model(run_dir / "run.json", Run)
    assert saved_run.cases[0].result == "passed"
