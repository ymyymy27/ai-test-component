import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ai_test.application.use_cases.capabilities import describe_capabilities
from ai_test.composition import create_component
from ai_test.domain.projects import Project


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aitest", description="AI 辅助测试组件")
    parser.add_argument("--workspace", type=Path, default=Path(".aitest"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="初始化工作空间")
    commands.add_parser("doctor", help="检查组件状态")
    commands.add_parser("capabilities", help="列出已实现及计划能力")
    create = commands.add_parser("project-create", help="创建项目上下文")
    create.add_argument("project_id")
    create.add_argument("name")
    get = commands.add_parser("project-get", help="读取项目上下文")
    get.add_argument("project_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "capabilities":
        _emit(describe_capabilities())
        return 0
    component = create_component(args.workspace)
    if args.command == "init":
        _emit({"status": "initialized", "workspace": str(component.workspace)})
    elif args.command == "doctor":
        _emit({"status": "ok", "workspace": str(component.workspace), "writable": True})
    elif args.command == "project-create":
        created_project = component.projects.create(Project(args.project_id, args.name))
        _emit(asdict(created_project))
    elif args.command == "project-get":
        loaded_project = component.projects.get(args.project_id)
        if loaded_project is None:
            _emit({"error": "PROJECT_NOT_FOUND", "project_id": args.project_id})
            return 2
        _emit(asdict(loaded_project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
