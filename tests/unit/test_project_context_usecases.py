"""项目上下文用例：建立项目、绑定、环境、依赖图与缺口检测。

依据：`docs/文档-feix-a/B包/12-项目上下文档与草稿生成设计说明.md`；
需求 P1-FR01、P1-FR03、P1-AC17、P1-AC25。
"""

from pathlib import PureWindowsPath

import pytest

from aitest.application.project.context import (
    GAP_MISSING_DEPENDENCY_REGISTRATION,
    GAP_MISSING_ENVIRONMENT_CARRIER,
    GAP_NO_MODULES,
    WARN_UNVERIFIED_DRIVE_KIND,
    BindingInputs,
    ContextGap,
    EnvironmentInputs,
    blocking_gaps,
    create_binding,
    create_environment,
    create_project,
    detect_context_gaps,
    move_binding,
    path_is_canonical_absolute,
    register_graph,
    revise_project,
)
from aitest.domain.project.context import (
    BindingForm,
    Dependency,
    DependencyOrigin,
    DriveKind,
    IsolationMode,
    Module,
    SecretRef,
    WorkspaceLocationRejection,
)


def _module(module_id: str = "m1", *, project_id: str = "p1") -> Module:
    return Module(
        module_id=module_id,
        project_id=project_id,
        name="core",
        responsibility="compute",
        interface_note="pure functions",
    )


def _git_inputs(**overrides: object) -> BindingInputs:
    base: dict[str, object] = {
        "binding_id": "b1",
        "project_id": "p1",
        "canonical_path": r"C:\work\project",
        "binding_form": BindingForm.GIT,
        "drive_kind": DriveKind.FIXED,
        "repository_id": "origin",
        "branch": "main",
        "base_commit": "abc123",
    }
    base.update(overrides)
    return BindingInputs(**base)  # type: ignore[arg-type]


def _plain_inputs(**overrides: object) -> BindingInputs:
    base: dict[str, object] = {
        "binding_id": "b1",
        "project_id": "p1",
        "canonical_path": r"C:\work\plain",
        "binding_form": BindingForm.PLAIN,
        "drive_kind": DriveKind.FIXED,
        "manifest_digest": "sha256:manifest",
    }
    base.update(overrides)
    return BindingInputs(**base)  # type: ignore[arg-type]


