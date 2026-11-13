"""Discover pytest scopes without creating a business record.

The inventory is a snapshot-bound derived cache: it stores file-level counts
and collection errors only.  Node IDs are parsed transiently for counting and
are deliberately not persisted; the JUnit result of an execution is the source
of the actual test nodes.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from aitest.application.ports import SourceSnapshotPort
from aitest.application.project.environment_resolution import EnvironmentResolutionService
from aitest.application.project.source_analysis import SourceAnalysisError, SourceAnalysisService


class TestDiscoveryError(ValueError):
    code = "B_TEST_DISCOVERY_UNAVAILABLE"


class TestDiscoveryBlocked(TestDiscoveryError):
    code = "B_TEST_DISCOVERY_BLOCKED"


def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _safe_relative(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise TestDiscoveryBlocked("test scope must be a non-empty POSIX relative path")
    if value == ".":
        return value
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise TestDiscoveryBlocked("test scope must stay inside the source snapshot")
    return path.as_posix()


class TestDiscoveryService:
    """Run ``pytest --collect-only`` in the frozen environment and summarize it."""

    def __init__(
        self,
        *,
        source_analysis: SourceAnalysisService,
        environments: EnvironmentResolutionService,
        snapshots: SourceSnapshotPort,
    ) -> None:
        self.source_analysis = source_analysis
        self.environments = environments
        self.snapshots = snapshots

    def discover(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        revision: int,
        environment_id: str,
        environment_revision: int,
        selected_paths: Sequence[str] = (),
        timeout_ms: int = 120_000,
    ) -> dict[str, object]:
        if not project_id.strip() or not snapshot_id.strip() or not environment_id.strip():
            raise TestDiscoveryError("project, snapshot and environment identity are required")
        if type(revision) is not int or revision < 1:
            raise TestDiscoveryError("snapshot revision must be a positive integer")
        if type(environment_revision) is not int or environment_revision < 1:
            raise TestDiscoveryError("environment revision must be a positive integer")
        if type(timeout_ms) is not int or not 1_000 <= timeout_ms <= 1_800_000:
            raise TestDiscoveryError("timeout must be between 1000 and 1800000 ms")

        try:
            source = self.source_analysis.verify_saved(
                project_id=project_id, snapshot_id=snapshot_id, revision=revision
            )
        except SourceAnalysisError as error:
            raise TestDiscoveryBlocked(str(error)) from error
        binding_id = source.get("binding_id")
        binding_revision = source.get("binding_revision")
        pinned_snapshot_id = source.get("pinned_snapshot_id")
        if (
            not isinstance(binding_id, str)
            or not binding_id.strip()
            or type(binding_revision) is not int
            or not isinstance(pinned_snapshot_id, str)
            or not pinned_snapshot_id.strip()
        ):
            raise TestDiscoveryBlocked("saved source lacks a verifiable binding or pinned snapshot")

        environment = self.environments.resolve(
            project_id=project_id,
            binding_id=binding_id,
            binding_revision=binding_revision,
            environment_id=environment_id,
            environment_revision=environment_revision,
        )
        resolution = environment.resolution
        if resolution is None or not resolution.executable_path:
            raise TestDiscoveryBlocked("environment has no verified interpreter")
        executable = resolution.executable_path

        source_selected = source.get("selected_paths")
        raw_scope: Sequence[str] = selected_paths
        if not raw_scope and isinstance(source_selected, (list, tuple)):
            raw_scope = tuple(str(value) for value in source_selected)
        scope = tuple(_safe_relative(value) for value in raw_scope)
        if not scope:
            scope = (".",)

        with tempfile.TemporaryDirectory(prefix="aitest-discover-") as temporary:
            materialized = self.snapshots.materialize(pinned_snapshot_id, temporary)
            if materialized.get("verified") is not True:
                raise TestDiscoveryBlocked("source snapshot could not be materialized")
            destination = Path(str(materialized["destination"]))
            inventory = self._collect(
                executable=executable,
                cwd=destination,
                scope=scope,
                timeout_ms=timeout_ms,
            )

        inventory.update(
            {
                "inventory_id": "inventory:" + _digest(inventory)[7:23],
                "source_snapshot_id": snapshot_id,
                "source_revision": revision,
                "environment_id": environment_id,
                "environment_revision": environment_revision,
                "selected_paths": list(scope),
            }
        )
        inventory["digest"] = _digest(
            {key: value for key, value in inventory.items() if key != "digest"}
        )
        return inventory

    def _collect(
        self, *, executable: str, cwd: Path, scope: tuple[str, ...], timeout_ms: int
    ) -> dict[str, object]:
        command = [
            executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
            *scope,
        ]
        environment = {
            key: os.environ[key]
            for key in ("TEMP", "TMP", "SYSTEMROOT", "WINDIR")
            if key in os.environ
        }
        environment.update(
            {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        )
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                shell=False,
                timeout=timeout_ms / 1000,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TestDiscoveryBlocked("pytest collection timed out") from error
        except OSError as error:
            raise TestDiscoveryBlocked(f"pytest could not be started: {error}") from error

        stdout = completed.stdout.decode("utf-8", "replace")
        stderr = completed.stderr.decode("utf-8", "replace")
        nodes = [
            line.strip()
            for line in stdout.splitlines()
            if "::" in line and not line.lstrip().startswith(("=", "FAILED", "ERROR"))
        ]
        counts = Counter(node.split("::", 1)[0] for node in nodes)
        nodes_by_file: dict[str, list[str]] = {}
        for node in nodes:
            nodes_by_file.setdefault(node.split("::", 1)[0], []).append(node)
        files: list[dict[str, object]] = [
            {
                "path": path,
                "test_count": count,
                "markers": [],
                "collection_error": None,
                # Node IDs are kept for manual stage-C validation; the AI-facing
                # projection deliberately excludes them.
                "nodes": sorted(nodes_by_file.get(path, ())),
            }
            for path, count in sorted(counts.items())
        ]
        for item in files:
            item["mock_candidates"] = _mock_candidates(cwd / str(item["path"]))
        collection_errors: list[str] = []
        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip()
            if detail:
                collection_errors.append(detail[-4000:])
        if not nodes and not collection_errors:
            collection_errors.append("pytest collected no tests")
        return {
            "pytest_version": self._version(executable, cwd, environment),
            "summary": {
                "file_count": len(files),
                "test_count": len(nodes),
                "collection_error_count": len(collection_errors),
            },
            "files": files,
            "collection_errors": collection_errors,
            "projection": self._projection(files),
        }

    @staticmethod
    def _version(executable: str, cwd: Path, environment: Mapping[str, str]) -> str:
        try:
            completed = subprocess.run(
                [executable, "-m", "pytest", "--version"],
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                shell=False,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        text = (completed.stdout or completed.stderr).decode("utf-8", "replace").strip()
        return text.splitlines()[-1][:200] if text else "unknown"

    @staticmethod
    def _projection(files: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        tree: dict[str, dict[str, object]] = {}
        for item in files:
            path = str(item["path"])
            parts = PurePosixPath(path).parts
            for index in range(1, len(parts)):
                directory = PurePosixPath(*parts[:index]).as_posix()
                entry = tree.setdefault(directory, {"path": directory, "tests": 0, "files": 0})
                entry["tests"] = _integer(entry["tests"]) + _integer(item["test_count"])
                entry["files"] = _integer(entry["files"]) + 1
        return [tree[key] for key in sorted(tree)]


def _integer(value: object) -> int:
    return value if type(value) is int else 0


def _mock_candidates(path: Path) -> list[str]:
    """Static hints only; never evidence that a test is real or mocked."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")[:256_000]
    except OSError:
        return []
    patterns = (
        ("unittest.mock", "unittest.mock"),
        ("from mock import", "mock"),
        ("pytest_mock", "pytest-mock"),
        ("mocker.patch", "pytest-mock"),
        ("monkeypatch", "monkeypatch"),
        ("MagicMock", "mock"),
    )
    return sorted({label for token, label in patterns if token in content})
