"""Startup location guard for the data workspace (P1-FR01)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aitest.domain.project.context import (
    DriveKind,
    WorkspaceLocationRejection,
    workspace_location_rejection,
)
from aitest.infrastructure.file_store import workspace as workspace_module
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.infrastructure.workspace_location import (
    _SYNC_ROOTS,
    WorkspaceLocationRejected,
    detect_drive_kind,
    require_supported_workspace_location,
)


def _clear_sync_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SYNC_ROOTS:
        monkeypatch.delenv(name, raising=False)


def test_domain_maps_only_sync_and_network_to_rejection() -> None:
    assert (
        workspace_location_rejection(DriveKind.NETWORK)
        is WorkspaceLocationRejection.NETWORK_DRIVE
    )
    assert (
        workspace_location_rejection(DriveKind.SYNC_FOLDER)
        is WorkspaceLocationRejection.SYNC_FOLDER
    )
    for kind in (DriveKind.FIXED, DriveKind.REMOVABLE, DriveKind.UNKNOWN):
        assert workspace_location_rejection(kind) is None


def test_a_sync_root_is_detected_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sync_roots(monkeypatch)
    monkeypatch.setenv("OneDrive", str(tmp_path))
    assert detect_drive_kind(tmp_path / "workspace") is DriveKind.SYNC_FOLDER


def test_a_regular_temporary_directory_is_not_a_sync_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_sync_roots(monkeypatch)
    assert detect_drive_kind(tmp_path) is not DriveKind.SYNC_FOLDER


def test_require_location_refuses_a_network_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "aitest.infrastructure.workspace_location.detect_drive_kind",
        lambda _path: DriveKind.NETWORK,
    )
    with pytest.raises(WorkspaceLocationRejected) as raised:
        require_supported_workspace_location(tmp_path)
    assert raised.value.rejection is WorkspaceLocationRejection.NETWORK_DRIVE


def test_workspace_creation_refuses_a_sync_folder_before_writing_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_path: Path) -> DriveKind:
        raise WorkspaceLocationRejected(
            _path, WorkspaceLocationRejection.SYNC_FOLDER
        )

    monkeypatch.setattr(
        workspace_module, "require_supported_workspace_location", refuse
    )
    with pytest.raises(WorkspaceLocationRejected):
        Workspace(tmp_path)
    assert not (tmp_path / "workspace.json").exists()


def test_workspace_opening_refuses_a_network_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Workspace(tmp_path)
    monkeypatch.setattr(
        workspace_module,
        "require_supported_workspace_location",
        lambda path: raise_rejection(path, WorkspaceLocationRejection.NETWORK_DRIVE),
    )
    with pytest.raises(WorkspaceLocationRejected):
        Workspace(tmp_path, create=False)


def raise_rejection(
    path: Path, rejection: WorkspaceLocationRejection
) -> DriveKind:
    raise WorkspaceLocationRejected(path, rejection)