def _environment(**overrides: object) -> EnvironmentInputs:
    base: dict[str, object] = {
        "environment_id": "env-1",
        "interpreter_requirement": "python>=3.13,<3.14",
        "dependency_declaration": "uv.lock",
    }
    base.update(overrides)
    return EnvironmentInputs(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ 项目


def test_project_is_created_with_the_business_alias() -> None:
    project = create_project(
        project_id="p1",
        workspace_id="ws-1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
    )
    assert project.local_project_id == "p1"
    assert project.project_id == "p1"


def test_project_without_a_git_repository_is_accepted() -> None:
    """需求 P1-AC25：不是 Git 仓库不构成拒绝理由；绑定形态由绑定决定。"""
    project = create_project(
        project_id="p1",
        workspace_id="ws-1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
    )
    assert project.modules == ()


def test_revising_a_project_produces_a_new_revision() -> None:
    project = create_project(
        project_id="p1",
        workspace_id="ws-1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
    )
    revised = revise_project(project, name="demo v2")
    assert revised.revision == 2
    assert revised.name == "demo v2"
    assert project.revision == 1
    assert project.name == "demo"


# ------------------------------------------------------------------ 路径


@pytest.mark.parametrize(
    "path",
    [r"C:\work\project", r"D:\a\b", "C:/work/project"],
)
def test_canonical_absolute_paths_are_accepted(path: str) -> None:
    """按 Windows 路径语义判定：`Path` 在 Linux 上会把 `C:\\work` 当相对路径。"""
    assert path_is_canonical_absolute(path) is True


@pytest.mark.parametrize(
    "path",
    ["", "   ", r"work\project", r"C:\work\..\escape", "/work/project"],
)
def test_non_canonical_paths_are_rejected(path: str) -> None:
    assert path_is_canonical_absolute(path) is False


def test_single_dot_segments_are_normalised_by_the_path_type() -> None:
    """说明一个容易误解的点：`PureWindowsPath` 会把 `.` 段规范化掉，
    所以 ``C:\\work\\.\\here`` 与 ``C:\\work\\here`` 是同一个规范路径。"""
    assert path_is_canonical_absolute(r"C:\work\.\here") is True
    assert PureWindowsPath(r"C:\work\.\here") == PureWindowsPath(r"C:\work\here")


def test_binding_rejects_a_non_canonical_path() -> None:
    with pytest.raises(ValueError, match="canonical absolute"):
        create_binding(_git_inputs(canonical_path=r"C:\work\..\escape"))


# ------------------------------------------------------------------ 绑定


def test_git_binding_requires_repository_branch_and_base_commit() -> None:
    for missing in ("repository_id", "branch", "base_commit"):
        result = create_binding(_git_inputs(**{missing: None}))
        assert result.binding is None
        assert any(missing in gap.detail for gap in result.gaps)
        assert blocking_gaps(result.gaps) == result.gaps


def test_plain_binding_requires_a_manifest_digest() -> None:
    result = create_binding(_plain_inputs(manifest_digest=None))
    assert result.binding is None
    assert result.gaps[0].kind == GAP_MISSING_ENVIRONMENT_CARRIER
    assert "manifest digest" in result.gaps[0].detail


def test_plain_binding_is_a_first_class_binding() -> None:
    result = create_binding(_plain_inputs())
    assert result.binding is not None
    assert result.binding.is_plain is True
    assert result.binding.manifest_digest == "sha256:manifest"


def test_git_binding_is_a_first_class_binding() -> None:
    result = create_binding(_git_inputs())
    assert result.binding is not None
    assert result.binding.is_git is True


def test_network_drive_is_rejected_with_a_reason() -> None:
    result = create_binding(_git_inputs(drive_kind=DriveKind.NETWORK))
    assert result.binding is None
    assert result.rejection is WorkspaceLocationRejection.NETWORK_DRIVE
    assert result.gaps == ()


def test_sync_folder_is_rejected_with_a_reason() -> None:
    result = create_binding(_git_inputs(drive_kind=DriveKind.SYNC_FOLDER))
    assert result.binding is None
    assert result.rejection is WorkspaceLocationRejection.SYNC_FOLDER


def test_unknown_drive_kind_is_not_rejected_but_warned() -> None:
    """`UNKNOWN` 不由领域层擅自拒绝：产出**非阻塞**提示，由调用方决定。"""
    result = create_binding(_git_inputs(drive_kind=DriveKind.UNKNOWN))
    assert result.binding is not None
    assert [gap.kind for gap in result.gaps] == [WARN_UNVERIFIED_DRIVE_KIND]
    assert blocking_gaps(result.gaps) == ()


def test_a_fixed_drive_produces_no_gaps() -> None:
    result = create_binding(_git_inputs())
    assert result.gaps == ()


def test_moving_a_directory_creates_a_new_binding_revision() -> None:
    first = create_binding(_git_inputs()).binding
    assert first is not None
    moved = move_binding(first, canonical_path=r"D:\moved\project", drive_kind=DriveKind.FIXED)
    assert moved.binding is not None
    assert moved.binding.binding_revision == 2
    assert moved.binding.project_id == first.project_id
    assert moved.binding.binding_id == first.binding_id
    assert first.binding_revision == 1


def test_binding_result_cannot_be_both_successful_and_rejected() -> None:
    from aitest.application.project.context import BindingResult

    with pytest.raises(ValueError, match="must not produce a binding"):
        BindingResult(
            binding=create_binding(_git_inputs()).binding,
            rejection=WorkspaceLocationRejection.NETWORK_DRIVE,
        )
    with pytest.raises(ValueError, match="must produce a binding"):
        BindingResult(binding=None)


# ------------------------------------------------------------------ 环境


def test_environment_is_created_with_carriers() -> None:
    result = create_environment(_environment())
    assert result.environment is not None
    assert result.environment.interpreter_requirement == "python>=3.13,<3.14"
    assert result.gaps == ()


def test_missing_interpreter_carrier_blocks() -> None:
    result = create_environment(_environment(interpreter_requirement=None))
    assert result.environment is None
    assert result.gaps[0].kind == GAP_MISSING_ENVIRONMENT_CARRIER
    assert "interpreter" in result.gaps[0].detail


def test_missing_dependency_carrier_blocks() -> None:
    result = create_environment(_environment(dependency_declaration="   "))
    assert result.environment is None
    assert "dependency carrier" in result.gaps[0].detail


def test_both_carriers_missing_produces_two_gaps() -> None:
    result = create_environment(
        _environment(interpreter_requirement=None, dependency_declaration=None)
    )
    assert len(result.gaps) == 2


def test_explicit_non_isolation_is_a_legal_fact() -> None:
    """显式不隔离是合法取值，不等于缺配置，也不据此降级。"""
    result = create_environment(
        _environment(
            isolation_mode=IsolationMode.NONE,
            isolation_confirmed=True,
            data_isolated=None,
        )
    )
    assert result.environment is not None
    assert result.environment.isolation_is_explicitly_disabled is True
    assert result.environment.data_isolated is None


def test_non_default_isolation_requires_explicit_confirmation() -> None:
    with pytest.raises(ValueError, match="explicitly confirmed"):
        create_environment(
            _environment(isolation_mode=IsolationMode.UNMANAGED, isolation_confirmed=False)
        )


def test_secret_refs_are_per_purpose_and_carry_no_body() -> None:
    result = create_environment(
        _environment(
            secret_refs=(
                SecretRef(env_key="MODEL_API_KEY", purpose="model"),
                SecretRef(env_key="HTTP_TOKEN", purpose="http"),
            )
        )
    )
    assert result.environment is not None
    purposes = {ref.purpose for ref in result.environment.secret_refs}
    assert purposes == {"model", "http"}


# ------------------------------------------------------------------ 依赖图


def test_graph_is_the_only_authority_for_dependencies() -> None:
    graph = register_graph(
        project_id="p1",
        modules=(_module("a"), _module("b")),
        dependencies=(
            Dependency(consumer_module_id="a", provider_module_id="b"),
        ),
    )
    assert graph.provider_ids() == {"a": ("b",)}


def test_cycles_are_allowed_and_self_loops_rejected() -> None:
    graph = register_graph(
        project_id="p1",
        modules=(_module("a"), _module("b")),
        dependencies=(
            Dependency(consumer_module_id="a", provider_module_id="b"),
            Dependency(consumer_module_id="b", provider_module_id="a"),
        ),
    )
    assert len(graph.dependencies) == 2

    with pytest.raises(ValueError, match="must not depend on itself"):
        Dependency(consumer_module_id="a", provider_module_id="a")


def test_inferred_dependency_must_declare_its_source() -> None:
    with pytest.raises(ValueError, match="requires a source"):
        Dependency(
            consumer_module_id="a",
            provider_module_id="b",
            origin=DependencyOrigin.INFERRED,
        )
    inferred = Dependency(
        consumer_module_id="a",
        provider_module_id="b",
        origin=DependencyOrigin.INFERRED,
        source="static import scan",
    )
    assert inferred.is_inferred is True


def test_dangling_dependency_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown module"):
        register_graph(
            project_id="p1",
            modules=(_module("a"),),
            dependencies=(Dependency(consumer_module_id="a", provider_module_id="ghost"),),
        )


# ------------------------------------------------------------------ 缺口检测


def _project_with_modules() -> object:
    return create_project(
        project_id="p1",
        workspace_id="ws-1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
        modules=(_module("m1"),),
    )


def test_project_without_modules_is_a_blocking_gap() -> None:
    project = create_project(
        project_id="p1",
        workspace_id="ws-1",
        name="demo",
        goal="verify",
        created_at_commit="commit-1",
    )
    gaps = detect_context_gaps(
        project=project,
        graph=register_graph(project_id="p1", modules=(), dependencies=()),
        environment=None,
    )
    assert GAP_NO_MODULES in {gap.kind for gap in gaps}


def test_modules_without_any_dependency_edge_is_a_blocking_gap() -> None:
    """关键：**"映射缺失不能解释为没有影响"**，所以这是阻塞缺口而不是"无依赖"。"""
    project = _project_with_modules()
    graph = register_graph(project_id="p1", modules=(_module("m1"),), dependencies=())
    gaps = detect_context_gaps(project=project, graph=graph, environment=None)  # type: ignore[arg-type]
    assert GAP_MISSING_DEPENDENCY_REGISTRATION in {gap.kind for gap in gaps}


def test_registered_dependency_edge_removes_that_gap() -> None:
    project = _project_with_modules()
    environment = create_environment(_environment()).environment
    graph = register_graph(
        project_id="p1",
        modules=(_module("m1"), _module("m2")),
        dependencies=(Dependency(consumer_module_id="m1", provider_module_id="m2"),),
    )
    gaps = detect_context_gaps(project=project, graph=graph, environment=environment)  # type: ignore[arg-type]
    assert GAP_MISSING_DEPENDENCY_REGISTRATION not in {gap.kind for gap in gaps}


def test_missing_environment_is_a_blocking_gap() -> None:
    project = _project_with_modules()
    graph = register_graph(project_id="p1", modules=(_module("m1"),), dependencies=())
    gaps = detect_context_gaps(project=project, graph=graph, environment=None)  # type: ignore[arg-type]
    assert GAP_MISSING_ENVIRONMENT_CARRIER in {gap.kind for gap in gaps}
    assert blocking_gaps(gaps) == gaps


def test_complete_context_has_no_blocking_gaps() -> None:
    project = _project_with_modules()
    environment = create_environment(_environment()).environment
    graph = register_graph(
        project_id="p1",
        modules=(_module("m1"), _module("m2")),
        dependencies=(Dependency(consumer_module_id="m1", provider_module_id="m2"),),
    )
    gaps = detect_context_gaps(
        project=project,  # type: ignore[arg-type]
        graph=graph,
        environment=environment,
        drive_kind=DriveKind.FIXED,
    )
    assert gaps == ()


def test_unknown_drive_kind_yields_only_a_non_blocking_warning() -> None:
    project = _project_with_modules()
    environment = create_environment(_environment()).environment
    graph = register_graph(
        project_id="p1",
        modules=(_module("m1"), _module("m2")),
        dependencies=(Dependency(consumer_module_id="m1", provider_module_id="m2"),),
    )
    gaps = detect_context_gaps(
        project=project,  # type: ignore[arg-type]
        graph=graph,
        environment=environment,
        drive_kind=DriveKind.UNKNOWN,
    )
    assert [gap.kind for gap in gaps] == [WARN_UNVERIFIED_DRIVE_KIND]
    assert blocking_gaps(gaps) == ()


def test_gap_requires_a_subject_and_detail() -> None:
    with pytest.raises(ValueError, match="subject"):
        ContextGap(kind=GAP_NO_MODULES, subject=" ", detail="x")
    with pytest.raises(ValueError, match="detail"):
        ContextGap(kind=GAP_NO_MODULES, subject="p1", detail="  ")


def test_a_blocking_kind_cannot_declare_itself_non_blocking() -> None:
    with pytest.raises(ValueError, match="blocking gap by definition"):
        ContextGap(
            kind=GAP_MISSING_ENVIRONMENT_CARRIER,
            subject="p1",
            detail="x",
            blocking=False,
        )
