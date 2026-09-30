"""发现或连接唯一核心，不能连接则WORKSPACE_IN_USE。

一期职责预留，尚未实现，不注册为可用能力。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from aitest.application.errors import WorkspaceInUse

# 连接器：给定 workspace_id 返回 (客户端连接对象, instance_id) 或 None
Connector = Callable[[str], tuple[object, str] | None]


class CoreLauncher(Protocol):
    """核心启动器协议：启动子进程并返回 instance_id。"""

    def start(self, workspace_id: str) -> str: ...


class CoreEndpoint:
    """已核对身份的核心连接端点（一期预留）。"""


class EditorHost:
    """编辑器宿主：发现或启动唯一核心（一期预留，尚未实现）。"""

    def __init__(
        self,
        *,
        connector: Connector,
        launcher: CoreLauncher,
        wait_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        raise NotImplementedError("EditorHost 一期预留，尚未实现")

    def acquire(self, workspace_id: str) -> CoreEndpoint:
        raise NotImplementedError


__all__ = [
    "Connector",
    "CoreEndpoint",
    "CoreLauncher",
    "EditorHost",
    "WorkspaceInUse",
]
