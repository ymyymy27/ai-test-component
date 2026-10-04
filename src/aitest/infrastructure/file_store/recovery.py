"""Conservative recovery; never replays unknown external effects.

两部分：

- :func:`recover_workspace`：既有轻量检查（完整性 + 活动标记）。
- :class:`RecoveryOrchestrator`：完整事务恢复编排——完整性核对、活动标记
  与提交清单对账、事件日志核对，以及可选的“从备份恢复到隔离目录”。
  决策只依据落盘事实；未知状态阻塞交人工，绝不自动重放。
"""

import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from aitest.application.errors import WorkspaceInUse
from aitest.infrastructure.security import guard_bytes, guard_value

from .backup import BackupError, FileBackupStore, RestoreReport
from .commit_manifest import FileCommitStore, canonical_bytes
from .events import EventMaintenanceRequired, FileEventJournal, ReconcileReport
from .integrity import check_workspace
from .locking import writer_lock
from .publication_backend import FilePublicationBackend
from .records import FileRecordRepository
from .sharded_records import find_commit, open_authority

_REPORT_SCHEMA: Final = "aitest.recovery-report/1.0"
_MARKER_MAX_BYTES: Final = 16 * 1024


def _read_marker(root: Path) -> dict[str, object] | None:
    store = FileCommitStore(root)
    path = root / "transactions" / "active.json"
    store.reject_links(path)
    if not path.exists():
        return None
    marker = store._decode(store._read_bytes(path, _MARKER_MAX_BYTES))
    safe, changed = guard_value(marker)
    if changed or safe != marker:
        raise ValueError("active transaction identity cannot be safely exposed")
    return marker


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
    try:
        active = _read_marker(root)
    except (OSError, ValueError, TypeError):
        return {
            "ok": False,
            "recovery_required": True,
            "active_marker_state": "unverified",
            "errors": ["active transaction marker cannot be verified"],
        }
    report = check_workspace(root)
    report["recovery_required"] = active is not None
    if active is not None:
        report["active_request_id"] = active.get("request_id")
        report["active_marker_state"] = "pending_verification"
    return report


