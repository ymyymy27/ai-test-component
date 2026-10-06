"""Controlled local command adapter with redaction and streaming spool output."""

from __future__ import annotations

import ctypes
import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from io import BufferedReader
from pathlib import Path
from typing import IO, Any, Protocol
from uuid import uuid4

from aitest.application.ports import SpoolStore, SpoolStreamWriter
from aitest.domain.evidence.evidence import RedactionSummary
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
    OutputBlockRef,
    OutputCursor,
    OutputStreamName,
    ProcessTerminationReason,
    StopRequestResult,
)
from aitest.infrastructure.file_store.execution_handles import PersistedExecutionHandle

from .redaction import StreamingRedactor

SecretResolver = Callable[[str], Mapping[str, str]]


class CommandHandleStore(Protocol):
    def save(self, record: PersistedExecutionHandle) -> Path: ...

    def load(self, handle_id: str) -> PersistedExecutionHandle: ...


_WINDOWS_JOB_PROCESS_ACCESS = 0x0001 | 0x0100 | 0x0200 | 0x1000

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
    registration: CommandRegistration
    handle: ExecutionHandle
    process: subprocess.Popen[bytes]
    startup_token: str
    writers: dict[OutputStreamName, SpoolStreamWriter] = field(default_factory=dict)
    redaction_summary_ids: dict[OutputStreamName, str] = field(default_factory=dict)
    output_blocks: list[OutputBlockRef] = field(default_factory=list)
    output_cursors: dict[OutputStreamName, OutputCursor] = field(default_factory=dict)
    captured_buffers: dict[OutputStreamName, bytearray] = field(default_factory=dict)
    read_errors: list[str] = field(default_factory=list)
    stop_requested: bool = False
    timed_out: bool = False
    timeout_timer: threading.Timer | None = None
    job_handle: int | None = None
    group_stopped: bool = False
    collection: ExecutionCollectionResult | None = None
    collection_lock: threading.Lock = field(default_factory=threading.Lock)
    lock: threading.Lock = field(default_factory=threading.Lock)
    threads: list[threading.Thread] = field(default_factory=list)


