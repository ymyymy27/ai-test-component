"""Unit tests for endpoint config and transport probing (infrastructure/connections.py)."""

from __future__ import annotations

import socket

import pytest

from aitest.infrastructure.connections import (
    ConnectionProbe,
    EndpointConfig,
    EndpointError,
    TransportErrorKind,
    classify_os_error,
)


def test_parse_full_address() -> None:
    endpoint = EndpointConfig.from_address("https://api.example.com:8443/v1/chat")
    assert endpoint.scheme == "https"
    assert endpoint.host == "api.example.com"
    assert endpoint.port == 8443
    assert endpoint.base_address == "https://api.example.com:8443"


def test_default_ports() -> None:
    assert EndpointConfig.from_address("https://api.example.com").port == 443
    assert EndpointConfig.from_address("http://api.example.com").port == 80


def test_invalid_scheme() -> None:
    with pytest.raises(EndpointError):
        EndpointConfig.from_address("ftp://api.example.com")


def test_missing_host() -> None:
    with pytest.raises(EndpointError):
        EndpointConfig.from_address("https:///path")


def test_probe_reachable_local_listener() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        endpoint = EndpointConfig("http", "127.0.0.1", port, f"http://127.0.0.1:{port}")
        fact = ConnectionProbe().probe(endpoint, timeout_seconds=2)
        assert fact.reachable is True
        assert fact.error_kind is None
        assert fact.elapsed_ms >= 0
    finally:
        listener.close()


def test_probe_connection_refused() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    closed_port = listener.getsockname()[1]
    listener.close()

    endpoint = EndpointConfig(
        "http", "127.0.0.1", closed_port, f"http://127.0.0.1:{closed_port}"
    )
    fact = ConnectionProbe().probe(endpoint, timeout_seconds=2)

    assert fact.reachable is False
    # Windows 对本机关闭端口可能表现为超时而非立即拒绝
    assert fact.error_kind in {
        TransportErrorKind.CONNECTION_REFUSED.value,
        TransportErrorKind.TIMEOUT.value,
    }


def test_probe_dns_error() -> None:
    endpoint = EndpointConfig.from_address("https://nonexistent-host.invalid")
    fact = ConnectionProbe().probe(endpoint, timeout_seconds=2)
    assert fact.reachable is False
    assert fact.error_kind == TransportErrorKind.DNS_ERROR.value


def test_probe_rejects_non_positive_timeout() -> None:
    endpoint = EndpointConfig("http", "127.0.0.1", 80, "http://127.0.0.1:80")
    with pytest.raises(EndpointError):
        ConnectionProbe().probe(endpoint, timeout_seconds=0)


def test_classify_os_error() -> None:
    assert classify_os_error(socket.gaierror()) is TransportErrorKind.DNS_ERROR
    assert classify_os_error(TimeoutError()) is TransportErrorKind.TIMEOUT
    assert classify_os_error(ConnectionRefusedError()) is (
        TransportErrorKind.CONNECTION_REFUSED
    )
    assert classify_os_error(OSError()) is TransportErrorKind.UNREACHABLE
