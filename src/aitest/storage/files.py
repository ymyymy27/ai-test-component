"""Atomic JSON file helpers. Every business file carries a schema_version."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from aitest.errors import AiTestError

T = TypeVar("T", bound=BaseModel)

SCHEMA_VERSION = 1


def _read_text(path: Path, attempts: int = 20) -> str:
    """Read UTF-8 text, retrying brief Windows sharing violations on replace."""
    last_error: PermissionError | None = None
    for _ in range(attempts):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
        except OSError:
            raise
    assert last_error is not None
    raise last_error


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"文件不存在：{path}", "检查路径或先创建记录")
    try:
        data = json.loads(_read_text(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise AiTestError(
            "INVALID_REQUEST", f"文件无法读取：{path}（{exc}）", "修复或重建该文件"
        ) from exc
    if not isinstance(data, dict):
        raise AiTestError("INVALID_REQUEST", f"文件内容不是对象：{path}", "修复或重建该文件")
    return data


def check_schema_version(data: dict[str, Any], path: Path) -> None:
    """Reject business JSON written by another schema before using it."""

    if data.get("schema_version") != SCHEMA_VERSION:
        raise AiTestError(
            "SCHEMA_UNSUPPORTED",
            f"数据格式版本不受支持：{path}（{data.get('schema_version')!r}）",
            "备份后转换或重建工作空间",
        )


def read_model(path: Path, model_cls: type[T]) -> T:
    data = read_json(path)
    check_schema_version(data, path)
    try:
        return model_cls.model_validate(data)
    except ValueError as exc:
        raise AiTestError(
            "INVALID_REQUEST", f"记录字段不合法：{path}（{exc}）", "修复或重建该文件"
        ) from exc


def _write_failure(path: Path, exc: OSError) -> AiTestError:
    """Map a write failure to the code that actually describes it."""

    errno_val = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, _ReplaceBusy):
        return AiTestError(
            "FILE_BUSY", f"目标文件持续被占用，写入失败：{path}（{exc}）", "关闭占用程序后重试"
        )
    if errno_val == 28 or (os.name == "nt" and winerror == 112):  # ENOSPC / ERROR_DISK_FULL
        return AiTestError("DISK_FULL", f"磁盘不足，写入失败：{path}（{exc}）", "清理磁盘后重试")
    if errno_val == 36 or (
        os.name == "nt" and winerror == 206
    ):  # ENAMETOOLONG / ERROR_FILENAME_EXCED_RANGE
        return AiTestError(
            "PATH_TOO_LONG", f"路径过长，写入失败：{path}（{exc}）", "缩短工作空间或文件名后重试"
        )
    if errno_val == 13 or (os.name == "nt" and winerror == 5):  # EACCES / ERROR_ACCESS_DENIED
        return AiTestError(
            "PERMISSION_DENIED", f"权限不足，写入失败：{path}（{exc}）", "检查目录权限后重试"
        )
    if isinstance(exc, PermissionError):
        return AiTestError(
            "PERMISSION_DENIED", f"权限不足，写入失败：{path}（{exc}）", "检查目录权限后重试"
        )
    return AiTestError("WRITE_FAILED", f"写入失败：{path}（{exc}）", "检查目标路径与可用空间")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    except OSError as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise _write_failure(path, exc) from exc


class _ReplaceBusy(OSError):
    """A file stayed locked for every replace attempt (Windows sharing)."""


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 20) -> None:
    """Replace ``path`` atomically, retrying brief Windows sharing violations."""
    last_error: PermissionError | None = None
    for _ in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            # Windows reports both sharing violations (32) and transient AV /
            # indexer access denials (5) while replacing; retry both briefly.
            # Persistent failures still surface as FILE_BUSY below.
            winerror = getattr(exc, "winerror", None)
            if os.name == "nt" and winerror not in {None, 5, 32}:
                raise
            if os.name != "nt":
                raise
            last_error = exc
            time.sleep(0.05)
    assert last_error is not None
    raise _ReplaceBusy(
        last_error.errno, f"目标文件持续被占用：{path}（{last_error}）"
    ) from last_error


def write_model(path: Path, model: BaseModel) -> None:
    write_json(path, model.model_dump(mode="json"))
