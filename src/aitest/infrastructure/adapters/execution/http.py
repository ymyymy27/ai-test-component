"""HTTP execution adapter framework with structured exchange facts."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aitest.domain.execution.assertions import (
    HttpAssertion as HttpAssertion,
)
from aitest.domain.execution.assertions import (
    HttpAssertionOperator as HttpAssertionOperator,
)
from aitest.domain.execution.assertions import (
    evaluate_http_assertion,
    freeze_json_value,
)

_MISSING = object()


@dataclass(frozen=True, slots=True)
class HttpAssertionResult:
    assertion_id: str
    matched: bool | None
    actual: object = None
    value_available: bool = True


@dataclass(frozen=True, slots=True)
class HttpRequestSpec:
    request_id: str
    method: str
    url: str
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None
    timeout_seconds: float = 10.0
    extract_paths: tuple[tuple[str, str], ...] = ()
    assertions: tuple[HttpAssertion, ...] = ()

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
    elapsed_ms: int = 0
    extracted: dict[str, object] = field(default_factory=dict)
    assertion_results: tuple[HttpAssertionResult, ...] = ()
    request_log_ref: str | None = None
    missing_extractions: tuple[str, ...] = ()


class HttpAdapter:
    """Minimal non-secret HTTP boundary; variable extraction remains a later slice."""

    def execute(self, spec: HttpRequestSpec) -> HttpExchangeResult:
        method = spec.method.upper()
        started = time.monotonic()
        try:
            spec = replace(
                spec,
                assertions=tuple(
                    replace(assertion, expected=freeze_json_value(assertion.expected))
                    for assertion in spec.assertions
                ),
            )
        except (TypeError, ValueError, RecursionError, RuntimeError):
            return HttpExchangeResult(
                request_id=spec.request_id,
                method=method,
                url=spec.url,
                status=None,
                error_class="assertion_input_invalid",
            )
        request = Request(
            spec.url,
            data=spec.body,
            headers=dict(spec.headers),
            method=method,
        )
        try:
            with urlopen(request, timeout=spec.timeout_seconds) as response:
                body = response.read()
                result = HttpExchangeResult(
                    request_id=spec.request_id,
                    method=method,
                    url=spec.url,
                    status=int(response.status),
                    headers=tuple(response.headers.items()),
                    body=body,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    request_log_ref=f"http-request:{spec.request_id}",
                )
                return self._enrich(result, spec)
        except HTTPError as error:
            body = error.read()
            result = HttpExchangeResult(
                request_id=spec.request_id,
                method=method,
                url=spec.url,
                status=int(error.code),
                headers=tuple(error.headers.items()),
                body=body,
                error_class="http_status",
                error_detail=str(error.code),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                request_log_ref=f"http-request:{spec.request_id}",
            )
            return self._enrich(result, spec)
        except (URLError, OSError) as error:
            return HttpExchangeResult(
                request_id=spec.request_id,
                method=method,
                url=spec.url,
                status=None,
                error_class="network",
                error_detail=str(error),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                request_log_ref=f"http-request:{spec.request_id}",
            )

    @staticmethod
    def _enrich(
        result: HttpExchangeResult,
        spec: HttpRequestSpec,
    ) -> HttpExchangeResult:
        payload = _json_payload(result.body)
        observations = {name: _json_path(payload, path) for name, path in spec.extract_paths}
        extracted = {
            name: None if value is _MISSING else value for name, value in observations.items()
        }
        assertions = []
        for assertion in spec.assertions:
            actual = _json_path(payload, assertion.json_path)
            available = actual is not _MISSING
            assertions.append(
                HttpAssertionResult(
                    assertion_id=assertion.assertion_id,
                    matched=evaluate_http_assertion(
                        actual,
                        assertion,
                        value_available=available,
                    ),
                    actual=actual if available else None,
                    value_available=available,
                )
            )
        return HttpExchangeResult(
            request_id=result.request_id,
            method=result.method,
            url=result.url,
            status=result.status,
            headers=result.headers,
            body=result.body,
            error_class=result.error_class,
            error_detail=result.error_detail,
            elapsed_ms=result.elapsed_ms,
            extracted=extracted,
            assertion_results=tuple(assertions),
            missing_extractions=tuple(
                name for name, value in observations.items() if value is _MISSING
            ),
            request_log_ref=(result.request_log_ref or f"http-request:{result.request_id}"),
        )


def _json_payload(body: bytes) -> object:
    if not body:
        return _MISSING
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _MISSING


def _json_path(payload: object, path: str) -> object:
    if not path:
        return payload
    current = payload
    for part in path.split("."):
        name, _, index_text = part.partition("[")
        if name:
            if not isinstance(current, dict) or name not in current:
                return _MISSING
            current = current[name]
        if index_text:
            try:
                index = int(index_text.rstrip("]"))
            except ValueError:
                return _MISSING
            if not isinstance(current, list) or not 0 <= index < len(current):
                return _MISSING
            current = current[index]
    return current


__all__ = [
    "HttpAdapter",
    "HttpAssertion",
    "HttpAssertionOperator",
    "HttpAssertionResult",
    "HttpExchangeResult",
    "HttpRequestSpec",
]
