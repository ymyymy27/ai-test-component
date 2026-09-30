"""Conservative recovery; never replays unknown external effects.

两部分：

- :func:`recover_workspace`：既有轻量检查（完整性 + 活动标记）。
- :class:`RecoveryOrchestrator`：完整事务恢复编排——完整性核对、活动标记
  与提交清单对账、事件日志核对，以及可选的“从备份恢复到隔离目录”。
  决策只依据落盘事实；未知状态阻塞交人工，绝不自动重放。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .backup import BackupError, FileBackupStore, RestoreReport
from .events import FileEventJournal, ReconcileReport
from .integrity import check_workspace

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

    def run(self) -> RecoveryState:
        """执行恢复编排。"""
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

        # Phase B：活动标记与提交清单对账。
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
        reconcile = journal.reconcile(committed_sequences=committed)
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

    def restore_backup(self, *, backup: Path, target: Path) -> RecoveryState:
        """从备份恢复到隔离目标并核对；不替换活动工作空间。"""
        try:
            report = FileBackupStore(self._root).restore(backup=backup, target=target)
        except BackupError as error:
            raise RecoveryBlocked(str(error)) from error
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
        path = self._root / "commit.json"
        if not path.exists():
            return ()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return ()
        commits = raw.get("commits")
        if not isinstance(commits, list):
            return ()
        sequences = [
            int(entry["commit_sequence"])
            for entry in commits
            if isinstance(entry, dict) and isinstance(entry.get("commit_sequence"), int)
        ]
        return tuple(sorted(sequences))


__all__ = [
    "RecoveryBlocked",
    "RecoveryOrchestrator",
    "RecoveryState",
    "recover_workspace",
]
