"""Sequential HTTP runner built on the standard library only."""

from __future__ import annotations

import http.client
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from aitest.core.http_spec import normalize_extract
from aitest.runners.assertions import MISSING, evaluate, resolve_path
from aitest.runners.base import RunnerResult, RunnerStatus
from aitest.security import is_secret_field, redact, redact_bytes, redact_tree, validate_target_url

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_VAR_PATTERN = re.compile(r"\$\{vars\.([A-Za-z_][A-Za-z0-9_]*)\}")
_ENV_PATTERN = re.compile(r"\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}")
_EXACT_VAR_RE = re.compile(r"^\$\{vars\.([A-Za-z_][A-Za-z0-9_]*)\}$")
_EXACT_ENV_RE = re.compile(r"^\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}$")
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


def _substitute(
    value: Any,
    vars_store: dict[str, Any],
    env_store: Mapping[str, str] | None = None,
    used_secrets: list[str] | None = None,
) -> Any:
    """递归将配置中的 ${vars.<name>} / ${env.<KEY>} 替换并保留整值原始类型。"""
    if isinstance(value, str):
        exact_var = _EXACT_VAR_RE.fullmatch(value)
        if exact_var:
            name = exact_var.group(1)
            if name not in vars_store:
                raise KeyError(f"vars.{name}")
            return vars_store[name]
        exact_env = _EXACT_ENV_RE.fullmatch(value)
        if exact_env:
            env_name = exact_env.group(1)
            if env_store is None or env_name not in env_store:
                raise KeyError(f"env.{env_name}")
            val = str(env_store[env_name])
            if used_secrets is not None and val and (
                is_secret_field(env_name) or env_name.startswith("AITEST_")
            ):
                used_secrets.append(val)
            return val

        def _replace_var(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in vars_store:
                raise KeyError(f"vars.{name}")
            return str(vars_store[name])

        def _replace_env(match: re.Match[str]) -> str:
            env_name = match.group(1)
            if env_store is None or env_name not in env_store:
                raise KeyError(f"env.{env_name}")
            val = str(env_store[env_name])
            if used_secrets is not None and val and (
                is_secret_field(env_name) or env_name.startswith("AITEST_")
            ):
                used_secrets.append(val)
            return val

        text = _VAR_PATTERN.sub(_replace_var, value)
        return _ENV_PATTERN.sub(_replace_env, text)
    if isinstance(value, list):
        return [_substitute(item, vars_store, env_store, used_secrets) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _substitute(item, vars_store, env_store, used_secrets)
            for key, item in value.items()
        }
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
_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect())
_ORIG_SAFE_OPEN = _SAFE_REDIRECT_OPENER.open
_ORIG_NO_OPEN = _NO_REDIRECT_OPENER.open


def _headers_dict(headers_obj: Any) -> dict[str, str]:
    if headers_obj is None:
        return {}
    if isinstance(headers_obj, Mapping):
        return {str(k).lower(): str(v) for k, v in headers_obj.items()}
    if hasattr(headers_obj, "items"):
        result: dict[str, str] = {}
        for k, v in headers_obj.items():
            lk = str(k).lower()
            if lk in result:
                result[lk] = f"{result[lk]}; {v}"
            else:
                result[lk] = str(v)
        return result
    return {}


def _encode_multipart(fields: Mapping[str, Any]) -> tuple[bytes, str]:
    """用标准库构造 multipart/form-data 请求体，支持普通字段及文件条目。"""
    boundary = f"----AitestBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for raw_name, val in fields.items():
        name = str(raw_name).replace('"', '\\"')
        if isinstance(val, Mapping) and (
            "content" in val or "filename" in val or "path" in val
        ):
            filename = str(val.get("filename") or "upload.bin").replace('"', '\\"')
            ctype = str(val.get("content_type") or "application/octet-stream")
            raw_content = val.get("content")
            if raw_content is None and isinstance(val.get("path"), str):
                file_path = Path(val["path"])
                raw_bytes = file_path.read_bytes()
                if "filename" not in val:
                    filename = file_path.name.replace('"', '\\"')
            elif isinstance(raw_content, bytes):
                raw_bytes = raw_content
            elif isinstance(raw_content, str):
                raw_bytes = raw_content.encode("utf-8")
            else:
                raw_bytes = json.dumps(raw_content, ensure_ascii=False).encode("utf-8")
            chunks.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n".encode()
            )
            chunks.append(raw_bytes)
            chunks.append(b"\r\n")
        else:
            chunks.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            )
            text_val = (
                json.dumps(val, ensure_ascii=False)
                if isinstance(val, (dict, list))
                else str(val)
            )
            chunks.append(text_val.encode("utf-8"))
            chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _send(
    request: urllib.request.Request,
    timeout_seconds: float,
    *,
    follow_redirects: bool,
) -> tuple[int, bytes]:
    opener = _SAFE_REDIRECT_OPENER if follow_redirects else _NO_REDIRECT_OPENER
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        body = error.read(MAX_RESPONSE_BYTES + 1)
        error.close()
        return error.code, body


