"""Offline resources or bounded JSON forwarding to an existing verified workspace core."""

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from aitest.application.errors import CapabilityUnavailable
from aitest.bootstrap import (
    SystemProcessLauncher,
    acquire_endpoint,
    acquire_existing_endpoint,
    cli_host_pid,
    create_api,
)
from aitest.contracts.commands import Command
from aitest.contracts.templates import TemplatePack
from aitest.interfaces.local.api import EntryKind, Session
from aitest.interfaces.local.core_client import MAX_COMMAND_BYTES, CoreClient, read_command
from aitest.interfaces.tools.agent_relay import run


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="aitest")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Offline diagnostics, or the verified existing core")
    doctor.add_argument("--workspace", type=Path)
    dispatch = sub.add_parser("dispatch", help="Forward one complete JSON Command on stdin")
    dispatch.add_argument("--workspace", required=True, type=Path)
    dispatch.add_argument(
        "--native-confirmation",
        action="store_true",
        help="Request the core-owned Windows confirmation window (never auto-confirms)",
    )
    sub.add_parser("templates", help="List installed draft template versions")
    carrier = sub.add_parser(
        "python-carrier", help="Generate host venv registration JSON; does not execute it"
    )
    carrier.add_argument("--project", required=True)
    carrier.add_argument("--environment", required=True)
    carrier.add_argument("--python", required=True, type=Path)
    carrier.add_argument("--base-python", required=True, type=Path)
    carrier.add_argument("--venv-root", required=True, type=Path)
    carrier.add_argument("--dependencies", required=True, type=Path, action="append")
    carrier.add_argument(
        "--output", type=Path, help="Write a new UTF-8 config file; never overwrite"
    )
    ensure = sub.add_parser(
        "ensure-core", help="Discover or start the single core for a workspace (host entry)"
    )
    ensure.add_argument("--workspace", required=True, type=Path)
    relay = sub.add_parser(
        "mcp-relay", help="stdio diagnostics and finite queries through the verified core"
    )
    relay.add_argument("--binding", required=True)
    relay.add_argument("--workspace", type=Path)
    relay.add_argument("--project")
    args = parser.parse_args()
    if args.command == "python-carrier":
        from aitest.bootstrap import carrier_configuration

        try:
            configuration = carrier_configuration(
                project_id=args.project,
                environment_id=args.environment,
                executable=args.python,
                base_executable=args.base_python,
                dependency_roots=tuple(args.dependencies),
                venv_root=args.venv_root,
            )
            if args.output is not None:
                with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(configuration, ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError):
            print(
                json.dumps(
                    {
                        "code": "INVALID_ENVIRONMENT_CONFIGURATION",
                        "message": "核对绝对路径、隔离虚拟环境与解释器文件",
                    }
                ),
                file=sys.stderr,
            )
            return 2
        print(json.dumps(configuration, ensure_ascii=False, indent=2))
        return 0
    if args.command == "templates":
        packs = []
        for directory in sorted(
            files("aitest.resources").joinpath("templates").iterdir(), key=lambda item: item.name
        ):
            source = directory.joinpath("1.0.0.json")
            if directory.is_dir() and source.is_file():
                pack = TemplatePack.model_validate_json(source.read_text(encoding="utf-8"))
                packs.append(
                    {
                        "template_id": pack.template_id,
                        "version": pack.version,
                        "status": pack.implementation_status,
                        "delivery_method": pack.delivery_method,
                    }
                )
        print(json.dumps(packs, ensure_ascii=False, indent=2))
        return 0
    if args.command == "ensure-core":
        # 宿主入口：先连既有核心，连不上再按架构 05 第 1 节启动一个；
        # 身份由 `acquire_endpoint` 核对（实例与启动事实不一致即拒绝）。
        # 本动作**不派发任何业务动作**，也不写业务记录。
        #
        # 但**绝不**用一条写错的路径造出一个数据工作空间：与 `doctor` 的既有
        # 口径一致——路径缺失或不是一个目录时结构化失败，让调用方去核对配置。
        if not args.workspace.is_dir():
            print(
                json.dumps(
                    {
                        "code": "WORKSPACE_NOT_FOUND",
                        "message": "工作空间路径不存在或不是目录；入口不据此创建数据工作空间",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        try:
            # The one-shot startup CLI exits after READY. Its OS parent is the
            # owning editor/host, not a caller-supplied business field.
            endpoint = acquire_endpoint(
                args.workspace,
                launcher=SystemProcessLauncher(
                    args.workspace, lifecycle_parent_pid=cli_host_pid(),
                ),
            )
        except Exception as error:
            code = getattr(error, "code", "CORE_UNAVAILABLE")
            print(
                json.dumps(
                    {"code": code, "message": "核心未能在限定时间内核实并连接，保留现场待恢复"},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        print(
            json.dumps(
                {
                    "status": "READY",
                    "workspace_id": endpoint.workspace_id,
                    "instance_id": endpoint.instance_id,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "dispatch" or (args.command == "doctor" and args.workspace is not None):
        client = None
        try:
            command = (
                read_command(sys.stdin.buffer.read(MAX_COMMAND_BYTES + 1))
                if args.command == "dispatch"
                else Command(request_id=str(uuid4()), action="doctor")
            )
            client = CoreClient(acquire_existing_endpoint(args.workspace))
            response = (
                client.send_native(command)
                if args.command == "dispatch" and args.native_confirmation
                else client.send(command)
            )
        except Exception as error:
            code = getattr(error, "code", "CLI_INPUT_OR_CONNECTION_UNVERIFIED")
            print(
                json.dumps({"code": code, "message": "输入或核心结果无法核实，保留原意图查询"}),
                file=sys.stderr,
            )
            return 2
        finally:
            if client is not None:
                client.close()
        print(response.model_dump_json(indent=2))
        if response.error is not None:
            return 2
        if args.command == "doctor":
            return (
                0 if response.result is not None and response.result.get("status") == "READY" else 2
            )
        return 0
    if args.command == "mcp-relay":
        try:
            run(args.binding, workspace_root=args.workspace, project_id=args.project)
            return 0
        except Exception as error:
            code = getattr(error, "code", CapabilityUnavailable.code)
            print(json.dumps({"code": code, "message": "MCP上下文或核心不可用"}), file=sys.stderr)
            return 2
    response = create_api().dispatch(
        Command(request_id=str(uuid4()), action="doctor"),
        Session("offline-cli", EntryKind.INTERACTIVE_CLI),
    )
    print(response.model_dump_json(indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
