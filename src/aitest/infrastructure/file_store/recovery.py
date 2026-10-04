"""Conservative recovery; never replays unknown external effects.

两部分：

- :func:`recover_workspace`：既有轻量检查（完整性 + 活动标记）。
- :class:`RecoveryOrchestrator`：完整事务恢复编排——完整性核对、活动标记
  与提交清单对账、事件日志核对，以及可选的“从备份恢复到隔离目录”。
  决策只依据落盘事实；未知状态阻塞交人工，绝不自动重放。
"""

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from .backup import BackupError, FileBackupStore, RestoreReport
from .commit_manifest import FileCommitStore, canonical_bytes
from .events import EventMaintenanceRequired, FileEventJournal, ReconcileReport
from .integrity import check_workspace
from .publication_backend import FilePublicationBackend
from .records import FileRecordRepository
from .sharded_records import find_commit, open_authority

_REPORT_SCHEMA: Final = "aitest.recovery-report/1.0"


class RecoveryBlocked(RuntimeError):
    """状态无法自动安全恢复，需要人工介入。"""


@dataclass(frozen=True, slots=True)
class RecoveryState:
    """恢复编排结论。"""

    state: str  # healthy | repaired | blocked
    integrity_ok: bool
    committed_sequences: tuple[int, ...]
    actions: tuple[str, ...]
    reconcile: ReconcileReport | None
    restore: RestoreReport | None
    full_history_checked: bool = True
    last_commit_sequence: int | None = None


def recover_workspace(root: Path) -> dict[str, object]:
    report = check_workspace(root)
    pending = root / "transactions" / "active.json"
    report["recovery_required"] = pending.exists()
    if pending.exists():
        active = json.loads(pending.read_text(encoding="utf-8"))
        report["active_request_id"] = active.get("request_id")
    return report


