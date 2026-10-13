"""工作空间维护模式：迁移等结构性动作期间**暂停新写入**（架构 04 §6/§9）。

合同依据：架构 04 §142「迁移前必须有对应迁移模式的可校验备份，**进入维护状态暂停新写入**」；
§190-§195「apply 前验证备份并进入维护模式，暂停新写动作，网络或活动执行须完成受控收尾/核实」。

这是**进程内**的维护闸门：核心进程本来独占工作空间写锁（一个工作空间一个核心进程），
但同一进程仍可并发受理多个入口的短事务；维护模式把"暂停新写入"变成一个可观察状态，
由入口适配器在分发写动作前拒绝，而不是靠纪律。

边界：

1. 只暂停**写动作**（一期动作表中非只读的部分）；只读查询、`doctor`、`storage_usage`
   与维护动作本身继续可用（诊断与迁移需要它们）；
2. 可重入计数：迁移内部再次进入不会提前解除；
3. 不持久化：进程重启后维护模式自然消失（重启即回到非维护态，符合"不假装已收尾"）。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class MaintenanceModeActive(RuntimeError):
    """已有维护窗口在运行：拒绝嵌套的第二个维护动作。"""


class MaintenanceMode:
    """进程内维护闸门；`active` 供入口适配器在分发写动作前查询。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._depth = 0
        self._reason = ""

    @property
    def active(self) -> bool:
        with self._lock:
            return self._depth > 0

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def enter(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("maintenance mode requires a reason")
        with self._lock:
            if self._depth > 0:
                raise MaintenanceModeActive(
                    f"another maintenance window is already active: {self._reason}"
                )
            self._depth = 1
            self._reason = reason

    def leave(self) -> None:
        with self._lock:
            if self._depth == 0:
                raise MaintenanceModeActive("no maintenance window is active")
            self._depth = 0
            self._reason = ""

    @contextmanager
    def window(self, reason: str) -> Iterator[None]:
        self.enter(reason)
        try:
            yield
        finally:
            self.leave()
