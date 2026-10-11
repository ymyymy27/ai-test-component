"""One direct HTTP exchange with a total deadline and bounded response material."""

from __future__ import annotations

import socket
import ssl
import threading
import time
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPResponse, IncompleteRead
from urllib.parse import urlsplit

# A timed-out resolver can finish DNS only. It cannot send requests, and repeated
# stalls cannot accumulate an unbounded number of background threads.
_DNS_SLOTS = threading.BoundedSemaphore(4)
_READ_BLOCK = 64 * 1024


@dataclass(frozen=True, slots=True)
class NetworkExchange:
    status: int | None = None
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    body_complete: bool = False
    error_class: str | None = None
    error_detail: str | None = None


class _DeadlineSocket:
    """The watchdog owns the actual socket object, never a reusable descriptor."""

    def __init__(self, expires: float) -> None:
        self.expires = expires
        self.expired = threading.Event()
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._timer = threading.Timer(max(0, expires - time.monotonic()), self._expire)
        self._timer.daemon = True
        self._timer.start()

    def remaining(self) -> float:
        remaining = self.expires - time.monotonic()
        if self.expired.is_set() or remaining <= 0:
            self.expired.set()
            raise TimeoutError
        return remaining

    def hold(self, stream: socket.socket) -> None:
        with self._lock:
            self.remaining()
            self._socket = stream

    def secure(self, context: ssl.SSLContext, host: str) -> ssl.SSLSocket:
        with self._lock:
            self.remaining()
            assert self._socket is not None
            stream = context.wrap_socket(
                self._socket,
                server_hostname=host,
                do_handshake_on_connect=False,
            )
            self._socket = stream
        stream.settimeout(self.remaining())
        stream.do_handshake()
        return stream

    def release(self) -> None:
        with self._lock:
            self._stop_socket()
            self._socket = None

    def close(self) -> None:
        self._timer.cancel()
        self.release()
        self._timer.join()

    def _expire(self) -> None:
        with self._lock:
            self.expired.set()
            self._stop_socket()

    def _stop_socket(self) -> None:
        if self._socket is not None:
            with suppress(OSError):
                self._socket.shutdown(socket.SHUT_RDWR)
            self._socket.close()


def _resolve(
    host: str, port: int, deadline: _DeadlineSocket
) -> list[
    tuple[
        socket.AddressFamily,
        socket.SocketKind,
        int,
        str,
        tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes],
    ]
]:
    deadline.remaining()
    if not _DNS_SLOTS.acquire(blocking=False):
        raise OSError("resolver_capacity")
    future: Future[
        list[
            tuple[
                socket.AddressFamily,
                socket.SocketKind,
                int,
                str,
                tuple[str, int] | tuple[str, int, int, int] | tuple[int, bytes],
            ]
        ]
    ] = Future()

    def resolve() -> None:
        try:
            future.set_result(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as error:
            future.set_exception(error)
        finally:
            _DNS_SLOTS.release()

    worker = threading.Thread(target=resolve, name="aitest-http-dns", daemon=True)
    try:
        worker.start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise OSError("resolver_unavailable") from None
    return future.result(timeout=deadline.remaining())


def _connect(host: str, port: int, deadline: _DeadlineSocket) -> socket.socket:
    addresses = _resolve(host, port, deadline)
    last_error: OSError = OSError("no_address")
    for family, kind, protocol, _, address in addresses:
        deadline.remaining()
        stream = socket.socket(family, kind, protocol)
        try:
            deadline.hold(stream)
            stream.settimeout(deadline.remaining())
            stream.connect(address)
            deadline.remaining()
            return stream
        except OSError as error:
            last_error = error
            deadline.release()
            stream.close()
    deadline.remaining()
    raise last_error


def _valid_framing(headers: tuple[tuple[str, str], ...]) -> bool:
    lengths = [value for key, value in headers if key.lower() == "content-length"]
    encodings = [value for key, value in headers if key.lower() == "transfer-encoding"]
    if len(lengths) > 1 or len(encodings) > 1 or (lengths and encodings):
        return False
    if lengths and (not lengths[0] or not lengths[0].isascii() or not lengths[0].isdigit()):
        return False
    return not encodings or encodings[0].strip().lower() == "chunked"


def exchange(
    *,
    method: str,
    url: str,
    headers: tuple[tuple[str, str], ...],
    body: bytes | None,
    expires: float,
    max_response_bytes: int,
) -> NetworkExchange:
    """Never retry after sending, follow redirects, or use implicit proxy settings."""
    target = urlsplit(url)
    assert target.hostname is not None
    host = target.hostname.encode("idna").decode("ascii")
    port = target.port or (443 if target.scheme == "https" else 80)
    path = (target.path or "/") + (f"?{target.query}" if target.query else "")
    deadline = _DeadlineSocket(expires)
    connection = HTTPConnection(host, port)
    response: HTTPResponse | None = None
    status: int | None = None
    observed_headers: tuple[tuple[str, str], ...] = ()
    captured = bytearray()
    complete = False
    error_class: str | None = None
    error_detail: str | None = None
    try:
        stream = _connect(host, port, deadline)
        if target.scheme == "https":
            context = ssl.create_default_context()
            context.set_alpn_protocols(["http/1.1"])
            stream = deadline.secure(context, host)
        connection.sock = stream
        stream.settimeout(deadline.remaining())
        connection.request(method, path, body=body, headers=dict(headers))
        deadline.remaining()
        response = HTTPResponse(stream, method=method)
        response.begin()
        deadline.remaining()
        status = response.status
        observed_headers = tuple(response.getheaders())
        if not _valid_framing(observed_headers):
            error_class = "response_protocol"
        else:
            while True:
                stream.settimeout(deadline.remaining())
                available = max_response_bytes - len(captured)
                chunk = response.read1(min(_READ_BLOCK, available + 1))
                deadline.remaining()
                captured.extend(chunk[:available])
                if len(chunk) > available:
                    error_class = "response_too_large"
                    break
                if not chunk:
                    if response.length not in (None, 0) or (
                        response.chunked and response.chunk_left is not None
                    ):
                        error_class = "response_incomplete"
                    else:
                        complete = True
                    break
            if complete:
                if 300 <= status < 400:
                    error_class = "redirect_blocked"
                elif status >= 400:
                    error_class, error_detail = "http_status", str(status)
    except TimeoutError:
        error_class = "timeout"
    except IncompleteRead as error:
        captured.extend(error.partial[: max_response_bytes - len(captured)])
        error_class = "response_incomplete"
    except (OSError, HTTPException, UnicodeError, ValueError):
        error_class = "network" if status is None else "response_incomplete"
    finally:
        timed_out = deadline.expired.is_set() or time.monotonic() >= expires
        deadline.close()
        if response is not None:
            response.close()
        connection.close()
    if timed_out:
        complete, error_class, error_detail = False, "timeout", None
    return NetworkExchange(
        status,
        observed_headers,
        bytes(captured),
        complete,
        error_class,
        error_detail,
    )
