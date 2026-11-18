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

from aitest.runners.base import RunnerResult

_MAX_OUTPUT_BYTES = 20 * 1024 * 1024


def _as_bytes(value: bytes | str | None) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return b""


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the shell and every descendant, not just the immediate child."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
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


def run_command(
    command: str,
    cwd: Path,
    timeout_seconds: float = 600,
    env: dict[str, str] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> RunnerResult:
    """Run ``command`` through the shell in ``cwd`` and map the outcome."""
    creationflags = 0x00000200 if os.name == "nt" else 0  # CREATE_NEW_PROCESS_GROUP
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
        out.seek(0)
        err.seek(0)
        stdout = out.read(_MAX_OUTPUT_BYTES)
        stderr = err.read(_MAX_OUTPUT_BYTES)
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
