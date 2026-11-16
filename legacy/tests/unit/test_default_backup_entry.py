"""受控备份入口（`backup`）的生产行为测试。

报告点名「backup/migrate 虽有底层组件，工作空间级受控写身份与产品入口尚未完成」。
本文件锁定入口的四道边界：**受控交互会话**、**目标在工作空间之外**、
**先建后校验**、**如实结论**（必需永久材料缺失即 `complete=false`，绝不宣称完整备份）；
以及维护模式备份的明确拒绝（没有维护模式状态可依赖时不假装做到）。

真实核心装配 + 真实文件底座 + 真实备份组件；真实掉电与真实主机未验收。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest

from aitest.application.maintenance.backup import (
    BackupBlocked,
    SavedBackupEntry,
)
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session

PROJECT = "project-1"


class _Actors:
    def __init__(self, *, interactive: bool) -> None:
        self._actor = SimpleNamespace(interactive=interactive)

    def current(self) -> Any:
        return self._actor


def _destination(name: str = "backup-1") -> pathlib.Path:
    """工作空间之外的独立目标（架构 04：备份位置独立于活动目录）。"""

    outer = pathlib.Path(tempfile.mkdtemp(prefix="aitest-backup-dest-"))
    return outer / name


def _entry(root: pathlib.Path, *, interactive: bool = True) -> SavedBackupEntry:
    from aitest.infrastructure.file_store.backup import FileBackupStore
    from aitest.infrastructure.file_store.maintenance import FileMaintenanceService

    return SavedBackupEntry(
        root=root,
        actors=_Actors(interactive=interactive),
        store=FileBackupStore(root),
        maintenance=FileMaintenanceService(root),
    )


def _command(destination: pathlib.Path, **parameters: object) -> Command:
    return Command(
        request_id="req-backup-1",
        action="backup",
        project_id=PROJECT,
        intent_id="intent-backup-1",
        expected_revision=0,
        parameters={"destination": str(destination), **parameters},
    )


@pytest.fixture()
def workspace() -> Any:
    root = pathlib.Path(tempfile.mkdtemp(prefix="aitest-backup-ws-"))
    core = assemble_workspace_core(root, instance_id="core-backup")
    try:
        yield root, core
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_online_backup_freezes_and_verifies_the_closure(workspace: Any) -> None:
    root, core = workspace
    destination = _destination()
    human = Session(session_id="cli", entry_kind=EntryKind.HUMAN_UI, interactive=True)
    response = core.api.dispatch(_command(destination), human)
    assert response.error is None
    result = response.result

    assert result["kind"] == "online"
    assert result["schema"] == "aitest.backup/1.0"
    assert result["verified"] is True
    assert result["restorable"] is True
    assert pathlib.Path(str(result["destination"])) == destination.resolve()
    # 清单最后写入且逐文件摘要齐备。
    manifest = json.loads((destination / "backup.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "aitest.backup/1.0"
    assert int(result["file_count"]) == len(manifest["files"]) > 0
    assert str(result["manifest_digest"]).startswith("sha256:")
    # 如实结论：新鲜工作空间缺必需永久材料时不得说"完整备份"。
    assert result["complete"] is False
    assert "commit.json" in list(result["known_missing"])
    assert result["gaps"] == ["required_permanent_material_missing"]


def test_complete_flag_requires_all_required_permanent_material(workspace: Any) -> None:
    root, _core = workspace
    (root / "commit.json").write_text(json.dumps({"commit_id": "commit-9"}), encoding="utf-8")
    result = _entry(root).apply(_command(_destination()))
    assert result["complete"] is True
    assert result["known_missing"] == []
    assert result["gaps"] == []
    # 历史根取自已提交记录，读得到就如实给出。
    assert result["root_commit"] == "commit-9"


def test_records_the_backup_in_the_maintenance_audit(workspace: Any) -> None:
    root, _core = workspace
    _entry(root).apply(_command(_destination()))
    audit = (root / "maintenance" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit) == 1
    entry = json.loads(audit[0])
    assert entry["action"] == "backed_up"
    assert entry["kind"] == "online"
    assert entry["verified"] is True
    assert entry["complete"] is False
    assert entry["known_missing"] == ["commit.json"]
    assert entry["file_count"] > 0
    assert entry["total_bytes"] > 0
    assert entry["manifest_digest"].startswith("sha256:")


def test_controlled_interactive_identity_is_required(workspace: Any) -> None:
    root, core = workspace
    agent = Session(session_id="mcp", entry_kind=EntryKind.AGENT_RELAY)
    response = core.api.dispatch(_command(_destination()), agent)
    assert response.error is not None
    assert response.error.code == "BACKUP_REFUSED"
    with pytest.raises(BackupBlocked):
        _entry(root, interactive=False).apply(_command(_destination()))


@pytest.mark.parametrize(
    "parameters",
    [
        {"kind": "maintenance"},
        {"kind": "online", "extra": 1},
        {"destination": "relative/backup"},
    ],
)
def test_unsupported_shapes_are_refused(workspace: Any, parameters: dict[str, object]) -> None:
    root, _core = workspace
    destination = _destination()
    payload = {"destination": str(destination), **parameters}
    if "destination" in parameters and isinstance(parameters["destination"], str):
        payload = dict(parameters)
    with pytest.raises(BackupBlocked):
        _entry(root).apply(
            Command(
                request_id="req-backup-2",
                action="backup",
                project_id=PROJECT,
                intent_id="intent-backup-2",
                expected_revision=0,
                parameters=payload,
            )
        )


def test_destination_inside_the_active_workspace_is_refused(workspace: Any) -> None:
    root, _core = workspace
    with pytest.raises(BackupBlocked) as refused:
        _entry(root).apply(_command(root / "inside"))
    assert "outside the active workspace" in str(refused.value)


def test_non_empty_destination_is_refused_without_deleting_anything(workspace: Any) -> None:
    root, _core = workspace
    destination = _destination()
    destination.mkdir(parents=True, exist_ok=True)
    keep = destination / "keep.bin"
    keep.write_bytes(b"permanent material that must not be touched")
    with pytest.raises(BackupBlocked):
        _entry(root).apply(_command(destination))
    assert keep.read_bytes() == b"permanent material that must not be touched"


def test_post_hoc_material_makes_the_backup_not_restorable(workspace: Any) -> None:
    """闭包双向核对：备份后塞入未登记文件即不再是冻结闭包（架构 04 §10）。"""

    root, _core = workspace
    destination = _destination()
    entry = _entry(root)
    result = entry.apply(_command(destination))
    assert result["restorable"] is True
    (destination / "smuggled.json").write_text("{}", encoding="utf-8")
    verification = entry.store.verify(destination)
    assert verification["ok"] is False
    assert "smuggled.json" in list(verification["errors"])
