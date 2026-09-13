from pathlib import Path

from ai_test.composition import Component, create_component
from ai_test.domain.deliveries import Delivery
from ai_test.domain.projects import Project
from ai_test.domain.tasks import AcceptanceItem, Task


class AITestAPI:
    def __init__(self, workspace: str | Path) -> None:
        self.component: Component = create_component(workspace)

    def create_project(self, project_id: str, name: str) -> Project:
        return self.component.projects.create(Project(project_id=project_id, name=name))

    def get_project(self, project_id: str) -> Project | None:
        return self.component.projects.get(project_id)

    def create_task(
        self,
        task_id: str,
        project_id: str,
        goal: str,
        scope: str,
        acceptance_items: tuple[AcceptanceItem, ...],
        *,
        owner: str | None = None,
        acceptor: str | None = None,
    ) -> Task:
        return self.component.tasks.create(
            Task(
                task_id=task_id,
                project_id=project_id,
                goal=goal,
                scope=scope,
                acceptance_items=acceptance_items,
                owner=owner,
                acceptor=acceptor,
            )
        )

    def get_task(self, task_id: str) -> Task | None:
        return self.component.tasks.get(task_id)

    def list_tasks(self, project_id: str | None = None) -> tuple[Task, ...]:
        return self.component.tasks.list(project_id)

    def create_delivery(
        self,
        delivery_id: str,
        task_id: str,
        version: str,
        run_method: str,
        *,
        completed: tuple[str, ...] = (),
        incomplete: tuple[str, ...] = (),
        changed_modules: tuple[str, ...] = (),
        api_changes: tuple[str, ...] = (),
        test_data: tuple[str, ...] = (),
        dependencies: tuple[str, ...] = (),
        mocks: tuple[str, ...] = (),
        known_issues: tuple[str, ...] = (),
        self_test_evidence: tuple[str, ...] = (),
        submitted_by: str | None = None,
    ) -> Delivery:
        return self.component.deliveries.create(
            Delivery(
                delivery_id=delivery_id,
                task_id=task_id,
                version=version,
                completed=completed,
                incomplete=incomplete,
                changed_modules=changed_modules,
                api_changes=api_changes,
                run_method=run_method,
                test_data=test_data,
                dependencies=dependencies,
                mocks=mocks,
                known_issues=known_issues,
                self_test_evidence=self_test_evidence,
                submitted_by=submitted_by,
            )
        )

    def get_delivery(self, delivery_id: str) -> Delivery | None:
        return self.component.deliveries.get(delivery_id)

    def list_deliveries(self, task_id: str | None = None) -> tuple[Delivery, ...]:
        return self.component.deliveries.list(task_id)