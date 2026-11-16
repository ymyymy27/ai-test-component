"""DEC-021 real Python service sample: build, readiness, controlled stop.

This is component/integration evidence, not a real Trae or production service AC.
"""

from __future__ import annotations

import sys
from pathlib import Path

from aitest.application.execution.source_checks import SourceCommandRequest
from aitest.domain.execution.sources import SourceCheckStatusState, SourceCheckType
from aitest.infrastructure.adapters.execution.source_check_commands import (
    ControlledSourceCheckRunner,
)
from aitest.infrastructure.file_store.objects import FileObjectStore


def _project(tmp_path: Path) -> Path:
    workdir = tmp_path / "sample-service"
    package = workdir / "src" / "sample_service"
    package.mkdir(parents=True)
    (workdir / "pyproject.toml").write_text(
        "[project]\nname='sample-service'\nversion='0.1.0'\nrequires-python='>=3.11'\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import time\n\ndef main():\n"
        "    print('sample-service ready', flush=True)\n"
        "    time.sleep(20)\n",
        encoding="utf-8",
    )
    (workdir / "run_service.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent / 'src'))\n"
        "from sample_service.__main__ import main\nmain()\n",
        encoding="utf-8",
    )
    return workdir


def _runner(tmp_path: Path) -> ControlledSourceCheckRunner:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ControlledSourceCheckRunner(FileObjectStore(workspace))


def _request(workdir: Path, *, check_type: SourceCheckType, command: tuple[str, ...]):
    return SourceCommandRequest(
        check_type=check_type,
        check_id="sample-" + check_type.value,
        project_id="project-sample",
        command=command,
        workdir=str(workdir),
        interpreter=sys.executable,
        timeout_ms=10_000,
        descriptor="pyproject.toml",
        ready_when=(
            "stdout_contains:sample-service ready"
            if check_type is SourceCheckType.MINIMAL_START
            else None
        ),
    )


def test_refuses_to_build_without_the_frozen_interpreter(tmp_path: Path) -> None:
    workdir = _project(tmp_path)
    outcome = _runner(tmp_path).run(
        _request(
            workdir,
            check_type=SourceCheckType.APPLICABLE_BUILD,
            command=("some-other-python", "-m", "compileall", "-q", "src"),
        )
    )
    assert outcome.state is SourceCheckStatusState.BLOCKED


def test_real_service_builds_reaches_readiness_and_is_stopped(tmp_path: Path) -> None:
    workdir = _project(tmp_path)
    runner = _runner(tmp_path)

    build = runner.run(
        _request(
            workdir,
            check_type=SourceCheckType.APPLICABLE_BUILD,
            command=("python", "-m", "compileall", "-q", "src"),
        )
    )
    assert build.state is SourceCheckStatusState.VERIFIED, build.reason
    assert build.evidence_ref is not None
    assert not (workdir / "src" / "sample_service" / "__pycache__").exists()

    start = runner.run(
        _request(
            workdir,
            check_type=SourceCheckType.MINIMAL_START,
            command=("python", "run_service.py"),
        )
    )
    assert start.state is SourceCheckStatusState.VERIFIED, start.reason
    evidence = (tmp_path / "workspace" / str(start.evidence_ref)).read_bytes()
    assert b"sample-service ready" in evidence
    assert b"ready_observed: True" in evidence
    assert b"stop_confirmed: True" in evidence
