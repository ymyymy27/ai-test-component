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

from aitest.domain.json_material import decode_json

from .integrity import check_workspace

_AUDIT_NAME: Final = "audit.jsonl"
_MAINTENANCE_DIR: Final = "maintenance"
_AUDIT_SCHEMA: Final = "aitest.maintenance-audit/1.0"
_ACTIVE_MARKER: Final = Path("transactions") / "active.json"
_STAGING_DIR: Final = Path("event-log") / "staging"
_EMPTY_DIGEST: Final = hashlib.sha256(b"").hexdigest()
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
class SpaceReport:
    """空间诊断结果。"""

    total_bytes: int
    total_files: int
    business_bytes: int
    temp_bytes: int
    backup_bytes: int
    other_bytes: int
    integrity_ok: bool
    integrity_errors: tuple[str, ...]


def detect_activity_blocker(workspace_root: Path) -> str | None:
    """从落盘事实证明是否存在未核实活动；无活动返回 None。

    独立事实来源，任一成立即阻塞回收/迁移，不能只凭活动标记文件
    缺失就把材料当垃圾（A-07）：

    - ``transactions/active.json``：事务边界仍标记在途；
    - ``event-log/staging/*.jsonl`` 非空：存在未封口事务暂存事件，
      发布结果未知，须先由恢复编排核实/抢救；
    - ``spool/<attempt>/`` 下已有输出流字节但没有 ``manifest.json``
      封口：可能是在途执行（写流先于清单封口，崩溃后须先抢救），
      无短事务标记也不能回收/迁移该工作空间。
    """
    root = workspace_root.resolve()
    # 管道封口不等于执行结束。没有核实并保存终态的句柄仍是活动/未知事实。
    handles = root / "execution-handles"
    if handles.is_dir():
        from .execution_handles import FileExecutionHandleStore

        handle_store = FileExecutionHandleStore(root)
        for path in handles.glob("*.json"):
            if path.stem.endswith(("-result", "-stop")):
                continue
            try:
                payload = decode_json(path.read_bytes())
                handle_id = payload["handle"]["handle_id"]
                saved = handle_store.load(handle_id)
                if handle_store.load_collection(saved.handle) is None:
                    return f"执行终态尚未核实: {saved.attempt_id}"
            except (OSError, ValueError, KeyError, TypeError):
                return f"执行句柄状态无法核实: {path.name}"
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
    spool = root / "spool"
    if spool.is_dir():
        for attempt in spool.iterdir():
            if not attempt.is_dir() or attempt.is_symlink():
                continue
            for capture in attempt.glob("capture-*.json"):
                try:
                    state = decode_json(capture.read_bytes())
                    if state.get("state") not in {"sealed", "recovered"}:
                        return f"执行输出采集尚未核实: {capture.relative_to(root)}"
                except (OSError, ValueError, AttributeError):
                    return f"执行输出采集状态无法核实: {capture.relative_to(root)}"
            try:
                streams = [
                    path
                    for path in attempt.iterdir()
                    if path.is_file() and path.suffix.lower() == ".log" and path.stat().st_size >= 0
                ]
            except OSError:
                return f"在途执行目录状态无法核实: spool/{attempt.name}"
            if not streams:
                continue
            if not (attempt / "manifest.json").exists():
                return f"存在未封口在途执行输出（无 manifest 封口）: spool/{attempt.name}"
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
        integrity = check_workspace(self._root)
        raw_errors = integrity.get("errors", [])
        errors = tuple(str(error) for error in raw_errors) if isinstance(raw_errors, list) else ()
        return SpaceReport(
            total_bytes=total_bytes,
            total_files=total_files,
            business_bytes=totals["business"],
            temp_bytes=totals["temp"],
            backup_bytes=totals["backup"],
            other_bytes=totals["other"],
            integrity_ok=bool(integrity.get("ok")),
            integrity_errors=errors,
        )

    # ----- 候选识别 ---------------------------------------------------

    # ----- 受控回收 ---------------------------------------------------

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

    def record_backup(
        self,
        *,
        destination: str,
        kind: str,
        verified: bool,
        complete: bool,
        known_missing: tuple[str, ...],
        file_count: int,
        total_bytes: int,
        manifest_digest: str,
    ) -> None:
        """把备份时间、校验结果与缺失内容写入维护审计（架构 04 §9/§10）。

        与回收审计同一份 `maintenance/audit.jsonl`：这是维护事实，不是业务记录；
        只追加，不轮换删除。
        """

        if not isinstance(destination, str) or not destination.strip():
            raise MaintenanceError("备份审计需要目标位置")
        if not isinstance(kind, str) or not kind.strip():
            raise MaintenanceError("备份审计需要类型")
        if type(verified) is not bool or type(complete) is not bool:
            raise MaintenanceError("备份审计需要明确的校验/完整性布尔")
        if type(file_count) is not int or file_count < 0 or type(total_bytes) is not int:
            raise MaintenanceError("备份审计需要非负计数")
        if any(not isinstance(item, str) or not item.strip() for item in known_missing):
            raise MaintenanceError("备份审计的缺失清单必须是非空文本项")
        entry = {
            "schema": _AUDIT_SCHEMA,
            "action": "backed_up",
            "destination": destination,
            "kind": kind,
            "verified": verified,
            "complete": complete,
            "known_missing": sorted(known_missing),
            "file_count": file_count,
            "total_bytes": total_bytes,
            "manifest_digest": manifest_digest,
        }
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def backup_facts(self) -> tuple[dict[str, object], ...]:
        """读回维护审计里的备份事实（架构 04 §10：备份登记与诊断可核对）。

        只解析 `maintenance/audit.jsonl` 里 `action="backed_up"` 的完整行；损坏行与
        其它动作跳过，不猜测、不补造。返回按写入顺序（即时间顺序）的事实。
        """

        if not self._audit_path.exists():
            return ()
        facts: list[dict[str, object]] = []
        try:
            with self._audit_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        entry = decode_json(line)
                    except ValueError:
                        continue
                    if isinstance(entry, dict) and entry.get("action") == "backed_up":
                        facts.append(entry)
        except OSError as error:
            raise MaintenanceError(f"备份审计不可读: {error}") from error
        return tuple(facts)

__all__ = [
    "FileMaintenanceService",
    "MaintenanceError",
    "SpaceReport",
    "detect_activity_blocker",
]
