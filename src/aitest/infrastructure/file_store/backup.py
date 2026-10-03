"""Closure backup and verification; no business material deletion."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_BACKUP_SCHEMA: Final = "aitest.backup/1.0"
_ACTIVE_MARKER: Final = Path("transactions") / "active.json"
_SPOOL_DIR: Final = "spool"

# 永久业务材料：以白名单闭包逐项复制，缺目录跳过，缺文件不伪造。
_PERMANENT_FILES: Final = frozenset(
    {
        "workspace.json",
        "records.json",
        "indexes.json",
        "current.json",
        "commit.json",
        "events.json",
        "transactions.json",
    }
)
_PERMANENT_DIRS: Final = frozenset(
    {
        "objects",
        "checkpoints",
        "transactions",
        "events",
        "manifests",
        "event-log",
        "snapshots",
        "diagnostics",
        "exports",
        "migrations",
        "reports",
    }
)


class BackupError(RuntimeError):
    """备份不可用、目标非空或恢复后核对失败。"""


@dataclass(frozen=True, slots=True)
class RestoreReport:
    """安全恢复结果；恢复后按清单重新核对。"""

    backup: Path
    target: Path
    files_restored: int
    bytes_restored: int
    verified: bool
    state: str  # restored | empty | rejected


def _contained_path(base: Path, relative: object) -> Path | None:
    """把清单相对路径严格限定在 base 内；绝对路径/上级跳转/NUL 一律拒绝。"""
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        return None
    pure = Path(relative)
    if pure.is_absolute() or pure.drive or pure.anchor:
        return None
    if any(part == ".." for part in pure.parts):
        return None
    candidate = (base / pure).resolve()
    if not candidate.is_relative_to(base):
        return None
    return candidate


def _read_manifest(backup: Path) -> dict[str, str]:
    raw = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
    files = raw.get("files")
    if not isinstance(files, dict):
        raise BackupError("备份清单损坏: files 不是对象")
    manifest: dict[str, str] = {}
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise BackupError("备份清单损坏: 条目类型非法")
        manifest[name] = digest
    return manifest


def _ignore_inside(destination: Path) -> Callable[[str, list[str]], set[str]]:
    """copytree 忽略函数：跳过备份目标自身，避免在工作空间内自包含递归。"""

    def _ignore(directory: str, names: list[str]) -> set[str]:
        base = Path(directory)
        skipped: set[str] = set()
        for name in names:
            candidate = (base / name).resolve()
            if candidate == destination:
                skipped.add(name)
        return skipped

    return _ignore


class FileBackupStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def create(self, destination: Path) -> Path:
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        ignore = _ignore_inside(destination) if destination.is_relative_to(self.root) else None
        for name in sorted(_PERMANENT_FILES):
            source = self.root / name
            if source.exists() and source.is_file():
                shutil.copy2(source, destination / name)
        for name in tuple(sorted(_PERMANENT_DIRS)) + (
            _SPOOL_DIR if not (self.root / _ACTIVE_MARKER).exists() else "",
        ):
            if not name:
                continue
            source = self.root / name
            if source.exists() and source.is_dir():
                shutil.copytree(
                    source,
                    destination / name,
                    dirs_exist_ok=True,
                    ignore=ignore,
                )
        # 在线活动 spool 不纳入闭包：活动事务的未确认材料另列，缺口不伪装完整。
        excluded_active: list[str] = []
        if (self.root / _ACTIVE_MARKER).exists():
            excluded_active.append(_SPOOL_DIR)
        # 清单键统一 POSIX 相对路径：备份是 portable 制品，不能把 Windows
        # 反斜杠写进冻结清单（A-06）。
        manifest = {
            p.relative_to(destination).as_posix(): self._digest(p)
            for p in destination.rglob("*")
            if p.is_file() and p.name != "backup.json"
        }
        (destination / "backup.json").write_text(
            json.dumps(
                {
                    "schema": _BACKUP_SCHEMA,
                    "files": manifest,
                    "excluded_active": excluded_active,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return destination

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def verify(self, backup: Path) -> dict[str, object]:
        backup = backup.resolve()
        manifest = _read_manifest(backup)
        errors: list[str] = []
        for name, digest in manifest.items():
            path = _contained_path(backup, name)
            if (
                path is None
                or not path.is_file()
                or path.is_symlink()
                or self._digest(path) != digest
            ):
                errors.append(name)
        # 闭包双向核对：备份内任何未登记文件（事后塞入/残留）都使备份不再
        # 是冻结闭包，不得作为迁移前可校验备份验收（A-06）。
        manifested = set(manifest)
        for present in backup.rglob("*"):
            if not present.is_file() or present.name == "backup.json":
                continue
            try:
                relative = present.relative_to(backup).as_posix()
            except ValueError:
                continue
            if present.is_symlink() or relative not in manifested:
                errors.append(relative)
        return {"ok": not errors, "errors": sorted(set(errors))}

    def restore(self, *, backup: Path, target: Path) -> RestoreReport:
        """把已校验备份恢复到空目标，恢复后按清单重新核对。

        从不覆盖非空目录；调用方须显式提供新的恢复目标。清单含越界/绝对
        路径时整体拒绝（不写入任何文件）并返回 ``state="rejected"``；
        内容摘要不符属于备份损坏，抛 :class:`BackupError`。
        """
        raw_backup = Path(backup)
        raw_target = Path(target)
        if raw_target.is_symlink():
            # 必须在 resolve() 之前检查；否则符号链接已被展开，
            # 后续写入会落到链接对端并越过工作空间边界。
            raise BackupError("恢复目标是符号链接，拒绝恢复")
        backup = raw_backup.resolve()
        target = raw_target.resolve()

        try:
            raw = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise BackupError(f"备份清单不可读: {error}") from error
        files = raw.get("files") if isinstance(raw, dict) else None
        if not isinstance(files, dict):
            raise BackupError("备份清单损坏: files 不是对象")
        manifest: dict[str, str] = {}
        for name, digest in files.items():
            if not isinstance(name, str) or not isinstance(digest, str):
                raise BackupError("备份清单损坏: 条目类型非法")
            manifest[name] = digest

        # 先逐项做路径限界：任何越界条目都使恢复整体拒绝，绝不写目标。
        destinations: dict[str, Path] = {}
        for name in manifest:
            source = _contained_path(backup, name)
            destination = _contained_path(target, name)
            if (
                source is None
                or destination is None
                or source.is_symlink()
                or destination.is_symlink()
                or not source.is_file()
            ):
                return RestoreReport(
                    backup=backup,
                    target=target,
                    files_restored=0,
                    bytes_restored=0,
                    verified=False,
                    state="rejected",
                )
            destinations[name] = destination

        if target.exists() and any(target.iterdir()):
            raise BackupError("恢复目标非空，拒绝覆盖")
        post_errors: list[str] = []
        target.mkdir(parents=True, exist_ok=True)
        files_restored = 0
        bytes_restored = 0
        for name, digest in manifest.items():
            destination = destinations[name]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / name, destination)
            files_restored += 1
            bytes_restored += destination.stat().st_size
            if self._digest(destination) != digest:
                post_errors.append(name)
        if post_errors:
            raise BackupError(f"恢复后核对失败: {post_errors}")
        state = "restored" if files_restored else "empty"
        return RestoreReport(
            backup=backup,
            target=target,
            files_restored=files_restored,
            bytes_restored=bytes_restored,
            verified=True,
            state=state,
        )
