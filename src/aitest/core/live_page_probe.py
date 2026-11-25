"""可选轻量真页面活 DOM 与控制台巡检探针（LivePageProbe）。

说明：
- 默认按需启用（``config.probes.live_page.enabled=true``
  或显式传入 ``enable_live_page=True``）；
- 优先使用项目已安装的 Playwright 无头 Chromium；若无则尝试本机
  Edge/Chrome ``--headless=new --dump-dom``；
- 若两者均不可用或仅检测到静态环境，自动优雅降级返回 ``status='skipped'``，绝不阻塞主测试链路；
- 本模块严格遵守插件安全边界：URL 必须通过白名单检查，输出文本经过脱敏后返回。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from aitest.security import redact, redact_tree, validate_target_url

_ERROR_OVERLAY_RE = re.compile(
    r"(?:Traceback \(most recent call last\)|Application error:\s*a client-side exception|"
    r"Unhandled Runtime Error|__next_error__|vite-error-overlay|Internal Server Error)",
    re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>|<[^>]+>",
    re.DOTALL | re.I,
)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.DOTALL | re.I)


def detect_local_browser() -> str:
    """探测本机可用的 Edge 或 Chrome 无头可执行文件路径，找不到返回空字符串。"""
    candidates = [
        "msedge",
        "msedge.exe",
        "chrome",
        "chrome.exe",
        "google-chrome",
        "chromium",
    ]
    for cmd in candidates:
        found = shutil.which(cmd)
        if found:
            return found

    if os.name == "nt":
        win_roots = [
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        rel_paths = (
            Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
        )
        for root in win_roots:
            if not root:
                continue
            for rel in rel_paths:
                candidate = Path(root) / rel
                if candidate.is_file():
                    return str(candidate)
    return ""


def _strip_html_visible_text(html: str) -> str:
    cleaned = _TAG_STRIP_RE.sub(" ", html)
    return " ".join(cleaned.split())


def _normalize_target_urls(base_url: str, extra_paths: list[str] | None = None) -> list[str]:
    urls: list[str] = [base_url]
    for raw_path in extra_paths or []:
        path = str(raw_path).strip()
        if not path.startswith("/"):
            continue
        candidate = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        if candidate not in urls:
            urls.append(candidate)
        if len(urls) >= 3:
            break
    return urls


def _analyze_rendered_dom(url: str, html: str, *, secrets: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not html.strip():
        findings.append(
            {
                "kind": "live_dom_empty",
                "method": "GET",
                "path": urllib.parse.urlparse(url).path or "/",
                "url": url,
                "detail": f"真页面巡检访问 {url} 未渲染出任何 HTML 内容",
            }
        )
        return findings

    err_match = _ERROR_OVERLAY_RE.search(html)
    if err_match:
        findings.append(
            {
                "kind": "live_dom_error_overlay",
                "method": "GET",
                "path": urllib.parse.urlparse(url).path or "/",
                "url": url,
                "detail": redact(
                    f"真页面巡检在 {url} 检测到前端错误浮层或服务端异常页：{err_match.group(0)}",
                    secrets,
                ),
            }
        )

    visible_text = _strip_html_visible_text(html)
    title_match = _TITLE_RE.search(html)
    has_root_shell = 'id="app"' in html or 'id="root"' in html
    if len(visible_text) < 12 and (has_root_shell or title_match is None):
        findings.append(
            {
                "kind": "live_dom_blank",
                "method": "GET",
                "path": urllib.parse.urlparse(url).path or "/",
                "url": url,
                "detail": (
                    f"真页面巡检在 {url} 疑似白屏（挂载节点可见文本仅 {len(visible_text)} 字符）"
                ),
            }
        )
    return findings


def _inspect_with_playwright(
    urls: list[str],
    *,
    timeout_seconds: float,
    secrets: list[str],
) -> tuple[bool, list[dict[str, Any]], int, str]:
    """尝试通过可选依赖 Playwright 访问页面并抓取 Console/PageError/4xx-5xx。"""
    if importlib.util.find_spec("playwright") is None:
        return False, [], 0, "未安装 playwright"
    try:
        sync_mod = importlib.import_module("playwright.sync_api")
        sync_playwright = getattr(sync_mod, "sync_playwright", None)
        if not callable(sync_playwright):
            return False, [], 0, "playwright.sync_api 不可用"
    except Exception as exc:  # noqa: BLE001
        return False, [], 0, f"加载 playwright 失败：{exc}"

    findings: list[dict[str, Any]] = []
    checked = 0
    timeout_ms = max(1000, int(timeout_seconds * 1000))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                console_errors: list[str] = []
                bad_responses: list[tuple[int, str]] = []
                page.on(
                    "console",
                    lambda msg: console_errors.append(str(msg.text))
                    if getattr(msg, "type", "") == "error"
                    else None,
                )
                page.on("pageerror", lambda err: console_errors.append(f"PageError: {err}"))
                page.on(
                    "response",
                    lambda resp: bad_responses.append((int(resp.status), str(resp.url)))
                    if int(getattr(resp, "status", 200)) >= 400
                    else None,
                )

                for url in urls:
                    console_errors.clear()
                    bad_responses.clear()
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    html = page.content()
                    checked += 1
                    findings.extend(_analyze_rendered_dom(url, html, secrets=secrets))
                    for c_err in console_errors[:5]:
                        findings.append(
                            {
                                "kind": "console_error",
                                "method": "GET",
                                "path": urllib.parse.urlparse(url).path or "/",
                                "url": url,
                                "detail": redact(
                                    f"页面 {url} 触发浏览器控制台错误：{c_err[:220]}",
                                    secrets,
                                ),
                            }
                        )
                    for status, resp_url in bad_responses[:5]:
                        findings.append(
                            {
                                "kind": "network_error",
                                "method": "GET",
                                "path": urllib.parse.urlparse(resp_url).path or "/",
                                "url": resp_url,
                                "status": status,
                                "detail": redact(
                                    f"页面 {url} 加载资源/接口返回 HTTP {status}：{resp_url}",
                                    secrets,
                                ),
                            }
                        )
            finally:
                browser.close()
        return True, findings, checked, ""
    except Exception as exc:  # noqa: BLE001
        return False, [], 0, f"Playwright 执行异常：{exc}"


def _inspect_with_local_browser(
    browser_exe: str,
    urls: list[str],
    *,
    timeout_seconds: float,
    secrets: list[str],
) -> tuple[bool, list[dict[str, Any]], int, str]:
    """使用本机 Edge/Chrome 无头 ``--dump-dom`` 兜底采集真实渲染后的 DOM。"""
    findings: list[dict[str, Any]] = []
    checked = 0
    for url in urls:
        cmd = [
            browser_exe,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--virtual-time-budget=2500",
            "--dump-dom",
            url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(3.0, timeout_seconds),
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, [], checked, f"本机浏览器无头巡检失败：{exc}"
        if proc.returncode != 0 and not proc.stdout.strip():
            return (
                False,
                [],
                checked,
                f"本机浏览器退出码 {proc.returncode}：{proc.stderr.strip()[:200]}",
            )
        checked += 1
        findings.extend(_analyze_rendered_dom(url, proc.stdout[:512 * 1024], secrets=secrets))
    return True, findings, checked, ""


class LivePageProbe:
    """按需采集真页面活 DOM、网络异常与浏览器报错的轻量探针。"""

    probe_id = "live_page_inspection"
    name = "真页面活 DOM 与控制台巡检"

    def run(self, **kwargs: Any) -> dict[str, Any]:
        raw_config = kwargs.get("config")
        config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
        raw_probes = config.get("probes")
        probes_cfg: dict[str, Any] = raw_probes if isinstance(raw_probes, dict) else {}
        raw_live = probes_cfg.get("live_page")
        live_cfg: dict[str, Any] = raw_live if isinstance(raw_live, dict) else {}
        enabled = bool(kwargs.get("enable_live_page") or live_cfg.get("enabled", False))
        if not enabled:
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "engine": "disabled",
                "summary": "未启用真页面巡检（可在 config.probes.live_page.enabled 中按需打开）",
                "findings": [],
            }

        base_url = str(kwargs.get("base_url") or "").strip()
        service_ready = bool(kwargs.get("service_ready", False))
        allowed_hosts = kwargs.get("allowed_hosts")
        secrets = [str(s) for s in (kwargs.get("secrets") or []) if isinstance(s, str)]
        timeout_seconds = float(kwargs.get("timeout_seconds") or 10.0)
        extra_paths = kwargs.get("paths") or live_cfg.get("paths") or []

        if not service_ready or not base_url:
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "skipped",
                "checked": 0,
                "engine": "none",
                "summary": "未就绪且缺少服务 URL，跳过真页面巡检",
                "findings": [],
            }

        ok_url, reason = validate_target_url(base_url, allowed_hosts=allowed_hosts)
        if not ok_url:
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": "failed",
                "checked": 0,
                "engine": "policy",
                "summary": f"真页面巡检目标地址被安全白名单拦截：{reason}",
                "findings": [
                    {
                        "kind": "url_blocked",
                        "method": "GET",
                        "path": urllib.parse.urlparse(base_url).path or "/",
                        "url": base_url,
                        "detail": f"目标地址 {base_url} 未通过主机白名单校验：{reason}",
                    }
                ],
            }

        paths_arg = list(extra_paths) if isinstance(extra_paths, list) else []
        targets = _normalize_target_urls(base_url, paths_arg)

        # 1) 优先尝试 Playwright（可捕获 Console Error 与网络 4xx/5xx）
        ok_pw, findings, checked, pw_reason = _inspect_with_playwright(
            targets,
            timeout_seconds=timeout_seconds,
            secrets=secrets,
        )
        if ok_pw:
            status = "failed" if findings else "passed"
            safe_findings = redact_tree(findings, secrets)
            return {
                "probe_id": self.probe_id,
                "name": self.name,
                "status": status,
                "checked": checked,
                "engine": "playwright",
                "summary": (
                    f"Playwright 巡检 {checked} 个页面，发现 {len(findings)} 处渲染或控制台异常"
                    if findings
                    else f"Playwright 巡检 {checked} 个页面，活 DOM、网络与控制台均正常"
                ),
                "findings": safe_findings if isinstance(safe_findings, list) else findings,
            }

        # 2) 回退到本机 Edge / Chrome 无头 --dump-dom
        browser_exe = detect_local_browser()
        if browser_exe:
            ok_local, local_findings, local_checked, local_reason = _inspect_with_local_browser(
                browser_exe,
                targets,
                timeout_seconds=timeout_seconds,
                secrets=secrets,
            )
            if ok_local:
                status = "failed" if local_findings else "passed"
                safe_findings = redact_tree(local_findings, secrets)
                findings_out = (
                    safe_findings if isinstance(safe_findings, list) else local_findings
                )
                return {
                    "probe_id": self.probe_id,
                    "name": self.name,
                    "status": status,
                    "checked": local_checked,
                    "engine": "local_browser",
                    "summary": (
                        f"本机浏览器巡检 {local_checked} 个页面，"
                        f"发现 {len(local_findings)} 处渲染异常"
                        if local_findings
                        else f"本机浏览器巡检 {local_checked} 个页面，DOM 渲染正常"
                    ),
                    "findings": findings_out,
                }
            fallback_note = f"{pw_reason}；{local_reason}"
        else:
            fallback_note = f"{pw_reason}，且未检测到本机 Edge/Chrome"

        return {
            "probe_id": self.probe_id,
            "name": self.name,
            "status": "skipped",
            "checked": 0,
            "engine": "unavailable",
            "summary": f"已跳过真页面巡检（{fallback_note}）",
            "findings": [],
        }
