"""DEC-021: controlled build/minimal-start command runner."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from aitest.application.execution.source_checks import SourceCommandRequest
from aitest.domain.execution.runs import FailureClass
from aitest.domain.execution.sources import SourceCheckStatusState, SourceCheckType
from aitest.infrastructure.adapters.execution.source_check_commands import (
    ControlledSourceCheckRunner,
)
from aitest.infrastructure.file_store.objects import FileObjectStore


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


def _workdir(tmp_path: Path) -> Path:
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    return workdir


def _request(
    tmp_path: Path,
    *,
    check_type: SourceCheckType,
    script: str,
    module: str,
    ready_when: str | None = None,
    timeout_ms: int = 5_000,
    extra: tuple[str, ...] = (),
    descriptor: str | None = None,
) -> SourceCommandRequest:
    workdir = _workdir(tmp_path)
    (workdir / f"{module}.py").write_text(script, encoding="utf-8")
    return SourceCommandRequest(
        check_type=check_type,
        check_id="check-1",
        project_id="p1",
        command=("python", "-m", module, *extra),
        workdir=str(workdir),
        interpreter=sys.executable,
        timeout_ms=timeout_ms,
        descriptor=descriptor,
        ready_when=ready_when,
    )


def _run(tmp_path: Path, request: SourceCommandRequest):
    return ControlledSourceCheckRunner(FileObjectStore(_workspace(tmp_path))).run(request)


def _evidence(tmp_path: Path, reference: str | None) -> bytes:
    assert reference is not None
    return (_workspace(tmp_path) / reference).read_bytes()


def test_build_success_records_exit_and_stdout(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.APPLICABLE_BUILD,
            script="print('built ok')\n",
            module="build_ok",
        ),
    )
    assert outcome.state is SourceCheckStatusState.VERIFIED
    assert outcome.failure_class is FailureClass.PASSED
    assert b"built ok" in _evidence(tmp_path, outcome.evidence_ref)


def test_missing_dependency_is_blocked_not_a_syntax_failure(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.APPLICABLE_BUILD,
            script="import aitest_missing_dependency_xyz\n",
            module="build_missing",
        ),
    )
    assert outcome.state is SourceCheckStatusState.BLOCKED
    assert outcome.failure_class is FailureClass.DEPENDENCY_MISSING


def test_nonzero_build_exit_is_a_source_failure(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.APPLICABLE_BUILD,
            script="raise SystemExit(2)\n",
            module="build_failed",
        ),
    )
    assert outcome.state is SourceCheckStatusState.MISMATCH
    assert outcome.failure_class is FailureClass.SOURCE_ERROR


def test_minimal_start_requires_observing_readiness(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.MINIMAL_START,
            script="import time\nprint('ready', flush=True)\ntime.sleep(20)\n",
            module="service_ready",
            ready_when="stdout_contains:ready",
        ),
    )
    assert outcome.state is SourceCheckStatusState.VERIFIED
    assert "readiness observed" in (outcome.reason or "")


def test_minimal_start_exit_without_readiness_is_unknown(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.MINIMAL_START,
            script="print('stopped early')\n",
            module="service_early",
            ready_when="stdout_contains:ready",
        ),
    )
    assert outcome.state is SourceCheckStatusState.UNKNOWN
    assert "before the readiness marker" in (outcome.reason or "")


def test_minimal_start_timeout_is_blocked(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.MINIMAL_START,
            script="import time\ntime.sleep(20)\n",
            module="service_slow",
            ready_when="stdout_contains:ready",
            timeout_ms=1_000,
        ),
    )
    assert outcome.state is SourceCheckStatusState.BLOCKED
    assert outcome.failure_class is FailureClass.ENVIRONMENT_UNREACHABLE
    assert "exceeded" in (outcome.reason or "")


def test_python_network_access_is_denied(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.APPLICABLE_BUILD,
            script=(
                "import socket\n"
                "try:\n"
                "    socket.create_connection(('127.0.0.1', 9), timeout=0.5)\n"
                "except Exception as error:\n"
                "    print('denied:', error)\n"
                "else:\n"
                "    raise SystemExit(9)\n"
            ),
            module="build_network",
        ),
    )
    assert outcome.state is SourceCheckStatusState.VERIFIED
    assert b"network access denied" in _evidence(tmp_path, outcome.evidence_ref)


def test_user_pythonpath_is_not_injected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "outside"))
    monkeypatch.setenv("AITEST_SOURCE_CHECK_SECRET", "must-not-appear")
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.APPLICABLE_BUILD,
            script=(
                "import os\n"
                "print('pythonpath=', os.environ.get('PYTHONPATH'))\n"
                "print('extra_value=', os.environ.get('AITEST_SOURCE_CHECK_SECRET'))\n"
            ),
            module="build_env",
        ),
    )
    evidence = _evidence(tmp_path, outcome.evidence_ref)
    assert b"pythonpath= None" in evidence
    assert b"extra_value= None" in evidence


def test_arguments_are_passed_without_a_shell(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.APPLICABLE_BUILD,
            script="import sys\nprint(sys.argv[1:])\n",
            module="build_argv",
            extra=("a b", "&&", "echo", "should-not-run"),
        ),
    )
    evidence = _evidence(tmp_path, outcome.evidence_ref)
    assert b"['a b', '&&', 'echo', 'should-not-run']" in evidence


def test_command_must_use_the_frozen_interpreter(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    (workdir / "tool.py").write_text("print('no')\n", encoding="utf-8")
    outcome = ControlledSourceCheckRunner(FileObjectStore(_workspace(tmp_path))).run(
        SourceCommandRequest(
            check_type=SourceCheckType.APPLICABLE_BUILD,
            check_id="check-1",
            project_id="p1",
            command=("some-other-python", "-m", "tool"),
            workdir=str(workdir),
            interpreter=sys.executable,
            timeout_ms=5_000,
        )
    )
    assert outcome.state is SourceCheckStatusState.BLOCKED
    assert "frozen interpreter" in (outcome.reason or "")


def test_script_cannot_escape_the_workdir(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    outcome = ControlledSourceCheckRunner(FileObjectStore(_workspace(tmp_path))).run(
        SourceCommandRequest(
            check_type=SourceCheckType.APPLICABLE_BUILD,
            check_id="check-1",
            project_id="p1",
            command=("python", str(outside)),
            workdir=str(workdir),
            interpreter=sys.executable,
            timeout_ms=5_000,
        )
    )
    assert outcome.state is SourceCheckStatusState.BLOCKED
    assert "inside the frozen workdir" in (outcome.reason or "")


def test_descriptor_selects_the_registered_subdirectory(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    package = workdir / "pkg"
    package.mkdir()
    (package / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (package / "in_package.py").write_text(
        "from pathlib import Path\nprint(Path.cwd().name)\n", encoding="utf-8"
    )
    outcome = ControlledSourceCheckRunner(FileObjectStore(_workspace(tmp_path))).run(
        SourceCommandRequest(
            check_type=SourceCheckType.APPLICABLE_BUILD,
            check_id="check-1",
            project_id="p1",
            command=("python", "-m", "in_package"),
            workdir=str(workdir),
            interpreter=sys.executable,
            timeout_ms=5_000,
            descriptor="pkg/pyproject.toml",
        )
    )
    assert outcome.state is SourceCheckStatusState.VERIFIED
    assert b"pkg" in _evidence(tmp_path, outcome.evidence_ref)


def test_minimal_start_requires_a_marker(tmp_path: Path) -> None:
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.MINIMAL_START,
            script="print('ready')\n",
            module="service_no_marker",
        ),
    )
    assert outcome.state is SourceCheckStatusState.BLOCKED


def test_minimal_start_reaps_descendant_processes(tmp_path: Path) -> None:
    marker = tmp_path / "child-survived.txt"
    outcome = _run(
        tmp_path,
        _request(
            tmp_path,
            check_type=SourceCheckType.MINIMAL_START,
            script=(
                "import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c',\n"
                "    'import time,sys; time.sleep(2); open(sys.argv[1],\"w\").write(\"x\")',\n"
                "    sys.argv[1]])\n"
                "print('ready', flush=True)\n"
                "time.sleep(20)\n"
            ),
            module="service_child",
            ready_when="stdout_contains:ready",
            extra=(str(marker),),
        ),
    )
    assert outcome.state is SourceCheckStatusState.VERIFIED
    time.sleep(3)
    assert not marker.exists()
