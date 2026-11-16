"""`save_project_view_preference` 出口动作的合同测试（P1-AC34）。

设计草案 §3.4 与功能 §16 只给这一条语义：

    ProjectViewPreference 只记录隐藏/恢复偏好；隐藏**不**删除项目、目录绑定或历史
    引用、**不**停止其运行；恢复只恢复列表可见性，不启动测试。

因此本文件锁定的是"边界"而不是功能量：

1. 只写一条 `project_view_preference`，项目/绑定等记录**一个字节都不动**；
2. 只有**已保存项目**可被隐藏/恢复（不能凭偏好造项目）；
3. 隐藏与恢复各自是一次带乐观锁的修订追加（`0` = 新建）；
4. 严格参数：`hidden` 必须是真布尔，未知参数按名字拒绝；
5. 偏好可经统一 `query` 动作按准确修订读回（列表/恢复的前提）。

不使用 `tmp_path`：受限执行环境拒绝 pytest 临时目录工厂在 basetemp 上的目录枚举。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aitest.application.planning.substrate_adapter import (
    PortsRecordReader,
    PortsUnitOfWork,
)
from aitest.application.usecase_registry import BUseCaseDependencies
from aitest.contracts.commands import Command
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from aitest.interfaces.local.b_registration import register_b_use_cases

PROJECT_ID = "project-visible"
OTHER_PROJECT_ID = "project-hidden"
WORKSPACE_ID = "ws-preference"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 13, 10, 0, tzinfo=UTC)

    def monotonic(self) -> float:
        return 0.0


class _Sequences:
    def __init__(self, root: Path) -> None:
        self._orchestrator = RecoveryOrchestrator(root, instance_id="preference-entry-test")

    def current_commit_sequence(self) -> int:
        committed = self._orchestrator.inspect()["committed_sequences"]
        assert isinstance(committed, tuple)
        return max(committed) if committed else 0


@dataclass(frozen=True, slots=True)
class _Stack:
    unit_of_work: PortsUnitOfWork
    reader: PortsRecordReader


def _start(root: Path) -> _Stack:
    raw = FileUnitOfWork(root)
    repository = raw.repo
    return _Stack(
        unit_of_work=PortsUnitOfWork(raw, repository=repository, sequence=_Sequences(root)),
        reader=PortsRecordReader(repository),
    )


@pytest.fixture
def workspace_root() -> Iterator[Path]:
    root = Path(tempfile.gettempdir()) / f"aitest-preference-{uuid4().hex[:12]}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _api(stack: _Stack) -> LocalAPI:
    api = LocalAPI(instance_id="core-preference", workspace_id=WORKSPACE_ID, handlers={})
    register_b_use_cases(
        api,
        BUseCaseDependencies(
            unit_of_work=stack.unit_of_work,
            reader=stack.reader,
            clock=_FixedClock(),
        ),
    )
    return api


def _session() -> Session:
    return Session(session_id="cli-preference", entry_kind=EntryKind.HUMAN_UI)


def _command(
    request_id: str, parameters: Mapping[str, object], *, project: str = PROJECT_ID,
    expected: int = 0,
) -> Command:
    return Command(
        request_id=request_id,
        action="save_project_view_preference",
        project_id=project,
        expected_revision=expected,
        intent_id=f"intent-{request_id}",
        parameters=dict(parameters),
    )


def _index(workspace_root: Path) -> None:
    FileQueryIndex(workspace_root).rebuild(())


def _save_project(api: LocalAPI, *, project_id: str, request: str) -> None:
    """先用既有 `save_context` 落一个真项目：偏好只能作用于已保存项目。"""

    from aitest.application.project.serialization import project_to_payload
    from aitest.domain.project.context import LocalProject

    project = LocalProject(
        local_project_id=project_id,
        workspace_id=WORKSPACE_ID,
        name="visible preference project",
        goal="check the hidden/restore preference only",
        created_at_commit="commit-1",
    )
    response = api.dispatch(
        Command(
            request_id=request,
            action="save_context",
            project_id=project_id,
            expected_revision=0,
            intent_id=f"intent-{request}",
            parameters={"project": project_to_payload(project)},
        ),
        _session(),
    )
    assert response.error is None, response.error


def test_hide_and_restore_append_revisions_without_touching_anything_else(
    workspace_root: Path,
) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    _save_project(api, project_id=PROJECT_ID, request="req-project")

    hidden = api.dispatch(_command("req-hide", {"hidden": True}), _session())
    assert hidden.error is None, hidden.error
    assert hidden.result is not None
    assert hidden.result["stopped_runs"] is False
    assert hidden.result["deleted"] is False
    saved = hidden.result["saved"]
    assert saved["aggregate_kind"] == "project_view_preference"
    assert saved["record_id"] == PROJECT_ID
    assert saved["revision"] == 1
    assert saved["hidden"] is True

    restored = api.dispatch(_command("req-restore", {"hidden": False}, expected=1), _session())
    assert restored.error is None, restored.error
    assert restored.result is not None
    assert restored.result["saved"]["revision"] == 2
    assert restored.result["saved"]["hidden"] is False

    reader = _start(workspace_root).reader
    # 只写了偏好：项目与绑定记录保持原样（隐藏不删业务数据）。
    assert (
        reader.current_revision(
            aggregate_kind="project_view_preference", record_id=PROJECT_ID
        )
        == 2
    )
    assert reader.read(aggregate_kind="project", record_id=PROJECT_ID, revision=1).revision == 1
    assert reader.current_revision(aggregate_kind="binding", record_id=PROJECT_ID) == 0
    first = reader.read(
        aggregate_kind="project_view_preference", record_id=PROJECT_ID, revision=1
    ).payload
    assert first["schema_version"] == "aitest.project-view-preference/1.0"
    assert first["hidden"] is True
    assert first["project_id"] == PROJECT_ID


def test_preference_is_readable_through_the_shared_query_action(
    workspace_root: Path,
) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    _save_project(api, project_id=PROJECT_ID, request="req-project")
    assert api.dispatch(_command("req-hide", {"hidden": True}), _session()).error is None

    response = api.dispatch(
        Command(
            request_id="req-read",
            action="query",
            project_id=PROJECT_ID,
            parameters={"aggregate_kind": "project_view_preference", "limit": 5},
        ),
        _session(),
    )
    assert response.error is None, response.error
    assert response.result is not None
    items = response.result["items"]
    assert isinstance(items, list) and items
    assert items[0]["aggregate_kind"] == "project_view_preference"
    assert items[0]["record_id"] == PROJECT_ID
    assert items[0]["revision"] == 1


def test_only_a_saved_project_can_be_hidden(workspace_root: Path) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    response = api.dispatch(
        _command("req-hide", {"hidden": True}, project=OTHER_PROJECT_ID), _session()
    )
    assert response.error is not None
    assert response.error.code == "B_INVALID_PARAMETER"
    assert OTHER_PROJECT_ID in response.error.message
    # 未保存项目不能产生任何偏好记录。
    assert (
        _start(workspace_root).reader.current_revision(
            aggregate_kind="project_view_preference", record_id=OTHER_PROJECT_ID
        )
        == 0
    )


def test_hidden_flag_must_be_a_real_boolean(workspace_root: Path) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    _save_project(api, project_id=PROJECT_ID, request="req-project")
    for value in ("yes", 1, None):
        response = api.dispatch(
            _command(f"req-bad-{value}", {"hidden": value}), _session()
        )
        assert response.error is not None, value
        assert response.error.code == "B_INVALID_PARAMETER"
        assert "hidden" in response.error.message


def test_unknown_parameters_and_stale_revisions_are_refused(
    workspace_root: Path,
) -> None:
    _index(workspace_root)
    api = _api(_start(workspace_root))
    _save_project(api, project_id=PROJECT_ID, request="req-project")

    unknown = api.dispatch(
        _command("req-unknown", {"hidden": True, "delete_project": True}), _session()
    )
    assert unknown.error is not None
    assert unknown.error.code == "B_INVALID_PARAMETER"
    assert "delete_project" in unknown.error.message

    assert api.dispatch(_command("req-hide", {"hidden": True}), _session()).error is None
    stale = api.dispatch(_command("req-stale", {"hidden": False}), _session())
    assert stale.error is not None
    assert stale.error.code == "B_REVISION_CONFLICT"
    # 冲突不产生新修订：已保存的偏好保持原样。
    assert (
        _start(workspace_root).reader.current_revision(
            aggregate_kind="project_view_preference", record_id=PROJECT_ID
        )
        == 1
    )
