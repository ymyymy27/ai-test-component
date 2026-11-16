"""Resolve a CLI's real OS host through the Windows venv redirector."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def cli_host_pid() -> int:
    parent = os.getppid()
    if sys.platform != "win32" or sys.prefix == sys.base_prefix:
        return parent
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, parent)
    if not handle:
        raise OSError("CLI host process cannot be verified")
    try:
        image = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
            raise OSError("CLI host image cannot be verified")
        if Path(image.value).resolve() != Path(sys.executable).resolve():
            return parent

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260),
            ]

        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32FirstW.restype = wintypes.BOOL
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32NextW.restype = wintypes.BOOL
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise OSError("CLI redirector ancestry cannot be verified")
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            valid = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while valid:
                if entry.pid == parent and entry.parent > 0:
                    return int(entry.parent)
                valid = kernel.Process32NextW(snapshot, ctypes.byref(entry))
            raise OSError("CLI redirector parent is unavailable")
        finally:
            kernel.CloseHandle(snapshot)
    finally:
        kernel.CloseHandle(handle)
