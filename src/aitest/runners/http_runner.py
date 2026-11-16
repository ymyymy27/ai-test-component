"""Sequential HTTP runner built on the standard library only."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from aitest.runners.assertions import MISSING, evaluate, resolve_path
from aitest.runners.base import RunnerResult

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _failed(message: str, *, stdout: bytes, assertions: list[dict[str, Any]]) -> RunnerResult:
    return RunnerResult(status="failed", stdout=stdout, assertions=assertions, error=message)


def _send(request: urllib.request.Request, timeout_seconds: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        body = error.read(MAX_RESPONSE_BYTES + 1)
        error.close()
        return error.code, body


def run_http_sequence(requests: list[dict[str, Any]], timeout_seconds: float = 30) -> RunnerResult:
    """Run requests in order, evaluating optional JSON-path assertions after each."""
    assertions: list[dict[str, Any]] = []
    last_body = b""
    for index, spec in enumerate(requests):
        label = f"request {index + 1}"
        method_value = spec.get("method", "GET")
        if not isinstance(method_value, str) or not method_value:
            return RunnerResult(status="error", error=f"{label} has an invalid method")
        method = method_value.upper()
        url = spec.get("url")
        if not isinstance(url, str) or not url:
            return RunnerResult(status="error", error=f"{label} is missing a url")

        headers: dict[str, str] = {}
        raw_headers = spec.get("headers")
        if raw_headers is not None:
            if not isinstance(raw_headers, Mapping):
                return RunnerResult(status="error", error=f"{label} headers must be an object")
            headers = {str(key): str(value) for key, value in raw_headers.items()}

        body: bytes | None = None
        if "json" in spec:
            try:
                body = json.dumps(spec["json"], ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as error:
                return RunnerResult(
                    status="error", error=f"{label} json is not serializable: {error}"
                )
            headers.setdefault("Content-Type", "application/json")

        try:
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            response_status, response_body = _send(request, timeout_seconds)
        except ValueError as error:
            return RunnerResult(status="error", error=f"{label} is invalid: {error}")
        except (OSError, http.client.HTTPException) as error:
            return RunnerResult(
                status="blocked",
                stdout=last_body,
                assertions=assertions,
                error=f"{label} {method} {url} failed: {error}",
            )

        if len(response_body) > MAX_RESPONSE_BYTES:
            return RunnerResult(
                status="blocked",
                stdout=last_body,
                assertions=assertions,
                error=f"{label} response exceeds the 4 MiB limit",
            )
        last_body = response_body
        if response_status >= 400:
            return _failed(
                f"{label} {method} {url} returned HTTP {response_status}",
                stdout=response_body,
                assertions=assertions,
            )

        expects = spec.get("expect") or []
        if not isinstance(expects, list):
            return RunnerResult(status="error", error=f"{label} expect must be a list")
        if not expects:
            continue
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            assertions.append(
                {
                    "request_index": index,
                    "url": url,
                    "path": "",
                    "operator": "",
                    "expected": None,
                    "actual": None,
                    "missing": False,
                    "passed": False,
                    "message": "response is not valid JSON",
                }
            )
            return _failed(
                f"{label} response is not valid JSON: {error}",
                stdout=response_body,
                assertions=assertions,
            )

        for check in expects:
            if not isinstance(check, Mapping):
                return RunnerResult(status="error", error=f"{label} expect entries must be objects")
            path = check.get("path", "")
            operator = check.get("operator", "equals")
            if not isinstance(path, str) or not isinstance(operator, str):
                return RunnerResult(
                    status="error", error=f"{label} expect path/operator must be strings"
                )
            expected = check.get("value")
            actual = resolve_path(payload, path)
            try:
                passed = evaluate(actual, operator, expected)
            except ValueError as error:
                return RunnerResult(
                    status="error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} {error}",
                )
            assertions.append(
                {
                    "request_index": index,
                    "url": url,
                    "path": path,
                    "operator": operator,
                    "expected": expected,
                    "actual": None if actual is MISSING else actual,
                    "missing": actual is MISSING,
                    "passed": passed,
                }
            )
            if not passed:
                return _failed(
                    f"{label} assertion failed at {path!r} ({operator})",
                    stdout=last_body,
                    assertions=assertions,
                )

    return RunnerResult(status="passed", stdout=last_body, assertions=assertions)
