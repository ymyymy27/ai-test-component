"""Detect where a data workspace lives before creating or opening it.

The workspace holds an exclusive write lock and performs atomic replacement.
Sync clients and network shares break both guarantees, so P1-FR01 requires the
location to be checked at startup and refused with a reason. Detection is I/O
and lives here; the decision itself stays in the domain.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from pathlib import Path

from aitest.domain.project.context import (
    DriveKind,
    WorkspaceLocationRejection,
    workspace_location_rejection,
)

#: Environment variables that identify a local sync client's root directory.
_SYNC_ROOTS = (
    "OneDrive",
    "OneDriveConsumer",
    "OneDriveCommercial",
    "Dropbox",
    "GoogleDrive",
    "iCloudDrive",
    "Nextcloud",
)

_DRIVE_REMOVABLE = 2
_DRIVE_FIXED = 3
_DRIVE_REMOTE = 4


class WorkspaceLocationRejected(ValueError):
    """The workspace is on a location that cannot preserve single-writer safety."""

    code = "WORKSPACE_LOCATION_REJECTED"

    def __init__(self, path: Path, rejection: WorkspaceLocationRejection) -> None:
        self.path = path
        self.rejection = rejection
        super().__init__(
            f"workspace location is not supported: {rejection.value} ({path})"
        )


def require_supported_workspace_location(path: Path) -> DriveKind:
    """Return the detected drive kind, raising when it must not host a workspace."""
    kind = detect_drive_kind(path)
    rejection = workspace_location_rejection(kind)
    if rejection is not None:
        raise WorkspaceLocationRejected(path, rejection)
    return kind


def detect_drive_kind(path: Path) -> DriveKind:
    """Classify the volume or sync root that contains ``path``."""
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    if _inside_sync_root(resolved):
        return DriveKind.SYNC_FOLDER
    if os.name == "nt":
        return _windows_drive_kind(resolved)
    # The phase-one product targets Windows; on other platforms only an
    # unambiguous network path is classified, everything else stays unknown.
    if str(resolved).startswith("//"):
        return DriveKind.NETWORK
    return DriveKind.UNKNOWN


def _inside_sync_root(path: Path) -> bool:
    roots = [os.environ.get(name) for name in _SYNC_ROOTS]
    roots.append(str(Path.home() / "OneDrive"))
    return any(
        candidate and _is_within(path, Path(candidate))
        for candidate in roots
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        resolved_root = root.resolve(strict=False)
    except OSError:
        resolved_root = root.absolute()
    normalized_path = os.path.normcase(os.path.abspath(str(path)))
    normalized_root = os.path.normcase(os.path.abspath(str(resolved_root)))
    try:
        return os.path.commonpath([normalized_path, normalized_root]) == normalized_root
    except ValueError:
        return False


def _windows_drive_kind(path: Path) -> DriveKind:
    drive = path.drive
    if not drive:
        return DriveKind.UNKNOWN
    if drive.startswith("\\\\"):
        return DriveKind.NETWORK
    try:
        get_drive_type = _get_drive_type()
        kind = get_drive_type(drive + "\\")
    except (AttributeError, OSError, ValueError):
        return DriveKind.UNKNOWN
    if kind == _DRIVE_REMOTE:
        return DriveKind.NETWORK
    if kind == _DRIVE_FIXED:
        return DriveKind.FIXED
    if kind == _DRIVE_REMOVABLE:
        return DriveKind.REMOVABLE
    return DriveKind.UNKNOWN


def _get_drive_type() -> Callable[[str], int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetDriveTypeW
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_uint
    return function


__all__ = [
    "WorkspaceLocationRejected",
    "detect_drive_kind",
    "require_supported_workspace_location",
]
