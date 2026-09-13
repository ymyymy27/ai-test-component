import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ai_test.application.use_cases.capabilities import describe_capabilities
from ai_test.composition import create_component
from ai_test.domain.deliveries import Delivery
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
    parser = argparse.ArgumentParser(prog="aitest", description="AI test component")
    parser.add_argument("--workspace", type=Path, default=Path(".aitest"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize workspace")
    commands.add_parser("doctor", help="check component state")
    commands.add_parser("capabilities", help="list implemented and planned capabilities")

    create_project = commands.add_parser("project-create", help="create project context")
    create_project.add_argument("project_id")
    create_project.add_argument("name")
    get_project = commands.add_parser("project-get", help="read project context")
    get_project.add_argument("project_id")

    create_task = commands.add_parser("task-create", help="create task and acceptance items")
    create_task.add_argument("project_id")
    create_task.add_argument("task_id")
    create_task.add_argument("goal")
    create_task.add_argument("--scope", required=True)
    create_task.add_argument(
        "--acceptance",
        action="append",
        required=True,
        metavar="ID=RESULT",
        help="acceptance item; repeatable",
    )
    create_task.add_argument("--owner")
    create_task.add_argument("--acceptor")
    get_task = commands.add_parser("task-get", help="read task")
    get_task.add_argument("task_id")
    list_tasks = commands.add_parser("task-list", help="list tasks")
    list_tasks.add_argument("--project")

    create_delivery = commands.add_parser(
        "delivery-create", help="create a standard delivery declaration"
    )
    create_delivery.add_argument("task_id")
    create_delivery.add_argument("delivery_id")
    create_delivery.add_argument("version")
    create_delivery.add_argument("--run-method", required=True)
    create_delivery.add_argument("--completed", action="append", default=[])
    create_delivery.add_argument("--incomplete", action="append", default=[])
    create_delivery.add_argument("--changed-module", action="append", default=[])
    create_delivery.add_argument("--api-change", action="append", default=[])
    create_delivery.add_argument("--test-data", action="append", default=[])
    create_delivery.add_argument("--dependency", action="append", default=[])
    create_delivery.add_argument("--mock", action="append", default=[])
    create_delivery.add_argument("--known-issue", action="append", default=[])
    create_delivery.add_argument("--self-test-evidence", action="append", default=[])
    create_delivery.add_argument("--submitted-by")
    get_delivery = commands.add_parser("delivery-get", help="read delivery")
    get_delivery.add_argument("delivery_id")
    list_deliveries = commands.add_parser("delivery-list", help="list deliveries")
    list_deliveries.add_argument("--task")
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
        writable, error = component.records.check_writable()
        _emit(
            {
                "status": "ok" if writable else "degraded",
                "workspace": str(component.workspace),
                "writable": writable,
                "error": error,
            }
        )
        return 0 if writable else 3
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
    elif args.command == "delivery-create":
        created_delivery = component.deliveries.create(
            Delivery(
                delivery_id=args.delivery_id,
                task_id=args.task_id,
                version=args.version,
                completed=tuple(args.completed),
                incomplete=tuple(args.incomplete),
                changed_modules=tuple(args.changed_module),
                api_changes=tuple(args.api_change),
                run_method=args.run_method,
                test_data=tuple(args.test_data),
                dependencies=tuple(args.dependency),
                mocks=tuple(args.mock),
                known_issues=tuple(args.known_issue),
                self_test_evidence=tuple(args.self_test_evidence),
                submitted_by=args.submitted_by,
            )
        )
        _emit(asdict(created_delivery))
    elif args.command == "delivery-get":
        loaded_delivery = component.deliveries.get(args.delivery_id)
        if loaded_delivery is None:
            _emit({"error": "DELIVERY_NOT_FOUND", "delivery_id": args.delivery_id})
            return 2
        _emit(asdict(loaded_delivery))
    elif args.command == "delivery-list":
        _emit([asdict(item) for item in component.deliveries.list(args.task)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())