"""HTTP execution adapter framework with structured exchange facts."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from aitest.domain.execution.assertions import (
    HttpAssertion as HttpAssertion,
)
from aitest.domain.execution.assertions import (
    HttpAssertionOperator as HttpAssertionOperator,
)
from aitest.domain.execution.assertions import (
    evaluate_http_assertion,
    freeze_json_value,
    read_json_path,
)

from .http_transport import exchange

_MISSING = object()
_HTTP_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


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
    max_response_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.request_id, self.url, self.method)
        ):
            raise ValueError("HTTP request requires request_id and url")
        if not _HTTP_TOKEN.fullmatch(self.method):
            raise ValueError("HTTP method requires its complete token")
        try:
            valid_timeout = (
                type(self.timeout_seconds) in {int, float}
                and math.isfinite(self.timeout_seconds)
                and 0 < self.timeout_seconds <= threading.TIMEOUT_MAX
            )
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("timeout_seconds must be a finite positive supported duration")
        if any(ord(character) <= 32 or ord(character) == 127 for character in self.url):
            raise ValueError("HTTP URL must not contain whitespace or control characters")
        target = urlsplit(self.url)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or "#" in self.url
            or (target.port is not None and not 1 <= target.port <= 65535)
        ):
            raise ValueError("HTTP target requires an explicit address without inline credentials")
        if self.body is not None and not isinstance(self.body, bytes):
            raise ValueError("HTTP request body requires explicit bytes")
        if (
            type(self.max_response_bytes) is not int
            or not 0 < self.max_response_bytes <= 64 * 1024 * 1024
        ):
            raise ValueError("HTTP response budget requires a positive integer up to 64MiB")
        seen_headers: set[str] = set()
        if not isinstance(self.headers, tuple):
            raise ValueError("HTTP headers require an immutable list of complete fields")
        for item in self.headers:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(isinstance(value, str) for value in item)
            ):
                raise ValueError("HTTP headers require complete text fields")
            name, value = item
            if (
                not _HTTP_TOKEN.fullmatch(name)
                or name.lower() in seen_headers
                or any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in value)
            ):
                raise ValueError("HTTP headers have an invalid name/value or duplicate field")
            seen_headers.add(name.lower())
        header_values = {name.lower(): value for name, value in self.headers}
        if "transfer-encoding" in header_values:
            raise ValueError("HTTP byte requests do not accept caller transfer framing")
        length = header_values.get("content-length")
        if length is not None and length != str(len(self.body or b"")):
            raise ValueError("HTTP content-length must match the frozen byte body")
        try:
            (target.path + target.query).encode("ascii")
            target.hostname.encode("idna")
            for _, value in self.headers:
                value.encode("latin-1")
        except UnicodeError as error:
            raise ValueError("HTTP request requires wire-encodable target and headers") from error
        if not isinstance(self.extract_paths, tuple):
            raise ValueError("HTTP extractions require immutable named paths")
        names: set[str] = set()
        for item in self.extract_paths:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(isinstance(value, str) for value in item)
                or not item[0].strip()
                or item[0] in names
            ):
                raise ValueError("HTTP extraction names must be complete and unique")
            names.add(item[0])
        if (
            not isinstance(self.assertions, tuple)
            or any(not isinstance(item, HttpAssertion) for item in self.assertions)
            or len({item.assertion_id for item in self.assertions}) != len(self.assertions)
        ):
            raise ValueError("HTTP assertions require complete unique identities")


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
    body_complete: bool = True


class HttpAdapter:
    """Bounded direct HTTP observations; assertions consume complete JSON only."""

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
        observed = exchange(
            method=method, url=spec.url, headers=spec.headers, body=spec.body,
            expires=started + spec.timeout_seconds, max_response_bytes=spec.max_response_bytes,
        )
        return self._enrich(HttpExchangeResult(
            request_id=spec.request_id, method=method, url=spec.url,
            status=observed.status, headers=observed.headers, body=observed.body,
            body_complete=observed.body_complete, error_class=observed.error_class,
            error_detail=observed.error_detail,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            request_log_ref=f"http-request:{spec.request_id}",
        ), spec)

    @staticmethod
    def _enrich(
        result: HttpExchangeResult,
        spec: HttpRequestSpec,
    ) -> HttpExchangeResult:
        payload = _json_payload(result.body) if result.body_complete is True else _MISSING
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
            body_complete=result.body_complete,
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
        return freeze_json_value(json.loads(body.decode("utf-8"), object_pairs_hook=_unique_fields))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _MISSING


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("HTTP response contains duplicate JSON fields")
        result[key] = value
    return result


def _json_path(payload: object, path: str) -> object:
    available, value = read_json_path(payload, path)
    return value if available else _MISSING


__all__ = [
    "HttpAdapter",
    "HttpAssertion",
    "HttpAssertionOperator",
    "HttpAssertionResult",
    "HttpExchangeResult",
    "HttpRequestSpec",
]
