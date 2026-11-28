"""第 1 步单测：模型 F01-F09 旧记录兼容、input_hash 缓存与脱敏、AI 治理开关及模板单向分层。"""

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
from aitest.errors import AiTestError
from aitest.model_config import build_client, model_settings
from aitest.models import Case, Plan, Project, Report, Run, RunCase
from aitest.storage.files import write_json
from aitest.storage.workspace import Workspace


class _CountingServer(ThreadingHTTPServer):
    daemon_threads = True


@contextmanager
def _serve_counting(
    content_factory: Any,
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    received: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            body_obj = json.loads(raw_body.decode("utf-8"))
            received.append(body_obj)
            content, usage = content_factory(len(received), body_obj)
            payload = json.dumps(
                {
                    "choices": [{"message": {"content": content}}],
                    "usage": usage,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = _CountingServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_legacy_json_deserializes_with_f01_to_f09_defaults() -> None:
    """不带 F01-F09 字段的历史 JSON 仍能零报错反序列化且保持 schema_version=1。"""
    legacy_case = {
        "case_id": "case_old",
        "layer": "L2",
        "expected": "返回 200",
    }
    case = Case.model_validate(legacy_case)
    assert case.generation is None
    assert case.assertion_strength is None

    legacy_plan = {
        "schema_version": 1,
        "plan_id": "plan_old",
        "task_id": "task_old",
        "scope": {"preset": "selected", "acceptance_item_ids": ["ai_1"]},
        "cases": [legacy_case],
    }
    plan = Plan.model_validate(legacy_plan)
    assert plan.schema_version == 1
    assert plan.capabilities is None
    assert plan.recipes == []
    assert plan.coverage_ledger == []
    assert plan.ai_reviews == []

    legacy_run_case = {"case_id": "case_old", "result": "failed"}
    rc = RunCase.model_validate(legacy_run_case)
    assert rc.script_repair is None

    legacy_run = {
        "schema_version": 1,
        "run_id": "run_old",
        "plan_id": "plan_old",
        "project_id": "proj_old",
        "scope": {"preset": "selected", "acceptance_item_ids": ["ai_1"]},
        "cases": [legacy_run_case],
    }
    run = Run.model_validate(legacy_run)
    assert run.schema_version == 1
    assert run.ai_hypotheses == []

    legacy_report = {
        "schema_version": 1,
        "report_id": "rep_old",
        "run_id": "run_old",
        "outcome": "passed",
    }
    report = Report.model_validate(legacy_report)
    assert report.schema_version == 1
    assert report.capabilities is None
    assert report.coverage_ledger == []
    assert report.source_summary == {}
    assert report.assertion_strength == {}


def test_model_input_hash_cache_hit_and_secret_redaction(tmp_path: Path) -> None:
    """相同 input_hash 命中内存与磁盘缓存，且磁盘缓存绝不保存凭据正文。"""
    secret = "sk-very-secret-token-987654"
    cache_dir = tmp_path / "cache"

    def factory(_n: int, _body: dict[str, Any]) -> tuple[str, dict[str, int]]:
        return (
            json.dumps({"note": f"leaked {secret} ok"}, ensure_ascii=False),
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

    with _serve_counting(factory) as (base_url, received):
        client1 = ModelClient(
            base_url,
            "deepseek-chat",
            secret,
            timeout_seconds=5,
            secrets=[secret],
            cache_dir=cache_dir,
        )
        first = client1.complete_json("sys", f"user {secret}")
        second = client1.complete_json("sys", f"user {secret}")
        assert len(received) == 1
        assert client1.cache_stats()["hits"] == 1
        assert second == first

        # 新客户端复用磁盘缓存，不发新请求
        client2 = ModelClient(
            base_url,
            "deepseek-chat",
            secret,
            timeout_seconds=5,
            secrets=[secret],
            cache_dir=cache_dir,
        )
        third = client2.complete_json("sys", f"user {secret}")
        assert len(received) == 1
        assert client2.cache_stats()["hits"] == 1
        assert third["note"] != f"leaked {secret} ok"
        assert secret not in json.dumps(third, ensure_ascii=False)

    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1
    raw_disk = cache_files[0].read_text(encoding="utf-8")
    assert secret not in raw_disk


def test_model_cache_can_be_disabled_for_test_isolation(tmp_path: Path) -> None:
    """enable_cache=False 或环境变量关闭缓存时每次均真实请求。"""

    def factory(n: int, _body: dict[str, Any]) -> tuple[str, dict[str, int]]:
        return (
            json.dumps({"call": n}),
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    with _serve_counting(factory) as (base_url, received):
        client = ModelClient(
            base_url,
            "deepseek-chat",
            "sk-key",
            timeout_seconds=5,
            cache_dir=tmp_path / "cache",
            enable_cache=False,
        )
        r1 = client.complete_json("sys", "same-user")
        r2 = client.complete_json("sys", "same-user")
        assert len(received) == 2
        assert r1["call"] == 1
        assert r2["call"] == 2


def test_model_tier_routing_and_task_token_budget_and_ai_mode_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证 model_tier=auto 分级路由、单任务 token 预算降级与 model.ai.mode=off 开关。"""

    def factory(_n: int, body: dict[str, Any]) -> tuple[str, dict[str, int]]:
        return (
            json.dumps({"targets": []}),
            {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        )

    with _serve_counting(factory) as (base_url, received):
        client = ModelClient(
            base_url,
            "deepseek-chat",
            "sk-key",
            timeout_seconds=5,
            enable_cache=False,
            model_tier="auto",
            fast_model="deepseek-fast",
            strong_model="deepseek-strong",
            task_token_budget=50,
        )
        assert client.resolve_model_name("suggest_targets") == "deepseek-fast"
        assert client.resolve_model_name("generate_cases") == "deepseek-strong"
        client.complete_json("sys", "next", task_kind="suggest_targets")
        assert received[0]["model"] == "deepseek-fast"
        assert client.is_budget_exceeded() is True
        with pytest.raises(AiTestError) as exc:
            client.complete_json("sys", "next", task_kind="generate_cases")
        assert exc.value.info.code == "TOKEN_BUDGET_EXCEEDED"

    # mode=off 直接拒调
    off_client = ModelClient(
        "http://127.0.0.1:1",
        "deepseek-chat",
        "sk-key",
        ai_mode="off",
    )
    with pytest.raises(AiTestError) as exc_off:
        off_client.complete_json("sys", "user")
    assert exc_off.value.info.code == "MODEL_DISABLED"

    # 配置层 model.ai.mode=off 使 build_client 返回 None
    ws = Workspace(tmp_path / "ws")
    repo = tmp_path / "repo"
    repo.mkdir()
    proj = Project(project_id="proj_ai", name="p", root=str(repo))
    ws.save_project(proj)
    write_json(
        ws.project_dir("proj_ai") / "project.config.json",
        {"schema_version": 1, "model": {"ai": {"mode": "off", "model_tier": "auto"}}},
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("AITEST_MODEL_KEY", "sk-env-key")
    assert model_settings(ws, "proj_ai")["ai"]["mode"] == "off"
    assert build_client(ws, "proj_ai") is None
