"""Lightweight CLI entry: one JSON request in, one JSON response out."""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aitest import __version__
from aitest.commands import MUTATING, REGISTRY, Payload, cached_result, store_result
from aitest.errors import AiTestError, ErrorInfo
from aitest.storage.workspace import Workspace

_Handler = Callable[[Workspace, Payload, bool], dict[str, Any]]

_LAZY_RUNTIME: dict[str, tuple[str, str]] = {
    "project.snapshot": ("aitest.runtime.snapshot", "cmd_project_snapshot"),
    "plan.generate": ("aitest.runtime.plan_commands", "cmd_plan_generate"),
    "plan.update": ("aitest.runtime.plan_commands", "cmd_plan_update"),
    "plan.show": ("aitest.runtime.plan_commands", "cmd_plan_show"),
    "plan.publish": ("aitest.runtime.plan_commands", "cmd_plan_publish"),
    "run.start": ("aitest.runtime.run_commands", "cmd_run_start"),
    "run.quick": ("aitest.runtime.run_commands", "cmd_run_quick"),
    "run.status": ("aitest.runtime.run_commands", "cmd_run_status"),
    "run.repair": ("aitest.runtime.run_commands", "cmd_run_repair"),
    "run.list": ("aitest.runtime.run_commands", "cmd_run_list"),
    "run.cancel": ("aitest.runtime.run_commands", "cmd_run_cancel"),
    "run.manual": ("aitest.runtime.run_commands", "cmd_run_manual"),
    "evidence.add": ("aitest.runtime.run_commands", "cmd_evidence_add"),
    "evidence.list": ("aitest.runtime.run_commands", "cmd_evidence_list"),
    "issue.add": ("aitest.runtime.issue_commands", "cmd_issue_add"),
    "issue.list": ("aitest.runtime.issue_commands", "cmd_issue_list"),
    "issue.show": ("aitest.runtime.issue_commands", "cmd_issue_show"),
    "issue.update": ("aitest.runtime.issue_commands", "cmd_issue_update"),
    "issue.close": ("aitest.runtime.issue_commands", "cmd_issue_close"),
    "report.generate": ("aitest.runtime.report_commands", "cmd_report_generate"),
    "report.show": ("aitest.runtime.report_commands", "cmd_report_show"),
    "report.list": ("aitest.runtime.report_commands", "cmd_report_list"),
    "report.export": ("aitest.runtime.report_commands", "cmd_report_export"),
    "report.opinion": ("aitest.runtime.report_commands", "cmd_report_opinion"),
    "backup.create": ("aitest.runtime.backup_cleanup", "cmd_backup_create"),
    "backup.restore": ("aitest.runtime.backup_cleanup", "cmd_backup_restore"),
    "data.cleanup": ("aitest.runtime.backup_cleanup", "cmd_data_cleanup"),
    "analysis.run": ("aitest.runtime.run_commands", "cmd_analysis_run"),
}

_LAZY_INTEGRATION: dict[str, tuple[str, str]] = {
    "github.read": ("aitest.integration_commands", "cmd_github_read"),
    "link.import": ("aitest.integration_commands", "cmd_link_import"),
    "link.assert": ("aitest.integration_commands", "cmd_link_assert"),
}

_LAZY_MUTATING: frozenset[str] = frozenset(
    {
        "project.map.refresh",
        "plan.generate",
        "plan.update",
        "plan.publish",
        "run.start",
        "run.quick",
        "run.cancel",
        "run.manual",
        "run.repair",
        "evidence.add",
        "issue.add",
        "issue.update",
        "issue.close",
        "report.generate",
        "report.export",
        "report.opinion",
        "backup.create",
        "backup.restore",
        "data.cleanup",
        "analysis.run",
        "github.read",
        "link.import",
        "link.assert",
    }
)


