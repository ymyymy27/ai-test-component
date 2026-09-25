import pytest

from aitest.domain.project.context import LocalProject, Module


def _module(module_id: str, name: str = "Module") -> Module:
    return Module(
        module_id=module_id,
        project_id="p1",
        name=name,
        responsibility="提供接口",
        interface_note="HTTP JSON",
    )


def test_project_rejects_duplicate_module_ids() -> None:
    with pytest.raises(ValueError, match="module_id"):
        LocalProject(
            local_project_id="p1",
            workspace_id="ws-1",
            name="Project",
            goal="目标",
            created_at_commit="0001",
            modules=(_module("api"), _module("api", "Duplicate")),
        )


def test_local_project_id_and_project_id_are_the_same_value() -> None:
    project = LocalProject(
        local_project_id="p1",
        workspace_id="ws-1",
        name="Project",
        goal="目标",
        created_at_commit="0001",
    )
    assert project.project_id == project.local_project_id


def test_project_rejects_module_from_another_project() -> None:
    foreign = Module(
        module_id="api",
        project_id="other",
        name="API",
        responsibility="提供接口",
        interface_note="HTTP JSON",
    )
    with pytest.raises(ValueError, match="match its project"):
        LocalProject(
            local_project_id="p1",
            workspace_id="ws-1",
            name="Project",
            goal="目标",
            created_at_commit="0001",
            modules=(foreign,),
        )


def test_project_requires_identity_and_commit_number() -> None:
    base = {
        "local_project_id": "p1",
        "workspace_id": "ws-1",
        "name": "Project",
        "goal": "目标",
        "created_at_commit": "0001",
    }
    with pytest.raises(ValueError, match="local_project_id"):
        LocalProject(**(base | {"local_project_id": "  "}))
    with pytest.raises(ValueError, match="workspace_id"):
        LocalProject(**(base | {"workspace_id": ""}))
    with pytest.raises(ValueError, match="commit"):
        LocalProject(**(base | {"created_at_commit": ""}))
    with pytest.raises(ValueError, match="revision"):
        LocalProject(**(base | {"revision": 0}))


def test_project_module_lookup_reports_unknown_id() -> None:
    project = LocalProject(
        local_project_id="p1",
        workspace_id="ws-1",
        name="Project",
        goal="目标",
        created_at_commit="0001",
        modules=(_module("api"),),
    )
    assert project.module("api").module_id == "api"
    with pytest.raises(ValueError, match="unknown module_id"):
        project.module("missing")


def test_module_requires_interface_note() -> None:
    with pytest.raises(ValueError, match="interface_note"):
        Module(
            module_id="api",
            project_id="p1",
            name="API",
            responsibility="提供接口",
            interface_note=" ",
        )
