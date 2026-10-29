"""Shared health model for `aitest doctor`, panel diagnostics and CLI (架构 05 §5).

所有入口使用同一判定：`READY` / `DEGRADED` / `NOT_READY` + 检查时间 + 原因 +
下一步。模型/连接器失败只降级 AI 与对应动作，不把本地核心能力整体判成不可用；
工作空间位于同步盘/网络盘、写入不可用、核心已排空或执行器状态不明时为
`NOT_READY`。
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from aitest.application.lifecycle import CoreLifecycleState


class DoctorStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"


#: 同步盘/网络盘的典型路径片段（小写比较）；命中即 NOT_READY。
_SHARED_PATH_SEGMENTS: frozenset[str] = frozenset(
    {
        "onedrive",
        "dropbox",
        "google drive",
        "google-drive",
        "icloud drive",
        "nutstore",
        "坚果云",
        "百度网盘",
        "baidunetdisk",
        "同步空间",
    }
)

#: 核心生命周期里不影响本地读写、只影响"保持/退出"的状态。
_NON_BLOCKING_LIFECYCLE: frozenset[str] = frozenset(
    {
        CoreLifecycleState.SERVING.value,
        CoreLifecycleState.PAUSED_HOLD.value,
        CoreLifecycleState.BACKLOG_HOLD.value,
        CoreLifecycleState.IDLE_EXIT_READY.value,
    }
)


def is_shared_path(root: str | None) -> bool:
    """工作空间是否位于同步盘/网络盘（UNC 或常见同步目录）。"""

    if not root:
        return False
    normalized = root.replace("\\", "/").lower()
    if normalized.startswith("//"):
        return True
    return any(segment in normalized for segment in _SHARED_PATH_SEGMENTS)


@dataclass(frozen=True, slots=True)
class DoctorInputs:
    """同一健康模型所需的输入事实；全部由装配层提供，判定本身是纯函数。"""

    workspace_id: str | None
    workspace_root: str | None = None
    lifecycle_state: str | None = None
    dependencies: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    last_backup: Mapping[str, object] | None = None


def evaluate_doctor(
    inputs: DoctorInputs,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """返回 doctor 结果尾段（status/checked_at/reason/next_step/checks）。"""

    now = (clock or (lambda: datetime.now(UTC)))().astimezone(UTC).isoformat()
    checks: list[dict[str, object]] = []

    def add(name: str, status: DoctorStatus, reason: str | None = None) -> None:
        checks.append({"name": name, "status": status.value, "reason": reason})

    status = DoctorStatus.READY
    reasons: list[str] = []
    next_step: str | None = None

    if not inputs.workspace_id:
        status = DoctorStatus.NOT_READY
        reasons.append("no workspace is bound")
        next_step = "run `aitest init` (or open a project) before using other actions"
        add("workspace", status, reasons[-1])
    else:
        add("workspace", DoctorStatus.READY)

    if is_shared_path(inputs.workspace_root):
        status = DoctorStatus.NOT_READY
        reasons.append("workspace is on a sync or network drive")
        next_step = "move the workspace out of the sync/network directory"
        add("workspace_location", DoctorStatus.NOT_READY, reasons[-1])
    else:
        add("workspace_location", DoctorStatus.READY)

    lifecycle = inputs.lifecycle_state
    if lifecycle is not None and lifecycle not in _NON_BLOCKING_LIFECYCLE:
        status = DoctorStatus.NOT_READY
        reasons.append(f"core lifecycle is {lifecycle}")
        next_step = "wait for the core to finish draining and reconnect"
        add("core_lifecycle", DoctorStatus.NOT_READY, reasons[-1])
    else:
        add("core_lifecycle", DoctorStatus.READY)

    # 未配置的可选外部能力不属于故障（模型只降 AI，不把本地能力判坏）；
    # 已配置但降级/失败的才记为 DEGRADED。
    degraded = [
        str(item.get("key") or item.get("name") or "dependency")
        for item in inputs.dependencies
        if str(item.get("state") or "ready") not in {"ready", "not_configured"}
    ]
    if degraded:
        if status is DoctorStatus.READY:
            status = DoctorStatus.DEGRADED
        reasons.append("degraded capabilities: " + ",".join(sorted(degraded)))
        add("dependencies", DoctorStatus.DEGRADED, reasons[-1])
    else:
        add("dependencies", DoctorStatus.READY)

    backup = inputs.last_backup
    if backup is not None and backup.get("complete") is False:
        if status is DoctorStatus.READY:
            status = DoctorStatus.DEGRADED
        reasons.append("the latest backup is incomplete")
        add("last_backup", DoctorStatus.DEGRADED, reasons[-1])
    elif backup is not None:
        add("last_backup", DoctorStatus.READY)
    else:
        add("last_backup", DoctorStatus.READY, "no backup registered yet")

    return {
        "status": status.value,
        "checked_at": now,
        "reason": "; ".join(reasons) if reasons else None,
        "next_step": next_step,
        "checks": checks,
    }


__all__ = [
    "DoctorInputs",
    "DoctorStatus",
    "evaluate_doctor",
    "is_shared_path",
]
