"""连接配置及传输事实，重试策略属于应用层。

提供：

- :class:`EndpointConfig`：端点地址的解析与校验（scheme/host/port），
  冻结不可变；
- :class:`TransportFact`：一次真实 TCP 传输探测的事实（可达性、耗时、
  归一错误类别）；
- :class:`ConnectionProbe`：socket 级连接探测与 OS 错误归一。

不做 HTTP 请求、不带凭据、不重试、不睡眠——重试口径唯一属于
``application/connectivity.py``。
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlparse


class EndpointError(ValueError):
    """端点地址不合法。"""


class TransportErrorKind(StrEnum):
    """传输层归一错误类别；与认证/限流等上层错误分开。"""

    DNS_ERROR = "dns_error"
    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class EndpointConfig:
    """冻结端点配置；只允许 http/https。"""

    scheme: str
    host: str
    port: int
    base_address: str

    @classmethod
    def from_address(cls, address: str) -> EndpointConfig:
        parsed = urlparse(address)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise EndpointError(f"不支持的 scheme: {parsed.scheme!r}")
        host = parsed.hostname
        if not host:
            raise EndpointError("缺少主机名")
        port = parsed.port
        if port is None:
            port = 443 if scheme == "https" else 80
        base = f"{scheme}://{host}:{port}"
        return cls(scheme=scheme, host=host, port=port, base_address=base)


@dataclass(frozen=True, slots=True)
class TransportFact:
    """一次探测的落盘事实；未知不猜测。"""

    reachable: bool
    elapsed_ms: int
    error_kind: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class ConnectionState:
    """同一端点跨多次探测的统一连接状态。

    每次探测都追加到 ``attempts`` 并更新最后的事实；调用方（doctor/能力
    诊断）只能读到这一份状态，不会在不同代码路径各自维护一份“最新结论”。
    """

    endpoint_address: str
    reachable: bool
    attempts: tuple[TransportFact, ...]
    updated_at_monotonic: float

    @property
    def last_fact(self) -> TransportFact:
        return self.attempts[-1]


def classify_os_error(error: OSError) -> TransportErrorKind:
    """把 socket 层异常归一到传输错误类别。"""
    if isinstance(error, socket.gaierror):
        return TransportErrorKind.DNS_ERROR
    if isinstance(error, TimeoutError):
        return TransportErrorKind.TIMEOUT
    if isinstance(error, ConnectionRefusedError):
        return TransportErrorKind.CONNECTION_REFUSED
    return TransportErrorKind.UNREACHABLE


class ConnectionProbe:
    """TCP 连接探测；只产生事实。"""

    def probe(
        self, endpoint: EndpointConfig, *, timeout_seconds: float = 2.0
    ) -> TransportFact:
        if timeout_seconds <= 0:
            raise EndpointError("timeout 必须为正")
        started = time.monotonic()
        try:
            with socket.create_connection(
                (endpoint.host, endpoint.port), timeout=timeout_seconds
            ):
                pass
        except OSError as error:
            kind = classify_os_error(error)
            return TransportFact(
                reachable=False,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error_kind=kind.value,
                detail=str(error),
            )
        return TransportFact(
            reachable=True,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_kind=None,
            detail="",
        )


class ConnectionProbePort(Protocol):
    """连接探测端口；允许测试替身与其他探测实现结构替换。"""

    def probe(
        self, endpoint: EndpointConfig, *, timeout_seconds: float = 2.0
    ) -> TransportFact: ...


class ConnectionMonitor:
    """对同一端点持久持有统一连接状态。

    只负责“探测 + 累积状态”，不决定重试节奏（重试口径唯一属于
    ``application/connectivity.py``），也不睡眠；应用层按策略决定下一次
    :meth:`probe_once` 的时机。同一端点的所有入口共享同一个 monitor 实例，
    从而读到同一份 :class:`ConnectionState`。
    """

    def __init__(
        self,
        endpoint: EndpointConfig,
        *,
        probe: ConnectionProbePort | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._probe = probe or ConnectionProbe()
        self._clock = clock or time.monotonic
        self._state: ConnectionState | None = None

    @property
    def endpoint(self) -> EndpointConfig:
        return self._endpoint

    @property
    def state(self) -> ConnectionState | None:
        return self._state

    def probe_once(self, *, timeout_seconds: float = 2.0) -> ConnectionState:
        fact = self._probe.probe(self._endpoint, timeout_seconds=timeout_seconds)
        previous = self._state.attempts if self._state is not None else ()
        self._state = ConnectionState(
            endpoint_address=self._endpoint.base_address,
            reachable=fact.reachable,
            attempts=(*previous, fact),
            updated_at_monotonic=self._clock(),
        )
        return self._state

    def reset(self) -> None:
        self._state = None


__all__ = [
    "ConnectionMonitor",
    "ConnectionProbe",
    "ConnectionProbePort",
    "ConnectionState",
    "EndpointConfig",
    "EndpointError",
    "TransportErrorKind",
    "TransportFact",
    "classify_os_error",
]
