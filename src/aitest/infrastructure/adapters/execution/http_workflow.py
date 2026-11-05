"""Execute one frozen `aitest.http-workflow/1.0` chain with actual exchanges."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from aitest.application.ports import HttpWorkflowCapture, HttpWorkflowExchange
from aitest.contracts.http_workflow import HttpWorkflowRequest, HttpWorkflowSpec
from aitest.domain.execution.assertions import HttpAssertion, HttpAssertionOperator

from .http import HttpAdapter, HttpRequestSpec

_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def _text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _render(value: str, variables: Mapping[str, object]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise KeyError(name)
        return _text(variables[name])

    return _PLACEHOLDER.sub(replace, value)


def _headers(
    request: HttpWorkflowRequest, variables: Mapping[str, object]
) -> tuple[tuple[str, str], ...]:
    rendered = []
    for name, value in request.headers:
        if name.lower() == "content-length":
            # The transport owns framing for the substituted byte body.
            continue
        rendered.append((name, _render(value, variables)))
    return tuple(rendered)


def _spec(request: HttpWorkflowRequest, variables: Mapping[str, object]) -> HttpRequestSpec:
    body = None if request.body is None else _render(request.body, variables).encode("utf-8")
    return HttpRequestSpec(
        request_id=request.request_id,
        method=request.method,
        url=_render(request.url, variables),
        headers=_headers(request, variables),
        body=body,
        timeout_seconds=request.timeout_seconds,
        extract_paths=tuple(request.extract.items()),
        assertions=tuple(
            HttpAssertion(
                assertion_id=item.assertion_id,
                json_path=item.json_path,
                operator=HttpAssertionOperator(item.operator),
                expected=item.expected,
            )
            for item in request.assertions
        ),
        max_response_bytes=request.max_response_bytes,
    )


class HttpWorkflowAdapter:
    """Actual request chain; assertions are evaluated by the shared assertion rules."""

    adapter_version = "http-workflow/1.0"

    def __init__(self, adapter: HttpAdapter | None = None) -> None:
        self._http = adapter or HttpAdapter()

    def execute(self, spec: HttpWorkflowSpec) -> HttpWorkflowCapture:
        variables: dict[str, object] = {}
        exchanges: list[HttpWorkflowExchange] = []
        gaps: list[str] = []
        for request in spec.requests:
            try:
                prepared = _spec(request, variables)
            except (KeyError, ValueError) as error:
                gaps.append(f"http_request_invalid:{request.request_id}:{error}")
                break
            observed = self._http.execute(prepared)
            exchanges.append(
                HttpWorkflowExchange(
                    request_id=observed.request_id,
                    method=observed.method,
                    url=observed.url,
                    status=observed.status,
                    response_headers=observed.headers,
                    body=observed.body,
                    body_complete=observed.body_complete,
                    error_class=observed.error_class,
                    error_detail=observed.error_detail,
                    extracted=dict(observed.extracted),
                    assertion_results=tuple(
                        (item.assertion_id, item.matched, item.actual)
                        for item in observed.assertion_results
                    ),
                    request_log_ref=observed.request_log_ref,
                )
            )
            if observed.error_class is not None:
                gaps.append(f"http_request_failed:{request.request_id}:{observed.error_class}")
                break
            if observed.body_complete is not True:
                gaps.append(f"http_response_incomplete:{request.request_id}")
                break
            if observed.missing_extractions:
                gaps.append(
                    f"http_extraction_missing:{request.request_id}:"
                    + ",".join(observed.missing_extractions)
                )
                break
            variables.update(observed.extracted)
        if not any(request.assertions for request in spec.requests):
            gaps.append("http_assertions_missing")
        observation = _observation(exchanges, gaps)
        return HttpWorkflowCapture(
            exchanges=tuple(exchanges),
            variables=dict(variables),
            observation=observation,
            gaps=tuple(gaps),
        )


def _observation(exchanges: list[HttpWorkflowExchange], gaps: list[str]) -> str:
    if gaps:
        return "no_result"
    if not exchanges:
        return "no_result"
    results = [item for exchange in exchanges for item in exchange.assertion_results]
    if not results or any(item[1] is None for item in results):
        return "no_result"
    return "mismatched" if any(item[1] is False for item in results) else "matched"


__all__ = ["HttpWorkflowAdapter"]
