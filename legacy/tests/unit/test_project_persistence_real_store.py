"""项目上下文落盘在 **A 的真实文件存储** 上的回归。

`tests/unit/test_project_persistence.py` 用内存替身验证规则；本模块验证同一套编排
经 `PortsUnitOfWork` / `PortsRecordReader` 落到真实文件后仍可**重启读回**。
"重启"由"重新构造一整套底座对象、复用同一目录"模拟。

`save_*` 只用 `open` / `stage_record` / `commit`，不读提交序号，
因此这里不需要 `CommitSequenceSource`。

不使用 `tmp_path`：受限执行环境拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举，
也拒绝写入 `tempfile.mkdtemp()` 建出来的目录（同一目录改用 `Path.mkdir()` 就能写）。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.project.context import (
    BindingInputs,
    EnvironmentInputs,
    create_binding,
    create_environment,
    create_project,
    register_graph,
)
from aitest.application.project.persistence import (
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
    DriveKind,
    EnvironmentRef,
    IsolationMode,
    LocalProject,
    LocalProjectBinding,
    Module,
    ModuleDependencyGraph,
    SecretRef,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

PROJECT_ID = "project-ticket"
WORKSPACE_ID = "ws-ticket"


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    """真实临时目录：`Path.mkdir()` 建、结束时清理。"""
    root = Path(tempfile.gettempdir()) / f"aitest-persist-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class _Stack:
    """一次"进程启动"：真实文件底座 + 转接头。重新构造即等于重启。"""

    unit_of_work: PortsUnitOfWork
    reader: PortsRecordReader


def _start(root: Path) -> _Stack:
    raw = FileUnitOfWork(root)
    repository = raw.repo
    return _Stack(
        unit_of_work=PortsUnitOfWork(raw, repository=repository),
        reader=PortsRecordReader(repository),
    )


def _modules() -> tuple[Module, ...]:
    return (
        Module(
            module_id="module-ticket",
            project_id=PROJECT_ID,
            name="ticket core",
            responsibility="create and read tickets",
            interface_note="registered HTTP handlers",
        ),
        Module(
            module_id="module-store",
            project_id=PROJECT_ID,
            name="ticket store",
            responsibility="persist tickets",
            interface_note="internal repository",
        ),
    )


def _project() -> LocalProject:
    return create_project(
        project_id=PROJECT_ID,
        workspace_id=WORKSPACE_ID,
        name="ticket service",
        goal="verify ticket creation",
        created_at_commit="commit-0",
        modules=_modules(),
    )


def _binding() -> LocalProjectBinding:
    result = create_binding(
        BindingInputs(
            binding_id="binding-1",
            project_id=PROJECT_ID,
            canonical_path=r"C:\work\ticket",
            binding_form=BindingForm.GIT,
            drive_kind=DriveKind.FIXED,
            binding_revision=1,
            repository_id="origin",
            branch="main",
            base_commit="9c44344bcd612df1a7d033efa1e7a47c810c49cf",
            local_owner="feix-a",
            confirmed=True,
        )
    )
    assert result.binding is not None
    return result.binding


def _environment() -> EnvironmentRef:
    result = create_environment(
        EnvironmentInputs(
            environment_id="env-local",
            interpreter_requirement="python>=3.13,<3.14",
            dependency_declaration="uv.lock",
            isolation_mode=IsolationMode.VENV,
            secret_refs=(SecretRef(env_key="MODEL_API_KEY", purpose="model"),),
        )
    )
    assert result.environment is not None
    return result.environment


def _graph() -> ModuleDependencyGraph:
    return register_graph(
        project_id=PROJECT_ID,
        modules=_modules(),
        dependencies=(
            Dependency(
                consumer_module_id="module-ticket",
                provider_module_id="module-store",
            ),
        ),
    )


def test_context_objects_survive_a_restart_on_real_storage(
    workspace_root: Path,
) -> None:
    """四类上下文对象落盘后，重新构造底座仍能按准确修订读回。"""
    first = _start(workspace_root)
    project = _project()
    binding = _binding()
    environment = _environment()
    graph = _graph()

    save_project(project, unit_of_work=first.unit_of_work)
    save_binding(binding, unit_of_work=first.unit_of_work)
    save_environment(
        environment, project_id=PROJECT_ID, unit_of_work=first.unit_of_work
    )
    save_dependency_graph(graph, unit_of_work=first.unit_of_work)

    restarted = _start(workspace_root)
    assert load_project(restarted.reader, project_id=PROJECT_ID, revision=1) == project
    assert (
        load_binding(
            restarted.reader, project_id=PROJECT_ID, binding_id="binding-1", revision=1
        )
        == binding
    )
    assert (
        load_environment(
            restarted.reader,
            project_id=PROJECT_ID,
            environment_id="env-local",
            revision=1,
        )
        == environment
    )
    assert load_dependency_graph(restarted.reader, project_id=PROJECT_ID, revision=1) == graph


def test_a_new_revision_survives_a_restart(workspace_root: Path) -> None:
    """追加修订后重启：新旧两个修订都在，历史不被覆盖。"""
    first = _start(workspace_root)
    project = _project()
    save_project(project, unit_of_work=first.unit_of_work)

    revised = create_project(
        project_id=PROJECT_ID,
        workspace_id=WORKSPACE_ID,
        name="ticket service",
        goal="verify ticket creation and status change",
        created_at_commit="commit-0",
        revision=2,
        modules=_modules(),
    )
    save_project(
        revised, unit_of_work=first.unit_of_work, expected_revision=1
    )

    restarted = _start(workspace_root)
    assert (
        load_project(restarted.reader, project_id=PROJECT_ID, revision=1).goal
        == "verify ticket creation"
    )
    assert (
        load_project(restarted.reader, project_id=PROJECT_ID, revision=2).goal
        == "verify ticket creation and status change"
    )


def test_real_storage_records_are_queryable_by_project(workspace_root: Path) -> None:
    """按项目范围的有限查询必须能查到这些记录：payload 里得有 `project_id`。"""
    from aitest.application.planning.substrate import RecordQuery

    stack = _start(workspace_root)
    save_project(_project(), unit_of_work=stack.unit_of_work)
    save_environment(
        _environment(), project_id=PROJECT_ID, unit_of_work=stack.unit_of_work
    )

    for kind, expected in (("project", 1), ("environment", 1)):
        page = stack.reader.query(
            RecordQuery(project_id=PROJECT_ID, aggregate_kind=kind)  # type: ignore[arg-type]
        )
        assert len(page.items) == expected, kind


def test_real_storage_rejects_a_stale_expected_revision(workspace_root: Path) -> None:
    """真实存储上同样"不自动覆盖"：过期修订被拒，并带上当前修订。"""
    from aitest.application.planning.substrate import ConcurrentEditError

    stack = _start(workspace_root)
    save_project(_project(), unit_of_work=stack.unit_of_work)

    with pytest.raises(ConcurrentEditError) as error:
        save_project(_project(), unit_of_work=stack.unit_of_work)
    assert error.value.current_revision == 1
    assert error.value.expected_revision is None
