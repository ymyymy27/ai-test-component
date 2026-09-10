import pytest

from ai_test.domain.projects import Module, Project


def test_project_rejects_duplicate_module_ids() -> None:
    modules = (Module("api", "API"), Module("api", "Duplicate"))
    with pytest.raises(ValueError, match="module_id"):
        Project("p1", "Project", modules=modules)

