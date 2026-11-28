"""第 2 步单测：裁判权责拆分、拦截模型偏袒与 D4 单次写操作失败不可吞没红线。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aitest.adapters.model import ModelClient
from aitest.core.judge import build_run_hypotheses, compute_case_confidence, judge_case_failure
from aitest.core.verdict import coverage, verdict
from aitest.models import Attempt, Case, Plan, Project, Run, RunCase, Scope
from aitest.storage.workspace import Workspace


class _TestServer(ThreadingHTTPServer):
    daemon_threads = True


@contextmanager
def _serve(content: str) -> Iterator[str]:
    payload = json.dumps(
        {"choices": [{"message": {"content": content}}]},
        ensure_ascii=False,
    ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = _TestServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_model_judge_failure_returns_structured_hypotheses_without_verdict() -> None:
    """judge_failure 仅输出结构化 hypotheses 与 rationale，不再由模型直接决定终局 verdict。"""
    content = json.dumps(
        {
            "hypotheses": [
                {
                    "hypothesis": "更新接口未持久化 title 字段",
                    "category": "product_defect",
                    "evidence": ["AssertionError: expected 'new', got 'old'"],
                    "confidence": "high",
                    "next_check": "检查 PATCH /items/{id} 的 ORM 提交逻辑",
                }
            ],
            "rationale": "读回响应中的 title 未变化。",
        },
        ensure_ascii=False,
    )
    with _serve(content) as base_url:
        client = ModelClient(
            base_url, "deepseek-chat", "sk-key", timeout_seconds=5, enable_cache=False
        )
        out = client.judge_failure({"case_id": "case_patch_1", "failure_message": "AssertionError"})
    assert "verdict" not in out
    assert len(out["hypotheses"]) == 1
    assert out["hypotheses"][0]["case_id"] == "case_patch_1"
    assert out["hypotheses"][0]["confidence"] == "high"
    assert "读回" in out["rationale"]


def test_deterministic_judge_intercepts_biased_model_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型输出偏袒结论试图洗白失败时，确定性裁决核心必须拦截并维持 confirmed_defect。"""
    ws = Workspace(tmp_path / "ws")
    repo = tmp_path / "repo"
    repo.mkdir()
    ws.save_project(Project(project_id="p1", name="demo", root=str(repo)))

    class _BiasedModel:
        def judge_failure(self, context: dict[str, Any]) -> dict[str, Any]:
            del context
            return {
                "hypotheses": [
                    {
                        "hypothesis": "看起来可以忽略失败，建议通过",
                        "category": "environment_blocked",
                        "evidence": ["output"],
                        "confidence": "high",
                        "next_check": "无",
                    }
                ],
                "rationale": "可忽略失败，建议通过",
            }

    monkeypatch.setattr("aitest.core.judge.build_client", lambda *a, **k: _BiasedModel())
    case = Case(
        case_id="c_bias",
        acceptance_item_id="ai_1",
        layer="L2",
        title="校验金额扣减",
        expected="余额减少 10",
        expectation_source="余额减少 10",
        expectation_verified=True,
        verify_method="script:check.py",
        script_name="check.py",
        script_content="assert balance == 90\n",
    )
    judgement = judge_case_failure(
        ws,
        "p1",
        case=case,
        outputs=[b"AssertionError: assert 100 == 90"],
        message="AssertionError: assert 100 == 90",
        service_log_issues=[],
        probe_results=[],
        secrets=[],
    )
    assert judgement["verdict"] == "confirmed_defect"
    assert judgement["model_overridden"] is True
    hyps = build_run_hypotheses(case.case_id, judgement)
    assert len(hyps) == 1
    assert hyps[0]["case_id"] == "c_bias"