def _lazy(module_name: str, func_name: str) -> _Handler:
    """按需延迟导入重型命令处理模块，降低高频 CLI 只读冷启动耗时。"""

    def _handler(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
        if module_name.startswith("aitest.runtime."):
            facade = sys.modules.get("aitest.runtime_commands")
            if facade is not None:
                override = getattr(facade, func_name, None)
                if callable(override):
                    return dict(override(ws, payload, confirm))
        mod = importlib.import_module(module_name)
        target = getattr(mod, func_name)
        return dict(target(ws, payload, confirm))

    return _handler


def _register_lazy_commands() -> None:
    """保证在不急切导入全部模块时，60 个命令契约与写命令集合仍完整就绪。"""
    for cmd, (mod_name, fn_name) in {**_LAZY_RUNTIME, **_LAZY_INTEGRATION}.items():
        REGISTRY.setdefault(cmd, _lazy(mod_name, fn_name))
    MUTATING.update(_LAZY_MUTATING)


_register_lazy_commands()

_SELF_LOCKED = frozenset(
    {
        "run.start",
        "run.quick",
        "run.manual",
        "run.repair",
        "evidence.add",
        "analysis.run",
        "run.cancel",
        "backup.create",
        "backup.restore",
        "data.cleanup",
    }
)

_GLOBAL_MUTATING = frozenset({"project.add", "project.delete"})

_LOCK_TIMEOUTS: dict[str, float] = {
    "task.add": 180.0,
    "project.map.refresh": 180.0,
    "plan.generate": 180.0,
    "delivery.draft": 180.0,
    "report.generate": 300.0,
    "backup.create": 600.0,
    "backup.restore": 600.0,
    "github.read": 120.0,
    "link.import": 120.0,
}


def _lock_timeout(command: str) -> float:
    return _LOCK_TIMEOUTS.get(command, 30.0)


def _usage(message: str, next_step: str = "修正参数后重试") -> AiTestError:
    return AiTestError("USAGE", message, next_step)


def _log(message: str) -> None:
    if os.environ.get("AITEST_LOG"):
        print(f"[aitest] {message}", file=sys.stderr)


def _parse(argv: list[str]) -> tuple[str, Path, str | None, bool]:
    if not argv:
        raise _usage("缺少命令", "用法：aitest <command> --workspace <dir>")
    command = argv[0]
    workspace: str | None = None
    input_file: str | None = None
    confirm = False
    index = 1
    while index < len(argv):
        item = argv[index]
        if item in {"--workspace", "-w"}:
            index += 1
            if index >= len(argv) or argv[index].startswith("--"):
                raise _usage("--workspace 缺少目录路径")
            workspace = argv[index]
        elif item in {"--input", "-i"}:
            index += 1
            if index >= len(argv) or argv[index].startswith("--"):
                raise _usage("--input 缺少 JSON 文件路径", "提供文件路径，或省略 --input 用 stdin")
            input_file = argv[index]
        elif item == "--confirm":
            confirm = True
        else:
            raise _usage(f"未知参数：{item}")
        index += 1
    if not workspace:
        raise _usage("缺少 --workspace", "用法：aitest <command> --workspace <dir>")
    return command, Path(workspace).expanduser().resolve(), input_file, confirm


def _read_payload(input_file: str | None) -> dict[str, Any]:
    if input_file:
        try:
            raw = Path(input_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise _usage(f"无法读取输入文件：{exc}") from exc
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        raw = ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _usage(f"输入不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise _usage("输入必须是 JSON 对象")
    return data


def _write(response: dict[str, Any]) -> None:
    print(json.dumps(response, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    # The extension decodes stdout/stderr as UTF-8; never depend on the console
    # locale (cp936 on Chinese Windows) for the JSON protocol.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--version"], ["-V"], ["version"]):
        print(__version__)
        return 0
    request_id = ""
    started = time.monotonic()
    try:
        command, workspace_root, input_file, confirm = _parse(args)
        payload = _read_payload(input_file)
        request_id = payload.get("request_id", "")
        if not isinstance(request_id, str):
            raise _usage("request_id 必须是字符串")
        envelope_command = payload.get("command", command)
        if envelope_command != command:
            raise _usage("信封 command 与参数不一致")
        inner = payload.get("payload", {})
        if not isinstance(inner, dict):
            raise _usage("payload 必须是对象")
        handler = REGISTRY.get(command)
        if handler is None:
            raise _usage(f"未知命令：{command}", "检查命令拼写")
        _log(f"start {command} workspace={workspace_root}")
        workspace = Workspace(workspace_root)
        workspace.init()
        def execute() -> tuple[dict[str, Any], bool]:
            if request_id and command in MUTATING:
                cached = cached_result(workspace, request_id, command, inner)
                if cached is not None:
                    return cached, True
            result = handler(workspace, inner, confirm)
            response = {"ok": True, "request_id": request_id, "data": result}
            if request_id and command in MUTATING:
                store_result(workspace, request_id, response, command, inner)
            return response, False

        if command in MUTATING and command not in _SELF_LOCKED:
            raw_project_id = inner.get("project_id")
            if (
                command not in _GLOBAL_MUTATING
                and isinstance(raw_project_id, str)
                and raw_project_id
                and raw_project_id.replace("-", "").replace("_", "").isalnum()
                and len(raw_project_id) <= 128
            ):
                lock_ctx = workspace.project_lock(
                    raw_project_id, timeout=_lock_timeout(command)
                )
            else:
                lock_ctx = workspace.lock(timeout=_lock_timeout(command))
            with lock_ctx:
                response, from_cache = execute()
        else:
            response, from_cache = execute()
        _write(response)
        if from_cache:
            _log(f"cached {command} in {time.monotonic() - started:.3f}s")
        else:
            _log(f"ok {command} in {time.monotonic() - started:.3f}s")
        return 0
    except AiTestError as exc:
        _write({"ok": False, "request_id": request_id, "error": exc.info.to_dict()})
        _log(f"error {exc.info.code}: {exc.info.message}")
        return 2 if exc.info.code == "USAGE" else 1
    except (OSError, ValueError) as exc:
        info = ErrorInfo(
            code="INVALID_REQUEST",
            message=str(exc),
            next_step="检查参数与环境后重试",
        )
        _write({"ok": False, "request_id": request_id, "error": info.to_dict()})
        _log(f"environment error: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - never leave stdout empty
        import traceback

        traceback.print_exc(file=sys.stderr)
        info = ErrorInfo(
            code="INTERNAL",
            message=f"未预期错误：{exc.__class__.__name__}",
            next_step="附 request_id 与 stderr 反馈",
        )
        _write({"ok": False, "request_id": request_id, "error": info.to_dict()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
