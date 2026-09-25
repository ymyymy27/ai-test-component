import pytest

from aitest.domain.project.context import (
    DriveKind,
    WorkspaceLocationRejection,
    reject_workspace_location,
)

PATH = r"C:\work\workspace"


def test_network_drive_is_rejected_with_a_reason() -> None:
    """同步器会破坏排他锁与原子替换语义（P1-FR01）。"""
    assert (
        reject_workspace_location(PATH, DriveKind.NETWORK)
        is WorkspaceLocationRejection.NETWORK_DRIVE
    )


def test_sync_folder_is_rejected_with_a_reason() -> None:
    assert (
        reject_workspace_location(PATH, DriveKind.SYNC_FOLDER)
        is WorkspaceLocationRejection.SYNC_FOLDER
    )


def test_local_drives_are_accepted() -> None:
    for kind in (DriveKind.FIXED, DriveKind.REMOVABLE):
        assert reject_workspace_location(PATH, kind) is None


def test_unknown_drive_kind_is_not_rejected_by_the_domain_layer() -> None:
    """是否因未知而阻塞由调用方决定，领域层不替它决定。"""
    assert reject_workspace_location(PATH, DriveKind.UNKNOWN) is None


def test_rejection_still_demands_a_canonical_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        reject_workspace_location("relative", DriveKind.FIXED)
