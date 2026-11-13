"""Private, bounded, memory-only source channel for an owned command process."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aitest.domain.execution.runs import ExecutionHandle, ExecutionRequest
from aitest.domain.execution.runtime_source import RuntimeSourceFile, RuntimeSourceObservation

_LIMIT = 2 * 1024 * 1024
_GAPS = {"opaque_execution", "dynamic_code_unverified", "external_code_unverified",
         "undeclared_source", "loaded_code_mismatch", "source_read_unverified",
         "preloaded_project_module", "live_thread_unverified"}


def carrier_source() -> str:
    return Path(__file__).with_name("_python_source_worker.py").read_text(encoding="utf-8")


def carrier_digest() -> str:
    return "sha256:" + hashlib.sha256(carrier_source().encode()).hexdigest()


class PythonSourceChannel:
    """The adapter owns both the birth identity and the pipe, never stdout markers."""

    def __init__(
        self, request: ExecutionRequest, secrets: set[str], *, mode: str = "script",
    ) -> None:
        plan = request.source_capture
        if plan is None or plan.policy != "python-script/1" or not plan.files:
            raise ValueError("unsupported source capture policy")
        if mode not in {"script", "module"}:
            raise ValueError("unsupported source capture mode")
        self.worker = carrier_source()
        if plan.carrier_digest != "sha256:" + hashlib.sha256(self.worker.encode()).hexdigest():
            raise ValueError("source carrier changed; prepare a new action")
        self.request, self.secrets = request, secrets
        self.mode = mode
        self.entry = _module_entry(request) if mode == "module" else ""
        self.expected = {item.relative_path: item.content_digest for item in plan.files}
        self.start: dict[str, Any] | None = None
        self.files: dict[str, str] = {}
        self.gaps: set[str] = set()
        self.ended = False
        self.done = False
        self.ownership: Callable[[int], str | None] = lambda _: None
        self.executing_identity = ""
        self.started = threading.Event()
        self.reader: threading.Thread | None = None
        self.read_fd, self.write_fd = os.pipe()
        if os.name == "nt":
            import msvcrt

            self.child_fd = msvcrt.get_osfhandle(self.write_fd)
            os.set_handle_inheritable(self.child_fd, True)
        else:
            self.child_fd = self.write_fd
        self.config = (json.dumps({"pipe": self.child_fd, "arguments":
                      request.registered_entry.arguments, "mode": mode,
                      "entry": self.entry, "files": [asdict(f) for f in plan.files]},
                      ensure_ascii=True) + "\n").encode()
        if len(self.config) >= _LIMIT:
            self.close()
            raise ValueError("source capture manifest exceeds bounded input")

    def command(self, executable: str) -> list[str]:
        return [executable, "-I", "-B", "-c", self.worker]

    def popen_options(self) -> dict[str, Any]:
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.lpAttributeList = {"handle_list": [self.child_fd]}
            return {"startupinfo": startup, "close_fds": True}
        return {"pass_fds": (self.write_fd,)}

    def attach(
        self, process: subprocess.Popen[bytes], ownership: Callable[[int], str | None],
    ) -> None:
        self.ownership = ownership
        os.close(self.write_fd)
        self.write_fd = -1
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

        def write() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(self.config)
                process.stdin.flush()
                self.started.wait(timeout=10)
                process.stdin.write(b"owned\n" if self.executing_identity else b"denied\n")
                process.stdin.close()
            except OSError:
                self.gaps.add("source_channel_input_unavailable")

        threading.Thread(target=write, daemon=True).start()

    def _read(self) -> None:
        total = 0
        try:
            with os.fdopen(self.read_fd, "rb") as stream:
                self.read_fd = -1
                while line := stream.readline(64 * 1024):
                    total += len(line)
                    if total > _LIMIT or not line.endswith(b"\n"):
                        self.gaps.add("source_channel_overflow")
                        continue
                    # No unfiltered metadata ever reaches a spool, result file or object.
                    event = json.loads(line)
                    if any(secret.encode() in line or json.dumps(secret)[1:-1].encode() in line
                           for secret in self.secrets):
                        self.gaps.add("source_channel_redacted")
                        if not isinstance(event, dict) or event.get("event") != "start":
                            continue
                    self._event(event)
        except (OSError, ValueError, TypeError, KeyError):
            self.gaps.add("source_channel_invalid")
        finally:
            self.done = True

    def _event(self, event: Any) -> None:
        if not isinstance(event, dict) or self.ended:
            raise ValueError("source event after terminal receipt")
        if event.get("event") == "start" and self.start is None and set(event) == {
            "event", "pid", "interpreter", "entry",
        }:
            if (type(event["pid"]) is not int or not isinstance(event["interpreter"], str)
                    or not isinstance(event["entry"], str)):
                raise ValueError("invalid process header")
            self.start = event
            self.executing_identity = self.ownership(event["pid"]) or ""
            self.started.set()
        elif self.start is not None and event.get("event") == "file" and set(event) == {
            "event", "relative_path", "digest",
        }:
            path, digest = event["relative_path"], event["digest"]
            if (not isinstance(path, str) or path in self.files
                    or self.expected.get(path) != digest):
                raise ValueError("unfrozen source observation")
            self.files[path] = digest
        elif self.start is not None and event.get("event") == "end" and set(event) == {
            "event", "gaps",
        }:
            if not isinstance(event["gaps"], list) or any(
                not isinstance(gap, str) or gap not in _GAPS for gap in event["gaps"]
            ):
                raise ValueError("unknown capture gap")
            self.gaps.update(event["gaps"])
            self.ended = True
        else:
            raise ValueError("invalid source event")

    def observation(self, handle: ExecutionHandle) -> RuntimeSourceObservation:
        if self.reader is not None:
            self.reader.join(timeout=5)
        start = self.start or {}
        relative_entry = (
            self.entry
            if self.mode == "module"
            else self.request.registered_entry.arguments[0]
        )
        entry = (Path(handle.workdir_ref) / relative_entry).resolve()
        executable = Path(self.request.registered_entry.entrypoint).resolve()
        identity = (bool(self.executing_identity)
                    and start.get("entry") == str(entry)
                    and start.get("interpreter") == str(executable))
        if not identity:
            self.gaps.add("source_process_identity_unverified")
        root = Path(handle.workdir_ref).resolve()
        relative_entry = entry.relative_to(root).as_posix()
        # A module-mode entry is the pytest scope (often a directory), while the
        # capture reports files. It counts as observed when at least one captured
        # file lives under it; a plain file entry still has to be listed exactly.
        if relative_entry not in self.files and not any(
            Path(path).is_relative_to(relative_entry) for path in self.files
        ):
            self.gaps.add("source_entry_not_observed")
        if not (self.done and self.ended):
            self.gaps.add("source_channel_incomplete")
        safe_entry, safe_executable = str(entry), str(executable)
        if any(secret in safe_entry or secret in safe_executable for secret in self.secrets):
            safe_entry = safe_executable = "redacted"
            self.gaps.add("source_channel_redacted")
        return RuntimeSourceObservation(
            attempt_id=self.request.attempt_id,
            process_start_identity=handle.process_start_identity,
            process_id=handle.real_execution_id, interpreter=safe_executable, entry=safe_entry,
            files=tuple(RuntimeSourceFile(key, value) for key, value in sorted(self.files.items())),
            gaps=tuple(sorted(self.gaps)), complete=self.done and self.ended and identity,
            executing_process_id=str(start.get("pid", "")),
            executing_process_start_identity=self.executing_identity,
        )

    def close(self) -> None:
        for fd in (self.read_fd, self.write_fd):
            if fd >= 0:
                os.close(fd)
        self.read_fd = self.write_fd = -1


_VALUE_OPTIONS = frozenset(
    {"-p", "-k", "-m", "-o", "-c", "--rootdir", "--junitxml", "--tb", "--ignore",
     "--deselect", "--maxfail", "--basetemp", "--confcutdir"}
)


def _module_scope(arguments: tuple[str, ...]) -> str | None:
    """Last path-like argument after ``-m <module>`` (the pytest scope)."""

    skip = False
    candidates: list[str] = []
    for value in arguments[2:]:
        if skip:
            skip = False
            continue
        if value in _VALUE_OPTIONS:
            skip = True
            continue
        if value.startswith("-") or not value:
            continue
        candidates.append(value)
    return candidates[-1] if candidates else None


def _module_entry(request: ExecutionRequest) -> str:
    """Canonical workdir-relative scope entry recorded in the start event."""

    root = Path(request.materialized_snapshot_ref).resolve()
    scope = _module_scope(request.registered_entry.arguments)
    if scope is None:
        plan = request.source_capture
        assert plan is not None
        return plan.files[0].relative_path
    candidate = (root / scope).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("module scope escapes the materialized workdir")
    return candidate.relative_to(root).as_posix()


def source_channel(request: ExecutionRequest, secrets: set[str]) -> PythonSourceChannel | None:
    arguments = request.registered_entry.arguments
    root = Path(request.materialized_snapshot_ref).resolve()
    if request.source_capture is None or not arguments:
        return None
    if arguments[0] == "-m" and len(arguments) >= 2 and arguments[1] == "pytest":
        return PythonSourceChannel(request, secrets, mode="module")
    if arguments[0].startswith("-") or Path(arguments[0]).suffix != ".py":
        return None
    entry = (root / arguments[0]).resolve()
    if not entry.is_relative_to(root):
        return None
    return PythonSourceChannel(request, secrets)
