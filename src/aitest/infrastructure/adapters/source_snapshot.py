"""读取、复取与物化实际字节，拒绝路径逃逸。

文件型 SourceSnapshotPort 适配：

- ``pin``：遍历目标目录，对真实字节流式计算 SHA256，落盘快照清单；
- ``materialize``：按清单复制到空目录并再核对摘要；
- ``read_pinned``：按稳定标识读元数据，不重新扫描；
- ``detect_changes``：重新扫描与清单比对，列出增/删/改。

默认排除 ``.git``（Git 身份走 SourceControl），排除规则按相对路径精确
匹配或目录前缀匹配。内容相同 → 相同 snapshot_id（幂等）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Final

from aitest.infrastructure.file_store.atomic import write_json

_SCHEMA: Final = "aitest.source-snapshot/1.0"
_HASH_BLOCK: Final = 64 * 1024
_DEFAULT_EXCLUSIONS: Final = (".git",)


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


def _is_excluded(relative: str, rules: tuple[str, ...]) -> bool:
    parts = relative.split("/")
    return any(
        relative == rule or any(prefix == rule for prefix in parts[:-1])
        for rule in rules
    )


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


class FileSourceSnapshotStore:
    """SourceSnapshotPort 的文件实现。"""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()
        self._dir = self._root / "snapshots"
        self._dir.mkdir(parents=True, exist_ok=True)

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
        rules = tuple(dict.fromkeys((*_DEFAULT_EXCLUSIONS, *exclusion_rules)))

        files: list[dict[str, object]] = []
        for current_root, _dirs, names in os.walk(source):
            current_dir = Path(current_root)
            for name in names:
                path = current_dir / name
                relative = path.relative_to(source).as_posix()
                if _is_excluded(relative, rules):
                    continue
                if selected_paths and not self._matches_selection(relative, selected_paths):
                    continue
                digest, size = _hash_file(path)
                files.append(
                    {"relative_path": relative, "size": size, "sha256": digest}
                )
        files.sort(key=lambda item: str(item["relative_path"]))

        identity_base = json.dumps(
            [source.as_posix(), files], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        snapshot_id = "snap-" + hashlib.sha256(identity_base).hexdigest()[:16]

        record = {
            "schema": _SCHEMA,
            "snapshot_id": snapshot_id,
            "purpose": purpose,
            "canonical_path": source.as_posix(),
            "exclusion_rules": rules,
            "files": files,
        }
        write_json(self._path(snapshot_id), record)
        return dict(record)

    def read_pinned(self, snapshot_id: str) -> dict[str, object]:
        path = self._path(snapshot_id)
        if not path.exists():
            raise SnapshotError(f"快照不存在: {snapshot_id}")
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def materialize(self, snapshot_id: str, destination: str) -> dict[str, object]:
        record = self.read_pinned(snapshot_id)
        source_root = Path(str(record["canonical_path"]))
        target = Path(destination).resolve()
        if target.exists() and any(target.iterdir()):
            raise SnapshotError("物化目标非空，拒绝覆盖")
        target.mkdir(parents=True, exist_ok=True)

        files = _files(record)
        copied: list[str] = []
        for item in files:
            name = str(item["relative_path"])
            destination_path = target / name
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_root / name, destination_path)
            copied.append(name)

        mismatches: list[str] = []
        for item in files:
            name = str(item["relative_path"])
            digest, _size = _hash_file(target / name)
            if digest != item["sha256"]:
                mismatches.append(name)
        if mismatches:
            raise SnapshotError(f"物化后核对失败: {mismatches}")
        return {
            "snapshot_id": snapshot_id,
            "destination": target.as_posix(),
            "materialized": copied,
            "verified": True,
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
        current: dict[str, str] = {}
        for current_root, _dirs, names in os.walk(source_root):
            current_dir = Path(current_root)
            for name in names:
                path = current_dir / name
                relative = path.relative_to(source_root).as_posix()
                if _is_excluded(relative, rules):
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
