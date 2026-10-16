"""Host-owned Python carrier configuration, frozen at core assembly."""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from aitest.application.ports import EnvironmentResolutionRequest
from aitest.contracts.prepared_run import EnvironmentRefFact
from aitest.domain.json_material import decode_json
from aitest.infrastructure.python_environment import (
    PythonEnvironmentCarrier,
    RegisteredPythonEnvironmentResolver,
)

CONFIG_VERSION = "aitest.python-carriers/1"
MAX_CONFIG_BYTES = 256 * 1024
FIELDS = {
    "project_id",
    "environment_id",
    "carrier_id",
    "executable",
    "expected_executable_digest",
    "base_executable",
    "expected_base_executable_digest",
    "dependency_roots",
    "isolation_mode",
    "venv_root",
}


def carrier_configuration(
    *,
    project_id: str,
    environment_id: str,
    executable: Path,
    base_executable: Path,
    dependency_roots: tuple[Path, ...],
    venv_root: Path,
) -> dict[str, object]:
    """Produce a registration document from explicit paths, without executing Python."""
    resolver = RegisteredPythonEnvironmentResolver()
    carrier = PythonEnvironmentCarrier(
        project_id,
        environment_id,
        uuid4().hex,
        executable,
        resolver._file_digest(executable),
        base_executable,
        resolver._file_digest(base_executable),
        dependency_roots,
        venv_root=venv_root,
    )
    RegisteredPythonEnvironmentResolver((carrier,))
    resolver._configuration(carrier)
    # No process is started; actual version/dependency observations happen at prepare.
    for path in dependency_roots:
        resolver._no_links(path)
        if not path.is_dir():
            raise ValueError("dependency root is missing")
    row: dict[str, object] = {
        "project_id": project_id,
        "environment_id": environment_id,
        "carrier_id": carrier.carrier_id,
        "executable": str(executable),
        "expected_executable_digest": carrier.expected_executable_digest,
        "base_executable": str(base_executable),
        "expected_base_executable_digest": carrier.expected_base_executable_digest,
        "dependency_roots": [str(p) for p in dependency_roots],
        "isolation_mode": "venv",
        "venv_root": str(venv_root),
    }
    return {"schema_version": CONFIG_VERSION, "carriers": [row]}


def _load(path: Path) -> RegisteredPythonEnvironmentResolver:
    if not path.is_absolute():
        raise ValueError("carrier configuration path must be absolute")
    RegisteredPythonEnvironmentResolver._no_links(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("carrier configuration exceeds limit")
    data = decode_json(raw)
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "carriers"}
        or data["schema_version"] != CONFIG_VERSION
        or not isinstance(data["carriers"], list)
        or len(data["carriers"]) > 256
    ):
        raise ValueError("invalid carrier configuration")
    carriers = []
    for row in data["carriers"]:
        if not isinstance(row, dict) or set(row) != FIELDS:
            raise ValueError("invalid carrier fields")
        for key in FIELDS - {"dependency_roots", "venv_root"}:
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError("invalid carrier text")
        roots = row["dependency_roots"]
        if (
            not isinstance(roots, list)
            or not roots
            or len(roots) > 32
            or any(not isinstance(p, str) or not p for p in roots)
            or (row["venv_root"] is not None and not isinstance(row["venv_root"], str))
        ):
            raise ValueError("invalid carrier paths")
        carriers.append(
            PythonEnvironmentCarrier(
                row["project_id"],
                row["environment_id"],
                row["carrier_id"],
                Path(row["executable"]),
                row["expected_executable_digest"],
                Path(row["base_executable"]),
                row["expected_base_executable_digest"],
                tuple(Path(p) for p in roots),
                row["isolation_mode"],
                Path(row["venv_root"]) if row["venv_root"] is not None else None,
            )
        )
    return RegisteredPythonEnvironmentResolver(tuple(carriers))


class HostConfiguredEnvironmentResolver:
    """Bad host configuration blocks only environment-dependent actions."""

    def __init__(self) -> None:
        self.resolver: RegisteredPythonEnvironmentResolver | None = None
        configured = os.environ.get("AITEST_PYTHON_CARRIERS")
        # Keep invalid configuration local to environment-dependent actions;
        # raw file content never enters shared diagnostics.
        with suppress(OSError, ValueError, TypeError):
            self.resolver = (
                _load(Path(configured)) if configured else RegisteredPythonEnvironmentResolver()
            )

    def resolve(self, request: EnvironmentResolutionRequest) -> EnvironmentRefFact:
        if self.resolver is None:
            raise ValueError("宿主 Python 载体配置无效；修正 AITEST_PYTHON_CARRIERS 后受控重启核心")
        return self.resolver.resolve(request)
