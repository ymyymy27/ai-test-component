from dataclasses import asdict, replace

from ai_test.application.ports.storage import RecordRepository
from ai_test.domain.projects import Project


class ProjectContext:
    def __init__(self, records: RecordRepository) -> None:
        self._records = records

    def create(self, project: Project) -> Project:
        revision = self._records.put(
            "projects", project.project_id, asdict(project), expected_revision=0
        )
        return replace(project, revision=revision)

    def get(self, project_id: str) -> Project | None:
        record = self._records.get("projects", project_id)
        if record is None:
            return None
        modules = tuple(record.pop("modules", ()))
        from ai_test.domain.projects import Module

        normalized = tuple(
            Module(**{**item, "dependencies": tuple(item.get("dependencies", ()))})
            for item in modules
        )
        return Project(modules=normalized, **record)