class RecoveryOrchestrator:
    """按落盘事实编排事务恢复的完整闭环。"""

    def __init__(self, workspace_root: Path, *, instance_id: str) -> None:
        self._root = workspace_root.resolve()
        self._instance_id = instance_id

    def inspect(self) -> dict[str, object]:
        """只读巡检：完整性、活动标记、已确认提交、事件日志位置。"""
        integrity = check_workspace(self._root)
        marker = self._read_active_marker()
        committed = self._committed_sequences()
        return {
            "integrity_ok": bool(integrity.get("ok")),
            "integrity_errors": integrity.get("errors", []),
            "active_marker": marker,
            "committed_sequences": committed,
        }

    def run(self, *, startup: bool = False) -> RecoveryState:
        """Inspect published recovery material on startup; full history on request."""
        if startup:
            try:
                current = FileCommitStore(self._root).read_current(verify_material=True)
                if current is not None:
                    return self._run_incremental(current)
            except (OSError, ValueError, KeyError, TypeError):
                return RecoveryState(
                    state="blocked",
                    integrity_ok=False,
                    committed_sequences=(),
                    actions=("当前提交或必要恢复材料无法核实，保留原字节并阻塞新写入",),
                    reconcile=None,
                    restore=None,
                    full_history_checked=False,
                )
        actions: list[str] = []

        # Phase A：完整性。
        integrity = check_workspace(self._root)
        integrity_ok = bool(integrity.get("ok"))
        if not integrity_ok:
            return RecoveryState(
                state="blocked",
                integrity_ok=False,
                committed_sequences=self._committed_sequences(),
                actions=("完整性核对失败，需人工修复",),
                reconcile=None,
                restore=None,
            )

        # Phase B：先以 records.json 权威提交台账自愈落后/缺失的投影
        # （commit.json/indexes.json/events.json）。台账与业务记录在同一次
        # 原子写中发布；投影全部可重建，健康工作空间不产生修复动作。
        repository = FileRecordRepository(self._root)
        actions.extend(repository.rebuild_projections())

        # 活动标记与权威提交对账。
        marker = self._read_active_marker()
        committed = set(self._committed_sequences())
        if marker is not None:
            marker_commit = marker.get("commit_sequence")
            if isinstance(marker_commit, int) and marker_commit in committed:
                actions.append(f"提交 {marker_commit} 已落盘；清除崩溃后残留活动标记")
            else:
                actions.append("活动事务未形成提交；仅清除标记，不重放任何外部效果")
            self._clear_active_marker()

        # Phase C：事件日志核对（提交清单为事实来源）。
        journal = FileEventJournal(self._root, instance_id=self._instance_id)
        try:
            reconcile = journal.reconcile(committed_sequences=committed)
        except EventMaintenanceRequired:
            return RecoveryState(
                state="blocked",
                integrity_ok=True,
                committed_sequences=tuple(sorted(committed)),
                actions=("事件边界身份或字节无法核实，需人工恢复",),
                reconcile=None,
                restore=None,
            )
        actions.extend(reconcile.actions)

        state = "repaired" if actions else "healthy"
        if not actions:
            actions.append("无需恢复")
        return RecoveryState(
            state=state,
            integrity_ok=integrity_ok,
            committed_sequences=tuple(sorted(committed)),
            actions=tuple(actions),
            reconcile=reconcile,
            restore=None,
        )

    def _run_incremental(self, current: dict[str, Any]) -> RecoveryState:
        """No rglob, historical payload scan, or enumeration of the commit ledger."""
        FilePublicationBackend(self._root).confirm_current()
        marker = self._read_active_marker()
        sequence = current["manifest"]["commit_sequence"]
        actions: tuple[str, ...] = ("当前提交及必要恢复根已核实；未扫描完整历史",)
        state = "healthy"
        if marker is not None:
            if (
                marker.get("state") != "in_progress"
                or not isinstance(marker.get("request_id"), str)
                or not marker["request_id"]
                or not isinstance(marker.get("project_id"), str)
                or not marker["project_id"]
                or type(marker.get("commit_sequence")) is not int
            ):
                raise ValueError("active transaction marker identity is unverified")
            authority = open_authority(self._root, current["manifest"]["record_header"])
            entry = find_commit(
                authority["_tree"],
                field="request_id",
                project_id=cast(str, marker["project_id"]),
                value=cast(str, marker["request_id"]),
            )
            if (
                not isinstance(entry, dict)
                or entry.get("state") != "committed"
                or any(
                    entry.get(key) != marker.get(key)
                    for key in ("request_id", "project_id", "intent_id", "commit_sequence")
                )
            ):
                raise ValueError("active transaction cannot be proved by this published root")
            self._save_marker_resolution(current, marker)
            self._clear_active_marker()
            actions = ("已发布根证明准确活动事务已提交；保存恢复诊断后清除残留标记",)
            state = "repaired"
        return RecoveryState(
            state=state,
            integrity_ok=True,
            # An incremental check intentionally does not enumerate this list.
            # The checked current high-water mark is reported separately.
            committed_sequences=(),
            actions=actions,
            reconcile=None,
            restore=None,
            full_history_checked=False,
            last_commit_sequence=sequence,
        )

    def _save_marker_resolution(self, current: dict[str, Any], marker: dict[str, object]) -> None:
        import hashlib

        from aitest.infrastructure.security import guard_bytes, guard_value

        fact = {
            "schema": "aitest.transaction-recovery/1",
            "instance_id": self._instance_id,
            "workspace_id": current["manifest"]["workspace_id"],
            "manifest_digest": current["pointer"]["manifest_digest"],
            "state": "verified_committed",
            "marker": {
                key: marker.get(key)
                for key in ("request_id", "project_id", "intent_id", "commit_sequence")
            },
        }
        raw = canonical_bytes(fact)
        safe, changed = guard_value(fact)
        guarded, bytes_changed = guard_bytes(raw)
        if changed or safe != fact or bytes_changed or guarded != raw:
            raise ValueError("transaction recovery identity cannot preserve safe bytes")
        digest = hashlib.sha256(raw).hexdigest()
        FilePublicationBackend(self._root).publish_immutable(
            self._root / "diagnostics" / "recovery" / f"{digest}.json", raw
        )

    def restore_backup(self, *, backup: Path, target: Path) -> RecoveryState:
        """从备份恢复到隔离目标并核对；不替换活动工作空间。

        备份恢复被目标边界（非空/符号链接/越界清单等）拒绝时必须如实以
        blocked 上报，不谎报 repaired，也不声称完整性已核实（A-06）。
        """
        try:
            report = FileBackupStore(self._root).restore(backup=backup, target=target)
        except BackupError as error:
            raise RecoveryBlocked(str(error)) from error
        if report.state == "rejected":
            return RecoveryState(
                state="blocked",
                integrity_ok=False,
                committed_sequences=self._committed_sequences(),
                actions=(f"备份恢复被拒绝，未写入目标: {report.target}",),
                reconcile=None,
                restore=report,
            )
        if not report.verified:
            return RecoveryState(
                state="blocked",
                integrity_ok=False,
                committed_sequences=self._committed_sequences(),
                actions=(f"备份恢复后核对未通过，保持阻塞: {report.target}",),
                reconcile=None,
                restore=report,
            )
        return RecoveryState(
            state="repaired",
            integrity_ok=True,
            committed_sequences=self._committed_sequences(),
            actions=(f"已从备份恢复到隔离目录: {report.target}",),
            reconcile=None,
            restore=report,
        )

    # ----- 内部 -------------------------------------------------------

    def _read_active_marker(self) -> dict[str, object] | None:
        path = self._root / "transactions" / "active.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"state": "unparseable"}
        return dict(raw) if isinstance(raw, dict) else None

    def _clear_active_marker(self) -> None:
        path = self._root / "transactions" / "active.json"
        if path.exists():
            path.unlink()

    def _committed_sequences(self) -> tuple[int, ...]:
        """已确认提交序列。

        records.json 的提交台账是唯一权威事实来源（与业务记录同一次原子
        写发布）；commit.json 仅作迁移期并集兜底，保证旧版工作空间与
        投影落后场景都不丢已确认序列。任一来源不可读时不伪造结论。
        """
        sequences: set[int] = set()
        with suppress(json.JSONDecodeError, OSError, TypeError, KeyError):
            sequences.update(FileRecordRepository(self._root).committed_sequences())
        sequences.update(self._commit_file_sequences())
        return tuple(sorted(sequences))

    def _commit_file_sequences(self) -> set[int]:
        """读取旧版提交清单 commit.json 中的序列（投影，可能落后）。"""
        path = self._root / "commit.json"
        if not path.exists():
            return set()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set()
        commits = raw.get("commits")
        if not isinstance(commits, list):
            return set()
        return {
            int(entry["commit_sequence"])
            for entry in commits
            if isinstance(entry, dict) and isinstance(entry.get("commit_sequence"), int)
        }


