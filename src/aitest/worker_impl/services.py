"""Worker 拆分模块：services。"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from aitest.models import Case
from aitest.runners.command_runner import terminate_process_tree
from aitest.security import (
    redact_bytes,
)
from aitest.worker_impl.env import _SINGLE_SERVICE_KEYS, _as_float


def _services(config: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Return every configured service as ``(name, config)``.

    Two shapes are accepted, matching what the panel stores:
    * a single service ``{start, health, stop, ...}`` (name is empty);
    * a mapping ``service name -> config``.
    Anything else is treated as "no service configured".
    """
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



def _healthy(url: str) -> bool | None:
    """Return True/False, or None when no health check is configured."""
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            status = int(response.status)
            return status < 400 or status in {401, 403}
    except urllib.error.HTTPError as exc:
        return exc.code < 400 or exc.code in {401, 403}
    except (urllib.error.URLError, OSError, ValueError):
        return False



def _pump_output(stream: Any, path: Path, secrets: list[str]) -> None:
    """Copy child output to a log file line by line, redacting known secrets."""
    try:
        with path.open("ab") as handle:
            for line in iter(stream.readline, b""):
                handle.write(redact_bytes(line, secrets))
                handle.flush()
    except OSError:
        return
    finally:
        with contextlib.suppress(OSError):
            stream.close()



def _start_service(
    config: dict[str, object],
    cwd: Path,
    log: Path,
    secrets: list[str],
    env: dict[str, str],
    cancelled: Callable[[], bool] | None = None,
) -> tuple[bool, bool, subprocess.Popen[bytes] | None]:
    health = str(config.get("health") or "").strip()
    initial = _healthy(health)
    if initial is True:
        # The service is already running; use it without taking ownership.
        return False, True, None
    start = str(config.get("start") or "").strip()
    if not start:
        return False, bool(health and initial), None
    timeout = _as_float(config.get("timeout_seconds"), 120.0)
    log.parent.mkdir(parents=True, exist_ok=True)
    creationflags = (0x00000200 | 0x08000000) if os.name == "nt" else 0
    process = subprocess.Popen(
        start,
        shell=True,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    assert process.stdout is not None
    threading.Thread(
        target=_pump_output, args=(process.stdout, log, secrets), daemon=True
    ).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled is not None and cancelled():
            with log.open("ab") as handle:
                handle.write("取消请求已收到，停止等待服务健康\n".encode())
            return True, False, process
        if health and _healthy(health) is True:
            return True, True, process
        if process.poll() is not None:
            if health:
                return True, _healthy(health) is True, process
            return True, process.returncode == 0, process
        time.sleep(0.5)
    if not health:
        with log.open("ab") as handle:
            handle.write("未配置 health，超时内无法确认服务就绪\n".encode())
    return True, False, process



def _stop_service(
    config: dict[str, object],
    cwd: Path,
    log: Path,
    secrets: list[str],
    env: dict[str, str] | None = None,
    process: subprocess.Popen[bytes] | None = None,
) -> None:
    stop = str(config.get("stop") or "").strip()
    if stop:
        creationflags = (0x00000200 | 0x08000000) if os.name == "nt" else 0
        stopper: subprocess.Popen[bytes] | None = None
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            stopper = subprocess.Popen(
                stop,
                shell=True,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
            )
            assert stopper.stdout is not None
            threading.Thread(
                target=_pump_output, args=(stopper.stdout, log, secrets), daemon=True
            ).start()
            stopper.wait(timeout=120)
        except subprocess.TimeoutExpired:
            with log.open("ab") as handle:
                handle.write("停止服务的命令超过 120 秒，改为结束进程树\n".encode())
            if stopper is not None:
                terminate_process_tree(stopper)
        except OSError:
            pass
    if process is not None:
        terminate_process_tree(process)



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



