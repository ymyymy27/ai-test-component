"""Finite startup facts and a launch mutex; no business execution authority."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import portalocker

from aitest.application.errors import WorkspaceInUse
from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.security import guard_bytes, guard_value

from . import atomic

if sys.version_info >= (3, 13):
    from ntpath import isreserved as isreserved_name
else:
    # Python 3.12 及以下没有 `os.path.isreserved`；按同一语义最小实现：
    # Windows 保留设备名与**歧义名**——尾随空格/点号、非法字符（<>:"/\|?* 与控制字符）
    # 均视为保留，含目录部分的路径返回假。3.13 起直接使用标准库实现。
    _RESERVED_DEVICE_NAMES = frozenset(
        ["CON", "PRN", "AUX", "NUL"]
        + [f"COM{index}" for index in range(1, 10)]
        + [f"LPT{index}" for index in range(1, 10)]
    )
    _INVALID_NAME_CHARS = frozenset('<>:"/\\|?*')

    def isreserved_name(path: str) -> bool:
        # 与 3.13 原生实现对齐：空串、"."、".." 返回假（调用方另有长度与点号检查）。
        if not path or path in {".", ".."}:
            return False
        if os.path.dirname(path):
            return False
        if path[-1] in ". ":
            return True
        if any(char in _INVALID_NAME_CHARS or ord(char) < 32 for char in path):
            return True
        return path.split(".")[0].upper() in _RESERVED_DEVICE_NAMES


@dataclass(frozen=True, slots=True)
class ProcessFact:
    alive: bool | None
    identity: str | None = None
    exit_code: int | None = None


def valid_instance_filename(name: str) -> bool:
    """One unambiguous Windows file, never a device name or alternate stream."""
    return (
        1 <= len(name) <= 128
        and name not in {".", ".."}
        and Path(name).name == name
        and not any(c in name for c in "/\\")
        and not isreserved_name(name)
    )


def python_launch_program(requested: str) -> tuple[str, str | None]:
    """Use the running CPython image directly, preserving its venv semantics.

    Windows venv python.exe is a redirector: its child has a different PID.
    CPython's own redirector supplies __PYVENV_LAUNCHER__ to the real image.
    Apply that same environment contract for the current interpreter only.
    Other explicitly selected executables still have to pass the child gate.
    """
    if sys.platform != "win32" or Path(requested).resolve() != Path(sys.executable).resolve():
        return requested, None
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetModuleFileNameW.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetModuleFileNameW(None, buffer, len(buffer))
    if not size or size >= len(buffer):
        raise WorkspaceInUse("running Python executable image cannot be verified")
    actual = str(Path(buffer.value).resolve())
    return actual, requested if Path(actual) != Path(requested).resolve() else None


def probe_process(pid: int) -> ProcessFact:
    """Query birth and signal together. Failure is unknown, never presumed dead."""
    if sys.platform != "win32" or type(pid) is not int or not 0 < pid < 2**32:
        return ProcessFact(None)
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000 | 0x00100000, False, pid)
    if not handle:
        return ProcessFact(False if ctypes.get_last_error() == 87 else None)
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return ProcessFact(None)
        birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        identity = hashlib.sha256(str(birth).encode("ascii")).hexdigest()
        signal = kernel.WaitForSingleObject(handle, 0)
        if signal == 258:
            return ProcessFact(True, identity)
        if signal != 0:
            return ProcessFact(None, identity)
        code = wintypes.DWORD()
        exit_code = (
            int(code.value) if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) else None
        )
        return ProcessFact(False, identity, exit_code)
    finally:
        kernel.CloseHandle(handle)


class FileCoreLaunchStore:
    SCHEMA = "aitest.core-launch/1"

    def __init__(self, root: Path) -> None:
        self.reject_links(root)
        self.root = root
        self.path = root / "core/launch.json"

    @staticmethod
    def reject_links(path: Path) -> None:
        if any(part.is_symlink() or compat.is_junction(part) for part in (path, *path.parents)):
            raise WorkspaceInUse("core startup path contains a filesystem link")

    @contextmanager
    def locked(self) -> Iterator[None]:
        path = self.root / "core/launch.lock"
        self.reject_links(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with portalocker.Lock(str(path), mode="a+b", timeout=0):
                yield
        except portalocker.exceptions.LockException as error:
            raise WorkspaceInUse("another host is claiming this core startup") from error

    def read(self) -> dict[str, Any] | None:
        self.reject_links(self.path)
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 8192:
                raise ValueError("oversized startup fact")

            def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, item in pairs:
                    if key in result:
                        raise ValueError("duplicate startup field")
                    result[key] = item
                return result

            value = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique)
            self.validate(value)
            return dict(value)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise WorkspaceInUse("core startup facts cannot be verified") from error

    @classmethod
    def validate(cls, value: Any) -> None:
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "schema",
                "endpoint_workspace_id",
                "instance_id",
                "state",
                "process_id",
                "process_identity",
                "exit_code",
                "reason",
            }
            or value["schema"] != cls.SCHEMA
            or not isinstance(value["endpoint_workspace_id"], str)
            or not 1 <= len(value["endpoint_workspace_id"]) <= 128
            or not isinstance(value["instance_id"], str)
            or not value["instance_id"].startswith("core-")
            or len(value["instance_id"]) != 17
            or any(c not in "0123456789abcdef" for c in value["instance_id"][5:])
            or value["state"]
            not in {
                "creating",
                "created",
                "stopping",
                "create_failed",
                "publication_uncertain",
                "exited",
            }
        ):
            raise ValueError("invalid startup fact")
        pid, identity = value["process_id"], value["process_identity"]
        if value["state"] in {"created", "stopping", "exited"}:
            if (
                type(pid) is not int
                or not 0 < pid < 2**32
                or not isinstance(identity, str)
                or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise ValueError("missing startup process identity")
        elif pid is not None or identity is not None:
            raise ValueError("uncertain startup cannot claim a verified process")
        code = value["exit_code"]
        if code is not None and (type(code) is not int or not -(2**31) <= code < 2**32):
            raise ValueError("invalid startup exit code")
        reasons: dict[str, set[str | None]] = {
            "creating": {None},
            "created": {None},
            "stopping": {"shutdown_requested"},
            "create_failed": {"create_failed"},
            "publication_uncertain": {"publication_failed"},
            "exited": {"process_exited", "publication_failed", "pid_reused"},
        }
        if value["reason"] not in reasons[value["state"]]:
            raise ValueError("invalid startup reason")
        if value["state"] != "exited" and code is not None:
            raise ValueError("startup cannot claim an exit code before verified exit")

    def save(self, value: dict[str, Any]) -> None:
        self.validate(value)
        safe, changed = guard_value(value)
        raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        guarded, bytes_changed = guard_bytes(raw)
        if changed or safe != value or bytes_changed or guarded != raw:
            raise WorkspaceInUse("core startup fact cannot preserve a safe identity")
        self.reject_links(self.path)
        history = self.root / "core/launch-history" / f"{uuid4().hex}.json"
        self.reject_links(history)
        atomic.write_json(history, value)
        atomic.write_json(self.path, value)

    def read_instance_id(self, path: Path) -> str | None:
        self.reject_links(path)
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 128:
                raise ValueError("oversized instance pointer")
            value = path.read_text(encoding="utf-8").strip()
            if (
                not value.startswith("core-")
                or not value.isascii()
                or any(not (c.isalnum() or c in "-_") for c in value)
            ):
                raise ValueError("invalid instance pointer")
            return value
        except (OSError, ValueError) as error:
            raise WorkspaceInUse("core discovery pointer cannot be verified") from error

    def publish_instance_id(self, path: Path, instance_id: str) -> None:
        """Final discovery switch, after successful creation and birth verification."""
        self.reject_links(path)
        raw = instance_id.encode("ascii")
        guarded, changed = guard_bytes(raw)
        if changed or guarded != raw:
            raise WorkspaceInUse("core discovery pointer cannot preserve a safe identity")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            atomic.replace_with_retry(temporary, path)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise
