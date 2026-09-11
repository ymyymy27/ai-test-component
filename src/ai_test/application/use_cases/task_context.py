from dataclasses import asdict, replace
from typing import Any

from ai_test.application.ports.storage import RecordRepository
from ai_test.application.use_cases.project_context import ProjectContext
from ai_test.domain.tasks import AcceptanceItem, Task


class TaskContext:
    def __init__(self, records: RecordRepository, projects: ProjectContext) -> None:
        self._records = records
        self._projects = projects

    def create(self, task: Task) -> Task:
        if self._projects.get(task.project_id) is None:
            raise ValueError(f"project not found: {task.project_id}")
        revision = self._records.put(
            "tasks", task.task_id, asdict(task), expected_revision=0
        )
        return replace(task, revision=revision)

    def get(self, task_id: str) -> Task | None:
        record = self._records.get("tasks", task_id)
        if record is None:
            return None
        return self._from_record(record)

    def list(self, project_id: str | None = None) -> tuple[Task, ...]:
        tasks = (self._from_record(record) for record in self._records.list("tasks"))
        if project_id is None:
            return tuple(tasks)
        return tuple(task for task in tasks if task.project_id == project_id)

    @staticmethod
    def _from_record(record: dict[str, Any]) -> Task:
        acceptance_items = tuple(
            AcceptanceItem(
                acceptance_item_id=item["acceptance_item_id"],
                observable_result=item["observable_result"],
                required=bool(item.get("required", True)),
            )
            for item in record.pop("acceptance_items", ())
        )
        for field_name in ("inputs", "outputs", "preconditions"):
            record[field_name] = tuple(record.get(field_name, ()))
        return Task(acceptance_items=acceptance_items, **record)
