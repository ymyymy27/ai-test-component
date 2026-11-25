"""Sequential HTTP runner built on the standard library only."""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from aitest.runners.assertions import MISSING, evaluate, resolve_path
from aitest.runners.base import RunnerResult, RunnerStatus
from aitest.security import is_secret_field, redact, redact_bytes, redact_tree, validate_target_url

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_VAR_PATTERN = re.compile(r"\$\{vars\.([A-Za-z_][A-Za-z0-9_]*)\}")
_BODY_PATH_PREFIX = "body."


def _body_path(path: str) -> str:
    """``body.status`` 用于断言响应体里恰好叫 status 的字段。"""
    if path.startswith(_BODY_PATH_PREFIX) and len(path) > len(_BODY_PATH_PREFIX):
        return path[len(_BODY_PATH_PREFIX) :]
    return path


def _finish(
    status: RunnerStatus,
    *,
    stdout: bytes = b"",
    assertions: list[dict[str, Any]] | None = None,
    error: str = "",
    secrets: list[str] | None = None,
) -> RunnerResult:
    """返回前对提取出的凭据类变量脱敏，防止敏感令牌落盘。"""
    safe_assertions = list(assertions or [])
    safe_error = error
    safe_stdout = stdout
    if secrets:
        if safe_error:
            safe_error = redact(safe_error, secrets)
        if safe_assertions:
            safe_assertions = redact_tree(safe_assertions, secrets)
        if safe_stdout:
            safe_stdout = redact_bytes(safe_stdout, secrets)
    return RunnerResult(
        status=status,
        stdout=safe_stdout,
        assertions=safe_assertions,
        error=safe_error,
    )


