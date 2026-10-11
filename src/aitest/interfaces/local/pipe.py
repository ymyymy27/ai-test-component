"""Windows 当前用户及会话命名管道、对端及实例校验；不监听 TCP。

字节管道 + 4 字节大端长度前缀的 JSON 消息帧；服务端使用可取消重叠IO，
两端读/写各共享整帧截止，API保留同步入口。管道名编码工作空间
与会话；接受连接后用 ``GetNamedPipeClientProcessId/SessionId`` 及进程
令牌 SID 校验对端必须是同会话同用户。不使用 TCP、不引入第三方依赖。

非 Windows 平台导入可用，实例化时报告不可用。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from collections.abc import Callable
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
_SERVER_FRAME_TIMEOUT_MS = 10000
_FILE_FLAG_OVERLAPPED = 0x40000000
_ERROR_IO_PENDING = 997
_ERROR_PIPE_CONNECTED = 535
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


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
    任一 SID 为空都拒绝：空 SID 相等不能证明同源（A-18）。
    """
    if not client_user_sid or not expected_user_sid:
        raise PeerRejected("空用户 SID 无法证明同源，拒绝连接")
    if client_session_id != expected_session_id:
        raise PeerRejected(
            f"跨会话连接被拒绝: client={client_session_id} expected={expected_session_id}"
        )
    if client_user_sid != expected_user_sid:
        raise PeerRejected(
            f"跨用户连接被拒绝: client={client_user_sid!r} expected={expected_user_sid!r}"
        )


def validate_workspace_id(workspace_id: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if not workspace_id or any(char not in allowed for char in workspace_id):
        raise PipeUnavailable(f"非法工作空间标识: {workspace_id!r}")
    return workspace_id


def process_exists(process_id: int) -> bool:
    """进程是否仍存活；非 Windows 或无法取证时按存活处理（不误杀核心）。

    供长生命周期核心在**连接边界**探测父进程（启动器/宿主）是否已消亡：
    父进程不在后，核心在当前客户端断开后自行退出，避免孤儿核心长期占管。
    权限不足等取证失败按存活返回——宁可留待显式停机，也不猜测父进程死亡。
    """
    if process_id <= 0:
        return True
    if not sys.platform.startswith("win"):
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        # ERROR_INVALID_PARAMETER(87)：进程不存在；其他错误（权限等）按存活。
        return ctypes.get_last_error() != 87
    try:
        exit_code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        # STILL_ACTIVE = 259
        return int(exit_code.value) == 259
    finally:
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)


class _Kernel:
    """kernel32/advapi32 函数签名的懒加载容器。"""

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel32 = kernel32
        self.advapi32 = advapi32

        kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
        kernel32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        kernel32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.PeekNamedPipe.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        kernel32.GetNamedPipeClientProcessId.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.ULONG),
        ]
        kernel32.GetNamedPipeClientSessionId.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.ULONG),
        ]
        kernel32.GetNamedPipeServerProcessId.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.ULONG),
        ]
        kernel32.GetNamedPipeServerSessionId.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.ULONG),
        ]
        kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ]


class _TokenUser(ctypes.Structure):
    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    _fields_ = [
        ("user", _SidAndAttributes),
    ]


