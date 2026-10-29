"""Closure backup and verification; no business material deletion."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from aitest.domain.json_material import decode_json
from aitest.infrastructure import path_compat as compat
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
#: 恢复副本的只读隔离标记；存在即表示不得与仍持有活动的活核心双接管。
_ISOLATION_NAME: Final = "restore-isolation.json"
_ISOLATION_SCHEMA: Final = "aitest.restore-isolation/1.0"

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
    #: 活动核实：原空间仍有在途执行时的阻塞原因（无活动为 None）。
    source_activity: str | None = None
    #: 原空间有活动执行时，恢复副本被标为只读隔离，不得与活核心双接管。
    isolated: bool = False
    #: 恢复副本中需要按待核实处理的执行材料（历史活动步骤不恢复成已完成）。
    pending_verification: tuple[str, ...] = ()
    #: 恢复副本的索引是否可直接使用；缺失即要求显式重建。
    index_rebuild_required: bool = False


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
        _long_path(path).is_symlink() or compat.is_junction(_long_path(path))
        for path in (raw_target, *raw_target.parents)
    )


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _long_path(path: Path) -> Path:
    """Windows 长路径：超过 MAX_PATH 时改用扩展长度前缀读写。

    未启用长路径的 Windows 上，深层迁移步骤/分代材料的绝对路径可能超过
    260 字符：``is_file()`` 会静默返回 False、``open()`` 抛 WinError 206。
    备份/恢复核对与复制统一经此转换，避免"写入成功却读不回"或"建目录失败"。
    """

    if sys.platform != "win32":
        return path
    absolute = str(path.absolute())
    if absolute.startswith("\\\\?\\") or len(absolute) < 240:
        return path
    return Path("\\\\?\\" + absolute)


def _integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _load_json(root: Path, name: str) -> dict[str, object]:
    """读回根级结构化事实；缺失/损坏返回空对象（调用方据此登记"未知"）。"""

    try:
        payload = decode_json((root / name).read_bytes())
    except (OSError, ValueError, UnicodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def closure_facts(root: Path) -> dict[str, object]:
    """备份清单必须登记的冻结上下文（架构 04 §6/§10）。

    登记 `root_commit`、工作空间身份、`generation`、`writer_epoch` 与事件有效
    边界；读不到的项显式写 `None`，不猜测、不补造。
    """

    current = _load_json(root, "current.json")
    workspace = _load_json(root, "workspace.json")
    commit_id = _integer(current.get("commit_id"))
    cursor = _text(current.get("event_cursor"))
    index_root = current.get("index_root")
    event_root = current.get("event_root")
    generation = (
        _integer(current.get("generation"))
        or _integer(index_root.get("generation") if isinstance(index_root, dict) else None)
        or _integer(event_root.get("generation") if isinstance(event_root, dict) else None)
    )
    # root_commit 是 current 指针指向的已提交清单的内容身份（manifest_digest）；
    # 旧指针没有该字段时退回提交序号，读不到才显式写 None。
    root_commit = (
        _text(current.get("root_commit"))
        or _text(current.get("manifest_digest"))
        or (str(commit_id) if commit_id is not None else None)
        or (_text(current.get("commit_sequence")))
    )
    return {
        "root_commit": root_commit,
        "workspace_id": _text(workspace.get("workspace_id"))
        or _text(current.get("workspace_id")),
        "generation": generation,
        "writer_epoch": _integer(workspace.get("writer_epoch")),
        "event_boundary": {
            "commit_id": commit_id,
            "cursor": cursor,
        },
    }


def _pending_verification(target: Path) -> tuple[str, ...]:
    """列出恢复副本中必须按"待核实"处理的活动材料（不恢复成已完成）。"""

    found: list[str] = []
    active = target / _ACTIVE_MARKER
    if active.is_file():
        found.append(_ACTIVE_MARKER.as_posix())
    for directory in ("checkpoints", "execution-handles"):
        base = target / directory
        if not base.is_dir():
            continue
        for entry in sorted(base.rglob("*")):
            if entry.is_file() and not entry.is_symlink():
                found.append(entry.relative_to(target).as_posix())
    return tuple(found[:64])


def backup_closure(backup: Path) -> dict[str, object]:
    """读回备份清单登记的冻结上下文；缺失/损坏如实返回空对象。"""

    raw = _read_backup_json(backup)
    if not isinstance(raw, dict):
        return {}
    boundary = raw.get("event_boundary")
    return {
        "root_commit": raw.get("root_commit"),
        "workspace_id": raw.get("workspace_id"),
        "generation": raw.get("generation"),
        "writer_epoch": raw.get("writer_epoch"),
        "event_boundary": boundary if isinstance(boundary, dict) else {},
    }


def _read_backup_json(backup: Path) -> object:
    if _chain_has_symlink(backup / "backup.json"):
        raise BackupError("备份清单路径含链接")
    try:
        with (backup / "backup.json").open("rb") as source:
            content = source.read(_MAX_JSON_BYTES + 1)
        if len(content) > _MAX_JSON_BYTES:
            raise BackupError("备份清单超出安全核对内存范围")
        return decode_json(content)
    except (OSError, ValueError) as error:
        raise BackupError("备份清单无法可靠核对") from error


def _read_manifest(backup: Path) -> dict[str, str]:
    raw = _read_backup_json(backup)
    if not isinstance(raw, dict):
        raise BackupError("备份清单不是对象")
    if raw.get("schema") != _BACKUP_SCHEMA:
        raise BackupError("备份清单版本无法核实")
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
            with _long_path(path).open("rb") as source:
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
                [decode_json(line) for line in text.splitlines() if line.strip()]
                if path.suffix.lower() == ".jsonl"
                else [decode_json(text)]
            )
            _safe, changed = guard_value(values, self._registry)
            if changed:
                raise BackupError("结构化备份材料含敏感材料")
            return content
        except (UnicodeError, ValueError) as error:
            raise BackupError("结构化备份材料无法安全核对") from error

    def _copy_safe_file(self, source: Path, destination: Path, expected_digest: str) -> None:
        if _chain_has_symlink(source) or _chain_has_symlink(destination):
            raise BackupError("备份复制路径含链接")
        try:
            # Windows 未启用长路径时，深层迁移步骤文件会超过 MAX_PATH：用扩展
            # 前缀读写同一路径，避免恢复/迁移备份在嵌套目录上静默缺文件。
            target = _long_path(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            with _long_path(source).open("rb") as input_file:
                content = self._safe_json_bytes(source, input_file)
                with target.open("xb") as output_file:
                    digest, _size = copy_unchanged_safe_bytes(
                        io.BytesIO(content) if content is not None else input_file,
                        output_file,
                        registry=self._registry,
                    )
                    output_file.flush()
                    os.fsync(output_file.fileno())
            if digest != expected_digest:
                raise BackupError("备份材料在核对后变化，拒绝发布")
            shutil.copystat(_long_path(source), target, follow_symlinks=False)
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
            _long_path(target).parent.mkdir(parents=True, exist_ok=True)
            self._copy_safe_file(source, target, manifest[name])
        atomic.write_json(
            destination / "backup.json",
            {
                "schema": _BACKUP_SCHEMA,
                "files": manifest,
                "excluded_active": [_SPOOL_DIR] if activity is not None else [],
                **closure_facts(self.root),
            },
        )
        return destination

    @staticmethod
    def _walk_error(error: OSError) -> None:
        raise error

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(_long_path(path).read_bytes()).hexdigest()

    def verify(self, backup: Path) -> dict[str, object]:
        if _chain_has_symlink(backup):
            raise BackupError("备份来源路径含链接")
        backup = backup.resolve()
        manifest = _read_manifest(backup)
        _safe, changed = guard_value(list(manifest), self._registry)
        if changed:
            raise BackupError("备份路径包含敏感材料")
        errors: list[str] = []
        resolved: set[Path] = set()
        for name, digest in manifest.items():
            path = _contained_path(backup, name)
            if (
                path is None
                or Path(name).as_posix() != name
                or path in resolved
                or not _long_path(path).is_file()
                or _chain_has_symlink(backup / name)
                or self._safe_digest(path) != digest
            ):
                errors.append(name)
            if path is not None:
                resolved.add(path)
        # 闭包双向核对：备份内任何未登记文件（事后塞入/残留）都使备份不再
        # 是冻结闭包，不得作为迁移前可校验备份验收（A-06）。
        manifested = set(manifest)
        for present in backup.rglob("*"):
            if _chain_has_symlink(present):
                errors.append("<linked material>")
                continue
            if not present.is_file() or present == backup / "backup.json":
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

        raw = _read_backup_json(backup)
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
                or not _long_path(source).is_file()
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
            _long_path(destination).parent.mkdir(parents=True, exist_ok=True)
            self._copy_safe_file(backup / name, destination, digest)
            files_restored += 1
            bytes_restored += _long_path(destination).stat().st_size
            if self._digest(destination) != digest:
                post_errors.append(name)
        if post_errors:
            raise BackupError(f"恢复后核对失败: {post_errors}")
        # 恢复到新空目标后按合同核实活动并隔离双接管（架构 04 §9/§10）：
        # 原空间仍有在途执行时，副本标只读隔离；副本内的历史活动材料保持待核实，
        # 不恢复成"已完成"。
        from .maintenance import detect_activity_blocker

        source_activity = detect_activity_blocker(self.root)
        isolated = False
        if source_activity is not None:
            atomic.write_json(
                target / _ISOLATION_NAME,
                {
                    "schema": _ISOLATION_SCHEMA,
                    "read_only": True,
                    "reason": source_activity,
                    "source_workspace_id": str(
                        self._load_workspace_id() or "unknown"
                    ),
                },
            )
            isolated = True
        pending = _pending_verification(target)
        state = "restored" if files_restored else "empty"
        return RestoreReport(
            backup=backup,
            target=target,
            files_restored=files_restored,
            bytes_restored=bytes_restored,
            verified=True,
            state=state,
            source_activity=source_activity,
            isolated=isolated,
            pending_verification=pending,
            index_rebuild_required=not (
                _long_path(target / "indexes.json").is_file()
                or _long_path(target / "indexes").is_dir()
            ),
        )

    def _load_workspace_id(self) -> str | None:
        value = _text(_load_json(self.root, "workspace.json").get("workspace_id"))
        return value


def manifest_facts(destination: Path) -> tuple[str, int, int]:
    """返回备份的 (清单摘要, 文件数, 总字节)（规范字节口径，与备份身份同一来源）。

    迁移前强制备份也要能进入维护审计（架构 04 §9/§10/§197：维护记录给出检查范围、
    已完成项与材料缺口），因此把清单读回、计数与摘要计算放在这里**一次**，
    避免迁移模块另算一套口径。
    """

    manifest = _read_manifest(destination)
    canonical = json.dumps(
        dict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    total = 0
    for name in manifest:
        path = _contained_path(destination, name)
        if path is None or not _long_path(path).is_file():
            raise BackupError(f"备份材料不可读: {name}")
        total += _long_path(path).stat().st_size
    return digest, len(manifest), total


__all__ = [
    "FileBackupStore",
    "RestoreReport",
    "backup_closure",
    "closure_facts",
    "manifest_facts",
]