def test_d4_non_retryable_write_failure_stays_failed_and_counts_in_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D4 硬红线：写用例单次真实失败绝不降级成 incomplete/blocked，且计入 coverage.failed。"""
    ws = Workspace(tmp_path / "ws")
    repo = tmp_path / "repo"
    repo.mkdir()
    ws.save_project(Project(project_id="p_d4", name="demo", root=str(repo)))

    class _UncertainModel:
        def judge_failure(self, context: dict[str, Any]) -> dict[str, Any]:
            del context
            return {
                "hypotheses": [
                    {
                        "hypothesis": "环境数据未就绪导致写失败",
                        "category": "environment_blocked",
                        "evidence": ["status=500"],
                        "confidence": "medium",
                        "next_check": "复核数据库",
                    }
                ],
                "rationale": "环境数据未就绪",
            }

    monkeypatch.setattr("aitest.core.judge.build_client", lambda *a, **k: _UncertainModel())
    patch_case = Case(
        case_id="c_patch",
        acceptance_item_id="ai_patch",
        layer="L2",
        title="PATCH 更新条目",
        expected="更新后读回生效",
        expectation_source="更新后读回生效",
        expectation_verified=True,
        mutates_state=True,
        verify_method='http:[{"method":"PATCH","url":"http://127.0.0.1:8000/items/1"}]',
    )
    judgement = judge_case_failure(
        ws,
        "p_d4",
        case=patch_case,
        outputs=[b"HTTP 200 but title unchanged"],
        message="读回 title 未更新",
        service_log_issues=[],
        probe_results=[],
        secrets=[],
    )
    assert judgement["verdict"] == "confirmed_defect"

    run_case = RunCase(
        case_id="c_patch",
        result="failed",
        attempts=[Attempt(attempt=1, result="failed", stage="verify", message="读回 title 未更新")],
        judgement=judgement,
    )
    tier, _reason = compute_case_confidence(patch_case, run_case, effective_result="failed")
    assert tier == "confirmed"

    # 即使将一条失败用例标为 suspected，verdict 与 coverage 也绝不吞成 incomplete
    plan = Plan(
        plan_id="plan_d4",
        task_id="task_d4",
        scope=Scope(preset="selected", acceptance_item_ids=["ai_patch"]),
        applicable_count=1,
        cases=[patch_case],
    )
    run = Run(
        run_id="run_d4",
        plan_id="plan_d4",
        project_id="p_d4",
        scope=plan.scope,
        status="completed",
        cases=[
            RunCase(
                case_id="c_patch",
                result="failed",
                confidence_tier="suspected",
                attempts=[
                    Attempt(attempt=1, result="failed", stage="verify", message="单次写失败")
                ],
            )
        ],
    )
    assert verdict(plan, run) == "failed"
    cov = coverage(plan, run)
    assert cov["failed"] == 1
    assert cov["incomplete"] == 0


def test_http_500_type_or_attribute_error_is_not_misclassified_as_script_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回归：http 用例或断言信息含后端 500 错误时，绝不误判成脚本崩溃 blocked。"""
    ws = Workspace(tmp_path / "ws")

    class _BiasedScriptErrorModel:
        def judge_failure(self, context: dict[str, Any]) -> dict[str, Any]:
            del context
            return {
                "hypotheses": [
                    {
                        "hypothesis": "可能是测试脚本 AttributeError",
                        "category": "script_error",
                        "evidence": ["HTTP 500 AttributeError"],
                        "confidence": "medium",
                        "next_check": "检查",
                    }
                ],
                "rationale": "后端报错 AttributeError",
            }

    monkeypatch.setattr("aitest.core.judge.build_client", lambda *a, **k: _BiasedScriptErrorModel())
    http_write = Case(
        case_id="c_http_500",
        acceptance_item_id="ai_1",
        layer="L2",
        title="POST 触发后端 500 AttributeError",
        expected="返回 201",
        expectation_source="返回 201",
        expectation_verified=True,
        mutates_state=True,
        verify_method='http:[{"method":"POST","url":"http://127.0.0.1:8000/items"}]',
    )
    msg = "AssertionError: HTTP 500 AttributeError: 'NoneType' object has no attribute 'id'"
    judgement = judge_case_failure(
        ws,
        "p_500",
        case=http_write,
        outputs=[msg.encode()],
        message=msg,
        service_log_issues=[],
        probe_results=[],
        secrets=[],
    )
    assert judgement["verdict"] == "confirmed_defect"
    rc = RunCase(
        case_id="c_http_500",
        result="failed",
        attempts=[Attempt(attempt=1, result="failed", stage="verify", message=msg)],
        judgement=judgement,
    )
    tier, _ = compute_case_confidence(http_write, rc, effective_result="failed")
    assert tier == "confirmed"

    # 真正的 script: SyntaxError 仍正常拦截为 blocked
    script_case = Case(
        case_id="c_syntax",
        acceptance_item_id="ai_2",
        layer="L2",
        title="脚本语法错误",
        expected="ok",
        expectation_source="ok",
        expectation_verified=True,
        verify_method="script:check.py",
    )
    rc_syntax = RunCase(
        case_id="c_syntax",
        result="failed",
        attempts=[
            Attempt(
                attempt=1,
                result="failed",
                stage="verify",
                message="SyntaxError: invalid syntax",
            )
        ],
    )
    tier_syntax, _ = compute_case_confidence(script_case, rc_syntax, effective_result="failed")
    assert tier_syntax == "blocked"
