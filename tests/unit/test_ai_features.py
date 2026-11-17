"""Tests for AI-assisted dependency judging and report summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aitest.ai_features import judge_mock, summarize_report


class FakeClient:
    """Model client stub that replays one response and records prompts."""

    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls.append((system, user))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _judged(
    root: Path,
    dependencies: list[str],
    response: dict[str, Any],
    *,
    evidence: list[str] | None = None,
    max_files: int = 40,
    max_depth: int = 3,
) -> tuple[list[dict[str, Any]], FakeClient]:
    client = FakeClient(response)
    result = judge_mock(
        root,
        dependencies,
        evidence or [],
        client=client,
        max_files=max_files,
        max_depth=max_depth,
    )
    return result, client


def _user_payload(client: FakeClient) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(client.calls[0][1])
    return payload


def test_judge_mock_returns_model_verdicts_in_input_order(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "import requests\n")
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "real",
                "reason": "直接调用真实网络",
                "evidence_refs": ["src/app.py"],
            },
            {
                "name": "payments",
                "kind": "mock",
                "reason": "测试替身",
                "evidence_refs": ["src/app.py"],
            },
        ]
    }
    result, _ = _judged(tmp_path, ["requests", "payments"], response)
    assert [item["name"] for item in result] == ["requests", "payments"]
    assert [item["kind"] for item in result] == ["real", "mock"]


def test_judge_mock_marks_missing_dependency_unknown(tmp_path: Path) -> None:
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "real",
                "reason": "真实调用",
                "evidence_refs": ["src/app.py"],
            }
        ]
    }
    result, _ = _judged(tmp_path, ["requests", "missing"], response)
    assert [item["kind"] for item in result] == ["real", "unknown"]
    assert result[1]["name"] == "missing"
    assert result[1]["reason"]


def test_judge_mock_degrades_invalid_kind_to_unknown(tmp_path: Path) -> None:
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "maybe",
                "reason": "无法确定",
                "evidence_refs": ["src/app.py"],
            }
        ]
    }
    result, _ = _judged(tmp_path, ["requests"], response)
    assert result[0]["kind"] == "unknown"
    assert result[0]["reason"] == "无法确定"


def test_judge_mock_degrades_missing_reason_to_unknown(tmp_path: Path) -> None:
    response = {
        "dependencies": [
            {"name": "requests", "kind": "real", "evidence_refs": ["src/app.py"]}
        ]
    }
    result, _ = _judged(tmp_path, ["requests"], response)
    assert result[0]["kind"] == "unknown"
    assert result[0]["reason"]


def test_judge_mock_degrades_empty_evidence_to_unknown(tmp_path: Path) -> None:
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "real",
                "reason": "看起来是真实依赖",
                "evidence_refs": [],
            }
        ]
    }
    result, _ = _judged(tmp_path, ["requests"], response)
    assert result[0]["kind"] == "unknown"
    assert result[0]["evidence_refs"] == []


def test_judge_mock_keeps_unknown_kind_without_evidence(tmp_path: Path) -> None:
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "unknown",
                "reason": "没有足够信号",
                "evidence_refs": [],
            }
        ]
    }
    result, _ = _judged(tmp_path, ["requests"], response)
    assert result[0]["kind"] == "unknown"
    assert result[0]["reason"] == "没有足够信号"


def test_judge_mock_degrades_all_on_model_error(tmp_path: Path) -> None:
    result, _ = _judged(
        tmp_path,
        ["requests", "payments"],
        RuntimeError("network down"),
    )
    assert [item["kind"] for item in result] == ["unknown", "unknown"]
    assert [item["name"] for item in result] == ["requests", "payments"]


def test_judge_mock_degrades_all_on_malformed_response(tmp_path: Path) -> None:
    result, _ = _judged(tmp_path, ["requests"], {"dependencies": "oops"})
    assert [item["kind"] for item in result] == ["unknown"]


def test_judge_mock_samples_files_without_skipped_directories(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "import requests\n")
    _write(tmp_path, "pyproject.toml", "[project]\n")
    _write(tmp_path, "node_modules/pkg/index.js", "SKIPPED_MARKER")
    _write(tmp_path, ".git/config", "SKIPPED_MARKER")
    _write(tmp_path, ".venv/lib/site.py", "SKIPPED_MARKER")
    _write(tmp_path, "dist/bundle.js", "SKIPPED_MARKER")
    _write(tmp_path, "build/out.py", "SKIPPED_MARKER")
    _write(tmp_path, "legacy/old.py", "SKIPPED_MARKER")

    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "real",
                "reason": "真实调用",
                "evidence_refs": ["src/app.py"],
            }
        ]
    }
    result, client = _judged(
        tmp_path,
        ["requests"],
        response,
        evidence=["src/app.py"],
        max_files=5,
    )
    assert result[0]["kind"] == "real"
    payload = _user_payload(client)
    paths = [item["path"] for item in payload["files"]]
    assert "src/app.py" in paths
    assert "pyproject.toml" in paths
    assert all("node_modules" not in path for path in paths)
    assert all(not path.startswith(".git/") for path in paths)
    assert all(not path.startswith("dist/") for path in paths)
    assert all(not path.startswith("build/") for path in paths)
    assert all(not path.startswith("legacy/") for path in paths)
    assert all("SKIPPED_MARKER" not in item["content"] for item in payload["files"])
    assert payload["dependencies"] == ["requests"]
    assert payload["evidence"] == ["src/app.py"]


def test_judge_mock_respects_max_files(tmp_path: Path) -> None:
    for index in range(10):
        _write(tmp_path, f"module_{index}.py", f"VALUE = {index}\n")
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "unknown",
                "reason": "没有足够信号",
                "evidence_refs": [],
            }
        ]
    }
    result, client = _judged(tmp_path, ["requests"], response, max_files=3)
    # Exceeding the read budget stops before the model and never guesses.
    assert client.calls == []
    assert result[0]["kind"] == "unknown"
    assert "达到读取上限" in result[0]["reason"]
    assert len(result[0]["read_files"]) == 3


def test_judge_mock_respects_max_depth(tmp_path: Path) -> None:
    _write(tmp_path, "root.py", "ROOT = 1\n")
    _write(tmp_path, "src/one.py", "ONE = 1\n")
    _write(tmp_path, "src/nested/two.py", "TWO = 2\n")
    response = {"dependencies": []}
    _, client = _judged(tmp_path, ["requests"], response, max_depth=1)
    paths = [item["path"] for item in _user_payload(client)["files"]]
    assert "root.py" in paths
    assert "src/one.py" in paths
    assert "src/nested/two.py" not in paths


def test_judge_mock_skips_model_call_for_empty_dependencies(tmp_path: Path) -> None:
    client = FakeClient({"dependencies": []})
    assert judge_mock(tmp_path, [], [], client=client) == []
    assert client.calls == []


def test_summarize_report_returns_text(tmp_path: Path) -> None:
    client = FakeClient({"summary": "运行了 2 条用例，其中 1 条因环境缺失中断。"})
    assert summarize_report({"runs": 2}, client=client) == (
        "运行了 2 条用例，其中 1 条因环境缺失中断。"
    )


@pytest.mark.parametrize(
    "word",
    ["通过", "不通过", "质量良好", "可以上线", "建议发布", "全部通过"],
)
def test_summarize_report_rejects_banned_words(word: str) -> None:
    client = FakeClient({"summary": f"本次测试{word}。"})
    assert summarize_report({"runs": 1}, client=client) is None


def test_summarize_report_returns_none_on_model_error() -> None:
    client = FakeClient(RuntimeError("network down"))
    assert summarize_report({"runs": 1}, client=client) is None


def test_summarize_report_returns_none_without_text() -> None:
    assert summarize_report({"runs": 1}, client=FakeClient({"other": "x"})) is None
    assert summarize_report({"runs": 1}, client=FakeClient({"summary": "  "})) is None


def test_judge_mock_caches_by_revision_and_file_digest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    workspace = tmp_path / "ws"
    _write(root, "src/app.py", "import requests\n")
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "real",
                "reason": "直接调用真实网络",
                "evidence_refs": ["src/app.py"],
            }
        ]
    }
    client = FakeClient(response)
    first = judge_mock(
        root,
        ["requests"],
        [],
        client=client,
        workspace=workspace,
        project_revision=1,
    )
    cache_files = list((workspace / ".cache" / "mock").glob("*.json"))
    assert len(cache_files) == 1
    assert json.loads(cache_files[0].read_text(encoding="utf-8")) == first

    second = judge_mock(
        root,
        ["requests"],
        [],
        client=client,
        workspace=workspace,
        project_revision=1,
    )
    assert second == first
    assert len(client.calls) == 1

    judge_mock(
        root,
        ["requests"],
        [],
        client=client,
        workspace=workspace,
        project_revision=2,
    )
    assert len(client.calls) == 2
    assert len(list((workspace / ".cache" / "mock").glob("*.json"))) == 2


def test_judge_mock_reports_read_budget_and_keeps_unknown(tmp_path: Path) -> None:
    for index in range(5):
        _write(tmp_path, f"module_{index}.py", f"VALUE = {index}\n")
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "unknown",
                "reason": "没有足够信号",
                "evidence_refs": [],
            }
        ]
    }
    result, client = _judged(tmp_path, ["requests"], response, max_files=2)

    assert result[0]["kind"] == "unknown"
    assert "达到读取上限" in result[0]["reason"]
    assert result[0]["limit_reached"] is True
    assert len(result[0]["read_files"]) == 2
    assert client.calls == []


def test_judge_mock_without_budget_does_not_flag_limit(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "import requests\n")
    response = {
        "dependencies": [
            {
                "name": "requests",
                "kind": "real",
                "reason": "真实调用",
                "evidence_refs": ["src/app.py"],
            }
        ]
    }
    result, _ = _judged(tmp_path, ["requests"], response, max_files=40)
    assert result[0]["limit_reached"] is False
    assert result[0]["read_files"] == ["src/app.py"]
