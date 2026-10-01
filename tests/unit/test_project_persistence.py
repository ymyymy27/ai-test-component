"""项目上下文的落盘编排：写入、按准确修订读回、不自动覆盖。

依据：架构文档《01-项目与计划》第 7、8 节；实施方案第 3 节
（"所有 `read_*` 必须接受显式修订参数，不得默默回退到最新值"）。

底座用 `tests/support/memory_substrate.py`；真实文件存储上的同名回归
需要 A 的正式端口签名落地后接入（见 `AB-001` 第 8.8 节）。
"""

from __future__ import annotations

import pytest

from aitest.application.planning.substrate import ConcurrentEditError
from aitest.application.project.context import (
    BindingInputs,
    EnvironmentInputs,
    create_binding,
    create_environment,
    create_project,
    register_graph,
    revise_project,
)
from aitest.application.project.persistence import (
    dependency_graph_record_id,
    load_binding,
    load_dependency_graph,
    load_environment,
    load_project,
    save_binding,
    save_dependency_graph,
    save_environment,
    save_project,
)
from aitest.domain.project.context import (
    BindingForm,
    Dependency,
    DependencyOrigin,
    DriveKind,
    ImplementationStatus,
    IsolationMode,
    Module,
    SecretRef,
)
from tests.support.memory_substrate import MemoryReader, MemoryStore, MemoryUnitOfWork

PROJECT_ID = "project-ticket"
WORKSPACE_ID = "ws-ticket"


def _world() -> tuple[MemoryUnitOfWork, MemoryReader]:
    store = MemoryStore()
    return MemoryUnitOfWork(store), MemoryReader(store)


def _modules() -> tuple[Module, Module]:
    return (
        Module(
            module_id="module-ticket",
            project_id=PROJECT_ID,
            name="ticket core",
            responsibility="create and read tickets",
            interface_note="registered HTTP handlers",
            inputs=("ticket payload",),
            outputs=("ticket id",),
            implementation_status=ImplementationStatus.COMPLETE,
        ),
        Module(
            module_id="module-store",
            project_id=PROJECT_ID,
            name="ticket store",
            responsibility="persist tickets",
            interface_note="internal repository",
        ),
    )


def _project() -> object:
    return create_project(
        project_id=PROJECT_ID,
        workspace_id=WORKSPACE_ID,
        name="ticket service",
        goal="verify ticket creation",
        created_at_commit="commit-0",
        modules=_modules(),
    )


def _binding(binding_form: BindingForm = BindingForm.GIT) -> object:
    is_git = binding_form is BindingForm.GIT
    result = create_binding(
        BindingInputs(
            binding_id="binding-1",
            project_id=PROJECT_ID,
            canonical_path=r"C:\work\ticket",
            binding_form=binding_form,
            drive_kind=DriveKind.FIXED,
            binding_revision=1,
            repository_id="origin" if is_git else None,
            branch="main" if is_git else None,
            base_commit="9c44344bcd612df1a7d033efa1e7a47c810c49cf" if is_git else None,
            manifest_digest=None if is_git else "sha256:manifest-1",
            local_owner="feix-a",
            confirmed=True,
        )
    )
    assert result.binding is not None
    return result.binding


def _environment() -> object:
    result = create_environment(
        EnvironmentInputs(
            environment_id="env-local",
            interpreter_requirement="python>=3.13,<3.14",
            dependency_declaration="uv.lock",
            isolation_mode=IsolationMode.VENV,
            data_isolated=True,
            data_reset_policy="drop and recreate the local table",
            target_deployment_identity="local-service",
            request_timeout_seconds=30,
            step_timeout_seconds=120,
            network_targets=("http://127.0.0.1:8080",),
            secret_refs=(SecretRef(env_key="MODEL_API_KEY", purpose="model"),),
        )
    )
    assert result.environment is not None
    return result.environment