class RecoveryOrchestrator:
    """按落盘事实编排事务恢复的完整闭环。"""

    def __init__(self, workspace_root: Path, *, instance_id: str) -> None:
        FileCommitStore.reject_links(workspace_root)
        self._root = workspace_root.resolve()
        self._instance_id = instance_id

    def inspect(self) -> dict[str, object]:
        """只读巡检：完整性、活动标记、已确认提交、事件日志位置。"""
        try:
            marker = self._read_active_marker()
        except (OSError, ValueError, TypeError):
            return {
                "integrity_ok": False,
                "integrity_errors": ["active transaction marker cannot be verified"],
                "active_marker": {"state": "unverified"},
                "committed_sequences": (),
            }
        integrity = check_workspace(self._root)
        committed = self._committed_sequences()
        return {
            "integrity_ok": bool(integrity.get("ok")),
            "integrity_errors": integrity.get("errors", []),
            "active_marker": marker,
            "committed_sequences": committed,
        }

    def run(self, *, startup: bool = False) -> RecoveryState:
        """Inspect published recovery material on startup; full history on request."""
        try:
            with writer_lock(self._root / "writer.lock"):
                return self._run_locked(startup=startup)
        except (WorkspaceInUse, OSError, ValueError, KeyError, TypeError):
            return RecoveryState(
                state="blocked",
                integrity_ok=False,
                committed_sequences=(),
                actions=("写入准入或必要恢复材料无法核实，保留原字节并阻塞恢复写入",),
                reconcile=None,
                restore=None,
                full_history_checked=False,
            )

    def _run_locked(self, *, startup: bool) -> RecoveryState:
        current = FileCommitStore(self._root).read_current(verify_material=True)
        marker = self._read_active_marker()
        if startup and current is not None:
            return self._run_incremental(current)
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

        # Verify activity before any projection repair; a sequence match is not ownership.
        if marker is not None:
            self._resolve_marker(current, marker)
            actions.append("准确权威提交已核实；保存恢复事实后清除残留活动标记")

        # Phase B：以 records.json 权威提交台账自愈落后/缺失的投影
        # （commit.json/indexes.json/events.json）。台账与业务记录在同一次
        # 原子写中发布；投影全部可重建，健康工作空间不产生修复动作。
        repository = FileRecordRepository(self._root)
        actions.extend(repository.rebuild_projections())

        committed = set(self._committed_sequences())

        # Phase C：事件日志逐项核对实际业务权威；提交序号只作查找提示。
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

        if reconcile.orphaned_staging:
            return RecoveryState(
                state="blocked",
                integrity_ok=True,
                committed_sequences=tuple(sorted(committed)),
                actions=tuple(actions),
                reconcile=reconcile,
                restore=None,
            )

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
            self._resolve_marker(current, marker)
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

    def _resolve_marker(self, current: dict[str, Any] | None, marker: dict[str, object]) -> None:
        if (
            marker.get("state") != "in_progress"
            or set(marker) - {"request_id", "project_id", "intent_id", "commit_sequence", "state"}
            or any(
                not isinstance(marker.get(key), str) or not str(marker[key]).strip()
                for key in ("request_id", "project_id")
            )
            or (
                marker.get("intent_id") is not None
                and (
                    not isinstance(marker["intent_id"], str) or not str(marker["intent_id"]).strip()
                )
            )
            or type(marker.get("commit_sequence")) is not int
            or cast(int, marker["commit_sequence"]) < 1
        ):
            raise ValueError("active transaction marker identity is unverified")
        repository = FileRecordRepository(self._root)
        if current is not None:
            FilePublicationBackend(self._root).confirm_current()
            authority = open_authority(self._root, current["manifest"]["record_header"])
            entry = find_commit(
                authority["_tree"],
                field="request_id",
                project_id=cast(str, marker["project_id"]),
                value=cast(str, marker["request_id"]),
            )
        else:
            # Validate the actual legacy authority, never commit.json or a sequence union.
            store = FileCommitStore(self._root)
            store._decode(store._read_bytes(self._root / "records.json", 64 * 1024 * 1024))
            entry = repository.find_committed_request(
                request_id=cast(str, marker["request_id"]),
                project_id=cast(str, marker["project_id"]),
                intent_id=cast(str | None, marker.get("intent_id")),
            )
        if (
            not isinstance(entry, dict)
            or entry.get("state") != "committed"
            or type(entry.get("commit_sequence")) is not int
            or any(
                entry.get(key) != marker.get(key)
                for key in ("request_id", "project_id", "intent_id", "commit_sequence")
            )
        ):
            raise ValueError("active transaction is not proved by the saved authority")
        if current is None:
            created = entry.get("created")
            if not isinstance(created, list) or not created:
                raise ValueError("legacy activity has no verifiable created records")
            for item in created:
                if not isinstance(item, dict) or type(item.get("revision")) is not int:
                    raise ValueError("legacy activity record revision is unverified")
                saved = repository.read(
                    aggregate_kind=item["aggregate_kind"],
                    record_id=item["record_id"],
                    revision=item["revision"],
                )
                owner = saved.payload.get("project_id", saved.payload.get("local_project_id"))
                if owner != marker["project_id"]:
                    raise ValueError("legacy activity record ownership is unverified")
        self._save_marker_resolution(current, marker)
        self._clear_active_marker(marker)

    def _save_marker_resolution(
        self, current: dict[str, Any] | None, marker: dict[str, object]
    ) -> None:
        if current is None:
            store = FileCommitStore(self._root)
            identity = store._decode(store._read_bytes(self._root / "workspace.json", 16384))
            workspace_id = identity["workspace_id"]
            legacy_digest = hashlib.sha256(
                store._read_bytes(self._root / "records.json", 64 * 1024 * 1024)
            ).hexdigest()
        else:
            workspace_id = current["manifest"]["workspace_id"]
            legacy_digest = None
        fact = {
            "schema": "aitest.transaction-recovery/1",
            "instance_id": self._instance_id,
            "workspace_id": workspace_id,
            "manifest_digest": current["pointer"]["manifest_digest"] if current else None,
            "legacy_authority_digest": legacy_digest,
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
        return _read_marker(self._root)

    def _clear_active_marker(self, expected: dict[str, object]) -> None:
        if canonical_bytes(self._read_active_marker()) != canonical_bytes(expected):
            raise ValueError("active marker changed during recovery; preserve the new activity")
        (self._root / "transactions" / "active.json").unlink()

    def _committed_sequences(self) -> tuple[int, ...]:
        """已确认提交序列。

        只使用与业务记录同一次发布的权威台账；投影不能补造已提交事实。
        """
        sequences: set[int] = set()
        with suppress(json.JSONDecodeError, OSError, TypeError, KeyError):
            sequences.update(FileRecordRepository(self._root).committed_sequences())
        return tuple(sorted(sequences))


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
