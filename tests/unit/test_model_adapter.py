"""Tests for the DeepSeek-compatible model adapter."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from aitest.adapters.model import (
    _SYSTEM_CASES,
    _SYSTEM_FREEZE_EXPECTATIONS,
    _SYSTEM_MAP,
    _SYSTEM_MAP_TESTS,
    ModelClient,
)
from aitest.errors import AiTestError

_API_KEY = "sk-test-secret-key"


class _TestServer(ThreadingHTTPServer):
    daemon_threads = True


@contextmanager
def _serve(content: str, *, status: int = 200) -> Iterator[str]:
    payload = json.dumps(
        {"choices": [{"message": {"content": content}}]},
        ensure_ascii=False,
    ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(status)
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


def _client(base_url: str) -> ModelClient:
    return ModelClient(base_url, "deepseek-chat", _API_KEY, timeout_seconds=5)


def _assert_error(exc: pytest.ExceptionInfo[AiTestError], code: str) -> None:
    assert exc.value.info.code == code
    assert _API_KEY not in str(exc.value)
    assert _API_KEY not in exc.value.info.message


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_complete_json_returns_object() -> None:
    with _serve(json.dumps({"ok": True})) as base_url:
        result = _client(base_url).complete_json("system", "user")
    assert result == {"ok": True}


def test_complete_json_rejects_non_json_content() -> None:
    with _serve("not json at all") as base_url, pytest.raises(AiTestError) as exc:
        _client(base_url).complete_json("system", "user")
    _assert_error(exc, "MODEL_OUTPUT_INVALID")


def test_complete_json_rejects_non_object_json() -> None:
    with _serve("[]") as base_url, pytest.raises(AiTestError) as exc:
        _client(base_url).complete_json("system", "user")
    _assert_error(exc, "MODEL_OUTPUT_INVALID")


def test_network_error_maps_to_unavailable() -> None:
    client = _client(f"http://127.0.0.1:{_unused_port()}")
    with pytest.raises(AiTestError) as exc:
        client.complete_json("system", "user")
    _assert_error(exc, "MODEL_UNAVAILABLE")


def test_auth_failure_maps_to_unavailable_without_leaking_key() -> None:
    with _serve("unauthorized", status=401) as base_url, pytest.raises(AiTestError) as exc:
        _client(base_url).complete_json("system", "user")
    _assert_error(exc, "MODEL_UNAVAILABLE")


def test_suggest_targets_returns_validated_items() -> None:
    content = json.dumps(
        {
            "targets": [
                {"title": "登录", "reason": "核心路径", "extra": "ignored"},
                {"title": "支付", "reason": "高风险"},
                {"title": "退款", "reason": "分支较多"},
            ]
        },
        ensure_ascii=False,
    )
    with _serve(content) as base_url:
        targets = _client(base_url).suggest_targets("订单系统")
    assert targets == [
        {
            "title": "登录",
            "reason": "核心路径",
            "scope_preview": {"business_functions": [], "acceptance_items_estimate": 0},
            "uncertainties": [],
        },
        {
            "title": "支付",
            "reason": "高风险",
            "scope_preview": {"business_functions": [], "acceptance_items_estimate": 0},
            "uncertainties": [],
        },
        {
            "title": "退款",
            "reason": "分支较多",
            "scope_preview": {"business_functions": [], "acceptance_items_estimate": 0},
            "uncertainties": [],
        },
    ]


def test_suggest_targets_allows_empty_result() -> None:
    with _serve('{"targets": []}') as base_url:
        targets = _client(base_url).suggest_targets("没有信号")
    assert targets == []


def test_suggest_targets_rejects_wrong_count() -> None:
    content = json.dumps(
        {"targets": [{"title": "A", "reason": "a"}, {"title": "B", "reason": "b"}]}
    )
    with _serve(content) as base_url, pytest.raises(AiTestError) as exc:
        _client(base_url).suggest_targets("订单系统")
    _assert_error(exc, "MODEL_OUTPUT_INVALID")


def test_project_map_returns_validated_business_functions() -> None:
    content = json.dumps(
        {
            "business_functions": [
                {
                    "name": "下单",
                    "modules": ["order", "stock"],
                    "entry_points": ["POST /orders"],
                    "test_commands": ["pytest tests/order"],
                    "testable_items": ["下单后订单出现"],
                    "evidence": ["README.md", "routes.py:42"],
                    "confidence": "high",
                },
                {
                    "name": "支付",
                    "modules": [],
                    "confidence": "unknown",
                },
            ],
            "unknowns": [{"area": "支付回调", "reason": "没有入口"}],
        },
        ensure_ascii=False,
    )
    with _serve(content) as base_url:
        result = _client(base_url).project_map("项目事实")
    assert result["business_functions"][0]["name"] == "下单"
    assert result["business_functions"][0]["confidence"] == "high"
    assert result["business_functions"][1]["confidence"] == "low"
    assert result["unknowns"] == [{"area": "支付回调", "reason": "没有入口"}]


def test_project_map_rejects_missing_function_list() -> None:
    with _serve('{"unknowns": []}') as base_url, pytest.raises(AiTestError) as exc:
        _client(base_url).project_map("项目事实")
    _assert_error(exc, "MODEL_OUTPUT_INVALID")


def _case(item_id: str) -> dict[str, Any]:
    return {
        "acceptance_item_id": item_id,
        "layer": "L2",
        "expected": "返回成功",
        "expectation_source": "需求规格",
        "preconditions": ["服务已启动"],
        "verify_method": "接口调用",
        "depends_on": ["fixture"],
    }


def test_generate_cases_requires_full_coverage() -> None:
    selected = [
        {"acceptance_item_id": "AC-1"},
        {"acceptance_item_id": "AC-2"},
    ]
    content = json.dumps({"cases": [_case("AC-1"), _case("AC-2")]})
    with _serve(content) as base_url:
        cases = _client(base_url).generate_cases({"title": "任务"}, selected)
    assert [case["acceptance_item_id"] for case in cases] == ["AC-1", "AC-2"]


def test_generate_cases_rejects_missing_acceptance_item() -> None:
    selected = [{"acceptance_item_id": "AC-1"}, {"acceptance_item_id": "AC-2"}]
    content = json.dumps({"cases": [_case("AC-1")]})
    with _serve(content) as base_url, pytest.raises(AiTestError) as exc:
        _client(base_url).generate_cases({"title": "任务"}, selected)
    _assert_error(exc, "MODEL_OUTPUT_INVALID")


def test_analyze_failure_returns_validated_sections() -> None:
    content = json.dumps(
        {
            "observed": "接口返回 500",
            "evidence_refs": ["evidence/log.txt"],
            "candidate_causes": ["下游超时"],
            "unverified": ["下游是否真的超时"],
            "next_steps": ["查看下游日志"],
        },
        ensure_ascii=False,
    )
    with _serve(content) as base_url:
        analysis = _client(base_url).analyze_failure({"run_id": "R1"}, ["log"])
    assert analysis == {
        "observed": "接口返回 500",
        "evidence_refs": ["evidence/log.txt"],
        "candidate_causes": [{"cause": "下游超时", "confidence": "unknown"}],
        "unverified": ["下游是否真的超时"],
        "next_steps": ["查看下游日志"],
    }


def test_analyze_failure_keeps_cause_objects_with_confidence() -> None:
    content = json.dumps(
        {
            "observed": "接口返回 500",
            "evidence_refs": ["evidence/log.txt"],
            "candidate_causes": [
                {"cause": "下游超时", "confidence": "medium"},
                {"cause": "连接池耗尽"},
            ],
            "unverified": ["下游是否真的超时"],
            "next_steps": ["查看下游日志"],
        },
        ensure_ascii=False,
    )
    with _serve(content) as base_url:
        analysis = _client(base_url).analyze_failure({"run_id": "R1"}, ["log"])
    assert analysis["candidate_causes"] == [
        {"cause": "下游超时", "confidence": "medium"},
        {"cause": "连接池耗尽", "confidence": "unknown"},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "observed": "接口返回 500",
            "evidence_refs": ["evidence/log.txt"],
            "candidate_causes": ["下游超时"],
            "next_steps": ["查看下游日志"],
        },
        {
            "observed": "接口返回 500",
            "evidence_refs": ["evidence/log.txt"],
            "candidate_causes": ["下游超时"],
            "unverified": [],
            "next_steps": ["查看下游日志"],
        },
    ],
)
def test_analyze_failure_requires_unverified(payload: dict[str, Any]) -> None:
    with (
        _serve(json.dumps(payload, ensure_ascii=False)) as base_url,
        pytest.raises(AiTestError) as exc,
    ):
        _client(base_url).analyze_failure({"run_id": "R1"}, ["log"])
    _assert_error(exc, "MODEL_OUTPUT_INVALID")


def test_draft_delivery_defaults_missing_fields_to_empty_arrays() -> None:
    content = json.dumps({"done": ["下单接口"], "attempted": ["忽略"]}, ensure_ascii=False)
    with _serve(content) as base_url:
        draft = _client(base_url).draft_delivery({"task": "下单验证"})
    assert draft["done"] == ["下单接口"]
    assert "attempted" not in draft
    for field in (
        "not_done",
        "changed_modules",
        "interface_changes",
        "run_commands",
        "test_data",
        "dependencies",
        "mocks",
        "self_test_evidence",
    ):
        assert draft[field] == []


def test_draft_delivery_unwraps_nested_object_and_drops_blanks() -> None:
    content = json.dumps(
        {
            "delivery": {
                "done": ["下单", " ", 3],
                "run_commands": [],
                "test_data": ["测试库"],
            }
        },
        ensure_ascii=False,
    )
    with _serve(content) as base_url:
        draft = _client(base_url).draft_delivery({})
    assert draft["done"] == ["下单"]
    assert draft["run_commands"] == []
    assert draft["test_data"] == ["测试库"]


def test_suggest_modules_normalizes_dependencies() -> None:
    content = json.dumps(
        {
            "modules": [
                {
                    "name": "order",
                    "responsibility": "下单",
                    "deps": [{"to": "stock", "note": "扣减库存"}],
                    "extra": "ignored",
                },
                {"name": "stock", "owner": "张三"},
            ]
        },
        ensure_ascii=False,
    )
    with _serve(content) as base_url:
        modules = _client(base_url).suggest_modules({"project": {"name": "订单"}})
    assert modules == [
        {
            "name": "order",
            "responsibility": "下单",
            "owner": "",
            "deps": [{"to": "stock", "type": "calls", "note": "扣减库存"}],
        },
        {"name": "stock", "responsibility": "", "owner": "张三", "deps": []},
    ]


def test_suggest_modules_rejects_unknown_dependency_type() -> None:
    content = json.dumps(
        {"modules": [{"name": "order", "deps": [{"to": "stock", "type": "magic"}]}]}
    )
    with _serve(content) as base_url, pytest.raises(AiTestError) as exc:
        _client(base_url).suggest_modules({})
    _assert_error(exc, "MODEL_OUTPUT_INVALID")


def test_suggest_modules_rejects_duplicate_names() -> None:
    content = json.dumps({"modules": [{"name": "order"}, {"name": "order"}]})
    with _serve(content) as base_url, pytest.raises(AiTestError) as exc:
        _client(base_url).suggest_modules({})
    _assert_error(exc, "MODEL_OUTPUT_INVALID")


def test_prompts_preserve_explicit_rules_and_protect_seed_accounts() -> None:
    assert "逐条" in _SYSTEM_MAP
    assert "不合并" in _SYSTEM_MAP
    assert "种子账号" in _SYSTEM_CASES
    assert "body.status" in _SYSTEM_CASES
    assert "inputs" in _SYSTEM_MAP_TESTS
    assert "必须返回 null" in _SYSTEM_MAP_TESTS
    assert "先以合法身份确认目标资源存在" in _SYSTEM_CASES
    assert "只能验证当前 acceptance_item_id" in _SYSTEM_CASES
    assert "先确认目标资源存在" in _SYSTEM_FREEZE_EXPECTATIONS
