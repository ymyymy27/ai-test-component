import pytest

from aitest.domain.project.context import (
    BindingForm,
    LocalProjectBinding,
)
from aitest.domain.project.context import (
    LocalProject as _LocalProject,
)


def _git_binding(**overrides: object) -> LocalProjectBinding:
    base: dict[str, object] = {
        "binding_id": "binding-1",
        "binding_revision": 1,
        "project_id": "p1",
        "canonical_path": r"C:\work\project",
        "binding_form": BindingForm.GIT,
        "repository_id": "origin",
        "branch": "main",
        "base_commit": "abc123",
    }
    return LocalProjectBinding(**(base | overrides))  # type: ignore[arg-type]


def _plain_binding(**overrides: object) -> LocalProjectBinding:
    base: dict[str, object] = {
        "binding_id": "binding-1",
        "binding_revision": 1,
        "project_id": "p1",
        "canonical_path": r"C:\work\plain",
        "binding_form": BindingForm.PLAIN,
        "manifest_digest": "deadbeef",
    }
    return LocalProjectBinding(**(base | overrides))  # type: ignore[arg-type]


def test_git_binding_requires_repository_branch_and_base_commit() -> None:
    for missing in ("repository_id", "branch", "base_commit"):
        with pytest.raises(ValueError, match=missing):
            _git_binding(**{missing: None})


def test_git_binding_must_not_carry_plain_manifest() -> None:
    with pytest.raises(ValueError, match="plain manifest"):
        _git_binding(manifest_digest="deadbeef")


def test_plain_binding_omits_every_repository_field() -> None:
    for present in ("repository_id", "branch", "base_commit"):
        with pytest.raises(ValueError, match=f"omit {present}"):
            _plain_binding(**{present: "value"})


def test_plain_binding_requires_manifest_digest() -> None:
    with pytest.raises(ValueError, match="manifest_digest"):
        _plain_binding(manifest_digest=None)


def test_plain_directory_is_a_first_class_binding() -> None:
    """目录不是 Git 仓库不得被拒绝，也不得降级检查范围（P1-FR01）。"""
    binding = _plain_binding()
    assert binding.is_plain
    assert not binding.is_git
    assert binding.repository_id is None
    assert binding.branch is None
    assert binding.base_commit is None
    assert binding.manifest_digest == "deadbeef"


def test_binding_rejects_relative_or_unnormalized_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        _plain_binding(canonical_path=r"work\plain")
    with pytest.raises(ValueError, match="'\\.\\.'"):
        _plain_binding(canonical_path=r"C:\work\..\plain")
    with pytest.raises(ValueError, match="empty"):
        _plain_binding(canonical_path="   ")


def test_moving_a_directory_creates_a_new_revision_and_keeps_history() -> None:
    """绑定与项目身份分离：移动产生新修订，历史修订仍归同一项目。"""
    first = _git_binding()
    moved = _git_binding(binding_revision=2, canonical_path=r"C:\work\renamed")
    assert moved.project_id == first.project_id
    assert moved.binding_revision > first.binding_revision
    assert first.canonical_path == r"C:\work\project"
    assert not moved.confirmed


def test_binding_requires_identity_and_revision() -> None:
    with pytest.raises(ValueError, match="binding_id"):
        _git_binding(binding_id=" ")
    with pytest.raises(ValueError, match="project_id"):
        _git_binding(project_id="")
    with pytest.raises(ValueError, match="binding_revision"):
        _git_binding(binding_revision=0)


def test_local_project_stays_the_same_business_identity_after_a_move() -> None:
    project = _LocalProject(
        local_project_id="p1",
        workspace_id="ws-1",
        name="项目显示名",
        goal="目标",
        created_at_commit="0001",
    )
    renamed = _LocalProject(
        local_project_id="p1",
        workspace_id="ws-1",
        name="改名后的显示名",
        goal="目标",
        created_at_commit="0001",
        revision=2,
    )
    assert renamed.project_id == project.project_id
