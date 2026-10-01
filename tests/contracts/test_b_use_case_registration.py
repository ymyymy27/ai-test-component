"""B 侧用例注册表的合同测试：用**真实 Command + 真实文件底座**驱动统一入口。

覆盖三件事，缺一不可：

1. **形状**：`build_b_use_case_registry()` 的 handler 能被 `contracts.Command` 调用
   （`Handler = Callable[[Command], Mapping]`），且错误码能透到 `Response.error.code`；
2. **真落盘**：经 `LocalAPI.dispatch()` 写入后，能按准确修订读回；重新构造底座仍读得到；
3. **边界**：只读动作不要求写身份；写动作缺参数时给**结构化**错误码而不是 `INTERNAL_ERROR`。

不使用 `tmp_path`：受限执行环境拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举
（与 `tests/unit/test_substrate_adapter.py` 同一原因）。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pydantic import JsonValue

from aitest.application.planning.substrate import AggregateKind
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
from aitest.application.project.serialization import (
    binding_to_payload,
    dependency_graph_to_payload,
    environment_to_payload,
    project_to_payload,
)
from aitest.application.usecase_registry import (
    OWNED_ACTIONS,
    BUseCaseDependencies,
)
from aitest.contracts.commands import Command
from aitest.domain.project.context import (
    BindingForm,
    Dependency,
    DriveKind,
    IsolationMode,
    Module,
    SecretRef,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.b_registration import (
    b_registration_for,
    register_b_use_cases,
)

PROJECT_ID = "project-registry"
WORKSPACE_ID = "ws-registry"
BREAKDOWN = "req-breakdown"
BREAKDOWN_2 = "req-breakdown-2"
BREAKDOWN_3 = "req-breakdown-3"
BREAKDOWN_4 = "req-breakdown-4"
BREAKDOWN_READ = "req-breakdown-read"
BREAKDOWN_CONFLICT = "req-breakdown-conflict"
BREAKDOWN_BAD = "req-breakdown-bad"
BREAKDOWN_MISMATCH = "req-breakdown-mismatch"
BREAKDOWN_MISSING = "req-breakdown-missing"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


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


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) / f"aitest-registry-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _session() -> Session:
    return Session(session_id="cli-registry", entry_kind=EntryKind.HUMAN_UI)


def _deps(stack: _Stack) -> BUseCaseDependencies:
    return BUseCaseDependencies(
        unit_of_work=stack.unit_of_work,
        reader=stack.reader,
        clock=_FixedClock(),
    )


def _api(stack: _Stack) -> LocalAPI:
    api = LocalAPI(instance_id="core-registry", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(api, _deps(stack))
    return api


# ------------------------------------------------------------------ 夹具数据


def _modules() -> tuple[Module, ...]:
    return (
        Module(
            module_id="module-ticket",
            project_id=PROJECT_ID,
            name="ticket core",
            responsibility="create and read tickets",
            interface_note="registered HTTP handlers",
            source_paths=("src/ticket/**",),
        ),
        Module(
            module_id="module-store",
            project_id=PROJECT_ID,
            name="ticket store",
            responsibility="persist tickets",
            interface_note="internal repository",
        ),
    )


def _project_payload() -> Mapping[str, object]:
    project = create_project(
        project_id=PROJECT_ID,
        workspace_id=WORKSPACE_ID,
        name="ticket service",
        goal="verify ticket creation",
        created_at_commit="commit-0",
        modules=_modules(),
    )
    return dict(project_to_payload(project))


def _binding_payload() -> Mapping[str, object]:
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
    return dict(binding_to_payload(result.binding))


def _environment_payload() -> Mapping[str, object]:
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
    return dict(environment_to_payload(result.environment, project_id=PROJECT_ID))


def _graph_payload() -> Mapping[str, object]:
    graph = register_graph(
        project_id=PROJECT_ID,
        modules=_modules(),
        dependencies=(
            Dependency(
                consumer_module_id="module-ticket",
                provider_module_id="module-store",
            ),
        ),
    )
    return dict(dependency_graph_to_payload(graph))


def _write_command(
    *,
    action: str,
    request_id: str,
    parameters: Mapping[str, object],
    expected_revision: int = 0,
) -> Command:
    return Command(
        request_id=request_id,
        action=action,
        project_id=PROJECT_ID,
        expected_revision=expected_revision,
        intent_id=f"intent-{request_id}",
        parameters=cast("dict[str, JsonValue]", dict(parameters)),
    )


# ------------------------------------------------------------------ 1 形状


def test_registry_exposes_exactly_the_owned_actions() -> None:
    """动作表与 `OWNED_ACTIONS` 必须逐字一致，防止"多注册一个动作没人知道"。"""
    root = Path(tempfile.gettempdir()) / f"aitest-registry-shape-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        registration = b_registration_for(_deps(_start(root)))
        assert set(registration) == set(OWNED_ACTIONS)
        assert all(callable(handler) for handler in registration.values())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_duplicate_registration_is_rejected(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    with pytest.raises(ValueError, match="already registered"):
        register_b_use_cases(api, _deps(_start(workspace_root)))


def test_doctor_reports_the_b_actions(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    response = api.dispatch(
        Command(request_id="req-doctor", action="doctor"), _session()
    )
    assert response.error is None
    assert response.result is not None
    supported = response.result["supported_actions"]
    assert isinstance(supported, list)
    assert set(OWNED_ACTIONS).issubset({str(item) for item in supported})


def test_action_without_a_handler_is_reported_as_unavailable(workspace_root: Path) -> None:
    """未注册的动作必须明确报"不支持"，不得静默成功。"""
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _write_command(
            action="start_run",
            request_id=BREAKDOWN_MISSING,
            parameters={},
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "CAPABILITY_UNAVAILABLE"


# ------------------------------------------------------------------ 2 真落盘


def test_context_writes_land_on_real_storage_and_read_back(workspace_root: Path) -> None:
    stack = _start(workspace_root)
    api = _api(stack)

    writes = (
        ("save_context", BREAKDOWN, {"project": _project_payload()}),
        ("save_binding", BREAKDOWN_2, {"binding": _binding_payload()}),
        ("save_environment", BREAKDOWN_3, {"environment": _environment_payload()}),
        ("save_dependency_graph", BREAKDOWN_4, {"dependency_graph": _graph_payload()}),
    )
    for action, request_id, parameters in writes:
        response = api.dispatch(
            _write_command(action=action, request_id=request_id, parameters=parameters),
            _session(),
        )
        assert response.error is None, (action, response.error)
        assert response.result is not None
        assert response.result["revision"] == 1

    # 重启：重新构造一整套底座对象，复用同一目录。
    restarted = _start(workspace_root)
    aggregates: tuple[tuple[AggregateKind, str], ...] = (
        ("project", PROJECT_ID),
        ("binding", "binding-1"),
        ("environment", "env-local"),
        ("dependency_set", f"graph:{PROJECT_ID}"),
    )
    for aggregate_kind, record_id in aggregates:
        committed = restarted.reader.read(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=1
        )
        assert committed.revision == 1


def test_query_reports_missing_index_instead_of_succeeding_with_nothing(
    workspace_root: Path,
) -> None:
    """索引缺失时，`query` 必须报"待重建"，**不能**返回空列表伪装成"没有数据"。

    依据：`AB-001` 关于索引缺失须显式维护的约定，以及
    "不把未知状态静默解释为成功或空结果"的规则。
    """
    api = _api(_start(workspace_root))
    response = api.dispatch(
        Command(
            request_id=BREAKDOWN_READ,
            action="query",
            project_id=PROJECT_ID,
            parameters={"aggregate_kind": "project"},
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INDEX_MAINTENANCE_REQUIRED"


# ------------------------------------------------------------------ 3 边界与错误码


def test_revision_conflict_is_reported_with_its_own_code(workspace_root: Path) -> None:
    """同一个 `expected_revision` 写两次：第二次是修订冲突，不是 `INTERNAL_ERROR`。"""
    api = _api(_start(workspace_root))
    first = api.dispatch(
        _write_command(
            action="save_context",
            request_id=BREAKDOWN,
            parameters={"project": _project_payload()},
        ),
        _session(),
    )
    assert first.error is None, first.error
    second = api.dispatch(
        _write_command(
            action="save_context",
            request_id=BREAKDOWN_CONFLICT,
            parameters={"project": _project_payload()},
        ),
        _session(),
    )
    assert second.error is not None
    assert second.error.code == "B_REVISION_CONFLICT"


def test_invalid_parameters_get_a_structured_error(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _write_command(
            action="save_context",
            request_id=BREAKDOWN_BAD,
            parameters={"project": "not-an-object"},
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"


def test_dependency_graph_for_another_project_is_rejected(workspace_root: Path) -> None:
    api = _api(_start(workspace_root))
    payload = dict(_graph_payload())
    payload["project_id"] = "project-other"
    response = api.dispatch(
        _write_command(
            action="save_dependency_graph",
            request_id=BREAKDOWN_MISMATCH,
            parameters={"dependency_graph": payload},
        ),
        _session(),
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"


def test_write_without_project_scope_is_rejected_by_the_contract(
    workspace_root: Path,
) -> None:
    """写动作缺 `project_id` 时，`Command` 自身就该拒绝，不会进到 handler。"""
    with pytest.raises(ValueError):
        Command(
            request_id=BREAKDOWN_MISSING,
            action="save_context",
            expected_revision=0,
            intent_id="intent-missing",
            parameters={"project": cast(JsonValue, _project_payload())},
        )
