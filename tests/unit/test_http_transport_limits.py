"""Actual outbound HTTP must have a total deadline and truthful bounded material."""

import socket
import socketserver
import ssl
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from aitest.infrastructure.adapters.execution.http import (
    HttpAdapter,
    HttpAssertion,
    HttpAssertionOperator,
    HttpRequestSpec,
)


@contextmanager
def server(reply, tls_context=None):
    stopped = threading.Event()
    requests = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            request = b""
            try:
                if tls_context is not None:
                    self.request = tls_context.wrap_socket(self.request, server_side=True)
                while b"\r\n\r\n" not in request:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        return
                    request += chunk
                head, _, received = request.partition(b"\r\n\r\n")
                lengths = [
                    line.split(b":", 1)[1].strip()
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                ]
                expected = int(lengths[0]) if lengths else 0
                while len(received) < expected:
                    chunk = self.request.recv(min(4096, expected - len(received)))
                    if not chunk:
                        return
                    received += chunk
                requests.append(head + b"\r\n\r\n" + received)
                reply(self.request, stopped)
            except OSError:
                pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    instance = Server(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}/", requests
    finally:
        stopped.set()
        instance.shutdown()
        instance.server_close()
        worker.join(1)
        assert not worker.is_alive()


def request(url, **kwargs):
    return HttpRequestSpec(
        "bounded",
        "GET",
        url,
        extract_paths=(("value", "value"),),
        assertions=(HttpAssertion("value", "value", HttpAssertionOperator.EQUALS, True),),
        **kwargs,
    )


def unknown(result):
    assert result.body_complete is False
    assert result.assertion_results[0].matched is None
    assert result.assertion_results[0].value_available is False
    assert result.missing_extractions == ("value",)


@pytest.mark.parametrize("status", [200, 500])
def test_trickled_response_has_one_deadline_for_success_and_error(status):
    body = b'{"value":true}' + b" " * 20

    def reply(connection, stopped):
        connection.sendall(
            f"HTTP/1.1 {status} Result\r\nContent-Length: {len(body)}\r\n\r\n".encode()
        )
        for byte in body:
            if stopped.wait(0.025):
                return
            connection.sendall(bytes([byte]))

    with server(reply) as (url, requests):
        started = time.monotonic()
        result = HttpAdapter().execute(request(url, timeout_seconds=0.16))
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert result.error_class == "timeout"
        assert result.status == status
        assert 0 < len(result.body) < len(body)
        unknown(result)
        assert len(requests) == 1


def test_trickled_response_headers_share_the_same_deadline():
    def reply(connection, stopped):
        for byte in b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n":
            if stopped.wait(0.02):
                return
            connection.sendall(bytes([byte]))

    with server(reply) as (url, requests):
        started = time.monotonic()
        result = HttpAdapter().execute(request(url, timeout_seconds=0.12))
        assert time.monotonic() - started < 0.4
        assert result.error_class == "timeout"
        unknown(result)
        assert len(requests) == 1


@pytest.mark.parametrize("status", [200, 500])
def test_short_content_length_preserves_prefix_but_cannot_verify(status):
    body = b'{"value":true}'

    def reply(connection, stopped):
        connection.sendall(
            f"HTTP/1.1 {status} Result\r\nContent-Length: {len(body) + 5}\r\n\r\n".encode() + body
        )

    with server(reply) as (url, requests):
        result = HttpAdapter().execute(request(url))
        assert result.error_class == "response_incomplete"
        assert result.status == status and result.body == body
        unknown(result)


def test_missing_final_chunk_does_not_validate_a_complete_json_prefix():
    body = b'{"value":true}'

    def reply(connection, stopped):
        connection.sendall(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + f"{len(body):x}\r\n".encode()
            + body
            + b"\r\n"
        )

    with server(reply) as (url, requests):
        result = HttpAdapter().execute(request(url))
        assert result.error_class == "response_incomplete"
        assert result.body == body
        unknown(result)


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
@pytest.mark.parametrize("extra", [0, 1])
def test_response_limit_distinguishes_exact_eof_from_an_extra_byte(framing, extra):
    body = b'{"value":true}' + b" " * extra
    limit = len(body) - extra

    def reply(connection, stopped):
        header = b"HTTP/1.1 200 OK\r\n"
        payload = body
        if framing == "length":
            header += f"Content-Length: {len(body)}\r\n".encode()
        elif framing == "chunked":
            header += b"Transfer-Encoding: chunked\r\n"
            payload = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        connection.sendall(header + b"\r\n" + payload)

    with server(reply) as (url, requests):
        result = HttpAdapter().execute(request(url, max_response_bytes=limit))
        assert result.body == body[:limit]
        assert len(requests) == 1
        if extra:
            assert result.error_class == "response_too_large"
            unknown(result)
        else:
            assert result.error_class is None and result.body_complete is True
            assert result.assertion_results[0].matched is True