class _PipeIO:
    """Shared owned-handle IO; cancellation completion precedes storage release."""

    _kernel: _Kernel

    def _init_io(self) -> None:
        self._handle: int | None = None
        self._io_guard = threading.RLock()
        self._pending_io: dict[int, _Overlapped] = {}
        self._closing = False

    @staticmethod
    def _deadline(timeout_ms: int | None) -> float | None:
        if timeout_ms is not None and (type(timeout_ms) is not int or timeout_ms < 0):
            raise ValueError("IO timeout must be a nonnegative integer")
        return None if timeout_ms is None else time.monotonic() + timeout_ms / 1000


    def _operation(
        self,
        begin: Callable[[int, _Overlapped], int],
        *,
        deadline: float | None,
        on_wait: Callable[[], None] | None,
        connecting: bool = False,
    ) -> int:
        """Keep the request storage alive through cancellation completion."""
        kernel = self._kernel.kernel32
        with self._io_guard:
            handle = self._handle
            if handle is None or self._closing:
                raise PipeUnavailable("管道已关闭")
            if deadline is not None and time.monotonic() >= deadline:
                raise PipeUnavailable("管道帧IO超时，业务结果待核实")
            event = kernel.CreateEventW(None, True, False, None)
            if not event:
                raise PipeUnavailable("无法创建管道IO事件")
            overlap = _Overlapped(hEvent=event)
            self._pending_io[event] = overlap
            pending = False
            try:
                completed = begin(handle, overlap)
                error = ctypes.get_last_error() if not completed else 0
            except BaseException:
                del self._pending_io[event]
                kernel.CloseHandle(event)
                raise
        transferred = wintypes.DWORD(0)
        try:
            if not completed and connecting and error == _ERROR_PIPE_CONNECTED:
                return 0
            if not completed and error != _ERROR_IO_PENDING:
                raise PipeUnavailable("管道IO失败或对端关闭")
            pending = not completed
            while pending:
                if deadline is not None and time.monotonic() >= deadline:
                    raise PipeUnavailable("管道帧IO超时，业务结果待核实")
                wait_ms = (
                    25
                    if deadline is None
                    else max(1, min(25, int((deadline - time.monotonic()) * 1000)))
                )
                state = kernel.WaitForSingleObject(event, wait_ms)
                if state == _WAIT_OBJECT_0:
                    break
                if state != _WAIT_TIMEOUT:
                    raise PipeUnavailable("管道IO等待失败")
                if on_wait is not None:
                    on_wait()
            if deadline is not None and time.monotonic() >= deadline:
                raise PipeUnavailable("管道帧IO超时，业务结果待核实")
            if not kernel.GetOverlappedResult(
                handle, ctypes.byref(overlap), ctypes.byref(transferred), False
            ):
                raise PipeUnavailable("管道IO失败或对端关闭")
            pending = False
            return int(transferred.value)
        finally:
            if pending:
                kernel.CancelIoEx(handle, ctypes.byref(overlap))
                # CancelIoEx only requests cancellation; the local pipe operation
                # must finish before its event, OVERLAPPED or buffer is released.
                kernel.GetOverlappedResult(
                    handle, ctypes.byref(overlap), ctypes.byref(transferred), True
                )
            with self._io_guard:
                del self._pending_io[event]
                kernel.CloseHandle(event)
                if self._closing and not self._pending_io and self._handle is not None:
                    kernel.CloseHandle(self._handle)
                    self._handle = None


    def close(self) -> None:
        with self._io_guard:
            self._closing = True
            if self._handle is not None:
                self._kernel.kernel32.CancelIoEx(self._handle, None)
                if not self._pending_io:
                    self._kernel.kernel32.CloseHandle(self._handle)
                    self._handle = None


    def _read_exact(
        self, size: int, *, deadline: float | None = None, on_wait: Callable[[], None] | None = None
    ) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            buffer = (wintypes.BYTE * (size - len(chunks)))()

            def begin_read(
                handle: int, overlap: _Overlapped, buffer: ctypes.Array[wintypes.BYTE] = buffer
            ) -> int:
                return int(
                    self._kernel.kernel32.ReadFile(
                        handle, buffer, len(buffer), None, ctypes.byref(overlap)
                    )
                )

            read = self._operation(
                begin_read,
                deadline=deadline,
                on_wait=on_wait,
            )
            if not 0 < read <= len(buffer):
                raise PipeUnavailable("管道读取失败或对端关闭")
            # ctypes 数组切片在 3.11/3.12 返回**有符号**整数（>=0x80 会变负），3.13 起返回无符号；
            # 统一经 memoryview 取原始字节，避免 `bytes(...)` 因负值报 "must be in range(0, 256)"。
            chunks.extend(memoryview(buffer)[:read])
        return bytes(chunks)


    def _write_all(
        self,
        data: bytes,
        *,
        deadline: float | None = None,
        on_wait: Callable[[], None] | None = None,
    ) -> None:
        offset = 0
        while offset < len(data):
            chunk = data[offset:]
            buffer = (wintypes.BYTE * len(chunk)).from_buffer_copy(chunk)

            def begin_write(
                handle: int, overlap: _Overlapped, buffer: ctypes.Array[wintypes.BYTE] = buffer
            ) -> int:
                return int(
                    self._kernel.kernel32.WriteFile(
                        handle, buffer, len(buffer), None, ctypes.byref(overlap)
                    )
                )

            written = self._operation(
                begin_write,
                deadline=deadline,
                on_wait=on_wait,
            )
            if not 0 < written <= len(chunk):
                raise PipeUnavailable("管道写入失败")
            offset += written


