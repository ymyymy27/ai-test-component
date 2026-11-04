"""受控适用构建/最小启动检查执行器（DEC-021）。

只接受显式登记的 argv，不经过 shell；cwd 固定在冻结 workdir 内，环境只保留
基础变量（不注入用户 ``PYTHONPATH``/凭据），输出先留在内存再交给对象仓过滤。
构建/启动命令必须使用冻结解释器；启动检查观察 ``stdout_contains:`` 判据后
回收整棵进程树，不把“进程还在”当成“已就绪”。
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast
from uuid import uuid4

from aitest.application.execution.source_checks import (
    SourceCommandOutcome,
    SourceCommandRequest,
)
from aitest.domain.execution.runs import FailureClass
from aitest.domain.execution.sources import SourceCheckStatusState, SourceCheckType
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.security import UnsafeMaterialError, known_secrets

from .command import (
    _assign_windows_job,
    _close_job,
    _resume_windows_process,
    _terminate_and_wait_windows_job,
)

_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_READY_PREFIX = "stdout_contains:"
_MAX_OUTPUT_BYTES = 64 * 1024
_MAX_EVIDENCE_BYTES = 192 * 1024
_POLL_SECONDS = 0.05
_STOP_TIMEOUT_SECONDS = 3.0

#: 出站网络禁用的 Python 审计钩子：拒绝 connect/DNS/UDP 发送，但允许本地 bind，
#: 否则最小启动的真实服务无法监听端口。只覆盖 Python 自身发起的连接；构建工具
#: 拉起的原生子进程不在审计范围内，因此证据里如实写明“Python 层禁网”。
_NETWORK_GUARD = r"""
import runpy, sys

_DENIED = frozenset({
    "socket.connect", "socket.sendto", "socket.sendmsg",
    "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
    "socket.gethostbyaddr", "socket.getnameinfo",
})

def _deny_network(event, args):
    if event in _DENIED:
        raise RuntimeError("network access denied for source check: " + event)

sys.addaudithook(_deny_network)
kind, target, *arguments = sys.argv[1:]
sys.argv = [target, *arguments]
if kind == "module":
    runpy.run_module(target, run_name="__main__", alter_sys=True)
elif kind == "script":
    import os
    sys.path.insert(0, os.path.dirname(target))
    runpy.run_path(target, run_name="__main__")
else:
    raise SystemExit("unsupported source check target")
