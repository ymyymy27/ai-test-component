"""`backup` 的 `inspect`／`restore` 模式：备份登记（`backup_id`）与安全恢复。

架构 04 §10：备份登记要能核对（`BackupManifest` 含 root_commit、逐文件摘要、缺失清单、
备份类型）；§10「恢复到新目录…不允许双工作空间接管同一 Attempt」/§11。接口级 `BackupPort`
早已声明 `inspect`／`verify`／`restore` 按 `backup_id` 工作，本文件锁定入口侧的这三件事：

- `backup_id` = **清单摘要**（内容寻址，不需要新记录类别）；
- `inspect` 读回登记事实，`verify=true` 时现场复核闭包（只读，任何受控会话可用）；
- `restore` 只能恢复到**新的空目标**，源必须现场复核通过，未覆盖/未删除任何材料，
  且回执如实登记尚未完成的后置条件（索引重建、活动步骤待核实、双接管检查）。

真实主机与真实掉电未验收。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest

from aitest.application.maintenance.backup import BackupBlocked, SavedBackupEntry
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session

PROJECT = "project-1"


class _Actors:
    def __init__(self, *, interactive: bool) -> None:
        self._actor = SimpleNamespace(interactive=interactive)

    def current(self) -> Any:
        return self._actor


def _entry(root: pathlib.Path, *, interactive: bool = True) -> SavedBackupEntry:
    from aitest.infrastructure.file_store.backup import FileBackupStore
    from aitest.infrastructure.file_store.maintenance import FileMaintenanceService

    return SavedBackupEntry(
        root=root,
        actors=_Actors(interactive=interactive),
        store=FileBackupStore(root),
        maintenance=FileMaintenanceService(root),
    )


def _command(**parameters: object) -> Command:
    return Command(
        request_id="req-backup-mode",
        action="backup",
        project_id=PROJECT,
        intent_id="intent-backup-mode",
        expected_revision=0,
        parameters=dict(parameters),
    )


@pytest.fixture()
def workspace(tmp_path: pathlib.Path) -> Any:
    root = pathlib.Path(tempfile.mkdtemp(prefix="aitest-backup2-ws-"))
    outer = pathlib.Path(tempfile.mkdtemp(prefix="aitest-backup2-dest-"))
    # 真实装配过的核心工作空间：备份闭包里有真实永久材料，恢复才有内容可核对。
    assemble_workspace_core(root, instance_id="core-backup-modes-fixture")
    try:
        yield root, outer
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outer, ignore_errors=True)


def test_create_registers_a_content_addressed_backup_id(workspace: Any) -> None:
    root, outer = workspace
    result = _entry(root).apply(_command(destination=str(outer / "b1")))
    assert str(result["backup_id"]).startswith("sha256:")
    assert result["backup_id"] == result["manifest_digest"]

    inspected = _entry(root).apply(_command(mode="inspect"))
    assert inspected["count"] == 1
    entry = inspected["backups"][0]
    assert entry["backup_id"] == result["backup_id"]
    assert entry["exists"] is True
    assert entry["verified_at_creation"] is True
    assert entry["complete"] is False  # 新鲜工作空间缺 commit.json
    assert entry["known_missing"] == ["commit.json"]


def test_inspect_can_reverify_now_and_filter_by_backup_id(workspace: Any) -> None:
    root, outer = workspace
    created = _entry(root).apply(_command(destination=str(outer / "b1")))
    backup_id = str(created["backup_id"])

    live = _entry(root).apply(_command(mode="inspect", verify=True))
    assert live["backups"][0]["verified_now"] is True
    assert live["backups"][0]["verification_errors"] == []

    filtered = _entry(root).apply(_command(mode="inspect", backup_id=backup_id))
    assert filtered["count"] == 1

    # 备份后塞入未登记文件：现场复核必须发现闭包被破坏。
    (outer / "b1" / "smuggled.json").write_text("{}", encoding="utf-8")
    tampered = _entry(root).apply(_command(mode="inspect", verify=True))
    assert tampered["backups"][0]["verified_now"] is False
    assert "smuggled.json" in tampered["backups"][0]["verification_errors"]

    with pytest.raises(BackupBlocked) as missing:
        _entry(root).apply(_command(mode="inspect", backup_id="sha256:" + "a" * 64))
    assert "no registered backup" in str(missing.value)


def test_restore_replays_a_registered_backup_into_an_empty_target(workspace: Any) -> None:
    root, outer = workspace
    created = _entry(root).apply(_command(destination=str(outer / "b1")))
    target = outer / "restored"

    result = _entry(root).apply(
        _command(mode="restore", backup_id=str(created["backup_id"]), target=str(target))
    )
    assert result["state"] == "restored"
    assert result["verified"] is True
    assert int(result["files_restored"]) > 0
    assert pathlib.Path(str(result["target"])) == target.resolve()
    # 复原后的目录是**可用工作空间材料**：清单里的文件逐字一致（清单存裸摘要）。
    manifest = json.loads((outer / "b1" / "backup.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        restored = target / name
        assert restored.is_file()
        assert hashlib.sha256(restored.read_bytes()).hexdigest() == digest
    # 后置条件如实登记，不当成已完成。
    assert result["next_steps"]
    assert "index_rebuild_not_performed" in result["gaps"]
    assert "double_takeover_check_not_performed" in result["gaps"]


def test_restore_refuses_unknown_id_tampered_source_and_non_empty_target(
    workspace: Any,
) -> None:
    root, outer = workspace
    created = _entry(root).apply(_command(destination=str(outer / "b1")))
    backup_id = str(created["backup_id"])

    with pytest.raises(BackupBlocked):
        _entry(root).apply(
            _command(mode="restore", backup_id="sha256:" + "b" * 64, target=str(outer / "r1"))
        )

    non_empty = outer / "r2"
    non_empty.mkdir(parents=True, exist_ok=True)
    keep = non_empty / "keep.bin"
    keep.write_bytes(b"existing material")
    with pytest.raises(BackupBlocked):
        _entry(root).apply(
            _command(mode="restore", backup_id=backup_id, target=str(non_empty))
        )
    assert keep.read_bytes() == b"existing material"

    # 源被改坏后拒绝恢复（现场复核，不信任创建时的结论）。
    (outer / "b1" / "smuggled.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BackupBlocked) as tampered:
        _entry(root).apply(
            _command(mode="restore", backup_id=backup_id, target=str(outer / "r3"))
        )
    assert "no longer verifies" in str(tampered.value)
    assert not (outer / "r3").exists()


def test_restore_requires_a_controlled_session(workspace: Any) -> None:
    root, outer = workspace
    created = _entry(root).apply(_command(destination=str(outer / "b1")))
    with pytest.raises(BackupBlocked):
        _entry(root, interactive=False).apply(
            _command(
                mode="restore",
                backup_id=str(created["backup_id"]),
                target=str(outer / "r4"),
            )
        )
    # inspect 是只读：非交互会话也能读回登记。
    assert _entry(root, interactive=False).apply(_command(mode="inspect"))["count"] == 1


@pytest.mark.parametrize(
    "parameters",
    [
        {"mode": "teleport"},
        {"mode": "inspect", "verify": "yes"},
        {"mode": "inspect", "backup_id": ""},
        {"mode": "inspect", "extra": 1},
        {"mode": "restore"},
        {"mode": "restore", "backup_id": "sha256:" + "c" * 64},
        {"mode": "restore", "backup_id": ""},
        {"mode": "restore", "backup_id": "sha256:" + "c" * 64, "target": "relative"},
        {"mode": "restore", "backup_id": "sha256:" + "c" * 64, "target": "x", "extra": 1},
    ],
)
def test_mode_shapes_are_refused_by_name(
    workspace: Any, parameters: dict[str, object]
) -> None:
    root, _outer = workspace
    with pytest.raises(BackupBlocked):
        _entry(root).apply(_command(**parameters))


def test_restore_target_inside_the_active_workspace_is_refused(workspace: Any) -> None:
    root, outer = workspace
    created = _entry(root).apply(_command(destination=str(outer / "b1")))
    with pytest.raises(BackupBlocked):
        _entry(root).apply(
            _command(
                mode="restore",
                backup_id=str(created["backup_id"]),
                target=str(root / "restored"),
            )
        )


def test_default_assembly_exposes_both_backup_modes(tmp_path: pathlib.Path) -> None:
    root = pathlib.Path(tempfile.mkdtemp(prefix="aitest-backup2-core-"))
    outer = pathlib.Path(tempfile.mkdtemp(prefix="aitest-backup2-core-dest-"))
    try:
        core = assemble_workspace_core(root, instance_id="core-backup-modes")
        human = Session(session_id="cli", entry_kind=EntryKind.HUMAN_UI, interactive=True)
        container = {"value": 0}

        def dispatch(action: str, **parameters: object) -> Any:
            container["value"] += 1
            return core.api.dispatch(
                Command(
                    request_id=f"req-{action}-{container['value']}",
                    action=action,
                    project_id=PROJECT,
                    intent_id=f"intent-{container['value']}",
                    expected_revision=0,
                    parameters=dict(parameters),
                ),
                human,
            )

        created = dispatch("backup", destination=str(outer / "core-b1"))
        assert created.error is None
        inspected = dispatch("backup", mode="inspect", verify=True)
        assert inspected.error is None
        assert inspected.result["count"] == 1
        restored = dispatch(
            "backup",
            mode="restore",
            backup_id=str(created.result["backup_id"]),
            target=str(outer / "core-restored"),
        )
        assert restored.error is None
        assert restored.result["state"] == "restored"
        assert restored.result["verified"] is True
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outer, ignore_errors=True)
