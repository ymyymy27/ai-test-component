"""HTTP execution adapter framework with structured exchange facts."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aitest.domain.evidence.evidence import Verification, VerificationObservation


class HttpAssertionOperator(StrEnum):
    EQUALS = "equals"
    CONTAINS = "contains"
    EXISTS = "exists"


@dataclass(frozen=True, slots=True)
class HttpAssertion:
    assertion_id: str
    json_path: str
    operator: HttpAssertionOperator
    expected: object = None


@dataclass(frozen=True, slots=True)
class HttpAssertionResult:
    assertion_id: str
    matched: bool
    actual: object = None


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


class HttpAdapter:
    """Minimal non-secret HTTP boundary; variable extraction remains a later slice."""

    def execute(self, spec: HttpRequestSpec) -> HttpExchangeResult:
        method = spec.method.upper()
        started = time.monotonic()
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
        extracted = {name: _json_path(payload, path) for name, path in spec.extract_paths}
        assertions = tuple(
            HttpAssertionResult(
                assertion_id=assertion.assertion_id,
                matched=_matches(
                    _json_path(payload, assertion.json_path),
                    assertion,
                ),
                actual=_json_path(payload, assertion.json_path),
            )
            for assertion in spec.assertions
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
            assertion_results=assertions,
            request_log_ref=(result.request_log_ref or f"http-request:{result.request_id}"),
        )


def http_assertion_verifications(
    exchange: HttpExchangeResult,
    *,
    business_object_id: str,
    evidence_refs: tuple[str, ...] = (),
    covers_critical_chain_item_ids: tuple[str, ...] = (),
    gap_ids_by_assertion: dict[str, tuple[str, ...]] | None = None,
    created_at: datetime | None = None,
) -> tuple[Verification, ...]:
    """Convert HTTP assertion results into the existing Verification contract.

    Raw expected/actual HTTP values are not added to ExecutionFacts. A matched
    or mismatched assertion becomes a Verification; any raw response needed for
    traceability is referenced through evidence/request-log refs and gaps.
    """

    if not business_object_id.strip():
        raise ValueError("business_object_id must not be empty")
    observed_at = created_at or datetime.now(UTC)
    gaps_by_id = gap_ids_by_assertion or {}
    if not exchange.assertion_results:
        if exchange.error_class is None:
            return ()
        return (
            Verification(
                verification_id=f"http-request:{exchange.request_id}",
                verification_of="http_request",
                business_object_id=business_object_id,
                query_method="http",
                observation=VerificationObservation.QUERY_ERROR,
                actual_result_ref=exchange.request_log_ref,
                evidence_refs=evidence_refs,
                gap_ids=(f"http_{exchange.error_class}",),
                created_at=observed_at,
            ),
        )

    verifications: list[Verification] = []
    for assertion in exchange.assertion_results:
        gaps = gaps_by_id.get(assertion.assertion_id, ())
        if not evidence_refs and not exchange.request_log_ref:
            gaps = (*gaps, "http_assertion_evidence_missing")
        verifications.append(
            Verification(
                verification_id=f"http:{exchange.request_id}:{assertion.assertion_id}",
                verification_of=f"http_assertion:{assertion.assertion_id}",
                business_object_id=business_object_id,
                query_method="http_assertion",
                observation=(
                    VerificationObservation.MATCHED
                    if assertion.matched
                    else VerificationObservation.MISMATCHED
                ),
                actual_result_ref=exchange.request_log_ref,
                covers_critical_chain_item_ids=covers_critical_chain_item_ids,
                evidence_refs=evidence_refs,
                gap_ids=gaps,
                created_at=observed_at,
            )
        )
    return tuple(verifications)


def _json_payload(body: bytes) -> object:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _json_path(payload: object, path: str) -> object:
    if not path:
        return payload
    current = payload
    for part in path.split("."):
        name, _, index_text = part.partition("[")
        if name:
            if not isinstance(current, dict) or name not in current:
                return None
            current = current[name]
        if index_text:
            try:
                index = int(index_text.rstrip("]"))
            except ValueError:
                return None
            if not isinstance(current, list) or not 0 <= index < len(current):
                return None
            current = current[index]
    return current


def _matches(actual: object, assertion: HttpAssertion) -> bool:
    if assertion.operator is HttpAssertionOperator.EXISTS:
        return actual is not None
    if assertion.operator is HttpAssertionOperator.EQUALS:
        return actual == assertion.expected
    if assertion.operator is HttpAssertionOperator.CONTAINS:
        if isinstance(actual, str):
            return str(assertion.expected) in actual
        if isinstance(actual, list):
            return assertion.expected in actual
        if isinstance(actual, dict):
            return assertion.expected in actual
    return False


__all__ = [
    "HttpAdapter",
    "HttpAssertion",
    "HttpAssertionOperator",
    "HttpAssertionResult",
    "HttpExchangeResult",
    "HttpRequestSpec",
    "http_assertion_verifications",
]
