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

import json
import math
import os
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlparse

from .security import UnsafeMaterialError, guard_value

_FACT_SCHEMA = "aitest.connection-fact/1.0"
_FACT_DIR = "diagnostics"
_FACT_FILE = "connection-facts.jsonl"
_FACT_KEYS = frozenset(
    {
        "schema",
        "endpoint_address",
        "reachable",
        "elapsed_ms",
        "error_kind",
        "detail",
        "source_session",
        "observed_at",
    }
)


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

    def probe(self, endpoint: EndpointConfig, *, timeout_seconds: float = 2.0) -> TransportFact:
        if timeout_seconds <= 0:
            raise EndpointError("timeout 必须为正")
        started = time.monotonic()
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout_seconds):
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

    def probe(self, endpoint: EndpointConfig, *, timeout_seconds: float = 2.0) -> TransportFact: ...


class ConnectionMonitor:
    """对同一端点持久持有统一连接状态。

    只负责“探测 + 累积状态”，不决定重试节奏（重试口径唯一属于
    ``application/connectivity.py``），也不睡眠；应用层按策略决定下一次
    :meth:`probe_once` 的时机。同一端点的所有入口共享同一个 monitor 实例，
    从而读到同一份 :class:`ConnectionState`。

    A-10：注入 :class:`ConnectionFactStore` 后，状态不再只活在实例内存：
    首次探测前从工作空间事实台账恢复历史尝试，每次探测事实即时追加落盘，
    核心换实例/进程重启后仍能读到同一份端点状态。
    """

    def __init__(
        self,
        endpoint: EndpointConfig,
        *,
        probe: ConnectionProbePort | None = None,
        clock: Callable[[], float] | None = None,
        store: ConnectionFactStore | None = None,
        source_session: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._probe = probe or ConnectionProbe()
        self._clock = clock or time.monotonic
        self._store = store
        self._source_session = source_session
        self._state: ConnectionState | None = None

    @property
    def endpoint(self) -> EndpointConfig:
        return self._endpoint

    @property
    def state(self) -> ConnectionState | None:
        return self._state

    def probe_once(self, *, timeout_seconds: float = 2.0) -> ConnectionState:
        fact = self._probe.probe(self._endpoint, timeout_seconds=timeout_seconds)
        previous: tuple[TransportFact, ...]
        if self._state is not None:
            previous = self._state.attempts
        elif self._store is not None:
            recovered = self._store.load(self._endpoint.base_address)
            previous = recovered.attempts if recovered is not None else ()
        else:
            previous = ()
        self._state = ConnectionState(
            endpoint_address=self._endpoint.base_address,
            reachable=fact.reachable,
            attempts=(*previous, fact),
            updated_at_monotonic=self._clock(),
        )
        if self._store is not None:
            self._store.append(
                endpoint_address=self._endpoint.base_address,
                fact=fact,
                source_session=self._source_session,
                observed_at=self._clock(),
            )
        return self._state

    def reset(self) -> None:
        self._state = None


class ConnectionFactStore:
    """端点探测事实的工作空间追加台账（A-10，跨重启可恢复）。

    存储于 ``<root>/diagnostics/connection-facts.jsonl``，每行一条不可变
    事实，追加后 ``fsync`` 文件；凭据正文绝不进入台账（传输探测本身不携带
    凭据，错误详情按长度截断并经脱敏出口后才能展示）。历史尝试全部保留
    （诊断事实永久留存），:meth:`load` 按端点重建统一状态。
    """

    def __init__(self, workspace_root: Path) -> None:
        self._root = Path(workspace_root).resolve()
        self._dir = self._root / _FACT_DIR
        self._path = self._dir / _FACT_FILE

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        *,
        endpoint_address: str,
        fact: TransportFact,
        source_session: str,
        observed_at: float,
    ) -> dict[str, object]:
        record = {
            "schema": _FACT_SCHEMA,
            "endpoint_address": endpoint_address,
            "reachable": fact.reachable,
            "elapsed_ms": fact.elapsed_ms,
            "error_kind": fact.error_kind,
            "detail": fact.detail,
            "source_session": source_session,
            "observed_at": observed_at,
        }
        _transport_fact(record)
        safe, _changed = guard_value(record)
        if not isinstance(safe, dict) or any(
            safe.get(name) != record[name] for name in ("schema", "endpoint_address")
        ):
            raise UnsafeMaterialError("connection fact identity cannot be safely persisted")
        _transport_fact(safe)
        # Filter the complete field before truncating it or JSON escaping it.
        # An escaped known value must not bypass the persistence boundary.
        safe["detail"] = cast(str, safe["detail"])[:300]
        line = (
            json.dumps(safe, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return safe

    def load(self, endpoint_address: str) -> ConnectionState | None:
        """重建某端点的统一状态；无任何事实返回 None。"""
        if not self._path.exists():
            return None
        attempts: list[TransportFact] = []
        observed = 0.0
        with self._path.open("rb") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                if not raw.endswith(b"\n"):
                    raise ValueError("connection fact tail is not durably delimited")
                record = json.loads(raw.decode("utf-8"), object_pairs_hook=_fact_object)
                if not isinstance(record, dict):
                    raise ValueError("connection fact must be a JSON object")
                fact = _transport_fact(record)
                if record["endpoint_address"] != endpoint_address:
                    continue
                attempts.append(fact)
                observed = float(record["observed_at"])
        if not attempts:
            return None
        return ConnectionState(
            endpoint_address=endpoint_address,
            reachable=attempts[-1].reachable,
            attempts=tuple(attempts),
            updated_at_monotonic=observed,
        )


class LocalAPIConnectionBridge:
    """:class:`LocalAPI` 连接状态字典与事实台账之间的窄桥。

    接口层不得导入基础设施，:class:`~aitest.interfaces.local.api.LocalAPI`
    以鸭子类型调用 ``load/save``；本桥在基础设施侧实现，探测仍经
    :class:`ConnectionProbe` 产生 :class:`TransportFact`，状态字典仅作为
    投影，事实以台账为准。
    """

    def __init__(
        self,
        endpoint: EndpointConfig,
        store: ConnectionFactStore,
        *,
        probe: ConnectionProbePort | None = None,
        source_session: str = "",
        timeout_seconds: float = 2.0,
    ) -> None:
        self._endpoint = endpoint
        self._store = store
        self._probe = probe or ConnectionProbe()
        self._source_session = source_session
        self._timeout = timeout_seconds

    @property
    def endpoint(self) -> EndpointConfig:
        return self._endpoint

    def load(self) -> dict[str, object] | None:
        """供 LocalAPI 启动时水合：None 表示该端点从无探测事实。"""
        state = self._store.load(self._endpoint.base_address)
        if state is None:
            return None
        last = state.last_fact
        return {
            "connected": state.reachable,
            "attempts": len(state.attempts),
            "last_error": None if last.reachable else (last.detail or "目标未就绪"),
            "endpoint_address": state.endpoint_address,
            "last_error_kind": last.error_kind,
            "recovered": True,
        }

    def __call__(self) -> TransportFact:
        """作为 LocalAPI 的 connector：返回事实（真值即可达性）。"""
        return self._probe.probe(self._endpoint, timeout_seconds=self._timeout)

    def save(self, state: dict[str, object]) -> None:
        """把一次探测结论落盘为不可变事实。"""
        error_message = state.get("last_error")
        raw_elapsed = state.get("elapsed_ms", 0)
        if type(state.get("connected")) is not bool or type(raw_elapsed) is not int:
            raise ValueError("connection projection lacks a typed transport fact")
        raw_kind = state.get("last_error_kind")
        self._store.append(
            endpoint_address=self._endpoint.base_address,
            fact=TransportFact(
                reachable=cast(bool, state["connected"]),
                elapsed_ms=raw_elapsed,
                error_kind=str(raw_kind) if raw_kind else None,
                detail=(
                    str(error_message)[:300]
                    if isinstance(error_message, str) and error_message
                    else ""
                ),
            ),
            source_session=self._source_session,
            observed_at=time.time(),
        )


def _fact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("connection fact has duplicate JSON fields")
        result[key] = value
    return result


def _transport_fact(record: Mapping[str, object]) -> TransportFact:
    if set(record) != _FACT_KEYS or record.get("schema") != _FACT_SCHEMA:
        raise ValueError("connection fact schema cannot be verified")
    address = record["endpoint_address"]
    if not isinstance(address, str) or EndpointConfig.from_address(address).base_address != address:
        raise ValueError("connection fact endpoint identity cannot be verified")
    if type(record["reachable"]) is not bool:
        raise ValueError("connection reachability must be a boolean fact")
    elapsed = record["elapsed_ms"]
    if type(elapsed) is not int or elapsed < 0:
        raise ValueError("connection duration must be a nonnegative integer")
    kind = record["error_kind"]
    if kind is not None and (
        not isinstance(kind, str) or kind not in {value.value for value in TransportErrorKind}
    ):
        raise ValueError("connection error classification cannot be verified")
    if record["reachable"] is True and kind is not None:
        raise ValueError("a successful transport fact cannot carry a failure classification")
    detail, session = record["detail"], record["source_session"]
    if not isinstance(detail, str) or not isinstance(session, str):
        raise ValueError("connection metadata must be strings")
    observed = record["observed_at"]
    if (
        type(observed) not in (int, float)
        or not math.isfinite(cast(float, observed))
        or cast(float, observed) < 0
    ):
        raise ValueError("connection observation time cannot be verified")
    return TransportFact(record["reachable"], elapsed, kind, detail)


__all__ = [
    "ConnectionFactStore",
    "ConnectionMonitor",
    "ConnectionProbe",
    "ConnectionProbePort",
    "ConnectionState",
    "EndpointConfig",
    "EndpointError",
    "LocalAPIConnectionBridge",
    "TransportErrorKind",
    "TransportFact",
    "classify_os_error",
]