_ORIG_SEND = _send


def _send_with_opener(
    request: urllib.request.Request,
    timeout_seconds: float,
    *,
    follow_redirects: bool,
    safe_opener: urllib.request.OpenerDirector,
    no_opener: urllib.request.OpenerDirector,
) -> tuple[int, dict[str, str], bytes]:
    if _send is not _ORIG_SEND:
        sent: Any = _send(request, timeout_seconds, follow_redirects=follow_redirects)
        if isinstance(sent, tuple) and len(sent) == 3:
            return int(sent[0]), dict(sent[1] or {}), bytes(sent[2])
        return int(sent[0]), {}, bytes(sent[1])
    if follow_redirects:
        open_fn = (
            _SAFE_REDIRECT_OPENER.open
            if _SAFE_REDIRECT_OPENER.open != _ORIG_SAFE_OPEN
            else safe_opener.open
        )
    else:
        open_fn = (
            _NO_REDIRECT_OPENER.open
            if _NO_REDIRECT_OPENER.open != _ORIG_NO_OPEN
            else no_opener.open
        )
    try:
        with open_fn(request, timeout=timeout_seconds) as response:
            hdrs = _headers_dict(getattr(response, "headers", None))
            return response.status, hdrs, response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        hdrs = _headers_dict(getattr(error, "headers", None))
        body = error.read(MAX_RESPONSE_BYTES + 1)
        error.close()
        return error.code, hdrs, body


def _setup_failure_summary(response_body: bytes) -> str:
    """从 setup 阶段响应体提取简短结构化校验错误信息，便于后续修订草稿定位。"""
    raw = response_body[:600].decode("utf-8", errors="replace").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:260]
    if isinstance(parsed, dict):
        for key in ("detail", "errors", "error", "msg", "message"):
            if key in parsed and parsed[key] not in (None, "", []):
                excerpt = json.dumps(parsed[key], ensure_ascii=False)
                return f"{key}={excerpt[:240]}"
    return raw[:260]


def _resolve_response_value(
    raw_path: str,
    *,
    response_headers: dict[str, str],
    response_text: str,
    json_payload: Any,
    has_json: bool,
) -> tuple[object, bool]:
    """解析响应值：优先支持 headers.<name> 与非 JSON 文本 body/body_text。"""
    if raw_path == "headers":
        return response_headers, True
    if raw_path.startswith("headers."):
        hname = raw_path[len("headers.") :].strip().lower()
        return response_headers.get(hname, MISSING), True
    if raw_path in {"body_text", "text"}:
        return response_text, True
    if raw_path == "body" and not has_json:
        return response_text, True
    if not has_json:
        return MISSING, False
    return resolve_path(json_payload, _body_path(raw_path)), True


