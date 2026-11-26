"""免 AI 确定性硬探针（Deterministic Probes）。

面向阶段 2 `DeterministicProbe` 插槽预留清晰协议接口，本期内置 3 个零项目硬编码探针：
1. 前后端契约自动对账（路由缺失与请求体字段不匹配）；
2. 本地静态资源全 200 与 MIME 检查（捕捉 404 与 SPA 假 200）；
3. 公开只读路由及不存在路径边界探针（捕捉 5xx 与堆栈泄露）。
"""

from __future__ import annotations

import contextlib
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from aitest.core.context.contracts import (
    _api_routes_with_meta,
    _frontend_api_calls,
    _frontend_assets,
)
from aitest.core.shared_config import extract_conventions, match_convention
from aitest.security import redact_tree, validate_target_url

_PARAM_BRACES_RE = re.compile(r"(?:\$\{[^}]+\}|\{[^}]+\}|<[^>]+>)")
_PARAM_COLON_RE = re.compile(r"(?<=/):[A-Za-z_][A-Za-z0-9_]*")
_HTML_LINK_RE = re.compile(r"<link\b[^>]*?\bhref\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_HTML_SCRIPT_RE = re.compile(r"<script\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_TRACEBACK_RE = re.compile(
    r"(Traceback \(most recent call last\)|UnhandledPromiseRejection|Internal Server Error)",
    re.IGNORECASE,
)
_SKIP_ASSET_PREFIXES = ("http://", "https://", "//", "data:", "javascript:", "mailto:", "#")
_STATIC_TEXT_ASSET_SUFFIXES = (".css", ".js", ".mjs", ".cjs")


class DeterministicProbe(Protocol):
    """确定性探针统一协议接口，为后续扩展插槽提供标准契约。"""

    probe_id: str
    name: str

    def run(self, **kwargs: Any) -> dict[str, Any]:
        """执行探针检查并返回标准化结构结果。"""
        ...


def _normalize_route_path(raw_path: str) -> str:
    """将前后端不同写法的动态路径参数统一归一化，便于准确比对。"""
    text = raw_path.strip().split("?", 1)[0].split("#", 1)[0]
    if not text.startswith("/"):
        text = "/" + text
    text = _PARAM_BRACES_RE.sub("{param}", text)
    text = _PARAM_COLON_RE.sub("{param}", text)
    if len(text) > 1:
        text = text.rstrip("/")
    return text


def _backend_fields_from_route(route: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for key in ("fields", "request_fields"):
        raw = route.get(key)
        if isinstance(raw, str):
            for part in raw.split(","):
                name = part.strip()
                if name:
                    fields.add(name)
        elif isinstance(raw, list):
            for part in raw:
                if isinstance(part, str) and part.strip():
                    fields.add(part.strip())
    return fields


class ContractReconciliationProbe:
    """确定性对账前端 API 调用与后端路由/OpenAPI 字段契约。"""

    probe_id = "contract_reconciliation"
    name = "前后端契约自动对账"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        frontend_api_calls = list(kwargs.get("frontend_api_calls") or [])
        api_routes = list(kwargs.get("api_routes") or [])
        if not frontend_api_calls or not api_routes:
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "summary": "缺少前端 API 调用或后端路由声明，跳过静态契约对账",
                "findings": [],
            }

        route_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        routes_by_path: dict[str, list[dict[str, Any]]] = {}
        for route in api_routes:
            if not isinstance(route, dict):
                continue
            method = str(route.get("method") or "GET").strip().upper()
            raw_path = str(route.get("path") or "").strip()
            if not raw_path:
                continue
            norm = _normalize_route_path(raw_path)
            route_index.setdefault((method, norm), []).append(route)
            routes_by_path.setdefault(norm, []).append(route)

        has_dynamic_loader = bool(kwargs.get("has_dynamic_loader", False))
        root_dir = kwargs.get("root")
        root_path = root_dir if isinstance(root_dir, Path) else None
        source_text_cache: dict[str, str] = {}

        def _read_backend_text(rel_file: str) -> str:
            if not rel_file or root_path is None:
                return ""
            if rel_file in source_text_cache:
                return source_text_cache[rel_file]
            try:
                txt = (root_path / rel_file).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                txt = ""
            source_text_cache[rel_file] = txt
            return txt

        findings: list[dict[str, Any]] = []
        checked = 0
        for call in frontend_api_calls:
            if not isinstance(call, dict):
                continue
            method = str(call.get("method") or "GET").strip().upper()
            raw_path = str(call.get("path") or "").strip()
            if not raw_path:
                continue
            checked += 1
            norm = _normalize_route_path(raw_path)
            source = str(call.get("source") or "frontend")
            if method == "ANY":
                matched_routes = list(routes_by_path.get(norm) or [])
            else:
                matched_routes = list(route_index.get((method, norm)) or [])
                if not matched_routes:
                    matched_routes = list(route_index.get(("ANY", norm)) or [])
            if not matched_routes:
                same_path_methods = sorted(
                    {
                        str(r.get("method") or "").upper()
                        for r in (routes_by_path.get(norm) or [])
                        if r.get("method")
                    }
                )
                is_uncertain = has_dynamic_loader or norm.count("{param}") >= 2
                severity = "warning" if is_uncertain else "error"
                hint = (
                    f"（后端同路径仅见 {'/'.join(same_path_methods)} 方法）"
                    if same_path_methods
                    else (
                        "（项目使用动态路由加载器，建议运行态验证确认）"
                        if has_dynamic_loader
                        else ""
                    )
                )
                findings.append(
                    {
                        "kind": "missing_route",
                        "severity": severity,
                        "method": method,
                        "path": raw_path,
                        "source": source,
                        "detail": (
                            f"前端（{source}）调用 {method} {raw_path}，"
                            f"但后端未声明对应路由{hint}"
                        ),
                    }
                )
                continue
            raw_body_keys = call.get("body_keys")
            body_keys = (
                [str(k).strip() for k in raw_body_keys if isinstance(k, str) and k.strip()]
                if isinstance(raw_body_keys, list)
                else []
            )
            if not body_keys:
                continue

            complete_routes = [r for r in matched_routes if r.get("fields_complete") is True]
            if complete_routes:
                declared_fields: set[str] = set()
                for route in complete_routes:
                    declared_fields.update(_backend_fields_from_route(route))
                if not declared_fields:
                    continue
                mismatched = [key for key in body_keys if key not in declared_fields]
                if mismatched:
                    findings.append(
                        {
                            "kind": "field_mismatch",
                            "severity": "error",
                            "method": method,
                            "path": raw_path,
                            "source": source,
                            "frontend_fields": body_keys,
                            "backend_fields": sorted(declared_fields),
                            "detail": (
                                f"前端（{source}）请求 {method} {raw_path} 提交了后端未声明字段："
                                f"{', '.join(mismatched)}"
                                f"（后端字段：{', '.join(sorted(declared_fields))}）"
                            ),
                        }
                    )
                continue

            # 源码路由无封闭 Schema 但附加 OpenAPI 辅助字段时，仅当源码自身未引用再轻告警
            openapi_ref_fields: set[str] = set()
            backend_files: list[str] = []
            for route in matched_routes:
                raw_oa = route.get("openapi_fields")
                if isinstance(raw_oa, str) and raw_oa.strip():
                    openapi_ref_fields.update(p.strip() for p in raw_oa.split(",") if p.strip())
                bf = str(route.get("file") or "")
                if bf:
                    backend_files.append(bf)
            if openapi_ref_fields:
                extra_keys = [key for key in body_keys if key not in openapi_ref_fields]
                truly_unmentioned = [
                    key
                    for key in extra_keys
                    if not any(
                        re.search(rf"\b{re.escape(key)}\b", _read_backend_text(bf))
                        for bf in backend_files
                    )
                ]
                if truly_unmentioned:
                    findings.append(
                        {
                            "kind": "openapi_drift",
                            "severity": "warning",
                            "method": method,
                            "path": raw_path,
                            "source": source,
                            "frontend_fields": body_keys,
                            "backend_fields": sorted(openapi_ref_fields),
                            "detail": (
                                f"前端（{source}）请求 {method} {raw_path} "
                                "提交了 OpenAPI 未列出且源码未引用的字段："
                                f"{', '.join(truly_unmentioned)}"
                            ),
                        }
                    )

        status = "failed" if findings else "passed"
        summary = (
            f"已对账 {checked} 处前端调用，发现 {len(findings)} 处契约不一致"
            if findings
            else f"已对账 {checked} 处前端调用，未发现路由或字段不匹配"
        )
        return {
            "probe_id": self.probe_id,
            "name": self.name,
            "status": status,
            "checked": checked,
            "summary": summary,
            "findings": findings,
        }


def _http_get_probe(
    url: str,
    *,
    allowed_hosts: list[str] | None,
    timeout_seconds: float = 5.0,
    max_bytes: int = 512 * 1024,
) -> tuple[int | None, str, bytes, str]:
    ok, reason = validate_target_url(url, allowed_hosts=allowed_hosts)
    if not ok:
        return None, "", b"", reason
    req = urllib.request.Request(
        url,
        headers={"Accept": "text/html,application/json;q=0.9,*/*;q=0.8"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            status = int(resp.status)
            ctype = str(resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            body = resp.read(max_bytes)
            return status, ctype, body, ""
    except urllib.error.HTTPError as exc:
        body = b""
        with contextlib.suppress(OSError):
            body = exc.read(64 * 1024)
        raw_ct = str(exc.headers.get("Content-Type") or "") if exc.headers else ""
        ctype = raw_ct.split(";", 1)[0].strip().lower()
        code = int(exc.code)
        exc.close()
        return code, ctype, body, f"HTTP {code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, "", b"", str(exc) or "请求异常"


class StaticAssetsProbe:
    """对运行中本地服务发起真实 GET 检查前端 CSS/JS 资源状态与 MIME 类型。"""

    probe_id = "static_assets"
    name = "静态资源全 200 检查"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        base_url = str(kwargs.get("base_url") or "")
        service_ready = bool(kwargs.get("service_ready", False))
        frontend_assets = list(kwargs.get("frontend_assets") or [])
        allowed_hosts = kwargs.get("allowed_hosts")
        if not service_ready or not base_url:
            reason = (
                "缺少服务基础 URL"
                if not base_url
                else "服务未通过健康检查"
            )
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "summary": f"{reason}，跳过在线静态资源探针",
                "findings": [],
            }
        ok, reason = validate_target_url(base_url, allowed_hosts=allowed_hosts)
        if not ok:
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "summary": f"地址不在允许范围内：{reason}",
                "findings": [],
            }

        refs: list[str] = []
        seen_refs: set[str] = set()
        for entry in frontend_assets:
            if not isinstance(entry, dict):
                continue
            href = str(entry.get("href") or "").strip()
            if not href or href.lower().startswith(_SKIP_ASSET_PREFIXES):
                continue
            if href not in seen_refs:
                seen_refs.add(href)
                refs.append(href)

        root_url = base_url.rstrip("/") + "/"
        home_status, home_ctype, home_body, _home_err = _http_get_probe(
            root_url, allowed_hosts=allowed_hosts
        )
        if home_status is not None and 200 <= home_status < 400 and "html" in home_ctype:
            html_text = home_body.decode("utf-8", errors="replace")
            for pattern in (_HTML_LINK_RE, _HTML_SCRIPT_RE):
                for match in pattern.finditer(html_text):
                    href = match.group(1).strip()
                    if not href or href.lower().startswith(_SKIP_ASSET_PREFIXES):
                        continue
                    if href not in seen_refs:
                        seen_refs.add(href)
                        refs.append(href)

        if not refs:
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "summary": "未发现需要校验的本地静态资源引用",
                "findings": [],
            }

        findings: list[dict[str, Any]] = []
        checked = 0
        for href in refs[:30]:
            rel_href = href if href.startswith("/") else href.lstrip("/")
            target_url = urllib.parse.urljoin(root_url, rel_href)
            status, ctype, body, err = _http_get_probe(target_url, allowed_hosts=allowed_hosts)
            checked += 1
            if status is None:
                findings.append(
                    {
                        "kind": "asset_unreachable",
                        "href": href,
                        "url": target_url,
                        "detail": f"静态资源 {href} 请求失败：{err}",
                    }
                )
                continue
            if status != 200:
                findings.append(
                    {
                        "kind": "asset_http_error",
                        "href": href,
                        "url": target_url,
                        "status_code": status,
                        "detail": f"静态资源 {href} 返回 HTTP {status}（预期 200）",
                    }
                )
                continue
            if len(body) == 0:
                findings.append(
                    {
                        "kind": "asset_empty",
                        "href": href,
                        "url": target_url,
                        "detail": f"静态资源 {href} 响应体为空（0 字节）",
                    }
                )
                continue
            clean_path = urllib.parse.urlsplit(href).path.lower()
            if clean_path.endswith(_STATIC_TEXT_ASSET_SUFFIXES) and "text/html" in ctype:
                findings.append(
                    {
                        "kind": "asset_mime_mismatch",
                        "href": href,
                        "url": target_url,
                        "content_type": ctype,
                        "detail": f"静态资源 {href} 返回了 {ctype}（疑似 SPA 兜底 HTML 假 200）",
                    }
                )

        status_str = "failed" if findings else "passed"
        summary = (
            f"共检查 {checked} 个静态资源，发现 {len(findings)} 个异常"
            if findings
            else f"共检查 {checked} 个静态资源，全部返回有效 200"
        )
        return {
            "probe_id": self.probe_id,
            "name": self.name,
            "status": status_str,
            "checked": checked,
            "summary": summary,
            "findings": findings,
        }


class RouteBoundaryProbe:
    """对公开只读路由和未知路径发起安全 GET 探针，检测 5xx 与服务器堆栈泄露。"""

    probe_id = "route_boundary"
    name = "路由可达与异常边界探针"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        base_url = str(kwargs.get("base_url") or "")
        service_ready = bool(kwargs.get("service_ready", False))
        api_routes = list(kwargs.get("api_routes") or [])
        allowed_hosts = kwargs.get("allowed_hosts")
        if not service_ready or not base_url:
            reason = (
                "缺少服务基础 URL"
                if not base_url
                else "服务未通过健康检查"
            )
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "summary": f"{reason}，跳过在线路由边界探针",
                "findings": [],
            }
        ok, reason = validate_target_url(base_url, allowed_hosts=allowed_hosts)
        if not ok:
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "summary": f"地址不在允许范围内：{reason}",
                "findings": [],
            }

        root_url = base_url.rstrip("/") + "/"
        targets: list[tuple[str, bool]] = []
        seen_paths: set[str] = set()
        for route in api_routes:
            if not isinstance(route, dict):
                continue
            method = str(route.get("method") or "").upper()
            auth = str(route.get("auth") or "public").lower()
            path = str(route.get("path") or "").strip()
            if (
                method == "GET"
                and auth == "public"
                and path.startswith("/")
                and not any(ch in path for ch in ("{", "<", ":"))
                and path not in seen_paths
            ):
                seen_paths.add(path)
                targets.append((path, False))
                if len(targets) >= 5:
                    break
        targets.append(("/__aitest_probe_nonexistent__", True))

        findings: list[dict[str, Any]] = []
        checked = 0
        for rel_path, is_not_found_probe in targets:
            target_url = urllib.parse.urljoin(root_url, rel_path.lstrip("/"))
            status, _ctype, body, _err = _http_get_probe(target_url, allowed_hosts=allowed_hosts)
            if status is None:
                continue
            checked += 1
            snippet = body[:8192].decode("utf-8", errors="replace")
            has_trace = bool(_TRACEBACK_RE.search(snippet))
            if status >= 500 or has_trace:
                label = "未知路径兜底请求" if is_not_found_probe else f"公开路由 GET {rel_path}"
                findings.append(
                    {
                        "kind": "server_error_or_traceback",
                        "path": rel_path,
                        "url": target_url,
                        "status_code": status,
                        "traceback_leaked": has_trace,
                        "detail": (
                            f"{label} 返回 HTTP {status}"
                            + (" 且响应泄露堆栈信息" if has_trace else "")
                        ),
                    }
                )

        if checked == 0:
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "summary": "没有成功连接并检查到可探测路由",
                "findings": [],
            }
        status_str = "failed" if findings else "passed"
        summary = (
            f"已探测 {checked} 个边界路径，发现 {len(findings)} 处 5xx 或堆栈泄露"
            if findings
            else f"已探测 {checked} 个边界路径，均未返回 5xx 或泄露堆栈"
        )
        return {
            "probe_id": self.probe_id,
            "name": self.name,
            "status": status_str,
            "checked": checked,
            "summary": summary,
            "findings": findings,
        }