def _graph() -> object:
    return register_graph(
        project_id=PROJECT_ID,
        modules=_modules(),
        dependencies=(
            Dependency(
                consumer_module_id="module-ticket",
                provider_module_id="module-store",
                origin=DependencyOrigin.REGISTERED,
            ),
        ),
    )


# ------------------------------------------------------------------ 往返


def test_project_round_trips_through_the_store() -> None:
    unit_of_work, reader = _world()
    project = _project()
    save_project(project, unit_of_work=unit_of_work)  # type: ignore[arg-type]

    loaded = load_project(reader, project_id=PROJECT_ID, revision=1)
    assert loaded == project
    assert [module.module_id for module in loaded.modules] == [
        "module-ticket",
        "module-store",
    ]


def test_git_binding_round_trips_and_keeps_repository_fields() -> None:
    unit_of_work, reader = _world()
    binding = _binding(BindingForm.GIT)
    save_binding(binding, unit_of_work=unit_of_work)  # type: ignore[arg-type]

    loaded = load_binding(
        reader, project_id=PROJECT_ID, binding_id="binding-1", revision=1
    )
    assert loaded == binding
    assert loaded.repository_id == "origin"


def test_plain_binding_round_trips_without_repository_keys() -> None:
    """需求 P1-AC25：`plain` 形态**不出现**仓库/分支/提交，也不是空值占位。"""
    unit_of_work, reader = _world()
    binding = _binding(BindingForm.PLAIN)
    save_binding(binding, unit_of_work=unit_of_work)  # type: ignore[arg-type]

    record = reader.read(aggregate_kind="binding", record_id="binding-1", revision=1)
    for key in ("repository_id", "branch", "base_commit"):
        assert key not in record.payload
    assert record.payload["manifest_digest"] == "sha256:manifest-1"

    loaded = load_binding(
        reader, project_id=PROJECT_ID, binding_id="binding-1", revision=1
    )
    assert loaded == binding


def test_environment_round_trips_including_secret_references() -> None:
    """凭据只落**引用**，不落正文。"""
    unit_of_work, reader = _world()
    environment = _environment()
    save_environment(
        environment, project_id=PROJECT_ID, unit_of_work=unit_of_work  # type: ignore[arg-type]
    )

    loaded = load_environment(
        reader, project_id=PROJECT_ID, environment_id="env-local", revision=1
    )
    assert loaded == environment
    assert loaded.secret_refs[0].env_key == "MODEL_API_KEY"
    assert loaded.secret_refs[0].ref is None


def test_environment_omits_unset_optional_keys() -> None:
    """可选字段未登记时**省略键**，不写 `null`。"""
    unit_of_work, reader = _world()
    environment = create_environment(
        EnvironmentInputs(
            environment_id="env-plain",
            interpreter_requirement="python>=3.13",
            dependency_declaration="uv.lock",
            isolation_mode=IsolationMode.VENV,
        )
    ).environment
    assert environment is not None
    save_environment(
        environment, project_id=PROJECT_ID, unit_of_work=unit_of_work
    )

    record = reader.read(
        aggregate_kind="environment", record_id="env-plain", revision=1
    )
    for key in (
        "data_isolated",
        "data_reset_policy",
        "target_deployment_identity",
        "request_timeout_seconds",
        "step_timeout_seconds",
        "revision_note",
    ):
        assert key not in record.payload

    loaded = load_environment(
        reader, project_id=PROJECT_ID, environment_id="env-plain", revision=1
    )
    assert loaded == environment


def test_dependency_graph_round_trips() -> None:
    unit_of_work, reader = _world()
    graph = _graph()
    save_dependency_graph(graph, unit_of_work=unit_of_work)  # type: ignore[arg-type]

    loaded = load_dependency_graph(reader, project_id=PROJECT_ID, revision=1)
    assert loaded == graph
    assert loaded.dependencies[0].provider_module_id == "module-store"


def test_dependency_graph_uses_a_project_scoped_record_id() -> None:
    assert dependency_graph_record_id(PROJECT_ID) == f"graph:{PROJECT_ID}"


