from dataclasses import dataclass
from pathlib import Path

from ai_test.application.use_cases.project_context import ProjectContext
from ai_test.application.use_cases.task_context import TaskContext
from ai_test.infrastructure.file_store.objects import FileObjectStore
from ai_test.infrastructure.file_store.records import FileRecordRepository


@dataclass(frozen=True, slots=True)
class Component:
    workspace: Path
    projects: ProjectContext
    tasks: TaskContext
    records: FileRecordRepository
    objects: FileObjectStore


def create_component(workspace: str | Path) -> Component:
    root = Path(workspace).expanduser().resolve()
    records = FileRecordRepository(root)
    records.initialize()
    projects = ProjectContext(records)
    return Component(
        workspace=root,
        projects=projects,
        tasks=TaskContext(records, projects),
        records=records,
        objects=FileObjectStore(root),
    )