class _JobBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class CommandAdapter:
    """Launch registered argv arrays without a shell and redact before spooling."""

    adapter_version = "command/1.0"

    def __init__(
        self,
        secret_resolver: SecretResolver | None = None,
        *,
        spool_store: SpoolStore | None = None,
        handle_store: CommandHandleStore | None = None,
        stream_block_size: int = 64 * 1024,
        graceful_stop_timeout_seconds: float = 3.0,
        force_kill_timeout_seconds: float = 3.0,
    ) -> None:
        if stream_block_size < 1:
            raise ValueError("stream_block_size must be positive")
        if graceful_stop_timeout_seconds < 0 or force_kill_timeout_seconds < 0:
            raise ValueError("stop timeouts must be non-negative")
        self._registrations: dict[str, CommandRegistration] = {}
        self._runtimes: dict[str, _CommandRuntime] = {}
        self._secret_resolver = secret_resolver or (lambda _scope: {})
        self._spool_store = spool_store
        self._handle_store = handle_store
        self._stream_block_size = stream_block_size
        self._graceful_stop_timeout_seconds = graceful_stop_timeout_seconds
        self._force_kill_timeout_seconds = force_kill_timeout_seconds

    def register(self, registration: CommandRegistration) -> None:
        if registration.entry_id in self._registrations:
            raise ValueError(f"command entry already registered: {registration.entry_id}")
        executable = Path(registration.executable).expanduser().resolve()
        if not executable.is_file():
            raise ValueError(f"command executable does not exist: {executable}")
        cwd = registration.cwd.expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError(f"command cwd does not exist: {cwd}")
        self._registrations[registration.entry_id] = CommandRegistration(
            entry_id=registration.entry_id,
            executable=str(executable),
            cwd=cwd,
            env_allowlist=registration.env_allowlist,
            max_arguments=registration.max_arguments,
        )

    def start(self, request: ExecutionRequest) -> ExecutionHandle:
        handle_id = f"command-handle:{request.attempt_id}"
        if handle_id in self._runtimes:
            raise ValueError("attempt already started; inspect the original execution")
        if self._handle_store is not None:
            try:
                self._handle_store.load(handle_id)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("attempt already started; inspect the persisted execution")
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
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004,
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

        if process.stdout is None or process.stderr is None:
            self._terminate_group(process.pid, force=True)
            raise RuntimeError("command process streams are unavailable")

        handle = ExecutionHandle(
            handle_id=f"command-handle:{request.attempt_id}",
            adapter_kind=AdapterKind.COMMAND,
            adapter_version=self.adapter_version,
            real_execution_id=str(process.pid),
            process_start_identity=_process_start_identity(process.pid),
            workdir_ref=str(registration.cwd),
        )
        startup_token = str(uuid4())
        runtime = _CommandRuntime(
            request=request,
            registration=registration,
            handle=handle,
            process=process,
            startup_token=startup_token,
            captured_buffers={
                OutputStreamName.STDOUT: bytearray(),
                OutputStreamName.STDERR: bytearray(),
            },
            job_handle=_assign_windows_job(process.pid, startup_token) if os.name == "nt" else None,
        )
        self._runtimes[handle.handle_id] = runtime
        try:
            if os.name == "nt" and runtime.job_handle is None:
                raise RuntimeError("exclusive Windows execution job could not be established")
            self._persist_handle(runtime)
        except BaseException:
            self._abort_launch(runtime)
            raise

        try:
            self._open_spool_writers(runtime)
            if os.name == "nt":
                _resume_windows_process(process.pid)
        except BaseException:
            self._abort_launch(runtime)
            raise

        self._start_reader(runtime, OutputStreamName.STDOUT, process.stdout, secrets)
        self._start_reader(runtime, OutputStreamName.STDERR, process.stderr, secrets)
        if request.timeout_ms is not None:
            timer = threading.Timer(request.timeout_ms / 1000, self._timeout_runtime, (runtime,))
            timer.daemon = True
            runtime.timeout_timer = timer
            timer.start()
        return handle

    def _abort_launch(self, runtime: _CommandRuntime) -> None:
        """清理只归本次 Popen 所有的资源；保留启动已发生的持久事实。"""
        try:
            self._terminate_group(runtime.process.pid, force=True)
            runtime.process.wait(timeout=self._force_kill_timeout_seconds)
        finally:
            _close_job(runtime.job_handle)
            runtime.job_handle = None
            for writer in runtime.writers.values():
                writer.abort()
            for stream in (runtime.process.stdout, runtime.process.stderr):
                if stream is not None:
                    stream.close()
            self._runtimes.pop(runtime.handle.handle_id, None)

    def inspect(self, handle: ExecutionHandle) -> ExecutionInspectionResult:
        runtime = self._runtimes.get(handle.handle_id)
        if runtime is None:
            return self._inspect_persisted(handle)
        if runtime.handle != handle:
            raise ValueError("execution handle identity mismatch")
        self._hydrate_stop(runtime)
        return_code = runtime.process.poll()
        if return_code is not None:
            self._cancel_timeout(runtime)
        if runtime.timed_out and return_code is not None:
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.EXITED,
                process_reachable=False,
                identity_matches=True,
                unknown_reason="command_timeout",
            )
        if runtime.stop_requested and return_code is not None:
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.STOPPED,
                process_reachable=return_code is None,
                identity_matches=True,
                stop_confirmed=runtime.group_stopped,
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
        cursors: tuple[OutputCursor, ...] | None = None,
    ) -> ExecutionCollectionResult:
        runtime = self._runtimes.get(handle.handle_id)
        if runtime is None:
            return self._collect_persisted(handle, cursors)
        if runtime.handle != handle:
            raise ValueError("execution handle identity mismatch")
        with runtime.collection_lock:
            if runtime.collection is not None:
                if self._spool_store is not None:
                    for block in runtime.collection.output_blocks:
                        self._spool_store.read_block(block)
                return runtime.collection
            result = self._collect_runtime(runtime, handle, cursors)
            if result.complete:
                save = getattr(self._handle_store, "save_collection", None)
                if callable(save):
                    save(handle, result)
                runtime.collection = result
            return result

    def _collect_runtime(
        self,
        runtime: _CommandRuntime,
        handle: ExecutionHandle,
        cursors: tuple[OutputCursor, ...] | None,
    ) -> ExecutionCollectionResult:
        self._hydrate_stop(runtime)
        return_code = runtime.process.poll()
        if return_code is None:
            return ExecutionCollectionResult(
                attempt_id=runtime.request.attempt_id,
                output_cursors=tuple(runtime.output_cursors.values()) or (cursors or ()),
                capture_completeness=CaptureCompleteness.GAP,
                complete=False,
            )

        self._cancel_timeout(runtime)
        group_stopped = self._cleanup_group(runtime)
        for thread in runtime.threads:
            thread.join(timeout=5)
        readers_done = all(not thread.is_alive() for thread in runtime.threads)
        complete = readers_done and not runtime.read_errors and group_stopped
        completeness = self._capture_completeness(runtime)
        if not complete:
            completeness = (
                CaptureCompleteness.GAP
                if runtime.read_errors or not group_stopped
                else CaptureCompleteness.PARTIAL
            )

        if self._spool_store is not None:
            manifest = self._spool_store.read_manifest(runtime.request.attempt_id)
            output_blocks = manifest.blocks
            output_cursors = manifest.cursors
            captured_blocks: tuple[CapturedOutputBlock, ...] = ()
        else:
            output_blocks = tuple(runtime.output_blocks)
            output_cursors = _cursors_from_buffers(runtime)
            captured_blocks = _captured_blocks(runtime) if complete else ()

        if not complete:
            return ExecutionCollectionResult(
                attempt_id=runtime.request.attempt_id,
                output_blocks=output_blocks,
                captured_blocks=captured_blocks,
                output_cursors=output_cursors or (cursors or ()),
                capture_completeness=completeness,
                complete=False,
            )

        exit_fact = ExitFact(
            attempt_id=runtime.request.attempt_id,
            startup_token=runtime.startup_token,
            process_start_identity=handle.process_start_identity,
            real_exit_code=return_code,
            last_block_index_by_stream=tuple(
                (cursor.stream_name, cursor.last_block_index) for cursor in output_cursors
            ),
            saved_bytes_by_stream=tuple(
                (cursor.stream_name, cursor.offset) for cursor in output_cursors
            ),
            capture_completeness=completeness,
            termination_reason=(
                ProcessTerminationReason.TIMEOUT
                if runtime.timed_out
                else ProcessTerminationReason.CONFIRMED_STOP
                if runtime.stop_requested
                else ProcessTerminationReason.NATURAL_EXIT
            ),
            timed_out=runtime.timed_out,
        )
        return ExecutionCollectionResult(
            attempt_id=runtime.request.attempt_id,
            output_blocks=output_blocks,
            captured_blocks=captured_blocks,
            output_cursors=output_cursors,
            exit_fact_ref=exit_fact,
            structured_result_ref=f"command-result:{runtime.request.attempt_id}:{return_code}",
            capture_completeness=completeness,
            complete=True,
        )

    def request_stop(self, handle: ExecutionHandle) -> StopRequestResult:
        runtime = self._runtimes.get(handle.handle_id)
        if runtime is None:
            return self._stop_persisted(handle)
        if runtime.handle != handle:
            raise ValueError("execution handle identity mismatch")
        saved_stop = self._saved_stop(handle)
        if saved_stop is not None:
            self._hydrate_stop(runtime)
            return saved_stop
        return_code = runtime.process.poll()
        if return_code is not None:
            self._cancel_timeout(runtime)
            self._cleanup_group(runtime)
            return StopRequestResult(
                handle_id=handle.handle_id,
                stop_confirmed=False,
                observed_state=ExecutionInspectionState.EXITED,
                unknown_reason="process_already_exited",
            )
        runtime.stop_requested = True
        self._cancel_timeout(runtime)
        self._terminate_group(runtime.process.pid, force=False)
        try:
            runtime.process.wait(timeout=self._graceful_stop_timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate_group(runtime.process.pid, force=True)
            runtime.process.wait(timeout=self._force_kill_timeout_seconds)
        group_stopped = self._cleanup_group(runtime)
        result = StopRequestResult(
            handle_id=handle.handle_id,
            stop_confirmed=group_stopped,
            observed_state=(
                ExecutionInspectionState.STOPPED
                if group_stopped
                else ExecutionInspectionState.UNKNOWN
            ),
            unknown_reason=None if group_stopped else "process_group_stop_unconfirmed",
        )
        self._persist_stop(handle, result)
        return result

    def _timeout_runtime(self, runtime: _CommandRuntime) -> None:
        if runtime.process.poll() is not None:
            return
        runtime.timed_out = True
        self._terminate_group(runtime.process.pid, force=True)
        try:
            runtime.process.wait(timeout=self._force_kill_timeout_seconds)
        except subprocess.TimeoutExpired:
            runtime.process.kill()
            runtime.process.wait(timeout=self._force_kill_timeout_seconds)

    def _open_spool_writers(self, runtime: _CommandRuntime) -> None:
        if self._spool_store is None:
            return
        for stream_name in (OutputStreamName.STDOUT, OutputStreamName.STDERR):
            summary_id = f"redaction:{runtime.request.attempt_id}:{stream_name.value}"
            runtime.redaction_summary_ids[stream_name] = summary_id
            writer = self._spool_store.open_stream(
                run_id=runtime.request.run_id,
                step_id=runtime.request.step_id,
                attempt_id=runtime.request.attempt_id,
                stream_name=stream_name,
                capture_source="command",
                block_size=self._stream_block_size,
                redaction_summary_id=summary_id,
            )
            runtime.writers[stream_name] = writer

    def _start_reader(
        self,
        runtime: _CommandRuntime,
        stream_name: OutputStreamName,
        stream: IO[bytes],
        secrets: set[str],
    ) -> None:
        redactor = StreamingRedactor(secrets)
        memory_target = runtime.captured_buffers[stream_name]
        writer = runtime.writers.get(stream_name)

        def consume() -> None:
            reader_error = False
            try:
                while True:
                    chunk = (
                        stream.read1(4096)
                        if isinstance(stream, BufferedReader)
                        else stream.read(4096)
                    )
                    if not chunk:
                        break
                    filtered = redactor.feed(chunk)
                    self._consume_filtered(runtime, stream_name, filtered, memory_target, writer)
                self._consume_filtered(
                    runtime,
                    stream_name,
                    redactor.finish(),
                    memory_target,
                    writer,
                )
            except Exception as error:  # noqa: BLE001
                reader_error = True
                with runtime.lock:
                    runtime.read_errors.append(type(error).__name__)
            finally:
                stats = redactor.stats
                if writer is not None:
                    try:
                        refs = writer.close(complete=not reader_error)
                        with runtime.lock:
                            runtime.output_blocks.extend(refs)
                    except Exception as error:  # noqa: BLE001
                        reader_error = True
                        with runtime.lock:
                            runtime.read_errors.append(type(error).__name__)
                        writer.abort()
                if self._spool_store is not None:
                    try:
                        self._spool_store.persist_redaction_summary(
                            runtime.request.attempt_id,
                            stream_name,
                            RedactionSummary(
                                policy_version="aitest.redaction/1.0",
                                applied_rule_categories=tuple(
                                    category
                                    for category, count in stats.replacement_categories
                                    if count
                                ),
                                filtered_streams=(stream_name.value,),
                                filtered_ranges=(f"{stream_name.value}:0-{stats.output_bytes}",),
                                replacement_count=stats.replacement_count,
                                completeness="gap" if reader_error else "complete",
                                gap_reasons=("reader_error",) if reader_error else (),
                            ),
                        )
                    except Exception as error:  # noqa: BLE001
                        with runtime.lock:
                            runtime.read_errors.append(type(error).__name__)
                stream.close()

        thread = threading.Thread(target=consume, name=f"command-{stream_name.value}", daemon=True)
        runtime.threads.append(thread)
        thread.start()

    @staticmethod
    def _consume_filtered(
        runtime: _CommandRuntime,
        stream_name: OutputStreamName,
        content: bytes,
        memory_target: bytearray,
        writer: SpoolStreamWriter | None,
    ) -> None:
        if not content:
            return
        if writer is None:
            with runtime.lock:
                memory_target.extend(content)
            return
        refs = writer.append(content)
        with runtime.lock:
            runtime.output_blocks.extend(refs)
            if refs:
                last = refs[-1]
                runtime.output_cursors[stream_name] = OutputCursor(
                    attempt_id=runtime.request.attempt_id,
                    stream_name=stream_name,
                    offset=last.offset + last.length,
                    last_block_index=last.block_index,
                    last_committed_digest=last.digest,
                    durable=True,
                )

    def _capture_completeness(self, runtime: _CommandRuntime) -> CaptureCompleteness:
        if runtime.read_errors:
            return CaptureCompleteness.GAP
        if runtime.timed_out:
            return CaptureCompleteness.PARTIAL
        return CaptureCompleteness.COMPLETE

    def _registration_for(self, request: ExecutionRequest) -> CommandRegistration:
        entry_id = request.registered_entry.entry_id
        registration = self._registrations.get(entry_id)
        if registration is None:
            raise KeyError(f"command entry is not registered: {entry_id}")
        if request.registered_entry.entrypoint != registration.executable:
            raise ValueError("execution request entrypoint does not match registration")
        return registration

    def _runtime_for(self, handle: ExecutionHandle) -> _CommandRuntime:
        runtime = self._runtimes.get(handle.handle_id)
        if runtime is None:
            raise KeyError(f"unknown command handle: {handle.handle_id}")
        return runtime

    def _persist_handle(self, runtime: _CommandRuntime) -> None:
        if self._handle_store is None:
            return
        self._handle_store.save(
            PersistedExecutionHandle(
                attempt_id=runtime.request.attempt_id,
                startup_token=runtime.startup_token,
                handle=runtime.handle,
            )
        )

    def _persisted_for(self, handle: ExecutionHandle) -> PersistedExecutionHandle:
        if self._handle_store is None:
            raise KeyError(f"unknown command handle: {handle.handle_id}")
        try:
            persisted = self._handle_store.load(handle.handle_id)
        except FileNotFoundError as error:
            raise KeyError(f"unknown persisted command handle: {handle.handle_id}") from error
        if persisted.handle != handle:
            raise ValueError("persisted command handle identity does not match")
        return persisted

    def _inspect_persisted(self, handle: ExecutionHandle) -> ExecutionInspectionResult:
        persisted = self._persisted_for(handle)
        collection = self._saved_collection(handle)
        if collection is not None:
            fact = collection.exit_fact_ref
            stopped = fact is not None and (
                fact.termination_reason is ProcessTerminationReason.CONFIRMED_STOP
                and not fact.timed_out
            )
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.STOPPED
                if stopped
                else ExecutionInspectionState.EXITED,
                process_reachable=False,
                identity_matches=True,
                stop_confirmed=stopped,
                unknown_reason="command_timeout" if fact is not None and fact.timed_out else None,
            )
        if self._saved_stop(handle) is not None:
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.STOPPED,
                process_reachable=False,
                identity_matches=True,
                stop_confirmed=True,
            )
        pid = _real_pid(handle)
        if not _process_is_alive(pid):
            return ExecutionInspectionResult(
                handle_id=handle.handle_id,
                state=ExecutionInspectionState.LOST,
                process_reachable=False,
                identity_matches=False,
                unknown_reason="execution_handle_not_live",
            )
        identity_matches = _process_start_identity(pid) == persisted.handle.process_start_identity
        return ExecutionInspectionResult(
            handle_id=handle.handle_id,
            state=(
                ExecutionInspectionState.RUNNING
                if identity_matches
                else ExecutionInspectionState.LOST
            ),
            process_reachable=True,
            identity_matches=identity_matches,
            unknown_reason=None if identity_matches else "process_identity_mismatch",
        )

    def _collect_persisted(
        self,
        handle: ExecutionHandle,
        cursors: tuple[OutputCursor, ...] | None,
    ) -> ExecutionCollectionResult:
        persisted = self._persisted_for(handle)
        confirmed = self._saved_collection(handle)
        if confirmed is not None:
            return confirmed
        stopped = self._saved_stop(handle)

        def stopped_exit(completeness: CaptureCompleteness) -> ExitFact | None:
            if stopped is None:
                return None
            # This observation is grounded in the original owned group-stop receipt.
            # The vanished process's exit code and unread output cannot be reconstructed.
            return ExitFact(
                attempt_id=persisted.attempt_id,
                startup_token=persisted.startup_token,
                process_start_identity=handle.process_start_identity,
                real_exit_code=None,
                capture_completeness=completeness,
                termination_reason=ProcessTerminationReason.CONFIRMED_STOP,
            )

        if self._spool_store is None:
            return ExecutionCollectionResult(
                attempt_id=persisted.attempt_id,
                output_cursors=cursors or (),
                exit_fact_ref=stopped_exit(CaptureCompleteness.GAP),
                capture_completeness=CaptureCompleteness.GAP,
                complete=False,
            )
        try:
            manifest = self._spool_store.read_manifest(persisted.attempt_id)
        except FileNotFoundError:
            return ExecutionCollectionResult(
                attempt_id=persisted.attempt_id,
                output_cursors=cursors or (),
                exit_fact_ref=stopped_exit(CaptureCompleteness.GAP),
                capture_completeness=CaptureCompleteness.GAP,
                complete=False,
            )
        completeness = CaptureCompleteness.PARTIAL if manifest.blocks else CaptureCompleteness.GAP
        return ExecutionCollectionResult(
            attempt_id=persisted.attempt_id,
            output_blocks=manifest.blocks,
            output_cursors=manifest.cursors or (cursors or ()),
            exit_fact_ref=stopped_exit(completeness),
            capture_completeness=completeness,
            complete=False,
        )

    def _stop_persisted(self, handle: ExecutionHandle) -> StopRequestResult:
        persisted = self._persisted_for(handle)
        saved_stop = self._saved_stop(handle)
        if saved_stop is not None:
            return saved_stop
        if self._saved_collection(handle) is not None:
            return StopRequestResult(
                handle_id=handle.handle_id,
                stop_confirmed=False,
                observed_state=ExecutionInspectionState.EXITED,
                unknown_reason="process_already_exited",
            )
        pid = _real_pid(handle)
        if not _process_is_alive(pid):
            return StopRequestResult(
                handle_id=handle.handle_id,
                stop_confirmed=False,
                observed_state=ExecutionInspectionState.EXITED,
                unknown_reason="process_already_exited",
            )
        if _process_start_identity(pid) != persisted.handle.process_start_identity:
            return StopRequestResult(
                handle_id=handle.handle_id,
                stop_confirmed=False,
                observed_state=ExecutionInspectionState.LOST,
                unknown_reason="process_identity_mismatch",
            )
        if os.name == "nt":
            stopped = _stop_owned_windows_job(persisted)
            result = StopRequestResult(
                handle_id=handle.handle_id,
                stop_confirmed=stopped,
                observed_state=(
                    ExecutionInspectionState.STOPPED
                    if stopped
                    else ExecutionInspectionState.UNKNOWN
                ),
                unknown_reason=None if stopped else "process_group_ownership_unavailable",
            )
            self._persist_stop(handle, result)
            return result
        _terminate_process_group(pid, force=True)
        if _wait_for_process_exit(pid):
            result = StopRequestResult(
                handle_id=handle.handle_id,
                stop_confirmed=True,
                observed_state=ExecutionInspectionState.STOPPED,
            )
            self._persist_stop(handle, result)
            return result
        return StopRequestResult(
            handle_id=handle.handle_id,
            stop_confirmed=False,
            observed_state=ExecutionInspectionState.UNKNOWN,
            unknown_reason="process_group_stop_unconfirmed",
        )

    @staticmethod
    def _cancel_timeout(runtime: _CommandRuntime) -> None:
        if runtime.timeout_timer is not None:
            runtime.timeout_timer.cancel()

    def _saved_collection(self, handle: ExecutionHandle) -> ExecutionCollectionResult | None:
        load = getattr(self._handle_store, "load_collection", None)
        if not callable(load):
            return None
        result = load(handle)
        if result is None:
            return None
        if not isinstance(result, ExecutionCollectionResult):
            raise ValueError("persisted execution collection is invalid")
        if self._spool_store is not None:
            manifest = self._spool_store.read_manifest(result.attempt_id)
            if manifest.blocks != result.output_blocks or manifest.cursors != result.output_cursors:
                raise ValueError("confirmed execution collection no longer matches its spool")
            for block in manifest.blocks:
                self._spool_store.read_block(block)
        return result

    def _saved_stop(self, handle: ExecutionHandle) -> StopRequestResult | None:
        load = getattr(self._handle_store, "load_stop", None)
        result = load(handle) if callable(load) else None
        if result is not None and not isinstance(result, StopRequestResult):
            raise ValueError("persisted execution stop is invalid")
        return result

    def _persist_stop(self, handle: ExecutionHandle, result: StopRequestResult) -> None:
        save = getattr(self._handle_store, "save_stop", None)
        if result.stop_confirmed and callable(save):
            save(handle, result)

    def _hydrate_stop(self, runtime: _CommandRuntime) -> None:
        if self._saved_stop(runtime.handle) is not None:
            runtime.stop_requested = True
            runtime.group_stopped = True

    def _terminate_group(self, pid: int, *, force: bool) -> None:
        _terminate_process_group(pid, force=force)

    def _cleanup_group(self, runtime: _CommandRuntime) -> bool:
        if runtime.group_stopped:
            _close_job(runtime.job_handle)
            runtime.job_handle = None
            return True
        if os.name == "nt":
            if runtime.job_handle is None:
                runtime.group_stopped = False
                return False
            stopped = _terminate_and_wait_windows_job(
                runtime.job_handle,
                timeout_seconds=self._force_kill_timeout_seconds,
            )
            _close_job(runtime.job_handle)
            runtime.job_handle = None
            runtime.group_stopped = stopped
            return stopped
        _terminate_process_group(runtime.process.pid, force=True)
        runtime.group_stopped = _process_group_stopped(runtime.process.pid)
        return runtime.group_stopped