class NamedPipeServer(_PipeIO):
    """唯一核心的服务端管道；同名管道已被占用即失败。"""

    def __init__(self, workspace_id: str, *, instance_id: str) -> None:
        if not sys.platform.startswith("win"):
            raise PipeUnavailable("命名管道仅支持 Windows")
        validate_workspace_id(workspace_id)
        self._kernel = _Kernel()
        self._session_id = self._current_session()
        self._pipe_name = f"{_PIPE_PREFIX}/{workspace_id}/session-{self._session_id}/{instance_id}"
        self._init_io()

    @property
    def name(self) -> str:
        return self._pipe_name

    def start(self) -> None:
        if self._handle is not None:
            raise PipeUnavailable("管道已启动")
        self._closing = False
        handle = self._kernel.kernel32.CreateNamedPipeW(
            self._pipe_name,
            _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED,
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


    def wait_for_client(
        self, *, timeout_ms: int | None = None, on_wait: Callable[[], None] | None = None
    ) -> None:
        self._operation(
            lambda handle, overlap: self._kernel.kernel32.ConnectNamedPipe(
                handle, ctypes.byref(overlap)
            ),
            deadline=self._deadline(timeout_ms),
            on_wait=on_wait,
            connecting=True,
        )

    def cancel_wait(self) -> None:
        with self._io_guard:
            if self._handle is not None:
                self._kernel.kernel32.CancelIoEx(self._handle, None)

    def validate_peer(self) -> None:
        assert self._handle is not None
        client_pid = wintypes.ULONG(0)
        client_session = wintypes.ULONG(0)
        if not self._kernel.kernel32.GetNamedPipeClientProcessId(
            self._handle, ctypes.byref(client_pid)
        ):
            raise PeerRejected("无法取得客户端进程标识")
        if not self._kernel.kernel32.GetNamedPipeClientSessionId(
            self._handle, ctypes.byref(client_session)
        ):
            raise PeerRejected("无法取得客户端会话标识")
        client_sid = self._process_user_sid(int(client_pid.value))
        local_sid = self._process_user_sid(self._kernel.kernel32.GetCurrentProcessId())
        check_peer_identity(
            client_session_id=int(client_session.value),
            expected_session_id=self._session_id,
            client_user_sid=client_sid,
            expected_user_sid=local_sid,
        )

    def peer_process_basename(self) -> str | None:
        """对端进程映像 basename（小写）；取证失败返回 None，调用方按
        最小权限归类入口（A-02）。"""
        assert self._handle is not None
        client_pid = wintypes.ULONG(0)
        if not self._kernel.kernel32.GetNamedPipeClientProcessId(
            self._handle, ctypes.byref(client_pid)
        ):
            return None
        return query_process_image_basename(self._kernel, int(client_pid.value))

    def read_message(
        self,
        *,
        timeout_ms: int | None = _SERVER_FRAME_TIMEOUT_MS,
        on_wait: Callable[[], None] | None = None,
    ) -> bytes:
        deadline = self._deadline(timeout_ms)
        length = int.from_bytes(self._read_exact(4, deadline=deadline, on_wait=on_wait), "big")
        if length > MAX_MESSAGE_BYTES:
            raise PipeUnavailable("消息超过上限")
        return self._read_exact(length, deadline=deadline, on_wait=on_wait)

    def write_message(
        self,
        payload: bytes,
        *,
        timeout_ms: int | None = _SERVER_FRAME_TIMEOUT_MS,
        on_wait: Callable[[], None] | None = None,
    ) -> None:
        deadline = self._deadline(timeout_ms)
        if len(payload) > MAX_MESSAGE_BYTES:
            raise PipeUnavailable("消息超过上限")
        frame = len(payload).to_bytes(4, "big") + payload
        self._write_all(frame, deadline=deadline, on_wait=on_wait)


    # ----- 内部 -------------------------------------------------------

    def _current_session(self) -> int:
        pid = self._kernel.kernel32.GetCurrentProcessId()
        session = wintypes.DWORD(0)
        if not self._kernel.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
            raise PipeUnavailable("无法核实当前登录会话")
        return session.value

    def _process_user_sid(self, process_id: int) -> str:
        """服务端对端 SID 取证：任何失败/空 SID 都拒绝连接（A-18）。

        复用与 :func:`current_user_sid` 同一份“长度探询 + 全结果核对
        + LocalFree”实现，不再使用旧的固定缓冲写法。
        """
        sid = _query_process_user_sid(self._kernel, process_id)
        if not sid:
            raise PeerRejected(f"无法核实客户端进程用户身份: {process_id}")
        return sid


class NamedPipeClient(_PipeIO):
    """连接唯一核心的客户端。"""

    def __init__(self, workspace_id: str, *, instance_id: str) -> None:
        if not sys.platform.startswith("win"):
            raise PipeUnavailable("命名管道仅支持 Windows")
        validate_workspace_id(workspace_id)
        self._kernel = _Kernel()
        session = self._current_session()
        self._session_id = session
        self._pipe_name = f"{_PIPE_PREFIX}/{workspace_id}/session-{session}/{instance_id}"
        self._init_io()
        self._peer_pid: int | None = None

    def connect(self, *, timeout_ms: int = 2000) -> None:
        """连接服务端管道，超时内重试 ``ERROR_PIPE_BUSY``。

        原实现遇 ``ERROR_PIPE_BUSY`` 后递归调用自身，并发场景会
        ``RecursionError``。改为基于 ``time.monotonic`` 的有限循环。
        """
        if self._handle is not None or self._pending_io:
            raise PipeUnavailable("客户端已有活动连接")
        self._closing = False
        kernel32 = self._kernel.kernel32
        deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
        while True:
            handle = kernel32.CreateFileW(
                self._pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                _FILE_FLAG_OVERLAPPED,
                None,
            )
            if handle != _INVALID_HANDLE:
                self._handle = handle
                try:
                    self._validate_server()
                except PeerRejected as error:
                    self.close()
                    raise PipeUnavailable("核心管道服务端身份无法核实") from error
                return
            if ctypes.get_last_error() != _ERROR_PIPE_BUSY:
                raise PipeUnavailable("核心管道不存在或不可连接")
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise PipeUnavailable("核心管道不可连接：超时")
            kernel32.WaitNamedPipeW(self._pipe_name, remaining_ms)

    def _validate_server(self) -> None:
        assert self._handle is not None
        kernel32 = self._kernel.kernel32
        pid, session = wintypes.ULONG(), wintypes.ULONG()
        if not kernel32.GetNamedPipeServerProcessId(self._handle, ctypes.byref(pid)):
            raise PeerRejected("无法取得核心进程身份")
        if not kernel32.GetNamedPipeServerSessionId(self._handle, ctypes.byref(session)):
            raise PeerRejected("无法取得核心会话身份")
        check_peer_identity(
            client_session_id=int(session.value),
            expected_session_id=self._session_id,
            client_user_sid=_query_process_user_sid(self._kernel, int(pid.value)) or "",
            expected_user_sid=(
                _query_process_user_sid(self._kernel, kernel32.GetCurrentProcessId()) or ""
            ),
        )
        self._peer_pid = int(pid.value)

    @property
    def peer_process_id(self) -> int | None:
        """Actual connected server PID, available only after same-user/session verification."""
        return self._peer_pid

    def write_message(
        self, payload: bytes, *, timeout_ms: int | None = _SERVER_FRAME_TIMEOUT_MS,
    ) -> None:
        deadline = self._deadline(timeout_ms)
        if len(payload) > MAX_MESSAGE_BYTES:
            raise PipeUnavailable("消息超过上限")
        self._write_all(len(payload).to_bytes(4, "big") + payload,
                        deadline=deadline, on_wait=None)

    def read_message(self, *, timeout_ms: int | None = _SERVER_FRAME_TIMEOUT_MS) -> bytes:
        deadline = self._deadline(timeout_ms)
        length = int.from_bytes(self._read_exact(4, deadline=deadline), "big")
        if length > MAX_MESSAGE_BYTES:
            raise PipeUnavailable("核心响应消息超过上限")
        return self._read_exact(length, deadline=deadline)

    def close(self) -> None:
        super().close()
        self._peer_pid = None

    def _current_session(self) -> int:
        pid = self._kernel.kernel32.GetCurrentProcessId()
        session = wintypes.DWORD(0)
        if not self._kernel.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
            raise PipeUnavailable("无法核实当前登录会话")
        return session.value


#: 进程内共享的 kernel32/advapi32 签名容器，避免重复加载与声明。
_SHARED_KERNEL: _Kernel | None = None


def _shared_kernel() -> _Kernel:
    global _SHARED_KERNEL
    if _SHARED_KERNEL is None:
        _SHARED_KERNEL = _Kernel()
    return _SHARED_KERNEL


def current_session_id() -> int | None:
    """当前进程所在的 Windows 会话 ID；非 Windows 返回 None。"""
    if not sys.platform.startswith("win"):
        return None
    kernel = _shared_kernel()
    pid = kernel.kernel32.GetCurrentProcessId()
    session = wintypes.DWORD(0)
    kernel.kernel32.ProcessIdToSessionId(pid, ctypes.byref(session))
    return session.value


def _query_process_user_sid(kernel: _Kernel, process_id: int) -> str | None:
    """查询指定进程令牌用户 SID；任何一步取证失败都返回 None（A-18）。

    TOKEN_USER 内嵌变长 SID：先以 NULL/0 探询所需缓冲区长度，再按精确
    长度二次分配；所有 Win32 返回值逐一核对；ConvertSidToStringSidW
    成功后必须 LocalFree。
    """
    kernel32 = kernel.kernel32
    advapi32 = kernel.advapi32
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process:
        return None
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
            return None
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        raw = (ctypes.c_byte * needed.value)()
        if not advapi32.GetTokenInformation(
            token,
            _TOKEN_USER,
            raw,
            needed.value,
            ctypes.byref(needed),
        ):
            return None
        token_user = _TokenUser.from_buffer(raw)
        sid_string = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(token_user.user.Sid, ctypes.byref(sid_string)):
            return None
        value = sid_string.value or None
        if sid_string:
            kernel32.LocalFree(sid_string)
        return value
    finally:
        if token:
            kernel32.CloseHandle(token)
        kernel32.CloseHandle(process)


def query_process_image_basename(kernel: _Kernel, process_id: int) -> str | None:
    """查询客户端进程可执行映像文件名（小写 basename）；失败返回 None。

    入口类型只能由核心依据对端**进程事实**判定，不能接受连接帧自报
    （A-02）。取证失败时调用方按最小权限（AGENT_RELAY）归类。
    """
    if process_id <= 0:
        return None
    kernel32 = kernel.kernel32
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return None
        full_path = buffer.value
        if not full_path:
            return None
        # rsplit 兼容路径中可能出现的正反斜杠；basename 比较只用于
        # 入口归类，不参与路径拼接。
        return full_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(process)


def current_user_sid() -> str | None:
    """当前进程令牌用户 SID 字符串；非 Windows 或取证失败返回 None。

    供连接台账记录“来源会话/来源用户”事实：连接核对通过后，台账里
    必须留下与管道对端核对所用的同一份身份，跨重启可查，不允许只
    在实例内存里保存。
    """
    if not sys.platform.startswith("win"):
        return None
    kernel = _shared_kernel()
    current_pid = kernel.kernel32.GetCurrentProcessId()
    return _query_process_user_sid(kernel, current_pid)


__all__ = [
    "MAX_MESSAGE_BYTES",
    "NamedPipeClient",
    "NamedPipeServer",
    "PeerRejected",
    "PipeUnavailable",
    "check_peer_identity",
    "current_session_id",
    "current_user_sid",
    "process_exists",
    "query_process_image_basename",
    "validate_workspace_id",
]
