"""Behaviour tests for the lightweight runner layer."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aitest.runners.assertions import evaluate, resolve_path
from aitest.runners.command_runner import run_command
from aitest.runners.http_runner import run_http_sequence
from aitest.runners.junit import parse_junit

_JUNIT = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="sample">
    <testcase classname="tests.test_sample" name="test_ok" time="0.01"/>
    <testcase classname="tests.test_sample" name="test_failure" time="0.02">
      <failure message="assertion broke">trace</failure>
    </testcase>
    <testcase classname="tests.test_sample" name="test_error" time="0.03">
      <error message="boom" type="RuntimeError"/>
    </testcase>
    <testcase classname="tests.test_sample" name="test_skipped" time="0.0">
      <skipped message="not needed"/>
    </testcase>
  </testsuite>
</testsuites>
"""


def _python(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/ok":
            payload = {"data": {"id": 7, "name": "alpha"}, "items": [1, 2]}
        else:
            payload = {"data": {"id": 8, "name": "beta"}}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture()
def base_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = str(server.server_address[0])
    port = int(server.server_address[1])
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_run_command_success(tmp_path: Path) -> None:
    result = run_command(_python("print('ok')"), tmp_path, timeout_seconds=10)
    assert result.status == "passed"
    assert result.exit_code == 0
    assert b"ok" in result.stdout
    assert result.error == ""


def test_run_command_failure_keeps_exit_code(tmp_path: Path) -> None:
    result = run_command(_python("import sys; sys.exit(3)"), tmp_path, timeout_seconds=10)
    assert result.status == "failed"
    assert result.exit_code == 3


def test_run_command_timeout_is_blocked(tmp_path: Path) -> None:
    result = run_command(_python("import time; time.sleep(2)"), tmp_path, timeout_seconds=0.2)
    assert result.status == "blocked"
    assert "timed out" in result.error


def test_run_command_bad_cwd_is_error(tmp_path: Path) -> None:
    result = run_command(_python("print('never')"), tmp_path / "missing", timeout_seconds=10)
    assert result.status == "error"
    assert result.error


def test_parse_junit_statuses() -> None:
    results = parse_junit(_JUNIT)
    assert [item["name"] for item in results] == [
        "test_ok",
        "test_failure",
        "test_error",
        "test_skipped",
    ]
    assert [item["status"] for item in results] == ["passed", "failed", "error", "skipped"]
    assert results[0]["message"] is None
    assert results[1]["message"] == "assertion broke"
    assert results[3]["message"] == "not needed"
    assert results[1]["time"] == 0.02
    assert results[0]["classname"] == "tests.test_sample"


def test_evaluate_equals_contains_exists() -> None:
    data = {"data": {"id": 7, "tags": ["a", "b"], "name": None}}
    assert evaluate(resolve_path(data, "data.id"), "equals", 7)
    assert not evaluate(resolve_path(data, "data.id"), "equals", 8)
    assert evaluate(resolve_path(data, "data.tags"), "contains", "b")
    assert not evaluate(resolve_path(data, "data.tags"), "contains", "z")
    assert evaluate(resolve_path(data, "data.tags.1"), "equals", "b")
    assert evaluate(resolve_path(data, "data.id"), "exists", None)
    assert not evaluate(resolve_path(data, "data.name"), "exists", None)
    assert not evaluate(resolve_path(data, "data.missing"), "exists", None)


def test_run_http_sequence_passes(base_url: str) -> None:
    result = run_http_sequence(
        [
            {
                "method": "GET",
                "url": f"{base_url}/ok",
                "headers": {"Accept": "application/json"},
                "expect": [
                    {"path": "data.id", "operator": "equals", "value": 7},
                    {"path": "data.name", "operator": "contains", "value": "lph"},
                    {"path": "items.1", "operator": "equals", "value": 2},
                    {"path": "data", "operator": "exists", "value": None},
                ],
            }
        ],
        timeout_seconds=5,
    )
    assert result.status == "passed"
    assert len(result.assertions) == 4
    assert all(item["passed"] for item in result.assertions)
    assert json.loads(result.stdout)["data"]["id"] == 7
    assert result.error == ""


def test_run_http_sequence_failed_assertion(base_url: str) -> None:
    result = run_http_sequence(
        [
            {
                "method": "GET",
                "url": f"{base_url}/ok",
                "expect": [{"path": "data.id", "operator": "equals", "value": 8}],
            }
        ],
        timeout_seconds=5,
    )
    assert result.status == "failed"
    assert len(result.assertions) == 1
    assert result.assertions[0]["passed"] is False
    assert result.assertions[0]["actual"] == 7
    assert "assertion failed" in result.error
