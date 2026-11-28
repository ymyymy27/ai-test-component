"""第 4 步单测：AI 工程治理 Golden Set 录制回放评测（离线 100% schema/结论词/证据门禁）。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from aitest.adapters.model import ModelClient
from aitest.runtime.plan_validation import merge_and_filter_reviews, review_text_conclusion_free


class _ReplayServer(ThreadingHTTPServer):
    daemon_threads = True


@contextmanager
def _replay_golden(responses: list[dict[str, Any]]) -> Iterator[str]:
    idx = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            pos = min(idx["n"], len(responses) - 1)
            idx["n"] += 1
            content = json.dumps(responses[pos], ensure_ascii=False)
            payload = json.dumps(
                {
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
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

    server = _ReplayServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_golden_set_replay_filters_conclusion_words_and_requires_evidence() -> None:
    """Golden Set 离线回放：schema 合法率 100%、结论词过滤率 100%、无证据项 100% 剔除。"""
    golden_review = {
        "reviews": [
            {
                "review_id": "bad_conclusion",
                "kind": "blind_conflict",
                "severity": "high",
                "statement": "全部通过，建议上线，没有问题",
                "evidence": ["app.py:10"],
                "patch_cases": [],
            },
            {
                "review_id": "bad_no_evidence",
                "kind": "permission_gap",
                "severity": "high",
                "statement": "疑似缺少管理接口越权校验",
                "evidence": [],
                "patch_cases": [],
            },
            {
                "review_id": "good_fact",
                "kind": "business_enhancement",
                "severity": "medium",
                "statement": "POST /api/orders 存在重复提交幂等边界尚未覆盖",
                "evidence": ["routes/orders.py:24"],
                "patch_cases": [],
            },
        ]
    }
    golden_judge = {
        "hypotheses": [
            {
                "hypothesis": "删除接口未校验所有权导致越权删除成功",
                "category": "product_defect",
                "evidence": ["status=204 expected=403"],
                "confidence": "high",
                "next_check": "核查 delete_order 中的 tenant_id 过滤条件",
            }
        ],
        "rationale": "响应状态码为 204，与预期 403 不一致。",
    }

    with _replay_golden([golden_review, golden_judge]) as base_url:
        client = ModelClient(
            base_url, "deepseek-chat", "sk-golden", timeout_seconds=5, enable_cache=False
        )
        raw_reviews = client.review_plan({"task_title": "订单测试"})
        filtered = merge_and_filter_reviews([], raw_reviews["reviews"], [])
        assert len(filtered) == 1
        assert filtered[0]["review_id"] == "good_fact"
        assert all(review_text_conclusion_free(r["statement"]) for r in filtered)
        assert all(bool(r["evidence"]) for r in filtered)

        judged = client.judge_failure({"case_id": "c_1", "failure_message": "403 != 204"})
        assert "verdict" not in judged
        assert len(judged["hypotheses"]) == 1
        assert judged["hypotheses"][0]["evidence"] == ["status=204 expected=403"]