def run_http_sequence(
    requests: list[dict[str, Any]],
    timeout_seconds: float = 30,
    request_id_header: str | None = None,
    request_id: str | None = None,
    cancelled: Callable[[], bool] | None = None,
    allowed_hosts: list[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> RunnerResult:
    """按顺序执行 HTTP 请求序列，内置独立 CookieJar、多媒体请求体、原类型提取与多操作符断言。"""
    assertions: list[dict[str, Any]] = []
    last_body = b""
    vars_store: dict[str, Any] = {}
    secret_values: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    if len(requests) > 50:
        return RunnerResult(status="error", error="HTTP sequence exceeds 50 requests")

    cookie_jar = http.cookiejar.CookieJar()
    cookie_handler = urllib.request.HTTPCookieProcessor(cookie_jar)
    safe_opener = urllib.request.build_opener(cookie_handler, _SafeRedirect())
    no_opener = urllib.request.build_opener(cookie_handler, _NoRedirect())

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
            url = str(_substitute(raw_url, vars_store, env, secret_values))
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
                resolved_headers = _substitute(raw_headers, vars_store, env, secret_values)
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
        body_kinds = [k for k in ("form", "json", "multipart", "raw_body") if k in spec]
        if len(body_kinds) > 1:
            return _finish(
                "error",
                error=f"{label} cannot combine multiple body types ({', '.join(body_kinds)})",
                secrets=secret_values,
            )
        has_content_type = any(key.lower() == "content-type" for key in headers)
        if "form" in spec:
            form = spec["form"]
            if not isinstance(form, Mapping):
                return _finish(
                    "error", error=f"{label} form must be an object", secrets=secret_values
                )
            try:
                resolved_form = _substitute(form, vars_store, env, secret_values)
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
        elif "json" in spec:
            try:
                resolved_json = _substitute(spec["json"], vars_store, env, secret_values)
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
        elif "multipart" in spec:
            mp = spec["multipart"]
            if not isinstance(mp, Mapping):
                return _finish(
                    "error", error=f"{label} multipart must be an object", secrets=secret_values
                )
            try:
                resolved_mp = _substitute(mp, vars_store, env, secret_values)
                body, mp_ctype = _encode_multipart(resolved_mp)
            except KeyError as missing_var:
                return _finish(
                    "blocked" if stage_raw == "setup" else "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} 引用了未定义变量：{missing_var.args[0]}",
                    secrets=secret_values,
                )
            except OSError as file_err:
                return _finish(
                    "blocked" if stage_raw == "setup" else "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} multipart 读取文件失败：{file_err}",
                    secrets=secret_values,
                )
            if not has_content_type:
                headers["Content-Type"] = mp_ctype
        elif "raw_body" in spec:
            raw_val = spec["raw_body"]
            if isinstance(raw_val, bytes):
                body = raw_val
            elif isinstance(raw_val, str):
                try:
                    resolved_raw = _substitute(raw_val, vars_store, env, secret_values)
                except KeyError as missing_var:
                    return _finish(
                        "blocked" if stage_raw == "setup" else "error",
                        stdout=last_body,
                        assertions=assertions,
                        error=f"{label} 引用了未定义变量：{missing_var.args[0]}",
                        secrets=secret_values,
                    )
                body = str(resolved_raw).encode("utf-8")
            else:
                return _finish(
                    "error", error=f"{label} raw_body must be a string", secrets=secret_values
                )

        raw_extract = spec.get("extract")
        if raw_extract is not None and not isinstance(raw_extract, Mapping):
            return _finish(
                "error", error=f"{label} extract must be an object", secrets=secret_values
            )
        extract_map = normalize_extract(raw_extract)
        if raw_extract and not extract_map:
            return _finish(
                "error",
                error=f"{label} extract must map variable names to response paths",
                secrets=secret_values,
            )

        try:
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            response_status, response_headers, response_body = _send_with_opener(
                request,
                max(0.1, min(timeout_seconds, deadline - time.monotonic())),
                follow_redirects=bool(spec.get("follow_redirects")),
                safe_opener=safe_opener,
                no_opener=no_opener,
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
            body_preview = _setup_failure_summary(response_body) if stage_raw == "setup" else ""
            detail = (
                f"{label} {method} {url} returned HTTP {response_status}"
                + (f": {body_preview}" if body_preview else "")
            )
            return _finish(
                fail_status,
                stdout=response_body,
                assertions=assertions,
                error=detail,
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
                body_preview = _setup_failure_summary(response_body) if stage_raw == "setup" else ""
                err_msg = f"{label} {method} {url} returned HTTP {response_status}" + (
                    f": {body_preview}" if body_preview else ""
                )
                return _finish(
                    fail_status,
                    stdout=response_body,
                    assertions=assertions,
                    error=err_msg,
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

        response_text = response_body.decode("utf-8", errors="replace")
        has_json = False
        payload: Any = None
        try:
            payload = json.loads(response_body.decode("utf-8"))
            has_json = True
        except (UnicodeDecodeError, json.JSONDecodeError) as json_err:
            json_decode_err = json_err

        for check in body_checks:
            raw_path = str(check.get("path", ""))
            operator = str(check.get("operator", "equals"))
            try:
                expected = _substitute(check.get("value"), vars_store, env, secret_values)
            except KeyError as missing_var:
                return _finish(
                    "blocked" if stage_raw == "setup" else "error",
                    stdout=last_body,
                    assertions=assertions,
                    error=f"{label} 引用了未定义变量：{missing_var.args[0]}",
                    secrets=secret_values,
                )
            actual, ok_type = _resolve_response_value(
                raw_path,
                response_headers=response_headers,
                response_text=response_text,
                json_payload=payload,
                has_json=has_json,
            )
            if not ok_type:
                assertions.append(
                    {
                        "request_index": index,
                        "url": url,
                        "path": raw_path,
                        "operator": operator,
                        "expected": expected,
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
                    error=f"{label} response is not valid JSON: {json_decode_err}",
                    secrets=secret_values,
                )
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
            if path_expr.startswith("headers."):
                hdr_name = path_expr[len("headers.") :].lower()
                extracted: object = response_headers.get(hdr_name, MISSING)
            elif path_expr in {"body_text", "text"} or (
                path_expr == "body" and not has_json
            ):
                extracted = response_text
            elif not has_json:
                return _finish(
                    fail_status,
                    stdout=response_body,
                    assertions=assertions,
                    error=f"{label} response is not valid JSON for extract {var_name}",
                    secrets=secret_values,
                )
            else:
                extracted = resolve_path(payload, _body_path(path_expr))
            if extracted is MISSING or extracted is None:
                return _finish(
                    fail_status,
                    stdout=response_body,
                    assertions=assertions,
                    error=f"{label} 未能提取变量 {var_name}（路径 {path_expr}）",
                    secrets=secret_values,
                )
            vars_store[var_name] = extracted
            secret_text = str(extracted)
            if secret_text and is_secret_field(var_name):
                secret_values.append(secret_text)

    return _finish("passed", stdout=last_body, assertions=assertions, secrets=secret_values)
