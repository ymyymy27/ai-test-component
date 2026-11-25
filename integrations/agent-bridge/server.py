"""轻量跨智能体工具协议适配器（标准 stdio JSON-RPC 2.0 入口）。

说明：
- 本脚本位于 ``integrations/agent-bridge/``，不触碰核心 ``src/aitest/`` 包；
- 所有业务数据读写与判定均通过子进程调用 ``python -m aitest.cli`` 完成，
  绝不在适配器内直接修改工作空间文件或重算业务结论；
- 强制保留人工确认边界：``aitest_run_start`` 必须由调用方显式携带
  ``human_confirmed=true`` 与真实人员标识 ``confirmed_by``，绝不代签确认。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
BRIDGE_NAME = "aitest-agent-bridge"
BRIDGE_VERSION = "0.5.0"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DISALLOWED_CONFIRMERS = frozenset(
    {
        "",
        "ai",
        "agent",
        "assistant",
        "bot",
        "llm",
        "codex",
        "claude",
        "gemini",
        "antigravity",
        "auto",
        "system",
    }
)

CliRunner = Callable[[str, dict[str, Any], bool], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """对外暴露的跨智能体工具描述。"""

    name: str
    description: str
    input_schema: dict[str, Any]


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="aitest_doctor",
        description="检查本地 Python 运行时、包版本、模型配置、gh 登录态与工作空间写权限。",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="aitest_project_list",
        description="列出当前工作空间中已登记的所有本地测试项目及其基础摘要。",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="aitest_project_map",
        description=(
            "获取项目 4 层全栈代码链路图、跨层契约冲突以及本地 Git 变更影响面；"
            "可选 refresh=true 重新索引。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "项目 ID"},
                "refresh": {
                    "type": "boolean",
                    "description": "是否强制重新扫描源码图与本地 Git 状态，默认 false",
                },
            },
            "required": ["project_id"],
        },
    ),
    ToolSpec(
        name="aitest_task_list",
        description="列出指定项目下的全部测试任务及当前所处阶段。",
        input_schema={
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "项目 ID"}},
            "required": ["project_id"],
        },
    ),
    ToolSpec(
        name="aitest_plan_get",
        description=(
            "查看指定测试计划的覆盖范围、用例清单、可信来源及是否过期；"
            "支持仅传 task_id 自动取当前计划。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "项目 ID"},
                "plan_id": {"type": "string", "description": "计划 ID（与 task_id 二选一）"},
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（未传 plan_id 时读取其 current_plan_id）",
                },
            },
            "required": ["project_id"],
        },
    ),
    ToolSpec(
        name="aitest_run_status",
        description=(
            "查看指定运行（或项目最新一次运行）的状态、安检门结果、确定性探针及 Token 消耗。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "项目 ID"},
                "run_id": {
                    "type": "string",
                    "description": "运行 ID（省略时自动读取最新一次运行）",
                },
                "task_id": {"type": "string", "description": "可选，按任务过滤最新运行"},
            },
            "required": ["project_id"],
        },
    ),
    ToolSpec(
        name="aitest_run_start",
        description=(
            "启动已发布的测试计划运行。硬性安全约束：必须由人类明确同意后传入 "
            "human_confirmed=true 及非空人类姓名/标识 confirmed_by，严禁 AI 自动代签。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "项目 ID"},
                "plan_id": {"type": "string", "description": "已发布的计划 ID"},
                "human_confirmed": {
                    "type": "boolean",
                    "description": "必须为 true 且代表人类操作员明确同意本次执行",
                },
                "confirmed_by": {
                    "type": "string",
                    "description": "人类确认人姓名或工号，禁止填写 ai/agent/assistant/bot 等代称",
                },
                "environment": {
                    "type": "string",
                    "description": "可选环境档案名称（如 local/staging）",
                },
                "acceptance_item_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，收窄本次运行的验收项 ID 列表",
                },
            },
            "required": ["project_id", "plan_id", "human_confirmed", "confirmed_by"],
        },
    ),
    ToolSpec(
        name="aitest_report_summary",
        description=(
            "读取指定报告（或运行最新报告）的四态结论、S/M 覆盖计数、"
            "三档可信度汇总与铁证缺陷。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "项目 ID"},
                "report_id": {"type": "string", "description": "报告 ID（与 run_id 二选一）"},
                "run_id": {
                    "type": "string",
                    "description": "运行 ID（未传 report_id 时取最新报告）",
                },
            },
            "required": ["project_id"],
        },
    ),
    ToolSpec(
        name="aitest_issues_list",
        description="列出项目去重后的缺陷账本、失败发生次数、不稳定标记及关联证据引用。",
        input_schema={
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "项目 ID"}},
            "required": ["project_id"],
        },
    ),
    ToolSpec(
        name="aitest_conventions_get",
        description=(
            "读取项目有效运行配置与错题本正常约定列表"
            "（自动合并仓库 .aitest.json 与工作空间配置）。"
        ),
        input_schema={
            "type": "object",
            "properties": {"project_id": {"type": "string", "description": "项目 ID"}},
            "required": ["project_id"],
        },
    ),
)


def default_workspace() -> Path:
    """解析默认用户数据工作空间目录（独立于被测源码仓库）。"""
    env_ws = os.environ.get("AITEST_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).expanduser().resolve()
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return (Path(appdata) / "aitest" / "workspaces" / "default").resolve()
    return (Path.home() / ".aitest" / "workspaces" / "default").resolve()


def make_cli_runner(workspace: Path, python_exe: str | None = None) -> CliRunner:
    """构造通过子进程调用 ``python -m aitest.cli`` 的标准转发器。"""
    py = (python_exe or sys.executable).strip() or sys.executable
    ws_str = str(Path(workspace).expanduser().resolve())

    def run_cli(command: str, payload: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
        req_id = f"bridge-{uuid.uuid4().hex[:12]}"
        envelope = {
            "command": command,
            "request_id": req_id,
            "payload": payload,
        }
        argv = [py, "-m", "aitest.cli", command, "--workspace", ws_str]
        if confirm:
            argv.append("--confirm")
        env = dict(os.environ)
        src_dir = _REPO_ROOT / "src"
        if src_dir.is_dir():
            existing_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else str(src_dir)
            )
        completed = subprocess.run(
            argv,
            input=json.dumps(envelope, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
        raw_out = completed.stdout.strip()
        last_line = raw_out.splitlines()[-1] if raw_out else ""
        if not last_line:
            stderr_tail = completed.stderr.strip()[-500:]
            return {
                "ok": False,
                "error": {
                    "code": "CLI_EMPTY_OUTPUT",
                    "message": f"CLI 执行 {command} 未返回 JSON（退出码 {completed.returncode}）",
                    "detail": stderr_tail,
                },
            }
        try:
            parsed = json.loads(last_line)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {
            "ok": False,
            "error": {
                "code": "CLI_INVALID_JSON",
                "message": f"CLI 返回了非 JSON 文本：{last_line[:240]}",
            },
        }

    return run_cli


def _tool_result(data: Any, *, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def _require_str(args: dict[str, Any], key: str) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"缺少必填字符串参数：{key}")
    return val.strip()


def dispatch_tool_call(name: str, arguments: dict[str, Any], runner: CliRunner) -> dict[str, Any]:
    """将工具调用映射为标准 CLI 命令并强制校验安全边界。"""
    args = dict(arguments or {})
    try:
        if name == "aitest_doctor":
            resp = runner("doctor", {}, False)
        elif name == "aitest_project_list":
            resp = runner("project.list", {}, False)
        elif name == "aitest_project_map":
            pid = _require_str(args, "project_id")
            cmd = "project.map.refresh" if args.get("refresh") is True else "project.map"
            resp = runner(cmd, {"project_id": pid}, False)
        elif name == "aitest_task_list":
            pid = _require_str(args, "project_id")
            resp = runner("task.list", {"project_id": pid}, False)
        elif name == "aitest_plan_get":
            pid = _require_str(args, "project_id")
            plan_id = str(args.get("plan_id") or "").strip()
            if not plan_id:
                task_id = str(args.get("task_id") or "").strip()
                if not task_id:
                    raise ValueError("aitest_plan_get 需要传入 plan_id 或 task_id")
                task_resp = runner("task.show", {"project_id": pid, "task_id": task_id}, False)
                if not task_resp.get("ok"):
                    return _tool_result(task_resp, is_error=True)
                task_obj = (task_resp.get("data") or {}).get("task") or {}
                plan_id = str(task_obj.get("current_plan_id") or "").strip()
                if not plan_id:
                    return _tool_result(
                        {
                            "ok": False,
                            "error": {
                                "code": "PLAN_NOT_FOUND",
                                "message": "该任务尚未生成计划",
                            },
                        },
                        is_error=True,
                    )
            resp = runner("plan.show", {"project_id": pid, "plan_id": plan_id}, False)
        elif name == "aitest_run_status":
            pid = _require_str(args, "project_id")
            run_id = str(args.get("run_id") or "").strip()
            if not run_id:
                list_payload: dict[str, Any] = {"project_id": pid}
                if args.get("task_id"):
                    list_payload["task_id"] = str(args["task_id"]).strip()
                runs_resp = runner("run.list", list_payload, False)
                if not runs_resp.get("ok"):
                    return _tool_result(runs_resp, is_error=True)
                runs = (runs_resp.get("data") or {}).get("runs") or []
                if not runs:
                    return _tool_result(
                        {
                            "ok": True,
                            "data": {"runs": [], "message": "当前项目暂无历史运行记录"},
                        },
                        is_error=False,
                    )
                run_id = str(runs[0].get("run_id") or "").strip()
            resp = runner("run.status", {"project_id": pid, "run_id": run_id}, False)
        elif name == "aitest_run_start":
            pid = _require_str(args, "project_id")
            plan_id = _require_str(args, "plan_id")
            if args.get("human_confirmed") is not True:
                return _tool_result(
                    {
                        "ok": False,
                        "error": {
                            "code": "HUMAN_CONFIRMATION_REQUIRED",
                            "message": (
                                "启动测试运行必须由人类操作员明确同意"
                                "（human_confirmed=true），AI 严禁代签确认。"
                            ),
                        },
                    },
                    is_error=True,
                )
            confirmed_by = str(args.get("confirmed_by") or "").strip()
            if confirmed_by.lower() in _DISALLOWED_CONFIRMERS:
                return _tool_result(
                    {
                        "ok": False,
                        "error": {
                            "code": "INVALID_CONFIRMED_BY",
                            "message": (
                                "confirmed_by 必须填写真实人类操作员姓名或工号，"
                                "不得使用 ai/agent/assistant/bot 等代称。"
                            ),
                        },
                    },
                    is_error=True,
                )
            payload: dict[str, Any] = {
                "project_id": pid,
                "plan_id": plan_id,
                "confirmed_by": confirmed_by,
            }
            if args.get("environment"):
                payload["environment"] = str(args["environment"]).strip()
            if isinstance(args.get("acceptance_item_ids"), list) and args["acceptance_item_ids"]:
                payload["acceptance_item_ids"] = [str(item) for item in args["acceptance_item_ids"]]
            resp = runner("run.start", payload, True)
        elif name == "aitest_report_summary":
            pid = _require_str(args, "project_id")
            report_id = str(args.get("report_id") or "").strip()
            if not report_id:
                run_id = str(args.get("run_id") or "").strip()
                if not run_id:
                    runs_resp = runner("run.list", {"project_id": pid}, False)
                    if not runs_resp.get("ok"):
                        return _tool_result(runs_resp, is_error=True)
                    runs = (runs_resp.get("data") or {}).get("runs") or []
                    if not runs:
                        return _tool_result(
                            {
                                "ok": False,
                                "error": {
                                    "code": "REPORT_NOT_FOUND",
                                    "message": "暂无可读取的运行报告",
                                },
                            },
                            is_error=True,
                        )
                    run_id = str(runs[0].get("run_id") or "").strip()
                list_resp = runner("report.list", {"project_id": pid, "run_id": run_id}, False)
                if not list_resp.get("ok"):
                    return _tool_result(list_resp, is_error=True)
                reports = (list_resp.get("data") or {}).get("reports") or []
                if not reports:
                    return _tool_result(
                        {
                            "ok": False,
                            "error": {
                                "code": "REPORT_NOT_FOUND",
                                "message": f"运行 {run_id} 尚未生成报告",
                            },
                        },
                        is_error=True,
                    )
                report_id = str(reports[0].get("report_id") or "").strip()
            resp = runner("report.show", {"project_id": pid, "report_id": report_id}, False)
        elif name == "aitest_issues_list":
            pid = _require_str(args, "project_id")
            resp = runner("issue.list", {"project_id": pid}, False)
        elif name == "aitest_conventions_get":
            pid = _require_str(args, "project_id")
            resp = runner("project.config", {"project_id": pid, "action": "get"}, False)
        else:
            return _tool_result(
                {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": f"未知工具：{name}"}},
                is_error=True,
            )
    except ValueError as exc:
        return _tool_result(
            {"ok": False, "error": {"code": "INVALID_ARGUMENTS", "message": str(exc)}},
            is_error=True,
        )

    return _tool_result(resp, is_error=not bool(resp.get("ok", False)))


def handle_jsonrpc_message(
    message: dict[str, Any],
    *,
    runner: CliRunner,
) -> dict[str, Any] | None:
    """处理单条 JSON-RPC 2.0 请求或通知消息，通知返回 None。"""
    method = str(message.get("method") or "")
    msg_id = message.get("id")
    is_notification = "id" not in message or msg_id is None

    if method == "notifications/initialized" or method.startswith("notifications/"):
        return None
    if method == "initialize":
        req_proto = str((message.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": req_proto or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": BRIDGE_NAME, "version": BRIDGE_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    }
                    for tool in TOOLS
                ]
            },
        }
    if method == "tools/call":
        params = message.get("params") or {}
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": "tools/call 的 arguments 必须是 JSON 对象"},
            }
        result = dispatch_tool_call(tool_name, arguments, runner)
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _read_framed_message(stream: Any) -> tuple[dict[str, Any] | None, str]:
    """同时兼容 Content-Length LSP 帧与按行 NDJSON 帧。"""
    while True:
        first_line = stream.readline()
        if not first_line:
            return None, "ndjson"
        if first_line in (b"\r\n", b"\n"):
            continue
        stripped = first_line.strip()
        if stripped.startswith(b"{"):
            return json.loads(stripped.decode("utf-8")), "ndjson"

        # Content-Length header frame
        headers: dict[str, str] = {}
        line = first_line
        while line and line not in (b"\r\n", b"\n"):
            text = line.decode("ascii", errors="replace").strip()
            if ":" in text:
                k, v = text.split(":", 1)
                headers[k.strip().lower()] = v.strip()
            line = stream.readline()
        length_raw = headers.get("content-length")
        if not length_raw:
            continue
        length = int(length_raw)
        body = stream.read(length)
        if not body:
            return None, "content-length"
        return json.loads(body.decode("utf-8")), "content-length"


def _write_framed_message(stream: Any, payload: dict[str, Any], framing: str) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if framing == "content-length":
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        stream.write(header + encoded)
    else:
        stream.write(encoded + b"\n")
    stream.flush()


def serve_stdio(workspace: Path, python_exe: str | None = None) -> int:
    """运行标准 stdio 服务循环直到客户端关闭输入流。"""
    runner = make_cli_runner(workspace, python_exe)
    in_stream = sys.stdin.buffer
    out_stream = sys.stdout.buffer
    while True:
        try:
            message, framing = _read_framed_message(in_stream)
        except (ValueError, UnicodeDecodeError) as exc:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            _write_framed_message(out_stream, err_resp, "ndjson")
            continue
        if message is None:
            break
        if not isinstance(message, dict):
            continue
        response = handle_jsonrpc_message(message, runner=runner)
        if response is not None:
            _write_framed_message(out_stream, response, framing)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Test Component 跨智能体 stdio 薄适配器")
    parser.add_argument(
        "--workspace",
        help="用户数据工作空间路径（默认读取 AITEST_WORKSPACE 或系统默认目录）",
    )
    parser.add_argument(
        "--python",
        dest="python_exe",
        help="指定已安装 aitest 的 Python 解释器路径",
    )
    args = parser.parse_args(argv)
    ws = Path(args.workspace).expanduser().resolve() if args.workspace else default_workspace()
    return serve_stdio(ws, args.python_exe)


if __name__ == "__main__":
    raise SystemExit(main())
