"""Contract tests for the CLI command set, envelopes, enums and error codes.

The single source of truth for external behaviour is
``docs/项目文档/一期/03-设计/03-CLI与数据契约.md``; the single source of truth for
what is actually dispatchable is ``aitest.commands.REGISTRY`` (extended by the
runtime and integration modules). A command or enum that exists in only one of
them is a contract drift, not a documentation nit.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import typing
from pathlib import Path
from typing import Any

import pytest

import aitest.cli  # noqa: F401 - importing registers runtime/integration commands
from aitest.cli import main
from aitest.commands import MUTATING, REGISTRY
from aitest.models import (
    CaseResult,
    IssueDisposition,
    IssueStatus,
    MockKind,
    Plan,
    RunStatus,
    Verdict,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/项目文档/一期/03-设计/03-CLI与数据契约.md"
PLAN = ROOT / "docs/修改日志/ymy/2026-11-16-轻量Trae插件重构计划.md"
SRC = ROOT / "src/aitest"

Capsys = pytest.CaptureFixture[str]


def _contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the body of a level-2/level-3 section, stopping at the next heading."""

    start = text.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    body = text[start + len(heading) :]
    stop = re.search(rf"^#{{1,{level}}} ", body, re.M)
    return body if stop is None else body[: stop.start()]


def _documented_commands() -> set[str]:
    section = _section(_contract_text(), "## 4 命令")
    commands: set[str] = set()
    for heading in re.findall(r"^#### (.+)$", section, re.M):
        for token in re.findall(r"`([^`]+)`", heading):
            commands.add(token.strip())
    return commands


def _documented_enum(heading: str, label: str = "") -> list[str]:
    body = _section(_contract_text(), heading)
    for line in body.splitlines():
        if label and not line.strip().startswith(label):
            continue
        match = re.search(r"`([^`]+)`", line)
        if match:
            return [item.strip() for item in match.group(1).split("/")]
    raise AssertionError(f"no enum line found under {heading} {label}")


def _literal_values(annotation: object) -> list[str]:
    if typing.get_origin(annotation) is typing.Literal:
        return list(typing.get_args(annotation))  # type: ignore[arg-type]
    raise AssertionError(f"not a Literal annotation: {annotation!r}")


def _documented_error_codes() -> set[str]:
    section = _section(_contract_text(), "## 2 错误码")
    return set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|", section, re.M))


def _error_codes_in_code() -> dict[str, list[str]]:
    """Collect literal codes passed to ``AiTestError``/``not_found`` and friends."""

    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in {"AiTestError", "not_found"} or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.setdefault(first.value, []).append(
                    f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                )
    return found


def _call(tmp_path: Path, capsys: Capsys, argv: list[str], request: dict[str, Any]) -> Any:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    code = main([*argv, "--workspace", str(tmp_path / "ws"), "--input", str(request_file)])
    payload = json.loads(capsys.readouterr().out.strip())
    return code, payload


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("AITEST_MODEL_KEY", raising=False)


def test_registry_matches_documented_commands() -> None:
    documented = _documented_commands()
    assert documented, "no commands parsed from the contract document"
    assert set(REGISTRY) == documented, (
        f"only in REGISTRY: {sorted(set(REGISTRY) - documented)}\n"
        f"only in contract: {sorted(documented - set(REGISTRY))}"
    )


def _plan_commands() -> set[str]:
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("### 7.3 命令清单")
    end = text.index("### 7.4 执行模型")
    commands: set[str] = set()
    for line in text[start:end].splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|")[1].strip().strip("`")
        names = first_cell.split("/")
        base = names[0]
        prefix = base.rsplit(".", 1)[0] if "." in base else ""
        commands.add(base)
        for name in names[1:]:
            commands.add(f"{prefix}.{name}" if prefix else name)
    return commands


def test_plan_command_list_matches_registry() -> None:
    planned = _plan_commands()
    assert planned, "no commands parsed from the plan"
    assert set(REGISTRY) == planned, (
        f"only in REGISTRY: {sorted(set(REGISTRY) - planned)}\n"
        f"only in plan: {sorted(planned - set(REGISTRY))}"
    )


def test_every_registry_handler_accepts_the_three_argument_form() -> None:
    wrong = {}
    for command, handler in REGISTRY.items():
        parameters = list(inspect.signature(handler).parameters.values())
        if len(parameters) != 3:
            wrong[command] = [parameter.name for parameter in parameters]
    assert wrong == {}


def test_mutating_commands_are_a_subset_of_the_registry() -> None:
    assert set(MUTATING) <= set(REGISTRY)
    assert {"project.add", "plan.generate", "run.start"} <= set(MUTATING)


def test_success_envelope_echoes_request_id_and_wraps_data(
    tmp_path: Path, capsys: Capsys
) -> None:
    code, response = _call(
        tmp_path,
        capsys,
        ["doctor"],
        {"command": "doctor", "request_id": "req-1", "payload": {}},
    )
    assert code == 0
    assert set(response) == {"ok", "request_id", "data"}
    assert response["ok"] is True
    assert response["request_id"] == "req-1"
    assert isinstance(response["data"], dict)


def test_failure_envelope_carries_code_message_and_next_step(
    tmp_path: Path, capsys: Capsys
) -> None:
    code, response = _call(
        tmp_path,
        capsys,
        ["project.show"],
        {"command": "project.show", "request_id": "req-2", "payload": {"project_id": "missing"}},
    )
    assert code == 1
    assert response["ok"] is False
    assert response["request_id"] == "req-2"
    error = response["error"]
    assert set(error) == {"code", "message", "next_step"}
    assert error["code"] == "PROJECT_NOT_FOUND"
    assert all(isinstance(value, str) and value for value in error.values())


def test_envelope_mismatch_is_rejected(tmp_path: Path, capsys: Capsys) -> None:
    code, response = _call(
        tmp_path,
        capsys,
        ["doctor"],
        {"command": "project.list", "request_id": "req-3", "payload": {}},
    )
    assert code == 2
    assert response["error"]["code"] == "USAGE"


def test_documented_run_status_enum_matches_the_model() -> None:
    assert _documented_enum("### 3.1 运行状态") == _literal_values(RunStatus)


def test_documented_verdict_enum_matches_the_model() -> None:
    assert _documented_enum("### 3.2 业务结论") == _literal_values(Verdict)


def test_documented_case_result_enum_matches_the_model() -> None:
    assert _documented_enum("### 3.3 用例结果") == _literal_values(CaseResult)


def test_documented_mock_enum_matches_the_model() -> None:
    assert _documented_enum("### 3.4 Mock 真实性") == _literal_values(MockKind)


def test_documented_issue_status_and_disposition_match_the_model() -> None:
    assert _documented_enum("### 3.5 问题状态与处置", "状态") == _literal_values(IssueStatus)
    assert _documented_enum("### 3.5 问题状态与处置", "处置") == _literal_values(IssueDisposition)


def test_documented_plan_status_matches_the_model() -> None:
    annotation = Plan.model_fields["status"].annotation
    assert _documented_enum("### 3.6 计划状态") == _literal_values(annotation)


def test_contract_error_table_covers_every_code_used_in_code() -> None:
    used = _error_codes_in_code()
    documented = _documented_error_codes()
    missing = {code: locations for code, locations in used.items() if code not in documented}
    assert missing == {}, "documented error table is missing: " + json.dumps(
        missing, ensure_ascii=False, indent=2
    )
