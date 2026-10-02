"""完整性、空间诊断与受控临时回收，无业务删除。

只回收**经落盘事实证明安全**的临时材料，不允许凭文件名或“同名目标存在”
猜测回收资格。候选必须同时满足：

1. **无真实活动**：不存在活动事务标记（``transactions/active.json``），
   也不存在非空事件暂存（``event-log/staging/*.jsonl``，未封口事务的
   活动输出）；
2. **发布事实在先**：原子暂存（``.<target>.<随机尾>``）对应的正式目标
   已存在，且候选不新于目标（写入中的暂存没有“目标已发布”事实）；
3. **无永久引用**：候选字节的 sha256 不出现在任何永久记录/台账中；
4. **进程已消亡**：``core/`` 下携带 PID 的暂存，其 PID 必须已不可见；
5. **位置白名单**：``spool/``、``workdirs/`` 等活动材料目录永不扫描。

业务记录、证据、对象、检查点、事件、报告、备份与迁移材料一律不可回收。
回收动作逐条入审计日志，删除前再次校验全部事实。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .integrity import _iter_digest_refs, check_workspace

_AUDIT_NAME: Final = "audit.jsonl"
_MAINTENANCE_DIR: Final = "maintenance"
_AUDIT_SCHEMA: Final = "aitest.maintenance-audit/1.0"
_ACTIVE_MARKER: Final = Path("transactions") / "active.json"
_STAGING_DIR: Final = Path("event-log") / "staging"
_MKSTEMP_TAIL: Final = re.compile(r"[A-Za-z0-9_-]{8}")
_PID_TMP: Final = re.compile(r"^\.(?P<base>.+)\.(?P<pid>\d+)\.tmp$")

#: 允许产生裸 ``*.tmp`` 原子遗留的顶层目录。
_TMP_ALLOWED_TOPS: Final = frozenset({"core", "snapshots", "event-log"})
#: 活动材料目录：其中任何文件都不允许按临时遗留回收。
_NEVER_SCAN_TOPS: Final = frozenset({"spool", "workdirs"})

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
    """一个经落盘事实确认可安全回收的候选。"""

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

    state: str  # preview | reclaimed | blocked
    removed: tuple[str, ...]
    bytes_freed: int
    refused: tuple[str, ...]
    blocked_reason: str = ""


def detect_activity_blocker(workspace_root: Path) -> str | None:
    """从落盘事实证明是否存在未核实活动；无活动返回 None。

    两个独立事实来源，任一成立即阻塞回收/迁移，不能只凭活动标记文件
    缺失就把材料当垃圾（A-07）：

    - ``transactions/active.json``：事务边界仍标记在途；
    - ``event-log/staging/*.jsonl`` 非空：存在未封口事务暂存事件，
      发布结果未知，须先由恢复编排核实/抢救。
    """
    root = workspace_root.resolve()
    marker = root / _ACTIVE_MARKER
    if marker.exists():
        return f"活动事务标记存在: {_ACTIVE_MARKER.as_posix()}"
    staging = root / _STAGING_DIR
    if staging.exists():
        for path in staging.glob("*.jsonl"):
            try:
                if path.is_file() and path.stat().st_size > 0:
                    return f"存在未封口事务暂存: {path.relative_to(root).as_posix()}"
            except OSError:
                # 取证失败按活动中处理（保守，不误删）。
                return f"活动暂存状态无法核实: {path.name}"
    return None


def _pid_is_dead(pid: int) -> bool:
    """PID 已不可见才算死亡；取证失败保守按存活处理。

    复用接口层进程取证；导入失败（非 Windows 测试替身等）时保守返回
    False，绝不因取证缺失而回收疑似活动文件。
    """
    if pid <= 0:
        return False
    try:
        from aitest.interfaces.local.pipe import process_exists
    except Exception:  # pragma: no cover - 取证不可用时保守
        return False
    return not process_exists(pid)


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
        """列出全部经活动/引用事实确认可安全回收的候选。"""
        candidates: list[ReclaimCandidate] = []
        referenced = self._referenced_digests()
        for current_root, _dirs, files in os.walk(self._root):
            current_dir = Path(current_root)
            for name in files:
                path = current_dir / name
                candidate = self._classify_candidate(path, referenced)
                if candidate is not None:
                    candidates.append(candidate)
        candidates.sort(key=lambda candidate: candidate.relative_path)
        return tuple(candidates)

    def _referenced_digests(self) -> set[str]:
        """永久记录/台账中声明的全部 sha256 引用（活动暂存除外）。"""
        referenced: set[str] = set()
        for path in self._root.rglob("*"):
            if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                continue
            parts = path.relative_to(self._root).parts
            if parts[:2] == ("event-log", "staging"):
                continue
            if path.suffix.lower() != ".json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            referenced.update(_iter_digest_refs(data))
        return referenced

    def _classify_candidate(
        self, path: Path, referenced: set[str]
    ) -> ReclaimCandidate | None:
        try:
            stat = path.stat()
            size = stat.st_size
            relative = path.relative_to(self._root)
        except (OSError, ValueError):
            return None
        parts = relative.parts
        if not parts or parts[0] in _NEVER_SCAN_TOPS:
            return None
        relative_posix = relative.as_posix()

        if _is_empty_staging(path, self._root):
            return ReclaimCandidate(relative_posix, size, "零字节空暂存文件")

        if not path.name.startswith("."):
            return None

        if size > 0:
            # 引用事实：候选字节本身被永久记录引用时绝不回收。
            with path.open("rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            if digest in referenced:
                return None
        else:
            digest = ""

        candidate = self._classify_temporary(path, stat.st_mtime_ns, digest)
        if candidate is None:
            return None
        return ReclaimCandidate(relative_posix, size, candidate)

    def _classify_temporary(
        self, path: Path, mtime_ns: int, digest: str
    ) -> str | None:
        """按真实发布/进程事实判定隐藏临时文件的回收资格与理由。"""
        name = path.name

        # 形态一：core 启动暂存 .<base>.<pid>.tmp —— PID 必须已消亡。
        pid_match = _PID_TMP.match(name)
        if pid_match is not None and path.parent == self._root / "core":
            pid = int(pid_match.group("pid"))
            if _pid_is_dead(pid):
                return f"进程 {pid} 已消亡的启动暂存"
            return None

        # 形态二：events 边界/位置发布的 .<name>.tmp（以及 snapshots/core
        # 无 PID 形态、工作空间根下的边界文件发布）：正式目标必须已发布
        # 且候选不新于目标。
        if name.endswith(".tmp"):
            at_root = path.parent == self._root
            top = path.relative_to(self._root).parts[0]
            if not at_root and top not in _TMP_ALLOWED_TOPS:
                return None
            base = name[1:-4]
            target = path.with_name(base)
            if not target.is_file():
                return None
            if mtime_ns > target.stat().st_mtime_ns:
                # 候选比正式目标还新：更像在途写入，不是发布遗留。
                return None
            return "正式目标已发布的原子暂存遗留"

        # 形态三：mkstemp 的 .<target>.<8 位随机尾>。
        body = name[1:]
        target_name, separator, tail = body.rpartition(".")
        if not separator or not _MKSTEMP_TAIL.fullmatch(tail) or not target_name:
            return None
        target = path.with_name(target_name)
        if not target.is_file():
            return None
        if mtime_ns > target.stat().st_mtime_ns:
            return None
        # 目标内容与候选一致是最常见的成功替换遗留；若不一致，则要求
        # 候选字节未被任何永久记录引用（上面统一已查 digest）。
        return "已发布目标对应的原子发布遗留临时文件"

    # ----- 受控回收 ---------------------------------------------------

    def reclaim(
        self,
        *,
        relative_paths: tuple[str, ...] | list[str] | None = None,
        dry_run: bool = True,
    ) -> ReclaimReport:
        """回收候选；默认仅预览。删除前再次按全部事实复核。

        存在未核实活动（活动标记或非空暂存）时整体阻塞：候选可能正被
        活动执行引用，资格未被证实，不得按位置/名字猜测回收。
        """
        blocker = detect_activity_blocker(self._root)
        allowed = {candidate.relative_path: candidate for candidate in self.reclaimable()}
        requested = list(allowed) if relative_paths is None else list(relative_paths)
        if blocker is not None:
            return ReclaimReport(
                state="blocked",
                removed=(),
                bytes_freed=0,
                refused=tuple(requested),
                blocked_reason=blocker,
            )
        removed: list[str] = []
        refused: list[str] = []
        bytes_freed = 0
        for relative in requested:
            candidate = allowed.get(relative)
            if (
                candidate is None
                or detect_activity_blocker(self._root) is not None
                or not self._still_safe(relative)
            ):
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
        """删除前重新按全部事实判定，防止候选识别后状态变化。"""
        path = self._resolve_within_root(relative)
        if not path.exists() or not path.is_file():
            return False
        if _is_empty_staging(path, self._root):
            return True
        if not path.name.startswith("."):
            return False
        if path.stat().st_size > 0:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in self._referenced_digests():
                return False
        candidate = self._classify_temporary(
            path, path.stat().st_mtime_ns, digest if path.stat().st_size else ""
        )
        return candidate is not None

    def _audit(self, relative: str, size: int) -> None:
        entry = {
            "schema": _AUDIT_SCHEMA,
            "action": "reclaimed",
            "relative_path": relative,
            "size_bytes": size,
        }
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())


__all__ = [
    "FileMaintenanceService",
    "MaintenanceError",
    "ReclaimCandidate",
    "ReclaimReport",
    "SpaceReport",
    "detect_activity_blocker",
]
