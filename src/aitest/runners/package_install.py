"""Install Python packages into a project interpreter, tolerating pip-less venvs."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class InstallOutcome:
    """One package-install attempt chain, with the installer that succeeded."""

    ok: bool
    exit_code: int | None
    output: bytes
    installer: str
    attempts: list[str] = field(default_factory=list)
    error: str = ""


def _pip_missing(output: bytes) -> bool:
    text = output.decode("utf-8", errors="replace").lower()
    return (
        "no module named pip" in text
        or "no module named 'pip'" in text
        or "pip is not available" in text
    )


def _run(
    command: list[str],
    cwd: Path,
    env: dict[str, str] | None,
    timeout_seconds: float,
) -> tuple[int | None, bytes, str]:
    """Run one installer command and return (exit_code, output, spawn_error)."""

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=max(0.1, timeout_seconds),
            check=False,
            cwd=cwd,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        parts = [exc.stdout or b"", exc.stderr or b""]
        return None, b"\n".join(part for part in parts if part), "timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc).encode("utf-8", errors="replace"), "spawn"
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    return completed.returncode, stdout + stderr, ""


def _pip_command(python: Path, packages: list[str]) -> list[str]:
    return [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        *packages,
    ]


def _uv_command(uv: str, python: Path, packages: list[str]) -> list[str]:
    return [
        uv,
        "pip",
        "install",
        "--python",
        str(python),
        *packages,
    ]


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def install_packages(
    python: Path,
    packages: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 600,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> InstallOutcome:
    """Install into ``python``; uv/ensurepip only when that interpreter lacks pip.

    A uv-created venv has no pip by design, so ``python -m pip`` is not a safe
    assumption. Order: pip, uv (if installed), ensurepip bootstrap + pip retry.
    Once an installer actually starts and fails, later installers are not tried:
    that is a dependency/network failure, not a missing-pip problem.
    """

    if not packages:
        return InstallOutcome(ok=True, exit_code=0, output=b"", installer="none")

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    chunks: list[bytes] = []
    attempts: list[str] = []
    code, output, spawn_error = _run(
        _pip_command(python, packages), cwd, env, _remaining(deadline)
    )
    attempts.append("pip")
    chunks.append(f"[pip] exit={code if code is not None else spawn_error}".encode())
    chunks.append(output)
    if code == 0:
        return InstallOutcome(True, 0, b"\n".join(chunks), "pip", attempts)
    if not _pip_missing(output):
        return InstallOutcome(
            False,
            code,
            b"\n".join(chunks),
            "pip",
            attempts,
            "pip install failed",
        )
    if _remaining(deadline) <= 0:
        return InstallOutcome(
            False,
            code,
            b"\n".join(chunks),
            "pip",
            attempts,
            "install timed out",
        )

    # uv-managed venvs normally have uv available; this keeps the install in the
    # same interpreter without mutating the project's environment layout.
    uv = which("uv")
    if uv:
        chunks.append(b"[bootstrap] pip missing; trying uv")
        uv_code, uv_output, uv_spawn = _run(
            _uv_command(str(uv), python, packages), cwd, env, _remaining(deadline)
        )
        attempts.append("uv")
        chunks.append(
            f"[uv] exit={uv_code if uv_code is not None else uv_spawn}".encode()
        )
        chunks.append(uv_output)
        if uv_code == 0:
            return InstallOutcome(True, 0, b"\n".join(chunks), "uv", attempts)
        if uv_spawn != "spawn":
            return InstallOutcome(
                False,
                uv_code,
                b"\n".join(chunks),
                "uv",
                attempts,
                "uv install failed",
            )

    if _remaining(deadline) <= 0:
        return InstallOutcome(
            False,
            None,
            b"\n".join(chunks),
            "none",
            attempts,
            "install timed out",
        )

    # Last resort for interpreters that ship ensurepip but not pip.
    chunks.append(b"[bootstrap] pip missing and uv unavailable; running ensurepip")
    boot_code, boot_output, boot_spawn = _run(
        [str(python), "-m", "ensurepip", "--upgrade"],
        cwd,
        env,
        _remaining(deadline),
    )
    attempts.append("ensurepip")
    chunks.append(
        f"[ensurepip] exit={boot_code if boot_code is not None else boot_spawn}".encode()
    )
    chunks.append(boot_output)
    if boot_code != 0:
        return InstallOutcome(
            False,
            boot_code,
            b"\n".join(chunks),
            "none",
            attempts,
            "ensurepip failed",
        )
    if _remaining(deadline) <= 0:
        return InstallOutcome(
            False,
            None,
            b"\n".join(chunks),
            "ensurepip+pip",
            attempts,
            "install timed out",
        )

    retry_code, retry_output, retry_spawn = _run(
        _pip_command(python, packages), cwd, env, _remaining(deadline)
    )
    attempts.append("pip-retry")
    chunks.append(
        f"[pip-retry] exit={retry_code if retry_code is not None else retry_spawn}".encode()
    )
    chunks.append(retry_output)
    ok = retry_code == 0
    return InstallOutcome(
        ok,
        retry_code,
        b"\n".join(chunks),
        "ensurepip+pip" if ok else "none",
        attempts,
        "" if ok else "pip install failed after ensurepip",
    )
