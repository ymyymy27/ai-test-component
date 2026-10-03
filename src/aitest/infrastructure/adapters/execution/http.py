"""HTTP execution adapter framework with structured exchange facts."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class HttpRequestSpec:
    request_id: str
    method: str
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.url.strip():
            raise ValueError("HTTP request requires request_id and url")
        if not self.method.strip():
            raise ValueError("HTTP method must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class HttpExchangeResult:
    request_id: str
    method: str
    url: str
    status: int | None
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""
    error_class: str | None = None
    error_detail: str | None = None


class HttpAdapter:
    """Minimal non-secret HTTP boundary; variable extraction remains a later slice."""

    def execute(self, spec: HttpRequestSpec) -> HttpExchangeResult:
        method = spec.method.upper()
        request = Request(
            spec.url,
            data=spec.body,
            headers=dict(spec.headers),
            method=method,
        )
        try:
            with urlopen(request, timeout=spec.timeout_seconds) as response:
                return HttpExchangeResult(
                    request_id=spec.request_id,
                    method=method,
                    url=spec.url,
                    status=int(response.status),
                    headers=tuple(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as error:
            return HttpExchangeResult(
                request_id=spec.request_id,
                method=method,
                url=spec.url,
                status=int(error.code),
                headers=tuple(error.headers.items()),
                body=error.read(),
                error_class="http_status",
                error_detail=str(error.code),
            )
        except (URLError, OSError) as error:
            return HttpExchangeResult(
                request_id=spec.request_id,
                method=method,
                url=spec.url,
                status=None,
                error_class="network",
                error_detail=str(error),
            )


__all__ = ["HttpAdapter", "HttpExchangeResult", "HttpRequestSpec"]