@pytest.mark.parametrize(
    "headers",
    [
        b"Content-Length: 13\r\nContent-Length: 13\r\n",
        b"Content-Length: 13\r\nTransfer-Encoding: chunked\r\n",
        b"Content-Length: -1\r\n",
        b"Content-Length: 1e1\r\n",
        b"Transfer-Encoding: gzip\r\n",
    ],
)
def test_ambiguous_response_framing_cannot_supply_assertions(headers):
    def reply(connection, stopped):
        connection.sendall(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + b'{"value":true}')

    with server(reply) as (url, requests):
        result = HttpAdapter().execute(request(url))
        assert result.error_class == "response_protocol"
        unknown(result)


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "16", 64 * 1024 * 1024 + 1])
def test_invalid_response_budget_is_rejected_before_io(value):
    with pytest.raises(ValueError):
        request("https://example.invalid/", max_response_bytes=value)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_does_not_send_the_frozen_body_or_credentials_to_another_target(status):
    def target(connection, stopped):
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

    with server(target) as (target_url, target_requests):

        def redirect(connection, stopped):
            connection.sendall(
                (
                    f"HTTP/1.1 {status} Redirect\r\nLocation: {target_url}\r\n"
                    "Content-Length: 0\r\n\r\n"
                ).encode()
            )

        with server(redirect) as (url, original_requests):
            result = HttpAdapter().execute(
                HttpRequestSpec(
                    "redirect",
                    "POST",
                    url,
                    headers=(("Authorization", "synthetic-token"),),
                    body=b"sensitive",
                )
            )
            assert result.status == status
            assert result.error_class == "redirect_blocked"
            assert len(original_requests) == 1
            assert target_requests == []


def test_timed_out_dns_cannot_connect_later(monkeypatch):
    original = socket.getaddrinfo
    released = threading.Event()
    entered = threading.Event()

    def resolve(*args, **kwargs):
        entered.set()
        released.wait(0.45)
        return original(*args, **kwargs)

    def reply(connection, stopped):
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with server(reply) as (url, requests):
        try:
            started = time.monotonic()
            result = HttpAdapter().execute(request(url, timeout_seconds=0.08))
            assert entered.is_set()
            assert time.monotonic() - started < 0.3
            assert result.error_class == "timeout"
            unknown(result)
        finally:
            released.set()
        assert not threading.Event().wait(0.05)
        assert requests == []


@pytest.mark.parametrize(
    "changes",
    [
        {"headers": (("Content-Length", "1"),), "body": b"longer"},
        {"headers": (("Content-Length", "0"),), "body": b"x"},
        {"headers": (("Transfer-Encoding", "chunked"),)},
        {"headers": (("X-Value", "不可编码"),)},
        {"url": "http://example.invalid/未转义"},
    ],
)
def test_unrepresentable_or_conflicting_request_framing_is_rejected(changes):
    with pytest.raises(ValueError):
        HttpRequestSpec("framing", "POST", **{"url": "http://example.invalid/", **changes})


def test_tls_handshake_uses_the_same_deadline_and_does_not_send_http():
    def reply(connection, stopped):
        raise AssertionError("No HTTP request may be sent before TLS completes")

    with server(reply) as (url, requests):
        started = time.monotonic()
        result = HttpAdapter().execute(request("https" + url[4:], timeout_seconds=0.12))
        assert time.monotonic() - started < 0.4
        assert result.error_class == "timeout" and result.status is None
        unknown(result)
        assert requests == []


def test_stalled_dns_workers_are_bounded_and_cannot_send_late_requests(monkeypatch):
    from aitest.infrastructure.adapters.execution import http_transport

    released = threading.Event()
    all_done = threading.Event()
    started, finished = [], []
    original = socket.getaddrinfo

    def resolve(*args, **kwargs):
        started.append(1)
        try:
            released.wait(2)
            return original(*args, **kwargs)
        finally:
            finished.append(1)
            if len(finished) == 4:
                all_done.set()

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with server(lambda connection, stopped: None) as (url, requests):
        try:
            results = [HttpAdapter().execute(request(url, timeout_seconds=0.015)) for _ in range(7)]
            assert len(started) == 4
            assert [result.error_class for result in results] == ["timeout"] * 4 + ["network"] * 3
            assert all(not result.body_complete for result in results)
            assert requests == []
        finally:
            released.set()
            assert all_done.wait(1)
    # The DNS-only workers have released the bounded capacity after completion.
    assert http_transport._DNS_SLOTS.acquire(blocking=False)
    http_transport._DNS_SLOTS.release()


@pytest.mark.parametrize(
    "trust,hostname,expected",
    [
        (False, "localhost", False),
        (True, "127.0.0.1", False),
        (True, "localhost", True),
    ],
)
def test_actual_tls_preserves_certificate_and_hostname_checks(
    monkeypatch, trust, hostname, expected
):
    from aitest.infrastructure.adapters.execution import http_transport

    directory = Path(__file__).resolve().parents[1] / "fixtures"
    certificate = directory / "http_transport_test.crt"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, directory / "http_transport_test.key")
    create_context = ssl.create_default_context
    configured = []

    def trusted_context():
        result = create_context(cafile=str(certificate) if trust else None)
        configured.append((result.check_hostname, result.verify_mode))
        return result

    monkeypatch.setattr(http_transport.ssl, "create_default_context", trusted_context)

    def reply(connection, stopped):
        body = b'{"value":true}'
        connection.sendall(
            f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
        )

    with server(reply, context) as (url, requests):
        url = ("https" + url[4:]).replace("127.0.0.1", hostname)
        result = HttpAdapter().execute(request(url))
        assert configured == [(True, ssl.CERT_REQUIRED)]
        if expected:
            assert result.status == 200 and result.body_complete is True
            assert result.assertion_results[0].matched is True
            assert len(requests) == 1
        else:
            assert result.status is None and result.error_class == "network"
            unknown(result)
            assert requests == []
