"""Run one shell command and capture raw output; no evidence persistence."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

from aitest.runners.base import RunnerResult

_MAX_OUTPUT_BYTES = 20 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


def _read_capped_stream(stream: IO[bytes], limit: int) -> tuple[bytes, int]:
    """分块读取临时文件流并限制在 limit 字节内；超限时保留头 25% + 尾 75% 以保完整堆栈。"""
    stream.seek(0, os.SEEK_END)
    total_size = int(stream.tell())
    stream.seek(0)
    limit = max(0, limit)
    if total_size <= limit or limit < 256:
        remaining = limit
        chunks: list[bytes] = []
        while remaining > 0:
            chunk = stream.read(min(_READ_CHUNK_BYTES, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks), total_size

    marker = b"\n...[middle output omitted to preserve error tail]...\n"
    usable = limit - len(marker)
    head_cap = usable // 4
    tail_cap = usable - head_cap
    head_bytes = stream.read(head_cap)
    stream.seek(max(head_cap, total_size - tail_cap))
    tail_bytes = stream.read(tail_cap)
    return head_bytes + marker + tail_bytes, total_size


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the shell and every descendant, not just the immediate child."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=0x08000000,
        )
        if process.poll() is None:
            process.kill()
        return
    try:
        os.killpg(  # type: ignore[attr-defined]
            os.getpgid(process.pid),  # type: ignore[attr-defined]
            signal.SIGKILL,  # type: ignore[attr-defined]
        )
    except (OSError, ProcessLookupError):
        process.kill()


def terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Public wrapper used by the worker when it owns a service process."""

    _kill_process_tree(process)


def run_command(
    command: str,
    cwd: Path,
    timeout_seconds: float = 600,
    env: dict[str, str] | None = None,
    cancelled: Callable[[], bool] | None = None,
    max_output_bytes: int = _MAX_OUTPUT_BYTES,
) -> RunnerResult:
    """Run ``command`` through the shell in ``cwd`` and map the outcome."""
    creationflags = (
        (0x00000200 | 0x08000000) if os.name == "nt" else 0
    )  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    # Files rather than pipes: a grandchild holding a pipe can otherwise block
    # communicate() forever after a timeout. Reads are capped below.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                shell=True,
                stdout=out,
                stderr=err,
                env=env,
                start_new_session=os.name != "nt",
                creationflags=creationflags,
            )
        except OSError as error:
            return RunnerResult(status="error", error=f"failed to start command: {error}")
        timed_out = False
        was_cancelled = False
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                process.communicate(timeout=min(0.5, remaining))
                break
            except subprocess.TimeoutExpired:
                if cancelled is not None and cancelled():
                    was_cancelled = True
                    break
        if timed_out or was_cancelled:
            _kill_process_tree(process)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=5)
        stdout, out_size = _read_capped_stream(out, max_output_bytes)
        stderr, err_size = _read_capped_stream(err, max_output_bytes)
        if out_size > max_output_bytes or err_size > max_output_bytes:
            if max_output_bytes >= 1024 * 1024:
                limit_label = f"{max_output_bytes // (1024 * 1024)} MiB"
            else:
                limit_label = f"{max_output_bytes} bytes"
            stderr += (
                f"\n[output exceeded the {limit_label} capture limit and was truncated]"
            ).encode()
    if was_cancelled:
        return RunnerResult(
            status="blocked",
            stdout=stdout,
            stderr=stderr,
            error="command cancelled",
        )
    if timed_out:
        return RunnerResult(
            status="blocked",
            stdout=stdout,
            stderr=stderr,
            error=f"command timed out after {timeout_seconds:g}s",
        )
    return RunnerResult(
        status="passed" if process.returncode == 0 else "failed",
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
