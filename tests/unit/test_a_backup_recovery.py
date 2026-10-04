"""Tests for safe backup restore and the recovery orchestration closed loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aitest.infrastructure.file_store.backup import BackupError, FileBackupStore
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.recovery import (
    RecoveryBlocked,
    RecoveryOrchestrator,
    recover_workspace,
)
from aitest.infrastructure.file_store.workspace import Workspace

_COMMIT_FILE = {
    "commits": [
        {"commit_sequence": 1, "request_id": "req-1", "revision": 1},
        {"commit_sequence": 2, "request_id": "req-2", "revision": 2},
    ]
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    Workspace(tmp_path)
    FileEventJournal(tmp_path, instance_id="instance-1")
    (tmp_path / "commit.json").write_text(json.dumps(_COMMIT_FILE), encoding="utf-8")
    return tmp_path


# ----- backup.restore -----------------------------------------------


def test_restore_copies_files_and_reverifies(workspace: Path, tmp_path: Path) -> None:
    store = FileBackupStore(workspace)
    backup = store.create(tmp_path / "my-backup")
    target = tmp_path / "restored"

    report = store.restore(backup=backup, target=target)

    assert report.verified is True
    assert report.state == "restored"
    assert report.files_restored >= 1
    assert (target / "workspace.json").exists()
    assert (target / "commit.json").read_text(encoding="utf-8") == (
        workspace / "commit.json"
    ).read_text(encoding="utf-8")


def test_restore_refuses_non_empty_target(workspace: Path, tmp_path: Path) -> None:
    store = FileBackupStore(workspace)
    backup = store.create(tmp_path / "my-backup")
    target = tmp_path / "nonempty"
    target.mkdir()
    (target / "sentinel.txt").write_text("x", encoding="utf-8")

    with pytest.raises(BackupError):
        store.restore(backup=backup, target=target)


def test_restore_detects_corrupt_backup(workspace: Path, tmp_path: Path) -> None:
    store = FileBackupStore(workspace)
    backup = store.create(tmp_path / "my-backup")
    (backup / "workspace.json").write_text('{"tampered": true}', encoding="utf-8")

    with pytest.raises(BackupError):
        store.restore(backup=backup, target=tmp_path / "restored")


# ----- recovery orchestration ---------------------------------------


def test_recover_workspace_function(workspace: Path) -> None:
    report = recover_workspace(workspace)
    assert report["ok"] is True
    assert report["recovery_required"] is False


def test_orchestrator_inspect(workspace: Path) -> None:
    orchestrator = RecoveryOrchestrator(workspace, instance_id="instance-1")
    state: dict[str, Any] = orchestrator.inspect()
    assert state["integrity_ok"] is True
    assert state["active_marker"] is None
    assert tuple(state["committed_sequences"]) == (1, 2)


def test_run_healthy_when_no_marker(workspace: Path) -> None:
    result = RecoveryOrchestrator(workspace, instance_id="instance-1").run()
    assert result.state == "healthy"
    assert result.committed_sequences == (1, 2)
    assert "无需恢复" in result.actions


def test_projection_sequence_alone_cannot_prove_a_committed_crash(workspace: Path) -> None:
    marker = workspace / "transactions" / "active.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"request_id": "req-2", "commit_sequence": 2}),
        encoding="utf-8",
    )

    result = RecoveryOrchestrator(workspace, instance_id="instance-1").run()

    assert result.state == "blocked"
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "request_id": "req-2",
        "commit_sequence": 2,
    }


def test_run_marker_without_commit_preserves_unknown_activity(workspace: Path) -> None:
    marker = workspace / "transactions" / "active.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"request_id": "req-9", "commit_sequence": 9}),
        encoding="utf-8",
    )
    before = (workspace / "commit.json").read_text(encoding="utf-8")

    result = RecoveryOrchestrator(workspace, instance_id="instance-1").run()

    assert result.state == "blocked"
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "request_id": "req-9",
        "commit_sequence": 9,
    }
    assert (workspace / "commit.json").read_text(encoding="utf-8") == before


def test_run_blocked_on_integrity_failure(workspace: Path) -> None:
    (workspace / "commit.json").write_text("{broken", encoding="utf-8")

    result = RecoveryOrchestrator(workspace, instance_id="instance-1").run()

    assert result.state == "blocked"
    assert result.integrity_ok is False


def test_restore_backup_into_isolated_target(workspace: Path, tmp_path: Path) -> None:
    store = FileBackupStore(workspace)
    backup = store.create(tmp_path / "my-backup")
    target = tmp_path / "recovered"

    result = RecoveryOrchestrator(workspace, instance_id="instance-1").restore_backup(
        backup=backup, target=target
    )

    assert result.state == "repaired"
    assert result.restore is not None
    assert result.restore.verified is True
    assert (target / "workspace.json").exists()
    # 活动工作空间未被替换
    assert (workspace / "workspace.json").exists()


def test_restore_backup_blocked_on_corrupt(workspace: Path, tmp_path: Path) -> None:
    store = FileBackupStore(workspace)
    backup = store.create(tmp_path / "my-backup")
    (backup / "workspace.json").write_text('{"x":', encoding="utf-8")

    with pytest.raises(RecoveryBlocked):
        RecoveryOrchestrator(workspace, instance_id="instance-1").restore_backup(
            backup=backup, target=tmp_path / "recovered"
        )
