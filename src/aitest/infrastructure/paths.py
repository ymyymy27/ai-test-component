"""Operating-system user-data workspace location (P1 architecture 04 §12).

Runtime data must live in the OS user-data workspace, never in the installed
package, the tested source tree or ``src/``. Entry points may still pass an
explicit root (tests, operators), but the default resolution and the location
proof both live here so every adapter reports the same fact.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIRECTORY = "aitest"
WORKSPACE_DIRECTORY = "workspace"


def default_workspace_root() -> Path:
    """Return `<OS user data>/aitest/workspace` without creating it."""

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DIRECTORY / WORKSPACE_DIRECTORY
        return Path.home() / "AppData" / "Local" / APP_DIRECTORY / WORKSPACE_DIRECTORY
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRECTORY / WORKSPACE_DIRECTORY
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / APP_DIRECTORY / WORKSPACE_DIRECTORY
    return Path.home() / ".local" / "share" / APP_DIRECTORY / WORKSPACE_DIRECTORY


def is_user_data_workspace(root: Path) -> bool:
    """True when `root` is inside the OS user-data workspace directory."""

    try:
        Path(root).resolve().relative_to(default_workspace_root().resolve())
    except ValueError:
        return False
    return True


__all__ = ["default_workspace_root", "is_user_data_workspace"]
