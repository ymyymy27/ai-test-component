"""读取、固定与物化历史字节，拒绝路径逃逸。

文件型 SourceSnapshotPort 适配：

- ``pin``：遍历目标目录，对真实字节流式计算 SHA256，并把字节不可变地保存
  到内容寻址 blob 区（``snapshots/blobs/<sha256>``）。源码随后变化时，
  旧快照仍能取到当时的历史字节；
- ``materialize``：只从固定 blob 按清单复制到空目录并再核对摘要，不读活动
  源码；清单逐项做路径限界，越界整体拒绝且不写目标；
- ``read_pinned``：按稳定标识读元数据，不重新扫描；
- ``detect_changes``：按清单的选定范围与排除规则重新扫描比对增/删/改。

默认排除 ``.git``（Git 身份走 SourceControl）。内容相同 → 相同 snapshot_id
（幂等）；快照清单一经写入不可变，不同 purpose 的重复 pin 不覆盖既有元数据。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from contextlib import suppress
from pathlib import Path
from typing import IO, Final

from aitest.infrastructure.file_store.atomic import write_json

_SCHEMA: Final = "aitest.source-snapshot/1.0"
_HASH_BLOCK: Final = 64 * 1024
_DEFAULT_EXCLUSIONS: Final = (".git",)
_DIGEST_RE: Final = re.compile(r"[0-9a-f]{64}")


class SnapshotError(RuntimeError):
    """路径不存在/越界、目标非空或物化核对失败。"""


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(_HASH_BLOCK):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _fsync_file(handle: IO[bytes]) -> None:
    """显式落盘文件数据；失败必须上抛，禁止在无耐久保证时声称发布成功。"""
    handle.flush()
    os.fsync(handle.fileno())


def _fsync_directory(directory: Path) -> None:
    """尽力持久化目录项（rename 结果）。

    POSIX 上 fsync 目录 fd 才能保证 rename 掉电不丢；Windows 不支持对
    目录 fd fsync，目录项持久化由文件数据 fsync + 原子替换 + NTFS
    journaling 兜底，故该平台直接跳过且不报错。
    """
    if os.name != "posix":
        return
    _try_fsync_directory(directory)


def _try_fsync_directory(directory: Path) -> None:
    """实际打开目录并 fsync；任何 OSError 都吞掉（尽力而为）。"""
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # 某些文件系统/平台不允许打开或 fsync 目录：尽力而为，不影响发布。
        pass
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _is_excluded(relative: str, rules: tuple[str, ...]) -> bool:
    parts = relative.split("/")
    return any(
        relative == rule or any(prefix == rule for prefix in parts[:-1])
        for rule in rules
    )


def _is_safe_relative(value: str) -> bool:
    if not value or "\x00" in value:
        return False
    pure = Path(value)
    if pure.is_absolute() or pure.drive or pure.anchor:
        return False
    return not any(part == ".." for part in pure.parts)


def _files(record: dict[str, object]) -> list[dict[str, object]]:
    raw = record.get("files", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _exclusion_rules(record: dict[str, object]) -> tuple[str, ...]:
    raw = record.get("exclusion_rules", [])
    if not isinstance(raw, list):
        return ()
    return tuple(str(rule) for rule in raw)


def _selected_paths(record: dict[str, object]) -> tuple[str, ...]:
    raw = record.get("selected_paths", [])
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


class FileSourceSnapshotStore:
    """SourceSnapshotPort 的文件实现。"""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()
        self._dir = self._root / "snapshots"
        self._blobs = self._dir / "blobs"
        self._blobs.mkdir(parents=True, exist_ok=True)

    def pin(
        self,
        *,
        canonical_path: str,
        purpose: str,
        selected_paths: tuple[str, ...] | list[str] = (),
        exclusion_rules: tuple[str, ...] | list[str] = (),
    ) -> dict[str, object]:
        source = Path(canonical_path).resolve()
        if not source.exists() or not source.is_dir():
            raise SnapshotError(f"源码目录不存在: {canonical_path}")
        selection = tuple(dict.fromkeys(selected_paths))
        for item in selection:
            if not _is_safe_relative(item):
                raise SnapshotError(f"选定路径越界或非法: {item}")
        rules = tuple(dict.fromkeys((*_DEFAULT_EXCLUSIONS, *exclusion_rules)))

        files: list[dict[str, object]] = []
        for current_root, _dirs, names in os.walk(source):
            current_dir = Path(current_root)
            for name in names:
                path = current_dir / name
                if path.is_symlink():
                    raise SnapshotError(f"拒绝符号链接，防止越界固定: {path}")
                relative = path.relative_to(source).as_posix()
                if _is_excluded(relative, rules):
                    continue
                if selection and not self._matches_selection(relative, selection):
                    continue
                digest, size = _hash_file(path)
                self._store_blob(path, digest)
                files.append(
                    {"relative_path": relative, "size": size, "sha256": digest}
                )
        files.sort(key=lambda item: str(item["relative_path"]))

        identity_base = json.dumps(
            [source.as_posix(), files], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        snapshot_id = "snap-" + hashlib.sha256(identity_base).hexdigest()[:16]

        record_path = self._path(snapshot_id)
        if record_path.exists():
            # 内容寻址快照幂等：既有清单不可变，重复 pin 不覆盖 purpose 等元数据。
            return dict(json.loads(record_path.read_text(encoding="utf-8")))

        record = {
            "schema": _SCHEMA,
            "snapshot_id": snapshot_id,
            "purpose": purpose,
            "canonical_path": source.as_posix(),
            "selected_paths": list(selection),
            "exclusion_rules": rules,
            "files": files,
        }
        write_json(record_path, record)
        # 清单原子发布后持久化目录项，掉电后快照身份与 blob 引用同时可达。
        _fsync_directory(record_path.parent)
        return dict(record)

    def read_pinned(self, snapshot_id: str) -> dict[str, object]:
        path = self._path(snapshot_id)
        if not path.exists():
            raise SnapshotError(f"快照不存在: {snapshot_id}")
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def materialize(self, snapshot_id: str, destination: str) -> dict[str, object]:
        record = self.read_pinned(snapshot_id)
        target = Path(destination).resolve()
        files = _files(record)

        # 先逐项限界并确认固定字节可达：任何越界/缺 blob 都整体拒绝，
        # 不创建或写入目标。
        planned: list[tuple[dict[str, object], Path, Path]] = []
        refused: list[str] = []
        for item in files:
            name = item.get("relative_path")
            digest = item.get("sha256")
            if (
                not isinstance(name, str)
                or not isinstance(digest, str)
                or not _is_safe_relative(name)
                or not _DIGEST_RE.fullmatch(digest)
            ):
                refused.append(str(name))
                continue
            destination_path = (target / name).resolve()
            blob_path = (self._blobs / digest).resolve()
            if (
                not destination_path.is_relative_to(target)
                or not blob_path.is_relative_to(self._blobs)
                or blob_path.is_symlink()
                or not blob_path.is_file()
            ):
                refused.append(name)
                continue
            planned.append((item, blob_path, destination_path))
        if refused:
            return {
                "snapshot_id": snapshot_id,
                "destination": target.as_posix(),
                "materialized": [],
                "verified": False,
                "state": "rejected",
                "refused": sorted(set(refused)),
            }

        if target.exists() and any(target.iterdir()):
            raise SnapshotError("物化目标非空，拒绝覆盖")
        target.mkdir(parents=True, exist_ok=True)

        copied: list[str] = []
        durable_directories: set[Path] = set()
        for item, blob_path, destination_path in planned:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            # 物化是给执行使用的来源投影，同样显式 fsync，掉电后不出现
            # 目录项存在但内容为零/残缺的文件（A-08）。
            with blob_path.open("rb") as src, destination_path.open("wb") as dst:
                shutil.copyfileobj(src, dst, _HASH_BLOCK)
                _fsync_file(dst)
            shutil.copystat(blob_path, destination_path, follow_symlinks=True)
            durable_directories.add(destination_path.parent)
            copied.append(str(item["relative_path"]))
        for directory in durable_directories:
            _fsync_directory(directory)

        mismatches: list[str] = []
        for item, _blob_path, destination_path in planned:
            digest, _size = _hash_file(destination_path)
            if digest != item["sha256"]:
                mismatches.append(str(item["relative_path"]))
        if mismatches:
            raise SnapshotError(f"物化后核对失败: {mismatches}")
        return {
            "snapshot_id": snapshot_id,
            "destination": target.as_posix(),
            "materialized": copied,
            "verified": True,
            "state": "materialized",
        }

    def detect_changes(self, snapshot_id: str) -> dict[str, object]:
        record = self.read_pinned(snapshot_id)
        source_root = Path(str(record["canonical_path"]))
        if not source_root.exists():
            return {"state": "unknown", "reason": "源码目录已不存在", "changed": True}
        pinned = {
            str(item["relative_path"]): str(item["sha256"])
            for item in _files(record)
        }
        rules = _exclusion_rules(record)
        selection = _selected_paths(record)
        current: dict[str, str] = {}
        for current_root, _dirs, names in os.walk(source_root):
            current_dir = Path(current_root)
            for name in names:
                path = current_dir / name
                relative = path.relative_to(source_root).as_posix()
                if _is_excluded(relative, rules):
                    continue
                if selection and not self._matches_selection(relative, selection):
                    continue
                digest, _size = _hash_file(path)
                current[relative] = digest

        added = sorted(set(current) - set(pinned))
        removed = sorted(set(pinned) - set(current))
        modified = sorted(
            name for name in set(current) & set(pinned) if current[name] != pinned[name]
        )
        return {
            "state": "changed" if (added or removed or modified) else "unchanged",
            "added": added,
            "removed": removed,
            "modified": modified,
        }

    # ----- 内部 -------------------------------------------------------

    def _path(self, snapshot_id: str) -> Path:
        if not snapshot_id.replace("-", "").isalnum():
            raise SnapshotError(f"非法快照标识: {snapshot_id}")
        return self._dir / f"{snapshot_id}.json"

    def _store_blob(self, source: Path, digest: str) -> None:
        blob = self._blobs / digest
        if blob.exists():
            return
        blob.parent.mkdir(parents=True, exist_ok=True)
        temporary = blob.with_name(f".{digest}.tmp")
        try:
            with source.open("rb") as src, temporary.open("wb") as dst:
                shutil.copyfileobj(src, dst, _HASH_BLOCK)
                # 耐久发布合同（A-08）：字节必须显式 fsync 后才允许进入
                # 内容寻址区；fsync 失败直接上抛，绝不静默发布未落盘字节。
                _fsync_file(dst)
            actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if actual != digest:
                raise SnapshotError(f"固定字节摘要不符: {source}")
            os.replace(temporary, blob)
            # 持久化 rename 目录项（POSIX）；不支持的平台尽力而为。
            _fsync_directory(blob.parent)
        except BaseException:
            with suppress(FileNotFoundError):
                temporary.unlink()
            raise

    @staticmethod
    def _matches_selection(
        relative: str, selected_paths: tuple[str, ...] | list[str]
    ) -> bool:
        return any(
            relative == item
            or relative.startswith(item.rstrip("/") + "/")
            for item in selected_paths
        )


__all__ = ["FileSourceSnapshotStore", "SnapshotError"]
