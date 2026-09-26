"""Controlled local command adapter with in-memory redaction before capture."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO
from uuid import uuid4

from aitest.domain.execution.runs import (
    AdapterKind,
    CaptureCompleteness,
    CapturedOutputBlock,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExecutionRequest,
    ExitFact,
    OutputCursor,
    OutputStreamName,
    ProcessTerminationReason,
    StopRequestResult,
)

from .redaction import StreamingRedactor

SecretResolver = Callable[[str], Mapping[str, str]]

_DEFAULT_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
)


@dataclass(frozen=True, slots=True)
class CommandRegistration:
    entry_id: str
    executable: str
    cwd: Path
    env_allowlist: tuple[str, ...] = _DEFAULT_ENV_ALLOWLIST
    max_arguments: int = 128

    def __post_init__(self) -> None:
        if not self.entry_id.strip():
            raise ValueError("entry_id must not be empty")
        if not self.executable.strip():
            raise ValueError("executable must not be empty")
        if self.max_arguments < 0:
            raise ValueError("max_arguments must be non-negative")


@dataclass(slots=True)
class _CommandRuntime:
    request: ExecutionRequest
    handle: ExecutionHandle
    process: subprocess.Popen[bytes]
    startup_token: str
    buffers: dict[OutputStreamName, bytearray] = field(default_factory=dict)
    read_errors: list[str] = field(default_factory=list)
    stop_requested: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    threads: list[threading.Thread] = field(default_factory=list)


class CommandAdapter:
    """Launch registered argv arrays without a shell and redact output in memory."""

    adapter_version = "command/1.0"

    def __init__(self, secret_resolver: SecretResolver | None = None) -> None:
        self._registrations: dict[str, CommandRegistration] = {}
        self._runtimes: dict[str, _CommandRuntime] = {}
        self._secret_resolver = secret_resolver or (lambda _scope: {})

    def register(self, registration: CommandRegistration) -> None:
        if registration.entry_id in self._registrations:
            raise ValueError(f"command entry already registered: {registration.entry_id}")
        executable = Path(registration.executable).expanduser().resolve()
        if not executable.is_file():
            raise ValueError(f"command executable does not exist: {executable}")
        cwd = registration.cwd.expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError(f"command cwd does not exist: {cwd}")
        normalized = CommandRegistration(
            entry_id=registration.entry_id,
            executable=str(executable),
            cwd=cwd,
            env_allowlist=registration.env_allowlist,
            max_arguments=registration.max_arguments,
        )
        self._registrations[registration.entry_id] = normalized

    def start(self, request: ExecutionRequest) -> ExecutionHandle:
        registration = self._registration_for(request)
        arguments = request.registered_entry.arguments
        if len(arguments) > registration.max_arguments:
            raise ValueError("registered command argument count exceeds limit")

        secrets = {
            str(value)
            for value in self._secret_resolver(
                request.authorization_ref.credential_scope_ref
            ).values()
            if value
        }
        if any(secret in argument for argument in arguments for secret in secrets):
            raise ValueError("resolved secret value must not appear in command arguments")

        environment = {
            key: os.environ[key] for key in registration.env_allowlist if key in os.environ
        }
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"

        command = [registration.executable, *arguments]
        if os.name == "nt":
            process = subprocess.Popen(
                command,
                cwd=registration.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(
                command,
                cwd=registration.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
        process_start_identity = _process_start_identity(process.pid)
        handle = ExecutionHandle(
            handle_id=f"command-handle:{request.attempt_id}",
            adapter_kind=AdapterKind.COMMAND,
            adapter_version=self.adapter_version,
            real_execution_id=str(process.pid),
            process_start_identity=process_start_identity,
            workdir_ref=str(registration.cwd),
        )
        runtime = _CommandRuntime(
            request=request,
            handle=handle,
            process=process,
            startup_token=str(uuid4()),
            buffers={
                OutputStreamName.STDOUT: bytearray(),
                OutputStreamName.STDERR: bytearray(),
            },
        )
        self._runtimes[handle.handle_id] = runtime
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("command process streams are unavailable")
        self._start_reader(runtime, OutputStreamName.STDOUT, process.stdout, secrets)
        self._start_reader(runtime, OutputStreamName.STDERR, process.stderr, secrets)
        return handle

    def inspect(self, handle: ExecutionHandle) -> ExecutionInspectionResult:
        runtime = self._runtime_for(handle)
        return_code = runtime.process.poll()
        if runtime.stop_requested:
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.STOPPED,
                process_reachable=return_code is None,
                identity_matches=True,
                stop_confirmed=return_code is not None,
            )
        if return_code is None:
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.RUNNING,
                process_reachable=True,
                identity_matches=True,
            )
        return ExecutionInspectionResult(
            handle_id=handle.handle_id,
            state=ExecutionInspectionState.EXITED,
            process_reachable=False,
            identity_matches=True,
        )

    def collect(
        self,
        handle: ExecutionHandle,
        cursor: OutputCursor | None = None,
    ) -> ExecutionCollectionResult:
        runtime = self._runtime_for(handle)
        return_code = runtime.process.poll()
        if return_code is None:
            return ExecutionCollectionResult(
                attempt_id=runtime.request.attempt_id,
                output_cursor_ref=cursor,
                capture_completeness=CaptureCompleteness.GAP,
                complete=False,
            )
        for thread in runtime.threads:
            thread.join(timeout=5)
        captured_blocks = self._captured_blocks(runtime, return_code)
        output_cursor = _latest_cursor(runtime)
        completeness = (
            CaptureCompleteness.GAP if runtime.read_errors else CaptureCompleteness.COMPLETE
        )
        return ExecutionCollectionResult(
            attempt_id=runtime.request.attempt_id,
            captured_blocks=captured_blocks,
            output_cursor_ref=output_cursor or cursor,
            exit_fact_ref=ExitFact(
                attempt_id=runtime.request.attempt_id,
                startup_token=runtime.startup_token,
                process_start_identity=handle.process_start_identity,
                real_exit_code=return_code,
                last_block_index_by_stream=tuple(
                    (stream, 0) for stream, data in runtime.buffers.items() if data
                ),
                saved_bytes_by_stream=tuple(
                    (stream, len(data)) for stream, data in runtime.buffers.items() if data
                ),
                capture_completeness=completeness,
                termination_reason=(
                    ProcessTerminationReason.CONFIRMED_STOP
                    if runtime.stop_requested
                    else ProcessTerminationReason.NATURAL_EXIT
                ),
            ),
            structured_result_ref=f"command-result:{runtime.request.attempt_id}:{return_code}",
            capture_completeness=completeness,
            complete=True,
        )

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult:
        runtime = self._runtime_for(handle)
        return_code = runtime.process.poll()
        if return_code is not None:
            return StopRequestResult(
                handle_id=handle.handle_id,
                stop_confirmed=False,
                observed_state=ExecutionInspectionState.EXITED,
                unknown_reason="process_already_exited",
            )
        runtime.stop_requested = True
        runtime.process.terminate()
        try:
            runtime.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            runtime.process.kill()
            runtime.process.wait(timeout=5)
        return StopRequestResult(
            handle_id=handle.handle_id,
            stop_confirmed=True,
            observed_state=ExecutionInspectionState.STOPPED,
        )

    def _registration_for(self, request: ExecutionRequest) -> CommandRegistration:
        entry_id = request.registered_entry.entry_id
        registration = self._registrations.get(entry_id)
        if registration is None:
            raise KeyError(f"command entry is not registered: {entry_id}")
        if request.registered_entry.entrypoint != registration.executable:
            raise ValueError("execution request entrypoint does not match registration")
        return registration

    def _start_reader(
        self,
        runtime: _CommandRuntime,
        stream_name: OutputStreamName,
        stream: IO[bytes],
        secrets: set[str],
    ) -> None:
        redactor = StreamingRedactor(secrets)
        target = runtime.buffers[stream_name]

        def consume() -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    filtered = redactor.feed(chunk)
                    if filtered:
                        with runtime.lock:
                            target.extend(filtered)
                tail = redactor.finish()
                if tail:
                    with runtime.lock:
                        target.extend(tail)
            except Exception as error:  # noqa: BLE001
                with runtime.lock:
                    runtime.read_errors.append(str(error))

        thread = threading.Thread(target=consume, name=f"command-{stream_name.value}", daemon=True)
        runtime.threads.append(thread)
        thread.start()

    @staticmethod
    def _captured_blocks(
        runtime: _CommandRuntime,
        return_code: int,
    ) -> tuple[CapturedOutputBlock, ...]:
        blocks: list[CapturedOutputBlock] = []
        for stream_name in (OutputStreamName.STDOUT, OutputStreamName.STDERR):
            content = bytes(runtime.buffers[stream_name])
            if not content:
                continue
            blocks.append(
                CapturedOutputBlock(
                    run_id=runtime.request.run_id,
                    step_id=runtime.request.step_id,
                    attempt_id=runtime.request.attempt_id,
                    stream_name=stream_name,
                    block_index=0,
                    offset=0,
                    content=content,
                    capture_source="command",
                )
            )
        return tuple(blocks)

    def _runtime_for(self, handle: ExecutionHandle) -> _CommandRuntime:
        runtime = self._runtimes.get(handle.handle_id)
        if runtime is None:
            raise KeyError(f"unknown command handle: {handle.handle_id}")
        return runtime


def _latest_cursor(runtime: _CommandRuntime) -> OutputCursor | None:
    for stream_name in (OutputStreamName.STDERR, OutputStreamName.STDOUT):
        content = bytes(runtime.buffers[stream_name])
        if content:
            return OutputCursor(
                attempt_id=runtime.request.attempt_id,
                stream_name=stream_name,
                offset=len(content),
                last_block_index=0,
                last_committed_digest=_sha256_digest(content),
                durable=False,
            )
    return None


def _sha256_digest(content: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(content).hexdigest()


def _process_start_identity(pid: int) -> str:
    if os.name == "nt":
        return _windows_process_start_identity(pid)
    if sys.platform.startswith("linux"):
        return _linux_process_start_identity(pid)
    return f"pid:{pid}:unverified"


def _windows_process_start_identity(pid: int) -> str:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return f"win32-pid:{pid}:unverified"
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return f"win32-pid:{pid}:unverified"
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return f"win32-pid:{pid}:created:{ticks}"
    finally:
        kernel32.CloseHandle(handle)


def _linux_process_start_identity(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.rsplit(")", 1)[1].split()
        start_ticks = fields[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return f"linux-pid:{pid}:unverified"
    return f"linux-boot:{boot_id}:pid:{pid}:start:{start_ticks}"


__all__ = ["CommandAdapter", "CommandRegistration", "SecretResolver"]
