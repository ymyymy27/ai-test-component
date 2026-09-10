from ai_test.application.use_cases.project_context import ProjectContext
from ai_test.domain.projects import Module, Project
from ai_test.infrastructure.file_store.records import FileRecordRepository


def test_project_round_trip(tmp_path) -> None:
    context = ProjectContext(FileRecordRepository(tmp_path / "workspace"))
    created = context.create(Project("p1", "示例项目", modules=(Module("web", "Web"),)))
    loaded = context.get("p1")
    assert created.revision == 1
    assert loaded == created