# ------------------------------------------------------------------ 不自动覆盖


def test_saving_an_existing_project_as_new_conflicts_with_the_current_revision() -> None:
    unit_of_work, reader = _world()
    save_project(_project(), unit_of_work=unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(ConcurrentEditError) as error:
        save_project(_project(), unit_of_work=unit_of_work)  # type: ignore[arg-type]
    assert error.value.expected_revision is None
    assert error.value.current_revision == 1


def test_a_new_revision_is_appended_when_the_seen_revision_is_passed() -> None:
    """`revise_project` 产出新对象；把**看到过的修订**传下去才允许追加。"""
    unit_of_work, reader = _world()
    project = _project()
    save_project(project, unit_of_work=unit_of_work)  # type: ignore[arg-type]

    revised = revise_project(project, goal="verify ticket creation and status change")  # type: ignore[arg-type]
    assert revised.revision == 2
    save_project(revised, unit_of_work=unit_of_work, expected_revision=1)

    assert (
        load_project(reader, project_id=PROJECT_ID, revision=2).goal
        == "verify ticket creation and status change"
    )
    # 旧修订原样保留，不被覆盖。
    assert load_project(reader, project_id=PROJECT_ID, revision=1).goal == (
        "verify ticket creation"
    )


def test_stale_expected_revision_is_rejected() -> None:
    """拿着过期修订再写入必须被拒，**不覆盖**别人已经写下的修订。"""
    unit_of_work, reader = _world()
    project = _project()
    save_project(project, unit_of_work=unit_of_work)  # type: ignore[arg-type]
    save_project(
        revise_project(project, goal="second"),  # type: ignore[arg-type]
        unit_of_work=unit_of_work,
        expected_revision=1,
    )

    with pytest.raises(ConcurrentEditError) as error:
        save_project(
            revise_project(project, goal="third"),  # type: ignore[arg-type]
            unit_of_work=unit_of_work,
            expected_revision=1,
        )
    assert error.value.current_revision == 2


# ------------------------------------------------------------------ 读侧约束


def test_reading_a_missing_revision_is_explicit() -> None:
    unit_of_work, reader = _world()
    save_project(_project(), unit_of_work=unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="unknown revision"):
        load_project(reader, project_id=PROJECT_ID, revision=2)


def test_a_record_from_another_project_is_not_returned() -> None:
    """项目范围必须核对：跨项目的记录不得被当成同一条读到。

    记录标识在存储层是**全局**的，所以"同 id、不同项目"是可达的读错路径。
    """
    unit_of_work, reader = _world()
    save_environment(
        _environment(), project_id=PROJECT_ID, unit_of_work=unit_of_work  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="another project"):
        load_environment(
            reader, project_id="project-other", environment_id="env-local", revision=1
        )


def test_environment_requires_a_project_scope() -> None:
    unit_of_work, _ = _world()
    with pytest.raises(ValueError, match="project_id"):
        save_environment(
            _environment(), project_id="  ", unit_of_work=unit_of_work  # type: ignore[arg-type]
        )


def test_each_save_uses_its_own_short_transaction() -> None:
    """一次 `save_*` 一次提交；序号可用于确认落盘顺序。"""
    unit_of_work, _ = _world()
    assert unit_of_work.commit_seq() == "commit-0"
    save_project(_project(), unit_of_work=unit_of_work)  # type: ignore[arg-type]
    assert unit_of_work.commit_seq() == "commit-1"
    save_binding(_binding(), unit_of_work=unit_of_work)  # type: ignore[arg-type]
    assert unit_of_work.commit_seq() == "commit-2"
    save_environment(
        _environment(), project_id=PROJECT_ID, unit_of_work=unit_of_work  # type: ignore[arg-type]
    )
    assert unit_of_work.commit_seq() == "commit-3"
    save_dependency_graph(_graph(), unit_of_work=unit_of_work)  # type: ignore[arg-type]
    assert unit_of_work.commit_seq() == "commit-4"
