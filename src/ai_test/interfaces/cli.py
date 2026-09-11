import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ai_test.application.use_cases.capabilities import describe_capabilities
from ai_test.composition import create_component
from ai_test.domain.projects import Project
from ai_test.domain.tasks import AcceptanceItem, Task


def _emit(value: Any) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        print(content.decode("utf-8"), end="")
        return
    stream.write(content)
    stream.flush()


def _parse_acceptance_items(values: Sequence[str]) -> tuple[AcceptanceItem, ...]:
    items: list[AcceptanceItem] = []
    for value in values:
        item_id, separator, observable_result = value.partition("=")
        if not separator or not item_id.strip() or not observable_result.strip():
            raise ValueError("acceptance items must use ID=observable result")
        items.append(
            AcceptanceItem(
                acceptance_item_id=item_id.strip(),
                observable_result=observable_result.strip(),
            )
        )
    return tuple(items)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aitest", description="AI 辅助测试组件")
    parser.add_argument("--workspace", type=Path, default=Path(".aitest"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="初始化工作空间")
    commands.add_parser("doctor", help="检查组件状态")
    commands.add_parser("capabilities", help="列出已实现及计划能力")

    create_project = commands.add_parser("project-create", help="创建项目上下文")
    create_project.add_argument("project_id")
    create_project.add_argument("name")
    get_project = commands.add_parser("project-get", help="读取项目上下文")
    get_project.add_argument("project_id")

    create_task = commands.add_parser("task-create", help="创建任务与验收项")
    create_task.add_argument("project_id")
    create_task.add_argument("task_id")
    create_task.add_argument("goal")
    create_task.add_argument("--scope", required=True)
    create_task.add_argument(
        "--acceptance",
        action="append",
        required=True,
        metavar="ID=RESULT",
        help="可重复指定验收项",
    )
    create_task.add_argument("--owner")
    create_task.add_argument("--acceptor")
    get_task = commands.add_parser("task-get", help="读取任务")
    get_task.add_argument("task_id")
    list_tasks = commands.add_parser("task-list", help="列出任务")
    list_tasks.add_argument("--project")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
    elif args.command == "task-create":
        if component.projects.get(args.project_id) is None:
            _emit({"error": "PROJECT_NOT_FOUND", "project_id": args.project_id})
            return 2
        try:
            acceptance_items = _parse_acceptance_items(args.acceptance)
        except ValueError as error:
            parser.error(str(error))
        created_task = component.tasks.create(
            Task(
                task_id=args.task_id,
                project_id=args.project_id,
                goal=args.goal,
                scope=args.scope,
                acceptance_items=acceptance_items,
                owner=args.owner,
                acceptor=args.acceptor,
            )
        )
        _emit(asdict(created_task))
    elif args.command == "task-get":
        loaded_task = component.tasks.get(args.task_id)
        if loaded_task is None:
            _emit({"error": "TASK_NOT_FOUND", "task_id": args.task_id})
            return 2
        _emit(asdict(loaded_task))
    elif args.command == "task-list":
        _emit([asdict(task) for task in component.tasks.list(args.project)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
