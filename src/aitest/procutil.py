"""Small process helpers shared by the CLI and the one-shot worker."""

from __future__ import annotations

import os


def _windows_created_at(pid: int) -> float | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return (ticks - 116444736000000000) / 10_000_000
    finally:
        kernel32.CloseHandle(handle)


def _posix_created_at(pid: int) -> float | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            stat = handle.read()
        with open("/proc/stat", encoding="utf-8") as handle:
            boot = next(
                int(line.split()[1]) for line in handle if line.startswith("btime ")
            )
    except (OSError, StopIteration, ValueError):
        return None
    fields = stat.rsplit(")", 1)[1].split()
    try:
        start_ticks = int(fields[19])
    except (IndexError, ValueError):
        return None
    ticks_per_second = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    return boot + start_ticks / ticks_per_second


def process_created_at(pid: int | None) -> float | None:
    """Return the OS creation time (epoch seconds), or None when unknown."""
    if not pid:
        return None
    return _windows_created_at(pid) if os.name == "nt" else _posix_created_at(pid)


def pid_alive(pid: int | None, created_at: float | None = None) -> bool:
    """Check pid liveness, rejecting a recycled pid when creation time differs."""
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
    if created_at:
        actual = process_created_at(pid)
        if actual is not None and abs(actual - created_at) > 2:
            return False
    return True