def seal_inflight_outputs(root: Path) -> tuple[str, ...]:
    """父进程消亡后核心退出前，补封所有未封口的在途 spool 输出（A-02）。

    对有 ``*.log`` 字节且清单已建立但仍有未封口尾部的 attempt 调用统一
    抢救路径补记清单，使已 fsync 的输出仍可被下一次启动恢复核对；抢救
    结果交启动恢复编排核实，不自动重放任何外部效果。连清单都不存在的
    attempt 缺少 run/step 身份事实，不在此猜测，留给启动恢复按活动标记
    核实处理。幂等：字节均已被清单覆盖时不重复声称。
    """
    import portalocker

    from aitest.domain.execution.runs import ExecutionInspectionState
    from aitest.infrastructure.adapters.execution.command import CommandAdapter

    from .execution_handles import FileExecutionHandleStore
    from .spool import FileSpoolStore

    spool_dir = root / "spool"
    if not spool_dir.is_dir():
        return ()
    store = FileSpoolStore(root)
    handles = FileExecutionHandleStore(root)
    execution = CommandAdapter(spool_store=store, handle_store=handles)
    sealed: list[str] = []
    for attempt in spool_dir.iterdir():
        if not attempt.is_dir() or attempt.is_symlink():
            continue
        if not (attempt / "manifest.json").exists():
            continue
        try:
            has_streams = any(
                path.is_file() and path.suffix.lower() == ".log" and path.name != "manifest.json"
                for path in attempt.iterdir()
            )
        except OSError:
            continue
        if not has_streams:
            continue
        owned = handles.find_by_attempt(attempt.name)
        if owned is not None:
            inspection = execution.inspect(owned.handle)
            if inspection.state not in {
                ExecutionInspectionState.EXITED,
                ExecutionInspectionState.STOPPED,
            }:
                # 存活、身份不符或取证失败的执行不能由退出清理夺走输出。
                continue
        before = len(store.read_manifest(attempt.name).blocks)
        try:
            salvaged = store.salvage_streams(attempt.name)
        except portalocker.exceptions.LockException:
            # 真实采集者持锁，继续保留原材料；后续核心按原句柄核实。
            continue
        if len(salvaged.blocks) > before:
            sealed.append(attempt.name)
    return tuple(sealed)


__all__ = [
    "RecoveryBlocked",
    "RecoveryOrchestrator",
    "RecoveryState",
    "recover_workspace",
    "seal_inflight_outputs",
]
