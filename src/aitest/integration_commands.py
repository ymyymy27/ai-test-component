"""GitHub and link commands, kept thin over the adapters."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from aitest.adapters.github import read_github
from aitest.adapters.link import import_link
from aitest.commands import MUTATING, REGISTRY, Payload, _project, _project_secrets, _require, _text
from aitest.core.link_location import locate_link
from aitest.errors import AiTestError, invalid_request
from aitest.model_config import load_project_config
from aitest.runners.assertions import evaluate
from aitest.security import redact_tree
from aitest.storage.files import check_schema_version, read_json, write_json
from aitest.storage.workspace import Workspace, iso_now


def _run_id(payload: Payload) -> str:
    run_id = _text({"run_id": _require(payload, "run_id")}, "run_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise invalid_request("run_id 不合法")
    return run_id


def cmd_github_read(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    raw_snapshot = payload.get("snapshot")
    if isinstance(raw_snapshot, dict):
        result: dict[str, Any] = dict(raw_snapshot)
    else:
        repo = _text({"repo": _require(payload, "repo")}, "repo")
        ref = _text(payload, "ref", "main")
        result = read_github(repo, ref)
    commit = str(result.get("commit") or "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", commit):
        raise invalid_request(
            "GitHub 返回的提交号不合法，未写入快照",
            "检查仓库与 ref 后重试",
        )
    target = ws.project_dir(project.project_id) / "github" / f"{commit}.json"
    secrets = _project_secrets(ws, project.project_id)
    safe_result = redact_tree(result, secrets)
    write_json(target, {"schema_version": 1, **safe_result})
    return {
        **safe_result,
        "change": safe_result,
        "path": str(target.relative_to(ws.root)).replace("\\", "/"),
    }


def _run_started_at(run_path: Path) -> datetime | None:
    if not run_path.is_file():
        return None
    text = read_json(run_path).get("started_at")
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _window_seconds(config: dict[str, Any]) -> int:
    value = config.get("window_seconds", 300)
    if isinstance(value, bool):
        return 300
    try:
        return int(value)
    except (TypeError, ValueError):
        return 300


def cmd_link_import(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_id = _run_id(payload)
    request_id = _text(payload, "request_id")
    config = load_project_config(ws, project.project_id).get("link")
    if not isinstance(config, dict):
        raise AiTestError("LINK_CONFIG_MISSING", "未配置链路数据来源", "在项目配置补充 link 段")
    log_paths = config.get("log_paths") or []
    if not isinstance(log_paths, list):
        raise invalid_request("link.log_paths 必须是数组")
    project_root = Path(project.root).resolve()
    resolved_logs: list[str] = []
    for item in log_paths:
        candidate = Path(str(item))
        resolved = (candidate if candidate.is_absolute() else project_root / candidate).resolve()
        if not candidate.is_absolute() and not resolved.is_relative_to(project_root):
            raise invalid_request(
                f"相对日志路径不能逃出项目目录：{item}",
                "使用项目内相对路径或明确配置绝对路径",
            )
        resolved_logs.append(str(resolved))
    run_dir = ws.project_dir(project.project_id) / "runs" / run_id
    if not run_dir.is_dir():
        raise AiTestError("RUN_NOT_FOUND", f"运行不存在：{run_id}", "检查运行 ID")
    run_record = read_json(run_dir / "run.json")
    result = import_link(
        run_id,
        request_id,
        log_paths=resolved_logs,
        log_format=str(config.get("log_format", "jsonl")),
        log_fields=config.get("log_fields") if isinstance(config.get("log_fields"), dict) else None,
        db_query=str(config.get("db_query") or "") or None,
        window_seconds=_window_seconds(config),
        started_at=_run_started_at(run_dir / "run.json"),
        ended_at=str(run_record.get("heartbeat_at") or run_record.get("started_at") or ""),
    )
    target = run_dir / "link.json"
    secrets = _project_secrets(ws, project.project_id)
    safe_result = redact_tree(result, secrets)
    write_json(target, {"schema_version": 1, "imported_at": iso_now(), **safe_result})
    return dict(safe_result)


def cmd_link_assert(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_id = _run_id(payload)
    node_id = _text({"node_id": _require(payload, "node_id")}, "node_id")
    expected = payload.get("expected")
    actual = payload.get("actual")
    if expected is None or actual is None:
        raise invalid_request("link.assert 需要 expected 与 actual")
    run_dir = ws.project_dir(project.project_id) / "runs" / run_id
    link_path = run_dir / "link.json"
    if not link_path.is_file():
        raise AiTestError(
            "RUN_STATE_CONFLICT", "该运行尚未导入链路", "先调用 link.import"
        )
    data = read_json(link_path)
    check_schema_version(data, link_path)
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not any(
        isinstance(node, dict) and node.get("node_id") == node_id for node in nodes
    ):
        raise invalid_request(f"链路中没有节点：{node_id}")
    operator = _text(payload, "operator", "equals")
    passed = evaluate(actual, operator, expected)
    raw_assertions = data.get("assertions")
    assertions = (
        [
            item
            for item in raw_assertions
            if not (isinstance(item, dict) and item.get("node_id") == node_id)
        ]
        if isinstance(raw_assertions, list)
        else []
    )
    record: dict[str, Any] = {
        "node_id": node_id,
        "expected": str(expected),
        "actual": str(actual),
        "result": "passed" if passed else "failed",
    }
    if str(payload.get("operator") or "").strip() or operator != "equals":
        record["operator"] = operator
    assertions.append(record)
    data["assertions"] = assertions
    data["location"] = locate_link(
        [node for node in nodes if isinstance(node, dict)], assertions
    )
    secrets = _project_secrets(ws, project.project_id)
    write_json(link_path, redact_tree(data, secrets))
    return {"result": "passed" if passed else "failed"}


__all__ = ["cmd_github_read", "cmd_link_import", "cmd_link_assert"]


REGISTRY.update(
    {
        "github.read": cmd_github_read,
        "link.import": cmd_link_import,
        "link.assert": cmd_link_assert,
    }
)
MUTATING.update({"github.read", "link.import", "link.assert"})
