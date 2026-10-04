"""读取、固定与物化历史字节，拒绝路径逃逸。

文件型 SourceSnapshotPort 适配：

- ``pin``：遍历目标目录，对真实字节流式计算 SHA256，并把字节不可变地保存
  到内容寻址 blob 区（``snapshots/blobs/<sha256>``）。源码随后变化时，
  旧快照仍能取到当时的历史字节；
- ``materialize``：只从固定 blob 按清单复制到空目录并再核对摘要，不读活动
  源码；清单逐项做路径限界，越界整体拒绝且不写目标；
- ``read_pinned``：按稳定标识读元数据，不重新扫描；
- ``detect_changes``：按清单的选定范围与排除规则重新扫描比对增/删/改。

默认排除 ``.git``（Git 身份走 SourceControl）。同一来源、同一选定范围/排除
规则/用途且内容相同 → 相同 snapshot_id（幂等）；范围或用途不同即使字节完全
相同也产生不同快照身份与各自清单（blob 仍按内容去重）。不同 purpose 的重复
pin 不覆盖既有元数据。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import suppress
from pathlib import Path
from typing import IO, Final

from aitest.infrastructure.file_store.atomic import write_json
from aitest.infrastructure.security import (
    KnownSecretRegistry,
    UnsafeMaterialError,
    copy_unchanged_safe_bytes,
    guard_value,
    known_secrets,
)

_SCHEMA: Final = "aitest.source-snapshot/1.0"
_HASH_BLOCK: Final = 64 * 1024
_MAX_UNRESOLVED_SOURCE_BYTES: Final = 1024 * 1024
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
    return any(relative == rule or any(prefix == rule for prefix in parts[:-1]) for rule in rules)


def _is_safe_relative(value: str) -> bool:
    if not value or "\x00" in value:
        return False
    pure = Path(value)
    if pure.is_absolute() or pure.drive or pure.anchor:
        return False
    return not any(part == ".." for part in pure.parts)


def _has_link_ancestor(path: Path) -> bool:
    return any(item.is_symlink() or item.is_junction() for item in (path, *path.parents))


def _files(record: dict[str, object]) -> list[dict[str, object]]:
    raw = record.get("files")
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise SnapshotError("源码快照清单损坏，不能解释为空源码")
    return raw


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

    def __init__(
        self, workspace_root: Path, *, registry: KnownSecretRegistry | None = None
    ) -> None:
        self._root = workspace_root.resolve()
        self._registry = registry if registry is not None else known_secrets()
        self._dir = self._root / "snapshots"
        self._blobs = self._dir / "blobs"
        if _has_link_ancestor(workspace_root) or _has_link_ancestor(self._blobs):
            raise SnapshotError("源码快照工作空间不能经过链接")
        self._blobs.mkdir(parents=True, exist_ok=True)

    def pin(
        self,
        *,
        canonical_path: str,
        purpose: str,
        selected_paths: tuple[str, ...] | list[str] = (),
        exclusion_rules: tuple[str, ...] | list[str] = (),
    ) -> dict[str, object]:
        raw_source = Path(canonical_path)
        if _has_link_ancestor(raw_source):
            raise SnapshotError("源码目录不能经过链接")
        source = raw_source.resolve()
        if not source.exists() or not source.is_dir():
            raise SnapshotError(f"源码目录不存在: {canonical_path}")
        selection = tuple(dict.fromkeys(selected_paths))
        for item in selection:
            if not _is_safe_relative(item):
                raise SnapshotError(f"选定路径越界或非法: {item}")
        rules = tuple(dict.fromkeys((*_DEFAULT_EXCLUSIONS, *exclusion_rules)))
        self._require_safe_metadata([source.as_posix(), purpose, selection, rules])

        files: list[dict[str, object]] = []
        for current_root, dirs, names in os.walk(source, onerror=self._walk_error):
            current_dir = Path(current_root)
            self._limit_walk(source, current_dir, dirs, rules, selection)
            for name in names:
                path = current_dir / name
                relative = path.relative_to(source).as_posix()
                if _is_excluded(relative, rules):
                    continue
                if selection and not self._matches_selection(relative, selection):
                    continue
                if _has_link_ancestor(path):
                    raise SnapshotError("选定源码不能经过链接")
                self._require_safe_metadata(relative)
                digest, size = _hash_file(path)
                self._store_blob(path, digest)
                files.append({"relative_path": relative, "size": size, "sha256": digest})
        files.sort(key=lambda item: str(item["relative_path"]))

        # 快照**记录身份**与内容对象去重分离（A-15）：blob 仍按 sha256
        # 内容寻址去重，但 snapshot_id 必须区分 canonical_path、选定范围、
        # 排除规则与用途——先 pin 单文件再 pin 整目录不能共用同一清单，
        # 否则整目录新增文件后按旧清单比对会误报 unchanged。
        identity_base = json.dumps(
            [
                source.as_posix(),
                purpose,
                list(selection),
                list(rules),
                files,
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        snapshot_id = "snap-" + hashlib.sha256(identity_base).hexdigest()[:16]

        record_path = self._path(snapshot_id)
        if _has_link_ancestor(record_path):
            raise SnapshotError("源码快照清单不能经过链接")
        if record_path.exists():
            # 内容寻址快照幂等：既有清单不可变，重复 pin 不覆盖 purpose 等元数据。
            return self.read_pinned(snapshot_id)

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
        if _has_link_ancestor(path):
            raise SnapshotError("源码快照清单不能经过链接")
        if not path.exists():
            raise SnapshotError(f"快照不存在: {snapshot_id}")
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise SnapshotError("源码快照清单不是对象")
        self._require_safe_metadata(record)
        files = _files(record)
        if (
            record.get("schema") != _SCHEMA
            or record.get("snapshot_id") != snapshot_id
            or not isinstance(record.get("canonical_path"), str)
            or not isinstance(record.get("purpose"), str)
            or not isinstance(record.get("selected_paths"), list)
            or not isinstance(record.get("exclusion_rules"), list)
        ):
            raise SnapshotError("源码快照身份或范围无法核实")
        if (
            not Path(record["canonical_path"]).is_absolute()
            or "\x00" in record["canonical_path"]
            or any(
                not isinstance(item, str) or not _is_safe_relative(item)
                for item in record["selected_paths"]
            )
            or any(
                not isinstance(item, str) or "\x00" in item for item in record["exclusion_rules"]
            )
        ):
            raise SnapshotError("源码快照范围条目无法核实")
        paths: set[str] = set()
        for item in files:
            name, digest, size = item.get("relative_path"), item.get("sha256"), item.get("size")
            if (
                not isinstance(name, str)
                or not _is_safe_relative(name)
                or name in paths
                or not isinstance(digest, str)
                or not _DIGEST_RE.fullmatch(digest)
                or type(size) is not int
                or size < 0
            ):
                raise SnapshotError("源码快照文件引用损坏")
            paths.add(name)
        identity_bytes = json.dumps(
            [
                record["canonical_path"],
                record["purpose"],
                record["selected_paths"],
                record["exclusion_rules"],
                files,
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if "snap-" + hashlib.sha256(identity_bytes).hexdigest()[:16] != snapshot_id:
            raise SnapshotError("源码快照清单摘要无法核实")
        return record

    def materialize(self, snapshot_id: str, destination: str) -> dict[str, object]:
        record = self.read_pinned(snapshot_id)
        raw_target = Path(destination)
        if _has_link_ancestor(raw_target):
            raise SnapshotError("源码物化目标不能经过链接")
        target = raw_target.resolve()
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
            raw_blob = self._blobs / digest
            if _has_link_ancestor(raw_blob):
                refused.append(name)
                continue
            blob_path = raw_blob.resolve()
            if (
                not destination_path.is_relative_to(target)
                or not blob_path.is_relative_to(self._blobs)
                or blob_path.is_symlink()
                or not blob_path.is_file()
            ):
                refused.append(name)
                continue
            planned.append((item, blob_path, destination_path))
            actual_digest, actual_size = self._copy_safe_bytes(blob_path)
            if actual_digest != digest or actual_size != item["size"]:
                refused.append(name)
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
                self._copy_safe_stream(src, dst)
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
        if _has_link_ancestor(source_root):
            return {"state": "unknown", "reason": "源码目录经过链接", "changed": True}
        if not source_root.is_dir():
            return {"state": "unknown", "reason": "源码目录已不存在", "changed": True}
        pinned = {str(item["relative_path"]): str(item["sha256"]) for item in _files(record)}
        rules = _exclusion_rules(record)
        selection = _selected_paths(record)
        current: dict[str, str] = {}
        try:
            for current_root, dirs, names in os.walk(source_root, onerror=self._walk_error):
                current_dir = Path(current_root)
                self._limit_walk(source_root, current_dir, dirs, rules, selection)
                for name in names:
                    path = current_dir / name
                    relative = path.relative_to(source_root).as_posix()
                    if _is_excluded(relative, rules):
                        continue
                    if selection and not self._matches_selection(relative, selection):
                        continue
                    if _has_link_ancestor(path):
                        raise SnapshotError("选定源码不能经过链接")
                    digest, _size = _hash_file(path)
                    current[relative] = digest
        except (OSError, SnapshotError):
            return {"state": "unknown", "reason": "选定源码无法完整核实", "changed": True}

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
        if _has_link_ancestor(source) or _has_link_ancestor(blob):
            raise SnapshotError("源码或对象不能经过链接")
        if blob.exists():
            actual, _size = self._copy_safe_bytes(blob)
            if actual != digest:
                raise SnapshotError("现有源码对象摘要无法核实，拒绝复用")
            return
        blob.parent.mkdir(parents=True, exist_ok=True)
        temporary = blob.with_name(f".{digest}.{uuid.uuid4().hex}.tmp")
        try:
            with source.open("rb") as src, temporary.open("xb") as dst:
                actual, _size = self._copy_safe_stream(src, dst)
                # 耐久发布合同（A-08）：字节必须显式 fsync 后才允许进入
                # 内容寻址区；fsync 失败直接上抛，绝不静默发布未落盘字节。
                _fsync_file(dst)
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
    def _walk_error(error: OSError) -> None:
        raise error

    @classmethod
    def _limit_walk(
        cls,
        root: Path,
        current: Path,
        dirs: list[str],
        rules: tuple[str, ...],
        selection: tuple[str, ...],
    ) -> None:
        kept: list[str] = []
        for name in dirs:
            path = current / name
            relative = path.relative_to(root).as_posix()
            if _is_excluded(relative, rules):
                continue
            if selection and not (
                cls._matches_selection(relative, selection)
                or any(item.startswith(relative + "/") for item in selection)
            ):
                continue
            if _has_link_ancestor(path):
                raise SnapshotError("选定源码目录不能经过链接")
            kept.append(name)
        dirs[:] = kept

    def _require_safe_metadata(self, value: object) -> None:
        _safe, changed = guard_value(value, self._registry)
        if changed:
            raise SnapshotError("源码路径或清单含敏感材料，不能安全固定")

    def _copy_safe_bytes(self, source: Path) -> tuple[str, int]:
        if _has_link_ancestor(source):
            raise SnapshotError("源码对象不能是链接")
        with source.open("rb") as src:
            return self._copy_safe_stream(src)

    def _copy_safe_stream(self, src: IO[bytes], dst: IO[bytes] | None = None) -> tuple[str, int]:
        # Only filtered bytes may reach a temporary file. Filtering changes the
        # claimed original source, so reject rather than publish a modified identity.
        try:
            return copy_unchanged_safe_bytes(
                src,
                dst,
                registry=self._registry,
                block_size=_HASH_BLOCK,
                max_pending_bytes=_MAX_UNRESOLVED_SOURCE_BYTES,
            )
        except UnsafeMaterialError as error:
            raise SnapshotError(str(error)) from error

    @staticmethod
    def _matches_selection(relative: str, selected_paths: tuple[str, ...] | list[str]) -> bool:
        return any(
            relative == item or relative.startswith(item.rstrip("/") + "/")
            for item in selected_paths
        )


__all__ = ["FileSourceSnapshotStore", "SnapshotError"]