def _cursors_from_buffers(runtime: _CommandRuntime) -> tuple[OutputCursor, ...]:
    cursors: list[OutputCursor] = []
    for stream_name in (OutputStreamName.STDOUT, OutputStreamName.STDERR):
        content = bytes(runtime.captured_buffers[stream_name])
        if not content:
            continue
        cursors.append(
            OutputCursor(
                attempt_id=runtime.request.attempt_id,
                stream_name=stream_name,
                offset=len(content),
                last_block_index=0,
                last_committed_digest=_sha256_digest(content),
                durable=False,
            )
        )
    return tuple(cursors)


def _captured_blocks(runtime: _CommandRuntime) -> tuple[CapturedOutputBlock, ...]:
    blocks: list[CapturedOutputBlock] = []
    for stream_name in (OutputStreamName.STDOUT, OutputStreamName.STDERR):
        content = bytes(runtime.captured_buffers[stream_name])
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


def _sha256_digest(content: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(content).hexdigest()


def _terminate_process_group(pid: int, *, force: bool) -> None:
    if os.name == "nt":
        command = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            command.append("/F")
        subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
        return
    sig = signal.SIGKILL if force else signal.SIGTERM  # type: ignore[attr-defined]
    try:
        os.killpg(pid, sig)  # type: ignore[attr-defined]
    except ProcessLookupError:
        return


def _process_group_stopped(pid: int) -> bool:
    if os.name == "nt":
        return True
    for _ in range(50):
        try:
            os.killpg(pid, 0)  # type: ignore[attr-defined]
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _job_name(startup_token: str) -> str:
    return "Local\\aitest-" + hashlib.sha256(startup_token.encode("utf-8")).hexdigest()


def _resume_windows_process(pid: int) -> None:
    kernel = _kernel32()
    handle = kernel.OpenProcess(0x0800, False, pid)
    if not handle:
        raise RuntimeError("suspended execution process could not be opened")
    try:
        resume = ctypes.WinDLL("ntdll", use_last_error=True).NtResumeProcess
        resume.argtypes = [ctypes.c_void_p]
        resume.restype = ctypes.c_long
        if resume(handle) != 0:
            raise RuntimeError("suspended execution process could not be resumed")
    finally:
        kernel.CloseHandle(handle)


def _assign_windows_job(pid: int, startup_token: str) -> int | None:
    if os.name != "nt":
        return None
    kernel32 = _kernel32()
    create_job = kernel32.CreateJobObjectW
    create_job.restype = ctypes.c_void_p
    job = create_job(None, _job_name(startup_token))
    if not job:
        return None

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = 0x2000
    set_info = kernel32.SetInformationJobObject
    if not set_info(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(job)
        return None
    process_handle = kernel32.OpenProcess(_WINDOWS_JOB_PROCESS_ACCESS, False, pid)
    if not process_handle:
        kernel32.CloseHandle(job)
        return None
    try:
        if not kernel32.AssignProcessToJobObject(job, process_handle):
            kernel32.CloseHandle(job)
            return None
    finally:
        kernel32.CloseHandle(process_handle)
    return int(job)


def _close_job(job_handle: int | None) -> None:
    if os.name == "nt" and job_handle:
        _kernel32().CloseHandle(ctypes.c_void_p(job_handle))


def _terminate_and_wait_windows_job(
    job_handle: int,
    *,
    timeout_seconds: float,
) -> bool:
    if os.name != "nt":
        return False
    kernel32 = _kernel32()
    kernel32.TerminateJobObject(ctypes.c_void_p(job_handle), 1)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        info = _JobBasicAccountingInformation()
        if not kernel32.QueryInformationJobObject(
            ctypes.c_void_p(job_handle),
            1,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            return False
        if info.ActiveProcesses == 0:
            return True
        time.sleep(0.02)
    return False


def _kernel32() -> Any:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    # 64 位进程中的 HANDLE 不能走 ctypes 默认的 32 位整数转换。
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], ctypes.c_void_p),
        "OpenJobObjectW": ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], ctypes.c_void_p),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], ctypes.c_void_p),
        "CloseHandle": ([ctypes.c_void_p], wintypes.BOOL),
        "AssignProcessToJobObject": ([ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL),
        "IsProcessInJob": (
            [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
        ),
        "SetInformationJobObject": (
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "QueryInformationJobObject": (
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            wintypes.BOOL,
        ),
        "TerminateJobObject": ([ctypes.c_void_p, wintypes.UINT], wintypes.BOOL),
        "GetProcessTimes": (
            [ctypes.c_void_p, *([ctypes.POINTER(wintypes.FILETIME)] * 4)],
            wintypes.BOOL,
        ),
    }
    for name, (args, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes = args
        function.restype = result
    return kernel


def _stop_owned_windows_job(record: PersistedExecutionHandle) -> bool:
    from ctypes import wintypes

    kernel = _kernel32()
    job = kernel.OpenJobObjectW(0x0008 | 0x0004, False, _job_name(record.startup_token))
    if not job:
        return False
    process = kernel.OpenProcess(0x1000, False, _real_pid(record.handle))
    if not process:
        _close_job(int(job))
        return False
    try:
        belongs = wintypes.BOOL()
        if not kernel.IsProcessInJob(process, job, ctypes.byref(belongs)) or not belongs.value:
            return False
        # 在同一个 OS 进程句柄上核对创建时间，避免 PID 重用期间终止别的业务。
        if _windows_handle_identity(process, _real_pid(record.handle)) != (
            record.handle.process_start_identity
        ):
            return False
        return _terminate_and_wait_windows_job(int(job), timeout_seconds=5)
    finally:
        kernel.CloseHandle(process)
        _close_job(int(job))


def _process_start_identity(pid: int) -> str:
    if os.name == "nt":
        return _windows_process_start_identity(pid)
    if sys.platform.startswith("linux"):
        return _linux_process_start_identity(pid)
    return f"pid:{pid}:unverified"


def _real_pid(handle: ExecutionHandle) -> int:
    try:
        return int(handle.real_execution_id)
    except ValueError as error:
        raise ValueError("command handle real_execution_id must be a process id") from error


def _process_is_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_exit(pid: int) -> bool:
    for _ in range(100):
        if not _process_is_alive(pid):
            return True
        time.sleep(0.02)
    return False


def _windows_process_start_identity(pid: int) -> str:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return f"win32-pid:{pid}:unverified"
    try:
        return _windows_handle_identity(handle, pid)
    finally:
        kernel32.CloseHandle(handle)


def _windows_handle_identity(handle: int, pid: int) -> str:
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(4)]
    if not _kernel32().GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        return f"win32-pid:{pid}:unverified"
    ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return f"win32-pid:{pid}:created:{ticks}"


def _linux_process_start_identity(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.rsplit(")", 1)[1].split()
        start_ticks = fields[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return f"linux-pid:{pid}:unverified"
    return f"linux-boot:{boot_id}:pid:{pid}:start:{start_ticks}"


__all__ = ["CommandAdapter", "CommandHandleStore", "CommandRegistration", "SecretResolver"]
