"""Windows 当前用户及会话命名管道、对端及实例校验；不监听 TCP。

阻塞式字节管道 + 4 字节大端长度前缀的 JSON 消息帧。管道名编码工作空间
与会话；接受连接后用 ``GetNamedPipeClientProcessId/SessionId`` 及进程
令牌 SID 校验对端必须是同会话同用户。不使用 TCP、不引入第三方依赖。

非 Windows 平台导入可用，实例化时报告不可用。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

_PIPE_PREFIX = r"\\.\pipe\aitest"
_INVALID_HANDLE = wintypes.HANDLE(-1).value
_PIPE_ACCESS_DUPLEX = 0x3
_PIPE_TYPE_BYTE = 0x0
_PIPE_WAIT = 0x0
_PIPE_MAX_ONE_INSTANCE = 1
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_ERROR_PIPE_BUSY = 231
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x8
_TOKEN_USER = 1
MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class PipeUnavailable(RuntimeError):
    """管道不存在、忙或平台不支持。"""


class PeerRejected(RuntimeError):
    """对端会话或用户不匹配。"""


def check_peer_identity(
    *,
    client_session_id: int,
    expected_session_id: int,
    client_user_sid: str,
    expected_user_sid: str,
) -> None:
    """纯函数校验对端会话与用户身份是否同源。

    抽出为独立函数便于单元测试；不依赖 Windows kernel32 调用结果。
    """
    if client_session_id != expected_session_id:
        raise PeerRejected(
            f"跨会话连接被拒绝: client={client_session_id} "
            f"expected={expected_session_id}"
        )
    if client_user_sid != expected_user_sid:
        raise PeerRejected(
            f"跨用户连接被拒绝: client={client_user_sid!r} "
            f"expected={expected_user_sid!r}"
        )


def validate_workspace_id(workspace_id: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if not workspace_id or any(char not in allowed for char in workspace_id):
        raise PipeUnavailable(f"非法工作空间标识: {workspace_id!r}")
    return workspace_id


class _Kernel:
    """kernel32/advapi32 函数签名的懒加载容器。"""

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel32 = kernel32
        self.advapi32 = advapi32

        kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
        kernel32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        ]
        kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.GetNamedPipeClientProcessId.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)
        ]
        kernel32.GetNamedPipeClientSessionId.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)
        ]
        kernel32.ProcessIdToSessionId.argtypes = [
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
        ]
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
        ]
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)
        ]


class _TokenUser(ctypes.Structure):
    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    _fields_ = [
        ("user", _SidAndAttributes),
    ]


class NamedPipeServer:
    """唯一核心的服务端管道；同名管道已被占用即失败。"""

    def __init__(self, workspace_id: str, *, instance_id: str) -> None:
        if not sys.platform.startswith("win"):
            raise PipeUnavailable("命名管道仅支持 Windows")
        validate_workspace_id(workspace_id)
        self._kernel = _Kernel()
        self._session_id = self._current_session()
        self._pipe_name = (
            f"{_PIPE_PREFIX}/{workspace_id}/session-{self._session_id}/{instance_id}"
        )
        self._handle: int | None = None

    @property
    def name(self) -> str:
        return self._pipe_name

    def start(self) -> None:
        handle = self._kernel.kernel32.CreateNamedPipeW(
            self._pipe_name,
            _PIPE_ACCESS_DUPLEX,
            _PIPE_TYPE_BYTE | _PIPE_WAIT,
            _PIPE_MAX_ONE_INSTANCE,
            MAX_MESSAGE_BYTES,
            MAX_MESSAGE_BYTES,
            0,
            None,
        )
        if handle == _INVALID_HANDLE:
            # 同名管道被他人持有/拒绝访问：唯一核心语义
            raise PipeUnavailable("管道已被占用，工作空间存在活动核心")
        self._handle = handle

    def wait_for_client(self) -> None:
        assert self._handle is not None
        connected = self._kernel.kernel32.ConnectNamedPipe(self._handle, None)
        if not connected and ctypes.get_last_error() != 535:  # ERROR_PIPE_CONNECTED
            raise PipeUnavailable("连接客户端失败")

    def validate_peer(self) -> None:
        assert self._handle is not None
        client_pid = wintypes.ULONG(0)
        client_session = wintypes.ULONG(0)
        self._kernel.kernel32.GetNamedPipeClientProcessId(
            self._handle, ctypes.byref(client_pid)
        )
        self._kernel.kernel32.GetNamedPipeClientSessionId(
            self._handle, ctypes.byref(client_session)
        )
        client_sid = self._process_user_sid(client_pid.value)
        local_sid = self._process_user_sid(
            self._kernel.kernel32.GetCurrentProcessId()
        )
        check_peer_identity(
            client_session_id=int(client_session.value),
            expected_session_id=self._session_id,
            client_user_sid=client_sid,
            expected_user_sid=local_sid,
        )

    def read_message(self) -> bytes:
        length = int.from_bytes(self._read_exact(4), "big")
        if length > MAX_MESSAGE_BYTES:
            raise PipeUnavailable("消息超过上限")
        return self._read_exact(length)

    def write_message(self, payload: bytes) -> None:
        if len(payload) > MAX_MESSAGE_BYTES:
            raise PipeUnavailable("消息超过上限")
        frame = len(payload).to_bytes(4, "big") + payload
        self._write_all(frame)

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.kernel32.CloseHandle(self._handle)
            self._handle = None

    # ----- 内部 -------------------------------------------------------

    def _current_session(self) -> int:
        pid = self._kernel.kernel32.GetCurrentProcessId()
        session = wintypes.DWORD(0)
        self._kernel.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session))
        return session.value

    def _process_user_sid(self, process_id: int) -> str:
        kernel32 = self._kernel.kernel32
        advapi32 = self._kernel.advapi32
        process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not process:
            raise PeerRejected(f"无法打开客户端进程: {process_id}")
        token = wintypes.HANDLE()
        try:
            advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token))
            buffer = (_TokenUser * 1)()
            returned = wintypes.DWORD(0)
            advapi32.GetTokenInformation(
                token, _TOKEN_USER, buffer, ctypes.sizeof(buffer), ctypes.byref(returned)
            )
            sid_string = wintypes.LPWSTR()
            advapi32.ConvertSidToStringSidW(
                buffer[0].user.Sid, ctypes.byref(sid_string)
            )
            return sid_string.value or ""
        finally:
            if token:
                kernel32.CloseHandle(token)
            kernel32.CloseHandle(process)

    def _read_exact(self, size: int) -> bytes:
        assert self._handle is not None
        chunks = bytearray()
        while len(chunks) < size:
            buffer = (wintypes.BYTE * (size - len(chunks)))()
            read = wintypes.DWORD(0)
            ok = self._kernel.kernel32.ReadFile(
                self._handle, buffer, len(buffer), ctypes.byref(read), None
            )
            if not ok or read.value == 0:
                raise PipeUnavailable("管道读取失败或对端关闭")
            chunks.extend(bytes(buffer[: read.value]))
        return bytes(chunks)

    def _write_all(self, data: bytes) -> None:
        assert self._handle is not None
        offset = 0
        while offset < len(data):
            chunk = data[offset:]
            buffer = (wintypes.BYTE * len(chunk)).from_buffer_copy(chunk)
            written = wintypes.DWORD(0)
            ok = self._kernel.kernel32.WriteFile(
                self._handle,
                buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            )
            if not ok or written.value == 0:
                raise PipeUnavailable("管道写入失败")
            offset += written.value


class NamedPipeClient:
    """连接唯一核心的客户端。"""

    def __init__(self, workspace_id: str, *, instance_id: str) -> None:
        if not sys.platform.startswith("win"):
            raise PipeUnavailable("命名管道仅支持 Windows")
        validate_workspace_id(workspace_id)
        self._kernel = _Kernel()
        session = self._current_session()
        self._pipe_name = (
            f"{_PIPE_PREFIX}/{workspace_id}/session-{session}/{instance_id}"
        )
        self._handle: int | None = None

    def connect(self, *, timeout_ms: int = 2000) -> None:
        """连接服务端管道，超时内重试 ``ERROR_PIPE_BUSY``。

        原实现遇 ``ERROR_PIPE_BUSY`` 后递归调用自身，并发场景会
        ``RecursionError``。改为基于 ``time.monotonic`` 的有限循环。
        """
        kernel32 = self._kernel.kernel32
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        while True:
            handle = kernel32.CreateFileW(
                self._pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                0,
                None,
            )
            if handle != _INVALID_HANDLE:
                self._handle = handle
                return
            if ctypes.get_last_error() != _ERROR_PIPE_BUSY:
                raise PipeUnavailable("核心管道不存在或不可连接")
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise PipeUnavailable("核心管道不可连接：超时")
            kernel32.WaitNamedPipeW(self._pipe_name, remaining_ms)

    def write_message(self, payload: bytes) -> None:
        if self._handle is None:
            raise PipeUnavailable("未连接")
        if len(payload) > MAX_MESSAGE_BYTES:
            raise PipeUnavailable("消息超过上限")
        frame = len(payload).to_bytes(4, "big") + payload
        written = wintypes.DWORD(0)
        self._kernel.kernel32.WriteFile(
            self._handle, frame, len(frame), ctypes.byref(written), None
        )

    def read_message(self) -> bytes:
        if self._handle is None:
            raise PipeUnavailable("未连接")
        length = int.from_bytes(self._read_exact(4), "big")
        return self._read_exact(length)

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.kernel32.CloseHandle(self._handle)
            self._handle = None

    def _current_session(self) -> int:
        pid = self._kernel.kernel32.GetCurrentProcessId()
        session = wintypes.DWORD(0)
        self._kernel.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session))
        return session.value

    def _read_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            buffer = (wintypes.BYTE * (size - len(chunks)))()
            read = wintypes.DWORD(0)
            self._kernel.kernel32.ReadFile(
                self._handle, buffer, len(buffer), ctypes.byref(read), None
            )
            if read.value == 0:
                raise PipeUnavailable("对端关闭")
            chunks.extend(bytes(buffer[: read.value]))
        return bytes(chunks)


__all__ = [
    "MAX_MESSAGE_BYTES",
    "NamedPipeClient",
    "NamedPipeServer",
    "PeerRejected",
    "PipeUnavailable",
    "check_peer_identity",
    "validate_workspace_id",
]
