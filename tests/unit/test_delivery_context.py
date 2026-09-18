import pytest

from ai_test.application.use_cases.delivery import DeliveryContext
from ai_test.application.use_cases.project_context import ProjectContext
from ai_test.application.use_cases.task_context import TaskContext
from ai_test.domain.deliveries import Delivery
from ai_test.domain.projects import Project
from ai_test.domain.tasks import AcceptanceItem, Task
from ai_test.infrastructure.file_store.records import FileRecordRepository


def build_context(tmp_path) -> DeliveryContext:
    records = FileRecordRepository(tmp_path / "workspace")
    projects = ProjectContext(records)
    projects.create(Project("project-1", "Project"))
    tasks = TaskContext(records, projects)
    tasks.create(
        Task(
            task_id="task-1",
            project_id="project-1",
            goal="Goal",
            scope="Scope",
            acceptance_items=(AcceptanceItem("AC1", "Observable result"),),
        )
    )
    return DeliveryContext(records, tasks)


def test_delivery_round_trip_preserves_structured_fields(tmp_path) -> None:
    context = build_context(tmp_path)
    delivery = Delivery(
        delivery_id="delivery-1",
        task_id="task-1",
        version="v1.0.0",
        completed=("Create endpoint",),
        incomplete=("Cleanup job",),
        changed_modules=("api",),
        api_changes=("POST /tickets",),
        run_method="uv run pytest",
        test_data=("seeded ticket",),
        dependencies=("postgres",),
        mocks=("payment gateway",),
        known_issues=("No retry",),
        self_test_evidence=("pytest: 12 passed",),
        submitted_by="developer-1",
    )

    created = context.create(delivery)

    assert created.revision == 1
    assert context.get("delivery-1") == created
    assert context.list("task-1") == (created,)
    assert context.list("another-task") == ()


def test_delivery_rejects_unknown_task(tmp_path) -> None:
    context = build_context(tmp_path)
    with pytest.raises(ValueError, match="task not found"):
        context.create(
            Delivery(
                delivery_id="delivery-1",
                task_id="missing",
                version="v1",
                run_method="pytest",
            )
        )