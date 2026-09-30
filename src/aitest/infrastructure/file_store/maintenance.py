"""完整性、空间诊断与受控临时回收，无业务删除。

只回收**已确认安全**的临时材料：原子发布遗留的隐藏 ``*.tmp`` 文件与零字节
空暂存文件。业务记录、证据、对象、检查点、事件、报告、备份与迁移材料
一律不可回收。回收动作逐条入审计日志，删除前再次校验路径与白名单。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .integrity import check_workspace

_AUDIT_NAME: Final = "audit.jsonl"
_MAINTENANCE_DIR: Final = "maintenance"
_AUDIT_SCHEMA: Final = "aitest.maintenance-audit/1.0"

# 业务与永久材料目录/文件名前缀，诊断分类用
_BUSINESS_MARKERS: Final = frozenset(
    {
        "records.json",
        "objects",
        "spool",
        "checkpoints",
        "events.json",
        "event-log",
        "workspace.json",
        "indexes.json",
        "current.json",
    }
)
_BACKUP_MARKERS: Final = frozenset({"migrations", "backup", "backups"})


class MaintenanceError(RuntimeError):
    """维护操作违反安全合同（路径越界、候选不可回收等）。"""


@dataclass(frozen=True, slots=True)
class ReclaimCandidate:
    """一个经白名单确认可安全回收的候选。"""

    relative_path: str
    size_bytes: int
    reason: str


@dataclass(frozen=True, slots=True)
class SpaceReport:
    """空间诊断结果。"""

    total_bytes: int
    total_files: int
    business_bytes: int
    temp_bytes: int
    backup_bytes: int
    other_bytes: int
    reclaimable_bytes: int
    reclaimable_files: int
    integrity_ok: bool
    integrity_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReclaimReport:
    """回收执行结果。"""

    state: str  # preview | reclaimed
    removed: tuple[str, ...]
    bytes_freed: int
    refused: tuple[str, ...]


def _is_atomic_leftover(path: Path) -> bool:
    name = path.name
    return name.startswith(".") and name.endswith(".tmp")


def _is_empty_staging(path: Path, workspace_root: Path) -> bool:
    if path.stat().st_size != 0:
        return False
    try:
        relative_parent = path.parent.relative_to(workspace_root / "event-log" / "staging")
    except ValueError:
        return False
    return str(relative_parent) in {"", "."}


class FileMaintenanceService:
    """完整性、空间与受控回收的文件实现。"""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()
        self._dir = self._root / _MAINTENANCE_DIR
        self._audit_path = self._dir / _AUDIT_NAME
        self._dir.mkdir(parents=True, exist_ok=True)

    # ----- 空间诊断 ---------------------------------------------------

    def diagnose(self) -> SpaceReport:
        """统计空间占用、分类汇总并核对完整性。"""
        totals = {"business": 0, "temp": 0, "backup": 0, "other": 0}
        total_bytes = 0
        total_files = 0
        for current_root, _dirs, files in os.walk(self._root):
            current_dir = Path(current_root)
            category = self._category(current_dir)
            for name in files:
                path = current_dir / name
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                totals[category] += size
                total_bytes += size
                total_files += 1
        candidates = self.reclaimable()
        reclaimable_bytes = sum(candidate.size_bytes for candidate in candidates)
        integrity = check_workspace(self._root)
        raw_errors = integrity.get("errors", [])
        errors = (
            tuple(str(error) for error in raw_errors)
            if isinstance(raw_errors, list)
            else ()
        )
        return SpaceReport(
            total_bytes=total_bytes,
            total_files=total_files,
            business_bytes=totals["business"],
            temp_bytes=totals["temp"],
            backup_bytes=totals["backup"],
            other_bytes=totals["other"],
            reclaimable_bytes=reclaimable_bytes,
            reclaimable_files=len(candidates),
            integrity_ok=bool(integrity.get("ok")),
            integrity_errors=errors,
        )

    # ----- 候选识别 ---------------------------------------------------

    def reclaimable(self) -> tuple[ReclaimCandidate, ...]:
        """列出全部经白名单确认可安全回收的候选。"""
        candidates: list[ReclaimCandidate] = []
        for current_root, _dirs, files in os.walk(self._root):
            current_dir = Path(current_root)
            for name in files:
                path = current_dir / name
                candidate = self._classify_candidate(path)
                if candidate is not None:
                    candidates.append(candidate)
        candidates.sort(key=lambda candidate: candidate.relative_path)
        return tuple(candidates)

    def _classify_candidate(self, path: Path) -> ReclaimCandidate | None:
        try:
            size = path.stat().st_size
            relative = path.relative_to(self._root).as_posix()
        except (OSError, ValueError):
            return None
        if _is_atomic_leftover(path):
            return ReclaimCandidate(relative, size, "原子发布遗留临时文件")
        if _is_empty_staging(path, self._root):
            return ReclaimCandidate(relative, size, "零字节空暂存文件")
        return None

    # ----- 受控回收 ---------------------------------------------------

    def reclaim(
        self,
        *,
        relative_paths: tuple[str, ...] | list[str] | None = None,
        dry_run: bool = True,
    ) -> ReclaimReport:
        """回收候选；默认仅预览。删除前再次白名单校验。"""
        allowed = {candidate.relative_path: candidate for candidate in self.reclaimable()}
        requested = list(allowed) if relative_paths is None else list(relative_paths)
        removed: list[str] = []
        refused: list[str] = []
        bytes_freed = 0
        for relative in requested:
            candidate = allowed.get(relative)
            if candidate is None or not self._still_safe(relative):
                refused.append(relative)
                continue
            if dry_run:
                continue
            path = self._resolve_within_root(relative)
            size = path.stat().st_size
            path.unlink()
            removed.append(relative)
            bytes_freed += size
            self._audit(relative, size)
        state = "preview" if dry_run else "reclaimed"
        return ReclaimReport(
            state=state,
            removed=tuple(removed),
            bytes_freed=bytes_freed,
            refused=tuple(refused),
        )

    # ----- 内部 -------------------------------------------------------

    def _category(self, directory: Path) -> str:
        try:
            parts = directory.relative_to(self._root).parts
        except ValueError:
            return "other"
        if not parts:
            return "other"
        head = parts[0]
        if head in _BUSINESS_MARKERS:
            return "business"
        if head in _BACKUP_MARKERS:
            return "backup"
        if head in {"transactions", _MAINTENANCE_DIR}:
            return "temp"
        return "other"

    def _resolve_within_root(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise MaintenanceError(f"候选路径越界: {relative}")
        return path

    def _still_safe(self, relative: str) -> bool:
        """删除前重新按白名单判定，防止候选识别后被替换。"""
        path = self._resolve_within_root(relative)
        if not path.exists() or not path.is_file():
            return False
        return _is_atomic_leftover(path) or _is_empty_staging(path, self._root)

    def _audit(self, relative: str, size: int) -> None:
        entry = {
            "schema": _AUDIT_SCHEMA,
            "action": "reclaimed",
            "relative_path": relative,
            "size_bytes": size,
        }
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


__all__ = [
    "FileMaintenanceService",
    "MaintenanceError",
    "ReclaimCandidate",
    "ReclaimReport",
    "SpaceReport",
]
