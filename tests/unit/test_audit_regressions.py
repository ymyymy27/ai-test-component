"""本次深度审计修复项的专项回归单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aitest.commands import cmd_project_add
from aitest.core.evidence import save_evidence
from aitest.core.issues import _excerpt, draft_failed_issues
from aitest.models import Attempt, Case, Plan, Run, RunCase, Scope
from aitest.runners.http_runner import run_http_sequence
from aitest.runtime_commands import _dedupe_script_names
from aitest.security import is_secret_field, redact
from aitest.storage.files import read_json, write_json, write_model
from aitest.storage.workspace import Workspace, iso_now
from aitest.worker import _write_validated_script


def _create_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Workspace, str]:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)
    monkeypatch.delenv("AITEST_GITHUB_TOKEN", raising=False)
    ws = Workspace(tmp_path / "ws")
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    record = cmd_project_add(ws, {"root": str(root), "name": "audit-regression"}, True)
    return ws, str(record["project_id"])


def test_draft_failed_issues_handles_l1_case_without_acceptance_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L1 检查用例没有验收项映射（case_item_ids 为空）时，不应因下标越界崩溃。"""
    ws, project_id = _create_project(tmp_path, monkeypatch)
    run_dir = ws.project_dir(project_id) / "runs" / "run_l1_fail"
    run_dir.mkdir(parents=True)
    l1_case = Case(
        case_id="case_l1_build",
        acceptance_item_id="",
        acceptance_item_ids=[],
        layer="L1",
        title="L1 构建检查",
        steps=["执行构建命令"],
        expected="构建退出码为 0",
        verify_method="command:npm run build",
    )
    plan = Plan(
        plan_id="plan_l1",
        task_id="task_l1",
        scope=Scope(preset="all_applicable"),
        cases=[l1_case],
    )
    run = Run(
        run_id="run_l1_fail",
        plan_id="plan_l1",
        project_id=project_id,
        scope=Scope(preset="all_applicable"),
        status="completed",
        started_at=iso_now(),
        cases=[
            RunCase(
                case_id="case_l1_build",
                result="failed",
                attempts=[Attempt(attempt=1, result="failed", message="build failed")],
            )
        ],
    )
    write_model(run_dir / "plan.json", plan)
    write_model(run_dir / "run.json", run)

    issue_ids = draft_failed_issues(ws, project_id, run_dir)
    assert len(issue_ids) == 1


def test_excerpt_blocks_path_traversal_and_redacts_in_run_evidence(tmp_path: Path) -> None:
    """问题摘要读取证据片段必须限制在 run_dir 内，并对内容进行凭据脱敏。"""
    run_dir = tmp_path / "runs" / "run_1"
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP_SECRET_VALUE", encoding="utf-8")
    inside = evidence_dir / "error.log"
    inside.write_text("Authorization: Bearer sk-test1234567890abcdef failed", encoding="utf-8")

    assert _excerpt(run_dir, "../secret.txt", []) == ""
    excerpt = _excerpt(run_dir, "evidence/error.log", [])
    assert "sk-test1234567890abcdef" not in excerpt
    assert "***" in excerpt


def test_dedupe_script_names_keeps_unique_filenames_and_syncs_method() -> None:
    """同名脚本用例应自动去重 script_name 并同步更新 script: verify_method。"""
    c1 = Case(
        case_id="case_a",
        layer="L2",
        expected="A",
        verify_method="script:check.py",
        script_name="check.py",
    )
    c2 = Case(
        case_id="case_b",
        layer="L2",
        expected="B",
        verify_method="script:check.py",
        script_name="check.py",
    )
    _dedupe_script_names([c1, c2])
    assert c1.script_name == "check.py"
    assert c2.script_name == "check_case_b.py"
    assert c2.verify_method == "script:check_case_b.py"


def test_write_validated_script_enforces_assertions_seed_guard_and_index(tmp_path: Path) -> None:
    """脚本修复写入必须拦截无断言、危险命令和破坏种子超管的脚本，并同步 index.json。"""
    generated_dir = tmp_path / "generated"
    generated_dir.mkdir(parents=True)
    write_json(generated_dir / "index.json", {"files": []})
    case = Case(case_id="c1", layer="L2", expected="ok", verify_method="script:case_c1.py")

    assert (
        _write_validated_script(
            generated_dir, "case_c1.py", "# hdr\n", "print('no assert')", case
        )
        is None
    )
    assert (
        _write_validated_script(
            generated_dir, "case_c1.py", "# hdr\n", "import os\nos.system('id')\nassert True", case
        )
        is None
    )
    seed_attack = "first_superuser\nclient.delete('/api/users/me')\nassert True"
    assert (
        _write_validated_script(generated_dir, "case_c1.py", "# hdr\n", seed_attack, case)
        is None
    )

    valid = _write_validated_script(generated_dir, "case_c1.py", "# hdr\n", "assert 1 == 1\n", case)
    assert valid == "# hdr\nassert 1 == 1\n"
    assert (generated_dir / "case_c1.py").is_file()
    index = read_json(generated_dir / "index.json")
    assert len(index["files"]) == 1
    assert index["files"][0]["name"] == "case_c1.py"
    assert index["files"][0]["model"] == "repair"


def test_http_runner_rejects_4xx_by_default_and_evaluates_status_plus_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP runner 应默认判 4xx 失败，且支持同时断言 status 与 JSON 响应体。"""
    next_response: tuple[int, bytes] = (404, b'{"detail":"not found"}')

    def fake_send(_request: Any, _timeout: float, *, follow_redirects: bool) -> tuple[int, bytes]:
        assert isinstance(follow_redirects, bool)
        return next_response

    monkeypatch.setattr("aitest.runners.http_runner._send", fake_send)

    default_404 = run_http_sequence([{"method": "GET", "url": "http://127.0.0.1:8000/items"}])
    assert default_404.status == "failed"

    next_response = (400, json.dumps({"error": "bad_request"}).encode("utf-8"))
    compound_spec = [
        {
            "method": "POST",
            "url": "http://127.0.0.1:8000/items",
            "expect": [
                {"path": "status", "operator": "equals", "value": 400},
                {"path": "error", "operator": "equals", "value": "bad_request"},
            ],
        }
    ]
    compound_pass = run_http_sequence(compound_spec)
    assert compound_pass.status == "passed"

    next_response = (400, json.dumps({"error": "different_reason"}).encode("utf-8"))
    compound_fail = run_http_sequence(compound_spec)
    assert compound_fail.status == "failed"


def test_security_redaction_and_field_classification() -> None:
    """字段敏感识别不误杀 key_model/fields.key，且脱敏包含带驱动后缀的数据库 URI。"""
    assert is_secret_field("key_model") is False
    assert is_secret_field("fields.key") is False
    assert is_secret_field("model_key") is True
    assert is_secret_field("api_key") is True

    masked = redact("dsn=postgresql+psycopg://admin:s3cr3t_pw@db.internal:5432/prod", [])
    assert "s3cr3t_pw" not in masked
    assert "admin" not in masked
    assert "***" in masked


def test_save_evidence_truncated_marker_records_original_size(tmp_path: Path) -> None:
    """传入 original_size 时，.truncated 标记应记录截断前真实字节数。"""
    run_dir = tmp_path / "run_trunc"
    ref = save_evidence(
        run_dir,
        "big.log",
        b"0123456789abcdefghij",
        result="failed",
        file_cap_bytes=10,
        original_size=12345,
    )
    assert ref == "evidence/big.log"
    marker = (run_dir / "evidence" / "big.log.truncated").read_text(encoding="utf-8")
    assert "12345" in marker
