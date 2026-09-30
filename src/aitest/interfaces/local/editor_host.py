"""发现或连接唯一核心，不能连接则 WORKSPACE_IN_USE。

编辑器宿主在打开工作空间时：

1. 先尝试连接该工作空间的核心管道；连接成功即复用现有核心；
2. 管道不存在 → 通过注入的启动器启动一个新核心，再有限次等待连接；
3. 若探测到的核心实例与启动/期望身份不一致，或连接始终失败 →
   :class:`WorkspaceInUse`（code=WORKSPACE_IN_USE），绝不静默连接
   身份不明的进程。

进程创建本身由宿主层（bootstrap/平台启动器）承担，本模块只编排发现、
身份核对与连接。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class WorkspaceInUse(RuntimeError):
    """存在身份不明的核心或无法建立可信连接。"""

    code = "WORKSPACE_IN_USE"


@dataclass(frozen=True, slots=True)
class CoreEndpoint:
    """已核对身份的核心连接端点。"""

    workspace_id: str
    instance_id: str
    connection: object


class CoreLauncher(Protocol):
    def start(self, workspace_id: str) -> str:
        """启动新核心并返回其实例 id。"""
        ...


#: 连接器：返回已连接对象及其实例 id；不可连接返回 None。
Connector = Callable[[str], tuple[object, str] | None]


class EditorHost:
    """发现并连接唯一核心；不直接创建业务进程。"""

    def __init__(
        self,
        *,
        connector: Connector,
        launcher: CoreLauncher,
        wait_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self._connect = connector
        self._launcher = launcher
        self._timeout = wait_timeout_seconds
        self._poll = poll_interval_seconds

    def acquire(self, workspace_id: str) -> CoreEndpoint:
        existing = self._connect(workspace_id)
        if existing is not None:
            connection, instance_id = existing
            return CoreEndpoint(workspace_id, instance_id, connection)

        expected_instance = self._launcher.start(workspace_id)

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            connected = self._connect(workspace_id)
            if connected is not None:
                connection, instance_id = connected
                if instance_id != expected_instance:
                    raise WorkspaceInUse(
                        "核心实例身份与启动事实不一致"
                    )
                return CoreEndpoint(workspace_id, instance_id, connection)
            time.sleep(self._poll)

        raise WorkspaceInUse("核心启动后在限定时间内不可连接")


__all__ = [
    "CoreEndpoint",
    "CoreLauncher",
    "EditorHost",
    "WorkspaceInUse",
]
