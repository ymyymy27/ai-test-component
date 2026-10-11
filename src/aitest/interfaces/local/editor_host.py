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
from typing import Literal, NoReturn, Protocol, cast

from aitest.application.errors import WorkspaceInUse

#: 连接器：返回已连接对象及其实例 id；不可连接返回 None。
Connector = Callable[[str], tuple[object, str] | None]


class CoreLauncher(Protocol):
    """核心启动器协议：启动子进程并返回 instance_id。"""

    def start(self, workspace_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class CoreStartupObservation:
    """Optional launcher observation; readiness still requires a verified connection."""

    state: Literal["starting", "exited", "unknown"]
    exit_code: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CoreEndpoint:
    """已核对身份的核心连接端点。"""

    workspace_id: str
    instance_id: str
    connection: object


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
        self._timeout = _wait_seconds(wait_timeout_seconds)
        self._poll = _wait_seconds(poll_interval_seconds)
        self._connect = connector
        self._launcher = launcher

    def acquire(self, workspace_id: str) -> CoreEndpoint:
        deadline = time.monotonic() + self._timeout
        existing = self._connect(workspace_id)
        if existing is not None:
            connection, instance_id = existing
            if time.monotonic() >= deadline:
                _reject_connection(connection, "核心连接返回时已超过限定时间")
            return CoreEndpoint(workspace_id, instance_id, connection)
        if time.monotonic() >= deadline:
            raise WorkspaceInUse("核心发现已超过限定时间")

        expected_instance = self._launcher.start(workspace_id)

        while time.monotonic() < deadline:
            connected = self._connect(workspace_id)
            if connected is not None:
                connection, instance_id = connected
                if instance_id != expected_instance:
                    _reject_connection(connection, "核心实例身份与启动事实不一致")
                if time.monotonic() >= deadline:
                    _reject_connection(connection, "核心连接返回时已超过限定时间")
                return CoreEndpoint(workspace_id, instance_id, connection)
            observe = getattr(self._launcher, "observe_start", None)
            if callable(observe):
                fact = observe(expected_instance)
                if isinstance(fact, CoreStartupObservation):
                    if fact.state == "exited":
                        code = str(fact.exit_code) if fact.exit_code is not None else "unknown"
                        raise WorkspaceInUse(f"核心已退出，exit_code={code}")
                    if fact.state == "unknown":
                        raise WorkspaceInUse("核心启动进程身份无法核实，保留现场待恢复")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self._poll, remaining))

        raise WorkspaceInUse("核心启动后在限定时间内不可连接")


def _wait_seconds(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("host wait policy requires a finite number in (0, 60] seconds")
    number = cast(int | float, value)
    if not 0 < number <= 60:
        raise ValueError("host wait policy requires a finite number in (0, 60] seconds")
    return float(number)


def _reject_connection(connection: object, reason: str) -> NoReturn:
    error = WorkspaceInUse(reason)
    try:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
    except Exception as close_error:
        raise error from close_error
    raise error


__all__ = [
    "Connector",
    "CoreEndpoint",
    "CoreLauncher",
    "CoreStartupObservation",
    "EditorHost",
    "WorkspaceInUse",
]
