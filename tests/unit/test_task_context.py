import pytest

from ai_test.application.use_cases.project_context import ProjectContext
from ai_test.application.use_cases.task_context import TaskContext
from ai_test.domain.projects import Project
from ai_test.domain.tasks import AcceptanceItem, Task
from ai_test.infrastructure.file_store.records import FileRecordRepository


def test_task_round_trip_preserves_acceptance_items(tmp_path) -> None:
    records = FileRecordRepository(tmp_path / "workspace")
    projects = ProjectContext(records)
    projects.create(Project("project-1", "Project"))
    context = TaskContext(records, projects)
    task = Task(
        task_id="task-1",
        project_id="project-1",
        goal="Create and query a ticket",
        scope="Ticket creation flow",
        acceptance_items=(
            AcceptanceItem("AC1", "The created ticket can be queried"),
            AcceptanceItem(
                "AC2",
                "The stored fields match the submitted data",
                required=False,
            ),
        ),
        inputs=("ticket payload",),
        outputs=("ticket id",),
        preconditions=("test database is available",),
        owner="developer-1",
        acceptor="reviewer-1",
    )

    created = context.create(task)

    assert created.revision == 1
    assert context.get("task-1") == created
    assert context.list("project-1") == (created,)
    assert context.list("another-project") == ()


def test_task_list_filters_by_project(tmp_path) -> None:
    records = FileRecordRepository(tmp_path / "workspace")
    projects = ProjectContext(records)
    projects.create(Project("project-1", "Project 1"))
    projects.create(Project("project-2", "Project 2"))
    context = TaskContext(records, projects)
    for task_id, project_id in (("task-1", "project-1"), ("task-2", "project-2")):
        context.create(
            Task(
                task_id=task_id,
                project_id=project_id,
                goal="Goal",
                scope="Scope",
                acceptance_items=(AcceptanceItem("AC1", "Observable result"),),
            )
        )

    assert [task.task_id for task in context.list("project-1")] == ["task-1"]
    assert [task.task_id for task in context.list("project-2")] == ["task-2"]


def test_task_rejects_unknown_project(tmp_path) -> None:
    records = FileRecordRepository(tmp_path / "workspace")
    context = TaskContext(records, ProjectContext(records))

    with pytest.raises(ValueError, match="project not found"):
        context.create(
            Task(
                task_id="task-1",
                project_id="missing",
                goal="Goal",
                scope="Scope",
                acceptance_items=(AcceptanceItem("AC1", "Observable result"),),
            )
        )
