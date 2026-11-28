"""后台服务日志窗口联动模块（Service Log Correlation）。

在单条用例执行前后采集受控服务日志文件（service*.log）字节偏移量，
截取本用例时间窗口内的新增后台日志（经凭据脱敏后落盘），用于：
1. 捕捉 5xx、未捕获 Traceback/CRITICAL/panic 等诊断信号（仅作证据附件与警告，绝不翻盘通过结论）；
2. 识别日志轮转（文件截断/重建）并从新文件头部重读。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aitest.security import redact, redact_bytes

_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):")
_UNHANDLED_RE = re.compile(
    r"(?:UnhandledPromiseRejection|Unhandled exception in|CRITICAL:|FATAL:|"
    r"\bpanic:\s|\[Nest\]\s+\d+\s+-\s+.*ERROR\b)",
    re.IGNORECASE,
)
_HTTP_5XX_RE = re.compile(
    r'(?:"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+[^"]+"\s+|HTTP/1\.[01]"?\s+|'
    r'\bstatus[=:]\s*)(50\d)\b'
)
_ACCESS_LOG_RE = re.compile(
    r'(?:"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+[^"]+"|HTTP/1\.[01])'
)
_SERVER_ERROR_NEGATION_RE = re.compile(
    r"(?:无|不(?:得|能|应|会|出现|返回|产生)|禁|避免|防止|未|never|no|not|without)"
    r"[\s\u4e00-\u9fff]{0,6}(?:50[023]|服务端异常|内部错误|服务器错误|server\s*error)",
    re.IGNORECASE,
)
_SERVER_ERROR_ITEM_RE = re.compile(
    r"(?:(?:应|需|预期|期望|验证|测试|返回|响应|触发)\s*50[023]|50[023]\s*(?:降级|错误页|提示|容错)|"
    r"expect(?:s|ed)?\s*50[023])",
    re.IGNORECASE,
)


def case_expects_server_error(title: str, expected: str, expectation_source: str) -> bool:
    """判断用例是否在主动验收 5xx 容错响应，避免否定表述（如“无 500”）误关监控。"""
    combined = f"{title} {expected} {expectation_source}"
    if _SERVER_ERROR_NEGATION_RE.search(combined):
        return False
    return bool(_SERVER_ERROR_ITEM_RE.search(combined))


def capture_log_offsets(service_states: list[dict[str, Any]]) -> dict[str, int]:
    """记录所有运行中服务日志文件的当前末尾字节偏移量。"""
    offsets: dict[str, int] = {}
    for state in service_states:
        log_path = state.get("log")
        if not isinstance(log_path, Path):
            continue
        key = str(log_path)
        try:
            offsets[key] = log_path.stat().st_size if log_path.is_file() else 0
        except OSError:
            offsets[key] = 0
    return offsets


def _read_slice(path: Path, start: int, max_read_bytes: int = 1024 * 1024) -> bytes:
    """读取日志增量片段；当发现 `size < start`（发生日志轮转/重建）时归零读取最新文件。"""
    try:
        if not path.is_file():
            return b""
        size = path.stat().st_size
        if size == 0 or size == start:
            return b""
        effective_start = 0 if size < start else start
        to_read = size - effective_start
        with path.open("rb") as handle:
            if to_read > max_read_bytes:
                handle.seek(size - max_read_bytes)
            else:
                handle.seek(effective_start)
            return handle.read()
    except OSError:
        return b""


def _prefix_has_access_log(path: Path, end_offset: int) -> bool:
    if end_offset <= 0 or not path.is_file():
        return False
    try:
        actual_end = min(end_offset, path.stat().st_size, 256 * 1024)
        if actual_end <= 0:
            return False
        with path.open("rb") as handle:
            head = handle.read(actual_end).decode("utf-8", errors="replace")
        return bool(_ACCESS_LOG_RE.search(head))
    except OSError:
        return False


def _extract_fatal_lines(text: str, service_label: str) -> list[str]:
    issues: list[str] = []
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue
        if _TRACEBACK_RE.search(stripped):
            block_end = min(len(lines), idx + 20)
            block_lines: list[str] = [stripped]
            for pos in range(idx + 1, block_end):
                cur = lines[pos].strip()
                if not cur:
                    break
                block_lines.append(cur)
                if not lines[pos].startswith((" ", "\t")) and ":" in cur:
                    break
            tail = block_lines[-1] if block_lines else stripped
            preview = " | ".join(block_lines[-3:])
            issues.append(f"[{service_label}] 后台出现未捕获异常：{tail[:160]} ({preview[:180]})")
            idx += len(block_lines)
            continue
        if _UNHANDLED_RE.search(stripped):
            issues.append(f"[{service_label}] {stripped[:200]}")
            idx += 1
            continue
        match = _HTTP_5XX_RE.search(stripped)
        if match:
            issues.append(f"[{service_label}] 后台返回 HTTP {match.group(1)}：{stripped[:200]}")
        idx += 1
    return issues[:6]


def _state_netloc(state: dict[str, Any]) -> str:
    """从服务配置的 health/base_url/url 取 host:port，用于按用例目标过滤日志。"""
    config = state.get("config")
    if not isinstance(config, dict):
        return ""
    for key in ("health", "health_url", "base_url", "url"):
        raw = str(config.get(key) or "").strip()
        if not raw:
            continue
        try:
            parsed = urlsplit(raw)
        except ValueError:
            continue
        if parsed.netloc:
            return parsed.netloc.lower()
    return ""


def inspect_service_log_window(
    service_states: list[dict[str, Any]],
    offsets: dict[str, int],
    *,
    case_method: str,
    expects_server_error: bool = False,
    secrets: list[str] | None = None,
    case_targets: set[str] | None = None,
    max_bytes: int = 65536,
) -> dict[str, Any]:
    """提取用例执行期间新增后台日志并检测 5xx/Traceback 诊断信号与零命中提示。"""
    secret_list = list(secrets or [])
    targets = {item.lower() for item in (case_targets or set()) if item}
    window_parts: list[str] = []
    fatal_errors: list[str] = []
    warnings: list[str] = []
    started_services = 0
    targeted_started = 0
    targeted_added = 0
    targeted_prior_access = False

    for state in service_states:
        log_path = state.get("log")
        if not isinstance(log_path, Path):
            continue
        label = str(state.get("name") or "default")
        if state.get("started") and state.get("ok"):
            started_services += 1
        key = str(log_path)
        start_offset = offsets.get(key, 0)
        prior_access = _prefix_has_access_log(log_path, start_offset)
        delta = _read_slice(log_path, start_offset)
        is_targeted = not targets or _state_netloc(state) in targets
        if is_targeted:
            if state.get("started") and state.get("ok"):
                targeted_started += 1
            targeted_added += len(delta)
            if prior_access:
                targeted_prior_access = True
        if not delta:
            continue
        safe_delta = redact_bytes(delta, secret_list)
        if len(safe_delta) > max_bytes:
            safe_delta = safe_delta[-max_bytes:]
        text = redact(safe_delta.decode("utf-8", errors="replace"), secret_list)
        window_parts.append(f"=== service:{label} (+{len(delta)} bytes) ===\n{text.strip()}")
        if not expects_server_error:
            fatal_errors.extend(_extract_fatal_lines(text, label))

    ambiguous_multi_service = not targets and started_services > 1
    zero_server_hit = False
    if (
        not ambiguous_multi_service
        and targeted_started > 0
        and targeted_added == 0
        and targeted_prior_access
        and case_method.startswith(("http:", "script:", "playwright:"))
    ):
        zero_server_hit = True
        warnings.append(
            "用例触发端到端或 HTTP 验证，但目标服务在此窗口期无任何新增请求日志"
            "（疑似空跑或未打到真实服务）"
        )

    joined = "\n\n".join(window_parts)
    if len(joined.encode("utf-8")) > max_bytes:
        joined = joined.encode("utf-8")[-max_bytes:].decode("utf-8", errors="ignore")

    return {
        "window_log": joined.encode("utf-8") if joined else b"",
        "fatal_errors": fatal_errors,
        "warnings": warnings,
        "zero_server_hit": zero_server_hit,
    }