BUILTIN_PROBES: tuple[DeterministicProbe, ...] = (
    ContractReconciliationProbe(),
    StaticAssetsProbe(),
    RouteBoundaryProbe(),
)


def run_deterministic_probes(
    root: Path,
    config: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    base_url: str = "",
    allowed_hosts: list[str] | None = None,
    service_ready: bool = False,
    probe_targets: dict[str, tuple[str, bool]] | None = None,
    secrets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """依次运行内置确定性硬探针，对照项目错题本过滤已豁免约定并执行脱敏。

    ``probe_targets`` 允许按探针指定目标服务（如静态资源探针打前端、路由边界
    探针打后端）；未列出的探针沿用 ``base_url`` / ``service_ready``。
    """
    conventions = extract_conventions(config)
    routes_meta: dict[str, Any] = {}
    if isinstance(context, dict) and "api_routes" in context:
        api_routes = list(context.get("api_routes") or [])
        raw_meta = context.get("api_routes_meta")
        if isinstance(raw_meta, dict):
            routes_meta = raw_meta
    else:
        api_routes, routes_meta = _api_routes_with_meta(root, max_routes=2000)
    frontend_api_calls = (
        list(context.get("frontend_api_calls") or [])
        if isinstance(context, dict) and "frontend_api_calls" in context
        else _frontend_api_calls(root, max_files=2000, max_calls=1000)
    )
    frontend_assets = (
        list(context.get("frontend_assets") or [])
        if isinstance(context, dict) and "frontend_assets" in context
        else _frontend_assets(root, max_assets=500, max_html=200)
    )

    from aitest.core.live_page_probe import LivePageProbe

    probes_to_run: list[DeterministicProbe] = list(BUILTIN_PROBES)
    probes_cfg = config.get("probes") if isinstance(config, dict) else None
    live_cfg = probes_cfg.get("live_page") if isinstance(probes_cfg, dict) else None
    if isinstance(live_cfg, dict) and live_cfg.get("enabled") is True:
        probes_to_run.append(LivePageProbe())

    results: list[dict[str, Any]] = []
    for probe in probes_to_run:
        target_url, target_ready = (probe_targets or {}).get(
            probe.probe_id, (base_url, service_ready)
        )
        result = probe.run(
            root=root,
            base_url=target_url,
            service_ready=target_ready,
            allowed_hosts=allowed_hosts,
            api_routes=api_routes,
            frontend_api_calls=frontend_api_calls,
            frontend_assets=frontend_assets,
            has_dynamic_loader=bool(routes_meta.get("has_dynamic_loader")),
            config=config,
            secrets=list(secrets or []),
        )
        if conventions and result.get("status") == "failed":
            active_findings: list[dict[str, Any]] = []
            suppressed: list[dict[str, Any]] = []
            for finding in result.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                detail = str(finding.get("detail") or "")
                matched = match_convention(detail, conventions, scope="probe")
                if matched is not None:
                    suppressed.append(
                        {
                            **finding,
                            "convention_matched": True,
                            "convention_reason": str(matched.get("reason") or ""),
                        }
                    )
                else:
                    active_findings.append(finding)
            if suppressed:
                result = {
                    **result,
                    "findings": active_findings,
                    "suppressed_findings": suppressed,
                    "status": "failed" if active_findings else "passed",
                    "summary": (
                        f"{result.get('summary', '')}"
                        f"（已按项目错题本豁免 {len(suppressed)} 项约定）"
                    ),
                }
        results.append(result)
    safe = redact_tree(results, list(secrets or []))
    return safe if isinstance(safe, list) else results
