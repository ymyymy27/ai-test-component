"""Core keepalive / exit lifecycle (architecture 05 §11).

八条工作空间状态在实现里收敛为七个可判定状态；`draining` 之后拒绝**新写动作**
并返回可重连原因，只读动作继续可用。状态机只在核心进程内持有，不落业务记录。
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from threading import RLock


class CoreLifecycleState(StrEnum):
    """核心生命周期状态（架构 05 §11 表格的一对一实现）。"""

    #: 有客户端连接：保持服务，长动作在事务外继续。
    SERVING = "serving"
    #: 无客户端但运行暂停/待核实：保存状态后仍保持。
    PAUSED_HOLD = "paused_hold"
    #: 仅剩无许可候选或待人工恢复的上传：保存候选后可退出。
    BACKLOG_HOLD = "backlog_hold"
    #: 无客户端、无活动工作、无未提交收尾：可正常退出，退出前发布检查点。
    IDLE_EXIT_READY = "idle_exit_ready"
    #: 已请求退出：拒绝新写动作、排空写队列，返回可重连原因。
    DRAINING = "draining"
    #: 已正常退出；不再接纳任何动作。
    STOPPED = "stopped"
    #: 活动执行器状态不明，可能仍占用 workdir：保留材料待核实，禁止维护切换/回收。
    EXECUTOR_UNKNOWN = "executor_unknown"


#: 进入后拒绝新写动作的状态；只读动作仍可用。
_WRITE_BLOCKING: frozenset[CoreLifecycleState] = frozenset(
    {CoreLifecycleState.DRAINING, CoreLifecycleState.STOPPED}
)

#: 允许的状态迁移；非法迁移显式拒绝，不静默吞掉。
_TRANSITIONS: Mapping[CoreLifecycleState, frozenset[CoreLifecycleState]] = {
    CoreLifecycleState.SERVING: frozenset(
        {
            CoreLifecycleState.PAUSED_HOLD,
            CoreLifecycleState.BACKLOG_HOLD,
            CoreLifecycleState.IDLE_EXIT_READY,
            CoreLifecycleState.EXECUTOR_UNKNOWN,
            CoreLifecycleState.DRAINING,
        }
    ),
    CoreLifecycleState.PAUSED_HOLD: frozenset(
        {
            CoreLifecycleState.SERVING,
            CoreLifecycleState.BACKLOG_HOLD,
            CoreLifecycleState.IDLE_EXIT_READY,
            CoreLifecycleState.EXECUTOR_UNKNOWN,
            CoreLifecycleState.DRAINING,
        }
    ),
    CoreLifecycleState.BACKLOG_HOLD: frozenset(
        {
            CoreLifecycleState.SERVING,
            CoreLifecycleState.IDLE_EXIT_READY,
            CoreLifecycleState.DRAINING,
        }
    ),
    CoreLifecycleState.IDLE_EXIT_READY: frozenset(
        {CoreLifecycleState.DRAINING, CoreLifecycleState.STOPPED}
    ),
    CoreLifecycleState.EXECUTOR_UNKNOWN: frozenset(
        {
            CoreLifecycleState.SERVING,
            CoreLifecycleState.IDLE_EXIT_READY,
            CoreLifecycleState.DRAINING,
        }
    ),
    CoreLifecycleState.DRAINING: frozenset({CoreLifecycleState.STOPPED}),
    CoreLifecycleState.STOPPED: frozenset(),
}


class LifecycleError(ValueError):
    """非法状态迁移。"""


class CoreLifecycle:
    """单进程核心的七态生命周期；线程安全。"""

    def __init__(self, initial: CoreLifecycleState = CoreLifecycleState.SERVING) -> None:
        self._guard = RLock()
        self._state = initial
        self._reason: str | None = None

    @property
    def state(self) -> CoreLifecycleState:
        with self._guard:
            return self._state

    @property
    def reason(self) -> str | None:
        with self._guard:
            return self._reason

    @property
    def rejects_new_writes(self) -> bool:
        return self.state in _WRITE_BLOCKING

    def transition(self, target: CoreLifecycleState, *, reason: str | None = None) -> None:
        with self._guard:
            allowed = _TRANSITIONS[self._state]
            if target not in allowed:
                raise LifecycleError(f"illegal lifecycle transition {self._state} -> {target}")
            self._state = target
            self._reason = reason

    def begin_drain(self, reason: str) -> None:
        """请求退出：进入 draining 并记录可重连原因。"""

        if self.state is CoreLifecycleState.DRAINING:
            with self._guard:
                self._reason = reason
            return
        if self.state is CoreLifecycleState.STOPPED:
            return
        self.transition(CoreLifecycleState.DRAINING, reason=reason)

    def mark_stopped(self) -> None:
        if self.state is not CoreLifecycleState.STOPPED:
            self.transition(CoreLifecycleState.STOPPED, reason=self.reason)

    def to_mapping(self) -> dict[str, object]:
        with self._guard:
            return {
                "state": self._state.value,
                "reason": self._reason,
                "accepts_new_writes": self._state not in _WRITE_BLOCKING,
            }


__all__ = [
    "CoreLifecycle",
    "CoreLifecycleState",
    "LifecycleError",
]
