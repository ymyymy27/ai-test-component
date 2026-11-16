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


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"文件不存在：{path}", "检查路径或先创建记录")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AiTestError(
            "INVALID_REQUEST", f"文件无法读取：{path}（{exc}）", "修复或重建该文件"
        ) from exc
    if not isinstance(data, dict):
        raise AiTestError("INVALID_REQUEST", f"文件内容不是对象：{path}", "修复或重建该文件")
    return data


def read_model(path: Path, model_cls: type[T]) -> T:
    data = read_json(path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise AiTestError(
            "SCHEMA_UNSUPPORTED",
            f"数据格式版本不受支持：{path}（{data.get('schema_version')!r}）",
            "备份后转换或重建工作空间",
        )
    try:
        return model_cls.model_validate(data)
    except ValueError as exc:
        raise AiTestError(
            "INVALID_REQUEST", f"记录字段不合法：{path}（{exc}）", "修复或重建该文件"
        ) from exc


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise AiTestError("DISK_FULL", f"写入失败：{path}（{exc}）", "检查磁盘空间与权限") from exc


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 20) -> None:
    """Replace ``path`` atomically, retrying brief Windows sharing violations."""
    last_error: PermissionError | None = None
    for _ in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
    assert last_error is not None
    raise last_error


def write_model(path: Path, model: BaseModel) -> None:
    write_json(path, model.model_dump(mode="json"))