def _substitute(value: Any, vars_store: dict[str, str]) -> Any:
    """递归将配置中的 ${vars.<name>} 替换为前序步骤提取的值。"""
    if isinstance(value, str):
        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in vars_store:
                raise KeyError(name)
            return vars_store[name]

        return _VAR_PATTERN.sub(_replace, value)
    if isinstance(value, list):
        return [_substitute(item, vars_store) for item in value]
    if isinstance(value, Mapping):
        return {key: _substitute(item, vars_store) for key, item in value.items()}
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within the same host/port and never downgrade TLS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if new.hostname != old.hostname or new.port != old.port:
            raise ValueError(f"redirect target host/port is not allowed: {new.netloc}")
        if new.scheme not in {"http", "https"} or new.scheme != old.scheme:
            raise ValueError("redirect target scheme is not allowed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SAFE_REDIRECT_OPENER = urllib.request.build_opener(_SafeRedirect())
_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _send(
    request: urllib.request.Request, timeout_seconds: float, *, follow_redirects: bool
) -> tuple[int, bytes]:
    opener = _SAFE_REDIRECT_OPENER.open if follow_redirects else _NO_REDIRECT_OPENER.open
    try:
        with opener(request, timeout=timeout_seconds) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        body = error.read(MAX_RESPONSE_BYTES + 1)
        error.close()
        return error.code, body


def run_http_sequence(
    requests: list[dict[str, Any]],
    timeout_seconds: float = 30,
    request_id_header: str | None = None,
    request_id: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    allowed_hosts: list[str] | None = None,
) -> RunnerResult:
    """Run requests in order, evaluating optional JSON-path assertions after each.

    When both ``request_id_header`` and ``request_id`` are given, every request
    carries the correlation header (unless the spec sets it explicitly) so the
    link importer can match logs and database rows to this run.
    """
    assertions: list[dict[str, Any]] = []
    last_body = b""
    vars_store: dict[str, str] = {}
    secret_values: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    if len(requests) > 50:
        return RunnerResult(status="error", error="HTTP sequence exceeds 50 requests")
    for index, spec in enumerate(requests):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _finish(
                "blocked",
                stdout=last_body,
                assertions=assertions,
                error="HTTP sequence timed out",
                secrets=secret_values,
            )
        if cancelled is not None and cancelled():
            return _finish(
                "blocked",
                stdout=last_body,
                assertions=assertions,
                error="cancelled",
                secrets=secret_values,
            )
        label = f"request {index + 1}"
        stage_raw = spec.get("stage", "assert")
        if not isinstance(stage_raw, str) or stage_raw not in {"setup", "assert"}:
            return _finish(
                "error",
                stdout=last_body,
                assertions=assertions,
                error=f"{label} stage must be 'setup' or 'assert'",
                secrets=secret_values,
            )
        fail_status: RunnerStatus = "blocked" if stage_raw == "setup" else "failed"
        method_value = spec.get("method", "GET")
        if not isinstance(method_value, str) or not method_value:
            return _finish("error", error=f"{label} has an invalid method", secrets=secret_values)
        method = method_value.upper()
        raw_url = spec.get("url")
        if not isinstance(raw_url, str) or not raw_url:
            return _finish("error", error=f"{label} is missing a url", secrets=secret_values)
        try:
            url = str(_substitute(raw_url, vars_store))
        except KeyError as missing_var:
            return _finish(
                "blocked" if stage_raw == "setup" else "error",
                stdout=last_body,
                assertions=assertions,
                error=f"{label} 引用了未定义变量：{missing_var.args[0]}",
                secrets=secret_values,
            )
        if urllib.parse.urlsplit(url).scheme.lower() not in {"http", "https"}:
            return _finish(
                "error",
                error=f"{label} only supports http/https urls",
                secrets=secret_values,
            )
        target_ok, target_reason = validate_target_url(url, allowed_hosts=allowed_hosts)
        if not target_ok:
            return _finish(
                "blocked",
                stdout=last_body,
                assertions=assertions,
                error=target_reason,
                secrets=secret_values,
            )

        headers: dict[str, str] = {}
        raw_headers = spec.get("headers")
        if raw_headers is not None:
            if not isinstance(raw_headers, Mapping):
                return _finish(
                    "error", error=f"{label} headers must be an object", secrets=secret_values
                )
            try:
                resolved_headers = _substitute(raw_headers, vars_store)
            except KeyError as missing_var:
                return _finish(
                    "blocked" if stage_raw == "setup" else "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} 引用了未定义变量：{missing_var.args[0]}",
                    secrets=secret_values,
                )
            headers = {str(key): str(value) for key, value in resolved_headers.items()}
        if request_id_header and request_id:
            headers.setdefault(request_id_header, request_id)

        body: bytes | None = None
        has_form = "form" in spec
        has_json = "json" in spec
        if has_form and has_json:
            return _finish(
                "error",
                error=f"{label} cannot send form and json together",
                secrets=secret_values,
            )
        has_content_type = any(key.lower() == "content-type" for key in headers)
        if has_form:
            form = spec["form"]
            if not isinstance(form, Mapping):
                return _finish(
                    "error", error=f"{label} form must be an object", secrets=secret_values
                )
            try:
                resolved_form = _substitute(form, vars_store)
            except KeyError as missing_var:
                return _finish(
                    "blocked" if stage_raw == "setup" else "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} 引用了未定义变量：{missing_var.args[0]}",
                    secrets=secret_values,
                )
            body = urllib.parse.urlencode(
                {str(key): str(value) for key, value in resolved_form.items()}
            ).encode("utf-8")
            if not has_content_type:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
        if has_json:
            try:
                resolved_json = _substitute(spec["json"], vars_store)
            except KeyError as missing_var:
                return _finish(
                    "blocked" if stage_raw == "setup" else "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} 引用了未定义变量：{missing_var.args[0]}",
                    secrets=secret_values,
                )
            try:
                body = json.dumps(resolved_json, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as error:
                return _finish(
                    "error",
                    error=f"{label} json is not serializable: {error}",
                    secrets=secret_values,
                )
            if not has_content_type:
                headers["Content-Type"] = "application/json"

        raw_extract = spec.get("extract")
        if raw_extract is not None and not isinstance(raw_extract, Mapping):
            return _finish(
                "error", error=f"{label} extract must be an object", secrets=secret_values
            )
        extract_map: dict[str, str] = {}
        if isinstance(raw_extract, Mapping):
            for var_key, path_expr in raw_extract.items():
                if not isinstance(var_key, str) or not isinstance(path_expr, str):
                    return _finish(
                        "error",
                        error=f"{label} extract keys and paths must be strings",
                        secrets=secret_values,
                    )
                extract_map[var_key] = path_expr

        try:
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            response_status, response_body = _send(
                request,
                max(0.1, min(timeout_seconds, deadline - time.monotonic())),
                follow_redirects=bool(spec.get("follow_redirects")),
            )
        except ValueError as error:
            return _finish("error", error=f"{label} is invalid: {error}", secrets=secret_values)
        except (OSError, http.client.HTTPException) as error:
            return _finish(
                "blocked",
                stdout=last_body,
                assertions=assertions,
                error=f"{label} {method} {url} failed: {error}",
                secrets=secret_values,
            )

        if len(response_body) > MAX_RESPONSE_BYTES:
            return _finish(
                "blocked",
                stdout=last_body,
                assertions=assertions,
                error=f"{label} response exceeds the 4 MiB limit",
                secrets=secret_values,
            )
        last_body = response_body
        expects = spec.get("expect") or []
        if not isinstance(expects, list):
            return _finish("error", error=f"{label} expect must be a list", secrets=secret_values)
        status_checks: list[Mapping[str, Any]] = []
        body_checks: list[Mapping[str, Any]] = []
        for check in expects:
            if not isinstance(check, Mapping):
                return _finish(
                    "error", error=f"{label} expect entries must be objects", secrets=secret_values
                )
            path = check.get("path", "")
            operator = check.get("operator", "equals")
            if not isinstance(path, str) or not isinstance(operator, str):
                return _finish(
                    "error",
                    error=f"{label} expect path/operator must be strings",
                    secrets=secret_values,
                )
            if path == "status":
                status_checks.append(check)
            else:
                body_checks.append(check)
        if not status_checks and response_status >= 400:
            return _finish(
                fail_status,
                stdout=response_body,
                assertions=assertions,
                error=f"{label} {method} {url} returned HTTP {response_status}",
                secrets=secret_values,
            )
        for check in status_checks:
            operator = str(check.get("operator", "equals"))
            expected = check.get("value")
            try:
                passed = evaluate(response_status, operator, expected)
            except ValueError as error:
                return _finish(
                    "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} {error}",
                    secrets=secret_values,
                )
            assertions.append(
                {
                    "request_index": index,
                    "url": url,
                    "path": "status",
                    "operator": operator,
                    "expected": expected,
                    "actual": response_status,
                    "missing": False,
                    "passed": passed,
                }
            )
            if not passed:
                return _finish(
                    fail_status,
                    stdout=response_body,
                    assertions=assertions,
                    error=f"{label} {method} {url} returned HTTP {response_status}",
                    secrets=secret_values,
                )
        if 300 <= response_status < 400 and not expects and not extract_map:
            return _finish(
                "blocked",
                stdout=response_body,
                assertions=assertions,
                error=f"{label} returned HTTP {response_status} without following the redirect",
                secrets=secret_values,
            )
        if not body_checks and not extract_map:
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
            return _finish(
                fail_status,
                stdout=response_body,
                assertions=assertions,
                error=f"{label} response is not valid JSON: {error}",
                secrets=secret_values,
            )

        for check in body_checks:
            raw_path = str(check.get("path", ""))
            path = _body_path(raw_path)
            operator = str(check.get("operator", "equals"))
            try:
                expected = _substitute(check.get("value"), vars_store)
            except KeyError as missing_var:
                return _finish(
                    "blocked" if stage_raw == "setup" else "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} 引用了未定义变量：{missing_var.args[0]}",
                    secrets=secret_values,
                )
            actual = resolve_path(payload, path)
            try:
                passed = evaluate(actual, operator, expected)
            except ValueError as error:
                return _finish(
                    "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} {error}",
                    secrets=secret_values,
                )
            assertions.append(
                {
                    "request_index": index,
                    "url": url,
                    "path": raw_path,
                    "operator": operator,
                    "expected": expected,
                    "actual": None if actual is MISSING else actual,
                    "missing": actual is MISSING,
                    "passed": passed,
                }
            )
            if not passed:
                return _finish(
                    fail_status,
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} assertion failed at {raw_path!r} ({operator})",
                    secrets=secret_values,
                )

        for var_name, path_expr in extract_map.items():
            extracted = resolve_path(payload, path_expr)
            if extracted is MISSING or extracted is None:
                return _finish(
                    fail_status,
                    stdout=response_body,
                    assertions=assertions,
                    error=f"{label} 未能提取变量 {var_name}（路径 {path_expr}）",
                    secrets=secret_values,
                )
            text_val = str(extracted)
            vars_store[var_name] = text_val
            if text_val and is_secret_field(var_name):
                secret_values.append(text_val)

    return _finish("passed", stdout=last_body, assertions=assertions, secrets=secret_values)
