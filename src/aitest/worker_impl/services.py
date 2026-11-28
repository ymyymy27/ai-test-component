"""Worker 拆分模块：services（含 Windows Job Object 进程树回收与文件流直写日志）。"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any, cast

from aitest.models import Case
from aitest.runners.command_runner import terminate_process_tree
from aitest.security import redact_bytes
from aitest.worker_impl.env import _SINGLE_SERVICE_KEYS, _as_float

_PROCESS_JOB_HANDLES: dict[int, int] = {}


def _services(config: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """返回全部配置的服务 `(name, config)`（兼容单服务扁平格式与多服务映射）。"""
    value = config.get("services")
    if not isinstance(value, dict):
        return []
    if _SINGLE_SERVICE_KEYS & set(value):
        return [("", value)]
    if not value or not all(isinstance(item, dict) for item in value.values()):
        return []
    services: list[tuple[str, dict[str, object]]] = []
    for name, item in value.items():
        if isinstance(item, dict):
            services.append((str(name), cast(dict[str, object], item)))
    return services


_HEALTH_ACCEPT = "text/html,application/json;q=0.9,*/*;q=0.8"


def _healthy(
    url: str,
    *,
    expect_statuses: set[int] | None = None,
) -> bool | None:
    """检查服务健康端点；不再把 401/403 盲目当成已健康复用。"""
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    request = urllib.request.Request(url, headers={"Accept": _HEALTH_ACCEPT}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            status = int(response.status)
            return status in expect_statuses if expect_statuses else status < 400
    except urllib.error.HTTPError as exc:
        return exc.code in expect_statuses if expect_statuses else exc.code < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _create_windows_job_object() -> int | None:
    """在 Windows 上创建 KILL_ON_JOB_CLOSE Job Object 确保子进程树彻底回收。"""
    if os.name != "nt":
        return None
    with contextlib.suppress(Exception):
        import ctypes
        from ctypes import wintypes

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _BASIC_LIMIT_INFO(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _EXTENDED_LIMIT_INFO(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC_LIMIT_INFO),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED_LIMIT_INFO()
        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000) | JOB_OBJECT_LIMIT_BREAKAWAY_OK (0x800)
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x0800
        # JobObjectExtendedLimitInformation = 9
        ok = kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return int(job)
    return None


def _assign_pid_to_job(job_handle: int | None, pid: int) -> None:
    if not job_handle or os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # PROCESS_SET_QUOTA (0x0100) | PROCESS_TERMINATE (0x0001)
        proc = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
        if proc:
            try:
                kernel32.AssignProcessToJobObject(job_handle, proc)
            finally:
                kernel32.CloseHandle(proc)


def _close_windows_job(job_handle: int | None) -> None:
    if not job_handle or os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateJobObject(job_handle, 1)
        kernel32.CloseHandle(job_handle)


def _record_service_pid(run_dir: Path | None, service_name: str, pid: int | None) -> None:
    if run_dir is None or pid is None:
        return
    pid_file = run_dir / "service-pids.json"
    with contextlib.suppress(OSError, json.JSONDecodeError):
        data: dict[str, int] = {}
        if pid_file.is_file():
            raw = json.loads(pid_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): int(v) for k, v in raw.items() if isinstance(v, int)}
        data[service_name or "default"] = pid
        pid_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _remove_service_pid(run_dir: Path | None, service_name: str) -> None:
    if run_dir is None:
        return
    pid_file = run_dir / "service-pids.json"
    if not pid_file.is_file():
        return
    with contextlib.suppress(OSError, json.JSONDecodeError):
        raw = json.loads(pid_file.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.pop(service_name or "default", None)
            if raw:
                pid_file.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                pid_file.unlink(missing_ok=True)


def _redact_log_file_in_place(log: Path, secrets: list[str]) -> None:
    """服务停止或日志落盘后执行一次凭据脱敏回写，确保磁盘文件不留敏感信息。"""
    if not secrets or not log.is_file():
        return
    with contextlib.suppress(OSError):
        if log.stat().st_size > 20 * 1024 * 1024:
            return
        raw = log.read_bytes()
        safe = redact_bytes(raw, secrets)
        if safe != raw:
            log.write_bytes(safe)


def _start_service(
    config: dict[str, object],
    cwd: Path,
    log: Path,
    secrets: list[str],
    env: dict[str, str],
    cancelled: Callable[[], bool] | None = None,
    *,
    run_dir: Path | None = None,
    service_name: str = "",
) -> tuple[bool, bool, subprocess.Popen[bytes] | None]:
    health = str(config.get("health") or "").strip()
    raw_expect = config.get("health_expect_status")
    expect_statuses = (
        {int(x) for x in raw_expect if isinstance(x, int)}
        if isinstance(raw_expect, list) and raw_expect
        else None
    )
    initial = _healthy(health, expect_statuses=expect_statuses)
    if initial is True:
        return False, True, None
    start = str(config.get("start") or "").strip()
    if not start:
        return False, bool(health and initial), None
    timeout = _as_float(config.get("timeout_seconds"), 120.0)
    log.parent.mkdir(parents=True, exist_ok=True)
    # 文件直写替代 PIPE + 泵线程，避免大量日志塞满管道导致服务死锁
    log_handle: IO[bytes] = log.open("ab")
    creationflags = (0x00000200 | 0x08000000) if os.name == "nt" else 0
    job_handle = _create_windows_job_object()
    try:
        process = subprocess.Popen(
            start,
            shell=True,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
    except OSError:
        log_handle.close()
        _close_windows_job(job_handle)
        return False, False, None

    # 父进程关闭自己的文件句柄副本或保留于属性上以便停止后清理；
    # 在 Windows/POSIX 上子进程继承后，父进程即可安全关闭写入端句柄不占锁
    log_handle.close()
    if job_handle is not None:
        _PROCESS_JOB_HANDLES[id(process)] = job_handle
    _assign_pid_to_job(job_handle, process.pid)
    _record_service_pid(run_dir, service_name, process.pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled is not None and cancelled():
            with contextlib.suppress(OSError), log.open("ab") as handle:
                handle.write("取消请求已收到，停止等待服务健康\n".encode())
            _redact_log_file_in_place(log, secrets)
            return True, False, process
        if health and _healthy(health, expect_statuses=expect_statuses) is True:
            _redact_log_file_in_place(log, secrets)
            return True, True, process
        if process.poll() is not None:
            _redact_log_file_in_place(log, secrets)
            if health:
                return True, _healthy(health, expect_statuses=expect_statuses) is True, process
            return True, process.returncode == 0, process
        time.sleep(0.5)
    if not health:
        with contextlib.suppress(OSError), log.open("ab") as handle:
            handle.write("未配置 health，超时内无法确认服务就绪\n".encode())
    _redact_log_file_in_place(log, secrets)
    return True, False, process


def _stop_service(
    config: dict[str, object],
    cwd: Path,
    log: Path,
    secrets: list[str],
    env: dict[str, str] | None = None,
    process: subprocess.Popen[bytes] | None = None,
    *,
    run_dir: Path | None = None,
    service_name: str = "",
) -> None:
    stop = str(config.get("stop") or "").strip()
    if stop:
        creationflags = (0x00000200 | 0x08000000) if os.name == "nt" else 0
        stopper: subprocess.Popen[bytes] | None = None
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("ab") as stop_handle:
                stopper = subprocess.Popen(
                    stop,
                    shell=True,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stop_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                    creationflags=creationflags,
                )
            stopper.wait(timeout=120)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError), log.open("ab") as handle:
                handle.write("停止服务的命令超过 120 秒，改为结束进程树\n".encode())
            if stopper is not None:
                terminate_process_tree(stopper)
        except OSError:
            pass
    if process is not None:
        job_handle = _PROCESS_JOB_HANDLES.pop(id(process), None)
        _close_windows_job(job_handle)
        terminate_process_tree(process)
    _remove_service_pid(run_dir, service_name)
    _redact_log_file_in_place(log, secrets)


_E2E_COMMAND_RE = re.compile(r"\b(?:playwright|cypress)\b", re.IGNORECASE)


_DEV_SERVER_RE = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?dev\b",
    re.IGNORECASE,
)


def _is_e2e_command(method: str) -> bool:
    return bool(_E2E_COMMAND_RE.search(method))


_SERVICE_SCRIPT_MARKERS = (
    "localhost:",
    "127.0.0.1:",
    "backend_base_url",
    "frontend_base_url",
    "vite_api_url",
    "app_base_url",
    "base_url",
)


def _case_needs_services(case: Case) -> bool:
    """True when a case talks to the running stack and needs it started."""
    method = case.verify_method or ""
    if method.startswith(("playwright:", "http:")):
        return True
    if method.startswith("script:"):
        content = (case.script_content or "").lower()
        if any(marker in content for marker in _SERVICE_SCRIPT_MARKERS):
            return True
    return _is_e2e_command(method)


def _is_dev_server_command(method: str) -> bool:
    if not method.startswith(("command:", "playwright:")):
        return False
    return bool(_DEV_SERVER_RE.search(method))


def _service_block_reason(
    method: str,
    service_configs: list[Any],
    service_ok: bool,
    env: dict[str, str],
) -> str:
    """Reasons that must block before a command can hang waiting for a server."""

    if _is_dev_server_command(method):
        return "长驻开发服务命令不能作为业务用例；改用快速检查或服务配置（不计为产品失败）"
    if (
        _is_e2e_command(method)
        and (not service_configs or not service_ok)
        and not env.get("PLAYWRIGHT_BASE_URL")
    ):
        return (
            "端到端测试需要可访问的服务；当前未配置或服务未就绪，"
            "未执行（不计为产品失败）"
        )
    return ""
