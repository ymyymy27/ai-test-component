from pathlib import Path

from ai_test.composition import Component, create_component
from ai_test.domain.projects import Project


class AITestAPI:
    def __init__(self, workspace: str | Path) -> None:
        self.component: Component = create_component(workspace)

    def create_project(self, project_id: str, name: str) -> Project:
        return self.component.projects.create(Project(project_id=project_id, name=name))

    def get_project(self, project_id: str) -> Project | None:
        return self.component.projects.get(project_id)