"""

_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "TZ",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
)


@dataclass(slots=True)
class _Captured:
    chunks: list[bytes]
    size: int = 0
    truncated: bool = False

    def add(self, data: bytes) -> None:
        if not data:
            return
        remaining = _MAX_OUTPUT_BYTES - self.size
        if remaining > 0:
            self.chunks.append(data[:remaining])
            self.size += min(remaining, len(data))
        if len(data) > remaining:
            self.truncated = True

    def bytes(self) -> bytes:
        return b"".join(self.chunks)


class _Readiness:
    def __init__(self, marker: bytes | None) -> None:
        self._marker = marker
        self._event = threading.Event()

    def feed(self, data: bytes) -> None:
        if self._marker is not None and self._marker in data:
            self._event.set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


@dataclass(frozen=True, slots=True)
class _Execution:
    state: SourceCheckStatusState
    failure_class: FailureClass
    reason: str
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    timed_out: bool
    ready_observed: bool
    stop_confirmed: bool
    stdout_truncated: bool
    stderr_truncated: bool


class ControlledSourceCheckRunner:
    """默认的 build/minimal_start 执行器；由装配层注入对象仓。"""

    adapter_version = "source-check-command/1.0"

    def __init__(self, objects: FileObjectStore) -> None:
        self._objects = objects

    def run(self, request: SourceCommandRequest) -> SourceCommandOutcome:
        try:
            cwd = _resolve_cwd(request)
            argv, kind, target = _resolve_argv(request, cwd)
            marker = _ready_marker(request)
        except ValueError as error:
            return SourceCommandOutcome(
                state=SourceCheckStatusState.BLOCKED,
                failure_class=FailureClass.ENVIRONMENT_UNREACHABLE,
                reason=str(error),
            )
        secrets = known_secrets().text_values()
        if any(secret in token for token in request.command for secret in secrets):
            return SourceCommandOutcome(
                state=SourceCheckStatusState.BLOCKED,
                failure_class=FailureClass.ENVIRONMENT_UNREACHABLE,
                reason="resolved secret value must not appear in source check arguments",
            )
        if request.check_type is SourceCheckType.MINIMAL_START and marker is None:
            return SourceCommandOutcome(
                state=SourceCheckStatusState.BLOCKED,
                failure_class=FailureClass.ENVIRONMENT_UNREACHABLE,
                reason="minimal_start requires a mechanical stdout_contains readiness marker",
            )
        if request.check_type is SourceCheckType.APPLICABLE_BUILD and marker is not None:
            return SourceCommandOutcome(
                state=SourceCheckStatusState.BLOCKED,
                failure_class=FailureClass.ENVIRONMENT_UNREACHABLE,
                reason="build check must not observe a readiness marker",
            )

        with tempfile.TemporaryDirectory(prefix="aitest-source-check-") as temporary:
            environment = _build_environment(Path(temporary))
            try:
                executed = self._execute(
                    request,
                    argv=argv,
                    cwd=cwd,
                    environment=environment,
                    marker=marker,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                return SourceCommandOutcome(
                    state=SourceCheckStatusState.BLOCKED,
                    failure_class=FailureClass.ENVIRONMENT_UNREACHABLE,
                    reason=f"source check could not start: {error}",
                )
        try:
            evidence_ref = self._publish(
                request,
                cwd=cwd,
                argv=argv,
                kind=kind,
                target=target,
                executed=executed,
            )
        except (OSError, ValueError, UnsafeMaterialError) as error:
            return SourceCommandOutcome(
                state=SourceCheckStatusState.UNVERIFIED,
                failure_class=FailureClass.TOOL_FAILURE,
                reason=f"source check evidence could not be saved: {error}",
            )
        return SourceCommandOutcome(
            state=executed.state,
            failure_class=executed.failure_class,
            reason=executed.reason,
            evidence_ref=evidence_ref,
        )

    def _execute(
        self,
        request: SourceCommandRequest,
        *,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str],
        marker: bytes | None,
    ) -> _Execution:
        process = cast(
            subprocess.Popen[bytes],
            subprocess.Popen(
                argv,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                text=False,
                **(
                    {
                        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                        | 0x00000004
                        | 0x08000000,
                    }
                    if os.name == "nt"
                    else {"start_new_session": True}
                ),
            ),
        )
        job = _assign_job(process)
        if os.name == "nt":
            handle = getattr(process, "_handle", None)
            if not isinstance(handle, int):
                _terminate_tree(process, job)
                raise OSError("the Windows process handle is unavailable")
            if job is None:
                process.kill()
                process.wait(timeout=_STOP_TIMEOUT_SECONDS)
                raise OSError("exclusive Windows execution job could not be established")
            _resume_windows_process(handle)
        stdout = _Captured([])
        stderr = _Captured([])
        readiness = _Readiness(marker)
        threads = [
            _reader(process.stdout, stdout, readiness),
            _reader(process.stderr, stderr, None),
        ]
        deadline = time.monotonic() + request.timeout_ms / 1000
        timed_out = False
        ready_observed = False
        while True:
            return_code = process.poll()
            if return_code is not None:
                break
            if request.check_type is SourceCheckType.MINIMAL_START and readiness.wait(
                _POLL_SECONDS
            ):
                ready_observed = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            if request.check_type is not SourceCheckType.MINIMAL_START:
                time.sleep(_POLL_SECONDS)

        stop_confirmed = True
        if ready_observed or timed_out:
            stop_confirmed = _terminate_tree(process, job)
        else:
            # Natural exit: still reap descendants that inherited the job/group.
            stop_confirmed = _terminate_tree(process, job, after_exit=True)
        try:
            return_code = process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stop_confirmed = False
            return_code = process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        for thread in threads:
            thread.join(timeout=5)
        _close_job(job)

        output = stdout.bytes()
        error_output = stderr.bytes()
        combined = output + b"\n" + error_output
        if timed_out:
            state, failure_class, reason = (
                SourceCheckStatusState.BLOCKED,
                FailureClass.ENVIRONMENT_UNREACHABLE,
                f"source check exceeded {request.timeout_ms} ms",
            )
        elif ready_observed and not stop_confirmed:
            state, failure_class, reason = (
                SourceCheckStatusState.UNVERIFIED,
                FailureClass.TOOL_FAILURE,
                "source check readiness observed but the process tree stop was unconfirmed",
            )
        elif ready_observed:
            state, failure_class, reason = (
                SourceCheckStatusState.VERIFIED,
                FailureClass.PASSED,
                "minimal start readiness observed and the process tree was stopped",
            )
        elif request.check_type is SourceCheckType.MINIMAL_START and return_code == 0:
            state, failure_class, reason = (
                SourceCheckStatusState.UNKNOWN,
                FailureClass.TOOL_FAILURE,
                "process exited before the readiness marker was observed",
            )
        elif return_code == 0:
            state, failure_class, reason = (
                SourceCheckStatusState.VERIFIED,
                FailureClass.PASSED,
                "build command exited successfully",
            )
        else:
            state, failure_class, reason = _classify_failure(combined, return_code)
        return _Execution(
            state=state,
            failure_class=failure_class,
            reason=reason,
            stdout=output,
            stderr=error_output,
            exit_code=return_code,
            timed_out=timed_out,
            ready_observed=ready_observed,
            stop_confirmed=stop_confirmed,
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
        )

    def _publish(
        self,
        request: SourceCommandRequest,
        *,
        cwd: Path,
        argv: list[str],
        kind: str,
        target: str,
        executed: _Execution,
    ) -> str | None:
        header = "\n".join(
            (
                f"source_check: {request.check_type.value}",
                f"check_id: {request.check_id}",
                f"argv: {argv}",
                f"cwd: {cwd.as_posix()}",
                f"interpreter: {request.interpreter}",
                f"target_kind: {kind}",
                f"target: {target}",
                f"timeout_ms: {request.timeout_ms}",
                f"exit_code: {executed.exit_code}",
                f"timed_out: {executed.timed_out}",
                f"ready_observed: {executed.ready_observed}",
                f"stop_confirmed: {executed.stop_confirmed}",
                f"state: {executed.state.value}",
                f"failure_class: {executed.failure_class.value}",
                f"reason: {executed.reason}",
                "network: denied at the Python audit-hook layer",
                f"stdout_truncated: {executed.stdout_truncated}",
                f"stderr_truncated: {executed.stderr_truncated}",
                "--- stdout ---",
            )
        )
        body = (
            header.encode("utf-8")
            + b"\n"
            + executed.stdout
            + b"\n--- stderr ---\n"
            + executed.stderr
        )
        if len(body) > _MAX_EVIDENCE_BYTES:
            body = body[:_MAX_EVIDENCE_BYTES] + b"\n[evidence truncated]\n"
        stored = self._objects.publish_bytes(
            request.project_id,
            body,
            media_type="text/plain; charset=utf-8",
        )
        return stored.relative_path


def _reader(
    stream: IO[bytes] | None,
    captured: _Captured,
    readiness: _Readiness | None,
) -> threading.Thread:
    def read() -> None:
        if stream is None:
            return
        try:
            while True:
                # `read(n)` waits for n bytes on a pipe; `read1` returns what is
                # available so a readiness line is observed before the process exits.
                read1 = getattr(stream, "read1", None)
                chunk = read1(64 * 1024) if callable(read1) else stream.read(64 * 1024)
                if not chunk:
                    break
                captured.add(chunk)
                if readiness is not None:
                    readiness.feed(chunk)
        finally:
            stream.close()

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return thread


def _resolve_cwd(request: SourceCommandRequest) -> Path:
    root = Path(request.workdir).resolve()
    if not root.is_dir():
        raise ValueError("source check workdir is not a directory")
    if request.descriptor is None:
        return root
    descriptor = (root / request.descriptor).resolve()
    if not descriptor.is_relative_to(root):
        raise ValueError("source check descriptor escapes the frozen workdir")
    if not descriptor.exists():
        raise ValueError("source check descriptor does not exist")
    cwd = descriptor if descriptor.is_dir() else descriptor.parent
    if not cwd.is_relative_to(root):
        raise ValueError("source check cwd escapes the frozen workdir")
    return cwd


def _resolve_argv(request: SourceCommandRequest, cwd: Path) -> tuple[list[str], str, str]:
    interpreter = Path(request.interpreter).resolve()
    if not interpreter.is_file():
        raise ValueError("frozen interpreter does not exist")
    command = list(request.command)
    first = command[0]
    direct = first in {"<interpreter>", "{interpreter}", "python", "python.exe", "python3"}
    frozen_path = Path(first).is_absolute() and Path(first).resolve() == interpreter
    if not (direct or frozen_path):
        raise ValueError("source check command must start with the frozen interpreter")
    arguments = command[1:]
    if not arguments:
        raise ValueError("source check command has no module or script")
    kind = arguments[0]
    if kind == "-m":
        if len(arguments) < 2 or not _MODULE_RE.fullmatch(arguments[1]):
            raise ValueError("source check -m target must be a dotted module name")
        target = arguments[1]
        rest = arguments[2:]
        return (
            [str(interpreter), "-E", "-s", "-c", _NETWORK_GUARD, "module", target, *rest],
            "module",
            target,
        )
    if kind.endswith(".py"):
        script = Path(kind)
        if not script.is_absolute():
            script = cwd / script
        script = script.resolve()
        if not script.is_relative_to(cwd) or not script.is_file():
            raise ValueError("source check script must be a file inside the frozen workdir")
        return (
            [
                str(interpreter),
                "-E",
                "-s",
                "-c",
                _NETWORK_GUARD,
                "script",
                str(script),
                *arguments[1:],
            ],
            "script",
            str(script),
        )
    raise ValueError("source check supports only 'python -m <module>' or a workdir script")


def _ready_marker(request: SourceCommandRequest) -> bytes | None:
    if request.ready_when is None:
        return None
    if not request.ready_when.startswith(_READY_PREFIX):
        raise ValueError("ready_when only supports 'stdout_contains:<text>'")
    value = request.ready_when[len(_READY_PREFIX) :]
    if not value.strip():
        raise ValueError("ready_when must carry a nonempty marker")
    return value.encode("utf-8")


def _build_environment(temporary: Path) -> dict[str, str]:
    environment = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": str(temporary / "pycache"),
            "TMP": str(temporary),
            "TEMP": str(temporary),
            "TMPDIR": str(temporary),
            "PIP_CACHE_DIR": str(temporary / "pip-cache"),
            "XDG_CACHE_HOME": str(temporary / "cache"),
            # 用户缓存也不写入真实 home；需要读取用户配置的构建应显式走环境合同。
            "HOME": str(temporary),
            "USERPROFILE": str(temporary),
        }
    )
    for denied in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        environment.pop(denied, None)
    return environment


def _assign_job(process: subprocess.Popen[bytes]) -> int | None:
    if os.name != "nt":
        return None
    handle = getattr(process, "_handle", None)
    if not isinstance(handle, int):
        return None
    return _assign_windows_job(handle, uuid4().hex)


def _terminate_tree(
    process: subprocess.Popen[bytes],
    job: int | None,
    *,
    after_exit: bool = False,
) -> bool:
    if os.name == "nt":
        if job is None:
            return not after_exit
        return _terminate_and_wait_windows_job(job, timeout_seconds=_STOP_TIMEOUT_SECONDS)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)  # type: ignore[attr-defined]
        except ProcessLookupError:
            return True
    if after_exit:
        try:
            os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
        except ProcessLookupError:
            return True
    for _ in range(int(_STOP_TIMEOUT_SECONDS / 0.02)):
        if process.poll() is not None:
            # Parent exited; make sure descendants that ignored SIGTERM are gone too.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
            return True
        time.sleep(0.02)
    try:
        os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
    except ProcessLookupError:
        return True
    return True


def _classify_failure(
    output: bytes, exit_code: int
) -> tuple[SourceCheckStatusState, FailureClass, str]:
    text = output.decode("utf-8", errors="replace").lower()
    dependency_markers = (
        "modulenotfounderror",
        "no module named",
        "importerror",
        "could not find a version",
        "no matching distribution",
    )
    environment_markers = (
        "permission denied",
        "no such file or directory",
        "not recognized as an internal or external command",
        "cannot find the path",
        "file not found",
    )
    if any(marker in text for marker in dependency_markers):
        return (
            SourceCheckStatusState.BLOCKED,
            FailureClass.DEPENDENCY_MISSING,
            f"source check exit {exit_code}: dependency missing",
        )
    if any(marker in text for marker in environment_markers):
        return (
            SourceCheckStatusState.BLOCKED,
            FailureClass.ENVIRONMENT_UNREACHABLE,
            f"source check exit {exit_code}: environment unavailable",
        )
    return (
        SourceCheckStatusState.MISMATCH,
        FailureClass.SOURCE_ERROR,
        f"source check exit {exit_code}",
    )


__all__ = ["ControlledSourceCheckRunner"]
