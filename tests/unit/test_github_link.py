"""Tests for the read-only GitHub and link import adapters."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from aitest.adapters.github import read_github
from aitest.adapters.link import import_link
from aitest.errors import AiTestError

_GH_CALL = Callable[..., subprocess.CompletedProcess[str]]


def _completed(
    args: list[str], *, code: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, code, stdout=stdout, stderr=stderr)


def _github_runner(
    responses: Mapping[str, str],
    *,
    missing: bool = False,
    failures: frozenset[str] = frozenset(),
) -> tuple[_GH_CALL, list[tuple[list[str], dict[str, Any]]]]:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((list(args), dict(kwargs)))
        if missing:
            raise FileNotFoundError("gh")
        endpoint = args[-1]
        if endpoint in failures:
            return _completed(args, code=1, stderr="authentication required")
        if endpoint not in responses:
            raise AssertionError(f"unexpected gh call: {args}")
        return _completed(args, stdout=responses[endpoint])

    return run, calls


def _github_responses() -> dict[str, str]:
    return {
        "repos/o/r": json.dumps(
            {"full_name": "o/r", "default_branch": "main", "private": True}
        ),
        "repos/o/r/commits/main": json.dumps(
            {
                "sha": "abc123",
                "commit": {"message": "should not leak"},
                "author": {"login": "should-not-leak"},
            }
        ),
        "repos/o/r/pulls?state=all": json.dumps(
            [
                {
                    "number": 12,
                    "title": "Add link import",
                    "state": "open",
                    "html_url": "https://github.com/o/r/pull/12",
                    "head": {"ref": "feature/link"},
                    "base": {"ref": "main"},
                    "user": {"login": "alice"},
                    "merged_at": None,
                    "updated_at": "2026-11-16T10:00:00Z",
                    "body": "secret body",
                }
            ]
        ),
        "repos/o/r/commits/abc123/check-runs": json.dumps(
            [
                {
                    "check_runs": [
                        {
                            "name": "ci",
                            "status": "completed",
                            "conclusion": "success",
                            "details_url": "https://github.com/o/r/actions/1",
                            "output": {"text": "secret output"},
                        }
                    ]
                }
            ]
        ),
        "repos/o/r/deployments?ref=main": json.dumps(
            [
                {
                    "id": 7,
                    "environment": "staging",
                    "ref": "main",
                    "sha": "abc123",
                    "created_at": "2026-11-16T09:00:00Z",
                    "creator": {"login": "alice", "email": "secret@example.com"},
                    "payload": {"token": "secret"},
                }
            ]
        ),
        "user": json.dumps({"login": "zhangsan", "email": "secret@example.com"}),
        "status": "github.com\n  Logged in to github.com account zhangsan (keyring)\n",
    }


def test_read_github_returns_whitelisted_change_record() -> None:
    runner, calls = _github_runner(_github_responses())

    data = read_github("o/r", "main", runner=runner)

    assert data["repo"] == "o/r"
    assert data["branch"] == "main"
    assert data["commit"] == "abc123"
    assert data["account"] == "zhangsan"
    assert data["host"] == "github.com"
    assert isinstance(data["fetched_at"], str) and data["fetched_at"]
    assert data["prs"] == [
        {
            "number": 12,
            "title": "Add link import",
            "state": "open",
            "url": "https://github.com/o/r/pull/12",
            "head": "feature/link",
            "base": "main",
            "author": "alice",
            "merged": False,
            "updated_at": "2026-11-16T10:00:00Z",
        }
    ]
    assert data["checks"] == [
        {
            "name": "ci",
            "status": "completed",
            "conclusion": "success",
            "started_at": "",
            "completed_at": "",
            "url": "https://github.com/o/r/actions/1",
        }
    ]
    assert data["deployment"] == {
        "id": 7,
        "environment": "staging",
        "ref": "main",
        "sha": "abc123",
        "created_at": "2026-11-16T09:00:00Z",
        "creator": "alice",
    }
    for leaked in ("secret body", "secret output", "secret@example.com", "should not leak"):
        assert leaked not in json.dumps(data, ensure_ascii=False)
    assert "payload" not in data["deployment"]

    api_calls = [args for args, _ in calls if args[:2] == ["gh", "api"]]
    assert api_calls
    assert all("api" in args for args in api_calls)
    for args in api_calls:
        for write_flag in ("--method", "-X", "-f", "-F", "--field", "--raw-field", "--input"):
            assert write_flag not in args
    assert any(args[-1].endswith("/abc123/check-runs") for args in api_calls)
    for _, kwargs in calls:
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        assert kwargs.get("shell") is not True


def test_read_github_missing_gh_is_unavailable() -> None:
    runner, _ = _github_runner({}, missing=True)

    with pytest.raises(AiTestError) as exc:
        read_github("o/r", "main", runner=runner)

    assert exc.value.info.code == "GITHUB_UNAVAILABLE"


def test_read_github_non_zero_exit_is_unavailable() -> None:
    runner, _ = _github_runner(
        _github_responses(), failures=frozenset({"repos/o/r"})
    )

    with pytest.raises(AiTestError) as exc:
        read_github("o/r", "main", runner=runner)

    assert exc.value.info.code == "GITHUB_UNAVAILABLE"
    assert "authentication required" not in exc.value.info.message


def test_read_github_account_probe_failure_is_not_blocking() -> None:
    runner, _ = _github_runner(
        _github_responses(), failures=frozenset({"user", "status"})
    )

    data = read_github("o/r", "main", runner=runner)

    assert data["account"] == ""
    assert data["host"] == ""
    assert data["commit"] == "abc123"


def _link(
    request_id: str,
    *,
    log_paths: list[Path],
    log_format: str = "jsonl",
    log_fields: dict[str, str] | None = None,
    db_query: str | None = None,
    runner: _GH_CALL = subprocess.run,
    max_lines: int = 5000,
) -> dict[str, Any]:
    return import_link(
        "run-1",
        request_id,
        log_paths=log_paths,
        log_format=log_format,
        log_fields=log_fields,
        db_query=db_query,
        runner=runner,
        max_lines=max_lines,
    )


_JSONL_FIELDS = {
    "request_id": "trace_id",
    "module": "logger",
    "status": "level",
    "message": "msg",
}


def _jsonl_lines() -> list[str]:
    return [
        json.dumps(
            {
                "trace_id": "req-1",
                "logger": "api",
                "level": "error",
                "msg": "third",
                "timestamp": "2026-01-01T00:00:03Z",
            }
        ),
        json.dumps({"trace_id": "req-2", "logger": "api", "level": "info", "msg": "other"}),
        json.dumps(
            {
                "trace_id": "req-1",
                "logger": "api",
                "level": "error",
                "msg": "first",
                "timestamp": "2026-01-01T00:00:01Z",
            }
        ),
        json.dumps(
            {
                "trace_id": "req-1",
                "logger": "db",
                "level": "info",
                "msg": "second",
                "timestamp": "2026-01-01T00:00:02Z",
            }
        ),
    ]


def _db_runner(
    *,
    code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> tuple[_GH_CALL, list[tuple[Any, dict[str, Any]]]]:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def run(command: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, dict(kwargs)))
        return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)

    return run, calls


def test_import_link_jsonl_matches_and_sorts_by_time(tmp_path: Path) -> None:
    log_path = tmp_path / "app.jsonl"
    log_path.write_text("\n".join(_jsonl_lines()), encoding="utf-8")
    before = log_path.read_bytes()
    result = _link("req-1", log_paths=[log_path], log_fields=_JSONL_FIELDS)

    assert [node["message"] for node in result["nodes"]] == ["first", "second", "third"]
    assert [node["type"] for node in result["nodes"]] == ["log", "log", "log"]
    assert [node["status"] for node in result["nodes"]] == ["error", "info", "error"]
    assert all(node["node_id"].startswith("log:") for node in result["nodes"])
    assert all(node["duration"] is None for node in result["nodes"])
    assert all(str(log_path) in node["evidence_ref"] for node in result["nodes"])
    assert result["assertions"] == []
    assert result["gaps"] == [{"node_id": "db", "reason": "未配置数据库查询"}]
    assert log_path.read_bytes() == before


def test_import_link_regex_uses_named_groups(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "\n".join(
            [
                "2026-01-01T00:00:01Z ERROR req-1 api boom",
                "2026-01-01T00:00:02Z INFO req-2 api other",
            ]
        ),
        encoding="utf-8",
    )
    pattern = (
        r"(?P<timestamp>\S+) (?P<status>\w+) (?P<request_id>req-\d+) "
        r"(?P<module>\w+) (?P<message>.*)"
    )

    result = _link(
        "req-1",
        log_paths=[log_path],
        log_format="regex",
        log_fields={"pattern": pattern},
    )

    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["message"] == "boom"
    assert result["nodes"][0]["status"] == "ERROR"
    assert result["nodes"][0]["evidence_ref"] == f"{log_path}:1"
    assert result["gaps"] == [{"node_id": "db", "reason": "未配置数据库查询"}]


def test_import_link_no_match_writes_log_gap(tmp_path: Path) -> None:
    log_path = tmp_path / "app.jsonl"
    log_path.write_text(_jsonl_lines()[1], encoding="utf-8")

    result = _link("req-404", log_paths=[log_path])

    assert result["nodes"] == []
    assert {"node_id": "log", "reason": "未找到该请求的日志"} in result["gaps"]
    assert {"node_id": "db", "reason": "未配置数据库查询"} in result["gaps"]


def test_import_link_missing_request_id_writes_gaps_without_running_db(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "app.jsonl"
    log_path.write_text(_jsonl_lines()[0], encoding="utf-8")
    runner, calls = _db_runner(stdout="unused")

    result = _link("", log_paths=[log_path], db_query="read req {request_id}", runner=runner)

    assert result["nodes"] == []
    assert result["assertions"] == []
    assert [gap["node_id"] for gap in result["gaps"]] == ["request_id", "log", "db"]
    assert calls == []


def test_import_link_db_failure_writes_gap(tmp_path: Path) -> None:
    log_path = tmp_path / "app.jsonl"
    log_path.write_text(_jsonl_lines()[0], encoding="utf-8")
    runner, calls = _db_runner(code=1, stderr="connection refused")

    result = _link(
        "req-1",
        log_paths=[log_path],
        log_fields=_JSONL_FIELDS,
        db_query="query --request {request_id}",
        runner=runner,
    )

    assert len(result["nodes"]) == 1
    assert {"node_id": "db", "reason": "数据库查询执行失败"} in result["gaps"]
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert "req-1" in command
    assert "{request_id}" not in command
    assert kwargs["timeout"] == 30
    assert kwargs["shell"] is True


def test_import_link_db_success_builds_nodes(tmp_path: Path) -> None:
    log_path = tmp_path / "app.jsonl"
    log_path.write_text(_jsonl_lines()[0], encoding="utf-8")
    runner, _ = _db_runner(
        stdout=json.dumps(
            [{"request_id": "req-1", "status": "ok", "message": "row found"}]
        )
    )

    result = _link(
        "req-1",
        log_paths=[log_path],
        log_fields=_JSONL_FIELDS,
        db_query="query --request {request_id}",
        runner=runner,
    )

    assert [node["type"] for node in result["nodes"]] == ["log", "db"]
    assert result["nodes"][-1]["message"] == "row found"
    assert result["nodes"][-1]["evidence_ref"] == "db:req-1:1"
    assert not [gap for gap in result["gaps"] if gap["node_id"] == "db"]


def test_import_link_db_empty_output_writes_gap(tmp_path: Path) -> None:
    log_path = tmp_path / "app.jsonl"
    log_path.write_text(_jsonl_lines()[0], encoding="utf-8")
    runner, _ = _db_runner(stdout=" \n")

    result = _link(
        "req-1",
        log_paths=[log_path],
        log_fields=_JSONL_FIELDS,
        db_query="query --request {request_id}",
        runner=runner,
    )

    assert {"node_id": "db", "reason": "未找到该请求的数据库结果"} in result["gaps"]


def test_import_link_truncates_matched_lines_and_adds_gap(tmp_path: Path) -> None:
    log_path = tmp_path / "app.jsonl"
    log_path.write_text("\n".join(_jsonl_lines()), encoding="utf-8")

    result = _link(
        "req-1", log_paths=[log_path], log_fields=_JSONL_FIELDS, max_lines=2
    )

    assert len(result["nodes"]) == 2
    truncation = [gap for gap in result["gaps"] if "截断" in gap["reason"]]
    assert len(truncation) == 1
    assert truncation[0]["node_id"] == "log"


def test_import_link_missing_log_paths_writes_gap() -> None:
    result = _link("req-1", log_paths=[])

    assert result["nodes"] == []
    assert result["gaps"] == [
        {"node_id": "log", "reason": "未配置日志路径"},
        {"node_id": "db", "reason": "未配置数据库查询"},
    ]


def test_import_link_regex_without_pattern_writes_gap(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text("req-1 boom", encoding="utf-8")

    result = _link("req-1", log_paths=[log_path], log_format="regex")

    assert result["nodes"] == []
    assert {"node_id": "log", "reason": "未配置日志正则"} in result["gaps"]


def test_import_link_rejects_unknown_log_format(tmp_path: Path) -> None:
    with pytest.raises(AiTestError) as exc:
        _link("req-1", log_paths=[tmp_path / "app.log"], log_format="csv")

    assert exc.value.info.code == "LINK_CONFIG_MISSING"
