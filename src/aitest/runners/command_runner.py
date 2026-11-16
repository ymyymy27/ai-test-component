"""Run one shell command and capture raw output; no evidence persistence."""

from __future__ import annotations

import subprocess
from pathlib import Path

from aitest.runners.base import RunnerResult


def _as_bytes(value: bytes | str | None) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    return b""


def run_command(
    command: str,
    cwd: Path,
    timeout_seconds: float = 600,
    env: dict[str, str] | None = None,
) -> RunnerResult:
    """Run ``command`` through the shell in ``cwd`` and map the outcome."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as error:
        return RunnerResult(
            status="blocked",
            stdout=_as_bytes(error.stdout),
            stderr=_as_bytes(error.stderr),
            error=f"command timed out after {timeout_seconds:g}s",
        )
    except OSError as error:
        return RunnerResult(status="error", error=f"failed to start command: {error}")
    return RunnerResult(
        status="passed" if completed.returncode == 0 else "failed",
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
