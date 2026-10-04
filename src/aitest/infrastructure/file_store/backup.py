"""Closure backup and verification; no business material deletion."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from aitest.infrastructure.security import (
    KnownSecretRegistry,
    UnsafeMaterialError,
    copy_unchanged_safe_bytes,
    guard_value,
    known_secrets,
)

from . import atomic

_BACKUP_SCHEMA: Final = "aitest.backup/1.0"
_ACTIVE_MARKER: Final = Path("transactions") / "active.json"
_SPOOL_DIR: Final = "spool"
_MAX_JSON_BYTES: Final = 16 * 1024 * 1024

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
        # generations 为历史世代永久材料；core 下的启动/运行台账与
        # maintenance/audit.jsonl 回收审计同为永久留存，备份闭包必须覆盖，
        # 缺目录跳过、缺文件不伪造（A-06）。
        "generations",
        "core",
        "maintenance",
        # indexes/ 为分片查询目录（A-05）：全局根 indexes.json 之外的
        # 有序分片、折叠键账同为可从权威边界重建但必须随备份闭包保存的
        # 投影，恢复后列表不得退回 INDEX_REBUILD_REQUIRED。
        "indexes",
        "execution-handles",
        "model-responses",
        "record-store",
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


def _chain_has_symlink(raw_target: Path) -> bool:
    """在 resolve() 之前按 lstat 逐层核对目标路径上的符号链接。

    ``resolve()`` 会跟随符号链接，先 resolve 再查 ``is_symlink()`` 永远
    发现不了目标自身或其现存祖先中的链接（A-06）。目标尚不存在时上溯到
    最近的现存祖先，再根→叶逐层检查；不存在的层 lstat 失败返回 False。
    """
    return any(
        path.is_symlink() or path.is_junction() for path in (raw_target, *raw_target.parents)
    )


def _read_manifest(backup: Path) -> dict[str, str]:
    if _chain_has_symlink(backup / "backup.json"):
        raise BackupError("备份清单路径含链接")
    raw = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise BackupError("备份清单不是对象")
    files = raw.get("files")
    if not isinstance(files, dict):
        raise BackupError("备份清单损坏: files 不是对象")
    manifest: dict[str, str] = {}
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise BackupError("备份清单损坏: 条目类型非法")
        manifest[name] = digest
    return manifest


class FileBackupStore:
    def __init__(self, root: Path, *, registry: KnownSecretRegistry | None = None) -> None:
        if _chain_has_symlink(root):
            raise BackupError("备份工作空间路径含链接")
        self.root = root.resolve()
        self._registry = registry if registry is not None else known_secrets()

    def _safe_digest(self, path: Path) -> str:
        if _chain_has_symlink(path):
            raise BackupError("永久材料路径含链接")
        try:
            with path.open("rb") as source:
                content = self._safe_json_bytes(path, source)
                digest, _size = copy_unchanged_safe_bytes(
                    io.BytesIO(content) if content is not None else source,
                    registry=self._registry,
                )
            return digest
        except UnsafeMaterialError as error:
            raise BackupError(str(error)) from error

    def _safe_json_bytes(self, path: Path, source: IO[bytes]) -> bytes | None:
        if path.suffix.lower() not in {".json", ".jsonl"}:
            return None
        try:
            content = source.read(_MAX_JSON_BYTES + 1)
            if len(content) > _MAX_JSON_BYTES:
                raise BackupError("结构化备份材料超出安全核对内存范围")
            text = content.decode("utf-8")
            values = (
                [json.loads(line) for line in text.splitlines() if line.strip()]
                if path.suffix.lower() == ".jsonl"
                else [json.loads(text)]
            )
            _safe, changed = guard_value(values, self._registry)
            if changed:
                raise BackupError("结构化备份材料含敏感材料")
            return content
        except (UnicodeError, json.JSONDecodeError) as error:
            raise BackupError("结构化备份材料无法安全核对") from error

    def _copy_safe_file(self, source: Path, destination: Path, expected_digest: str) -> None:
        if _chain_has_symlink(source) or _chain_has_symlink(destination):
            raise BackupError("备份复制路径含链接")
        try:
            with source.open("rb") as input_file:
                content = self._safe_json_bytes(source, input_file)
                with destination.open("xb") as output_file:
                    digest, _size = copy_unchanged_safe_bytes(
                        io.BytesIO(content) if content is not None else input_file,
                        output_file,
                        registry=self._registry,
                    )
                    output_file.flush()
                    os.fsync(output_file.fileno())
            if digest != expected_digest:
                raise BackupError("备份材料在核对后变化，拒绝发布")
            shutil.copystat(source, destination, follow_symlinks=False)
        except UnsafeMaterialError as error:
            raise BackupError(str(error)) from error

    def create(self, destination: Path) -> Path:
        if _chain_has_symlink(destination):
            raise BackupError("备份目标路径含链接，拒绝创建")
        destination = destination.resolve()
        if destination.exists() and any(destination.iterdir()):
            raise BackupError("备份目标非空，拒绝覆盖永久材料")
        if destination == self.root:
            raise BackupError("备份目标不能是活动工作空间")
        from .maintenance import detect_activity_blocker

        activity = detect_activity_blocker(self.root)
        names = (*_PERMANENT_FILES, *_PERMANENT_DIRS)
        if activity is None:
            names += (_SPOOL_DIR,)
        planned: dict[str, Path] = {}
        for name in sorted(names):
            source = self.root / name
            if _chain_has_symlink(source):
                raise BackupError("永久材料包含链接，拒绝跟随")
            if not source.exists():
                continue
            if source.is_file():
                planned[name] = source
                continue
            for current, dirs, files in os.walk(source, onerror=self._walk_error):
                parent = Path(current)
                kept = []
                for entry in dirs:
                    child = parent / entry
                    if _chain_has_symlink(child):
                        raise BackupError("永久材料包含链接，拒绝跟随")
                    if child.resolve() != destination:
                        kept.append(entry)
                dirs[:] = kept
                for entry in files:
                    child = parent / entry
                    if _chain_has_symlink(child):
                        raise BackupError("永久材料包含链接，拒绝跟随")
                    if child.resolve().is_relative_to(destination):
                        continue
                    planned[child.relative_to(self.root).as_posix()] = child
        # Validate the entire frozen closure before creating any destination.
        # Redaction would invalidate record/object hashes, so reject unsafe legacy
        # material rather than silently rewriting a supposedly exact backup.
        _safe, changed = guard_value(list(planned), self._registry)
        if changed:
            raise BackupError("备份路径包含敏感材料")
        manifest = {name: self._safe_digest(source) for name, source in planned.items()}
        _safe, changed = guard_value(manifest, self._registry)
        if changed:
            raise BackupError("备份清单摘要含敏感材料，不能安全保存")
        destination.mkdir(parents=True, exist_ok=True)
        for name, source in planned.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            self._copy_safe_file(source, target, manifest[name])
        atomic.write_json(
            destination / "backup.json",
            {
                "schema": _BACKUP_SCHEMA,
                "files": manifest,
                "excluded_active": [_SPOOL_DIR] if activity is not None else [],
            },
        )
        return destination

    @staticmethod
    def _walk_error(error: OSError) -> None:
        raise error

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def verify(self, backup: Path) -> dict[str, object]:
        if _chain_has_symlink(backup):
            raise BackupError("备份来源路径含链接")
        backup = backup.resolve()
        manifest = _read_manifest(backup)
        _safe, changed = guard_value(list(manifest), self._registry)
        if changed:
            raise BackupError("备份路径包含敏感材料")
        errors: list[str] = []
        for name, digest in manifest.items():
            path = _contained_path(backup, name)
            if (
                path is None
                or not path.is_file()
                or _chain_has_symlink(backup / name)
                or self._safe_digest(path) != digest
            ):
                errors.append(name)
        # 闭包双向核对：备份内任何未登记文件（事后塞入/残留）都使备份不再
        # 是冻结闭包，不得作为迁移前可校验备份验收（A-06）。
        manifested = set(manifest)
        for present in backup.rglob("*"):
            if _chain_has_symlink(present):
                errors.append("<linked material>")
                continue
            if not present.is_file() or present.name == "backup.json":
                continue
            try:
                relative = present.relative_to(backup).as_posix()
            except ValueError:
                continue
            if _chain_has_symlink(present) or relative not in manifested:
                errors.append(relative)
        return {"ok": not errors, "errors": sorted(set(errors))}

    def restore(self, *, backup: Path, target: Path) -> RestoreReport:
        """把已校验备份恢复到空目标，恢复后按清单重新核对。

        从不覆盖非空目录；调用方须显式提供新的恢复目标。清单含越界/绝对
        路径时整体拒绝（不写入任何文件）并返回 ``state="rejected"``；
        内容摘要不符属于备份损坏，抛 :class:`BackupError`。
        """
        if _chain_has_symlink(backup):
            raise BackupError("备份来源路径含链接")
        backup = backup.resolve()
        if _chain_has_symlink(backup / "backup.json"):
            raise BackupError("备份清单路径含链接")
        # 先按原始路径核对目标及各现存层的符号链接，再 resolve：否则链接
        # 已被跟随，后续 is_symlink() 永远为假，写入会落到链接对端（A-06）。
        if _chain_has_symlink(target):
            raise BackupError("恢复目标路径含符号链接，拒绝恢复")
        target = target.resolve()

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
                or _chain_has_symlink(backup / name)
                or _chain_has_symlink(target / name)
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

        verified = self.verify(backup)
        if not verified["ok"]:
            raise BackupError("备份闭包或摘要无法核实，拒绝恢复")
        if target.exists() and any(target.iterdir()):
            raise BackupError("恢复目标非空，拒绝覆盖")
        post_errors: list[str] = []
        target.mkdir(parents=True, exist_ok=True)
        files_restored = 0
        bytes_restored = 0
        for name, digest in manifest.items():
            destination = destinations[name]
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._copy_safe_file(backup / name, destination, digest)
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
