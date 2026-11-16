"""Deterministic project understanding: scanner and versioned persistence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

import pytest

from aitest.application.project.understanding import (
    ProjectUnderstandingError,
    ProjectUnderstandingService,
    scan_sources,
)
from aitest.domain.planning.understanding import FunctionTrigger
from tests.support.memory_substrate import MemoryReader, MemoryUnitOfWork


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class _FakeSnapshots:
    def __init__(self, files: Mapping[str, bytes]) -> None:
        self.files = dict(files)
        self.calls: list[tuple[str, str]] = []

    def materialize(self, snapshot_id: str, destination: str) -> dict[str, object]:
        self.calls.append((snapshot_id, destination))
        root = Path(destination)
        paths: list[dict[str, object]] = []
        for relative, content in sorted(self.files.items()):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            paths.append(
                {
                    "relative_path": relative,
                    "actual_path": target.as_posix(),
                    "sha256": _digest(content),
                    "size": len(content),
                }
            )
        return {
            "snapshot_id": snapshot_id,
            "destination": root.as_posix(),
            "materialized": [item["relative_path"] for item in paths],
            "verified": True,
            "state": "materialized",
            "paths": paths,
        }


_SOURCES = {
    "app/main.py": b"""from fastapi import FastAPI
app = FastAPI()

@app.get("/orders/{order_id}")
def get_order(order_id: str):
    return order_id
""",
    "app/cli.py": b"""import argparse
parser = argparse.ArgumentParser()
sub = parser.add_subparsers()
sub.add_parser("create-order")
""",
    "app/lib.py": b"""def calculate_total(items):
    return sum(items)
""",
}


def test_scan_recognises_http_cli_and_library_entries() -> None:
    understanding = scan_sources(
        files=_SOURCES,
        source_snapshot_id="snap-1",
        understanding_id="understanding-1",
        revision=1,
        scope="project",
    )

    triggers = {function.name: function.trigger for function in understanding.catalog.functions}
    assert triggers["GET /orders/{order_id}"] is FunctionTrigger.HTTP
    assert triggers["create-order"] is FunctionTrigger.CLI
    assert triggers["calculate_total"] is FunctionTrigger.LIBRARY
    assert len(understanding.test_intents) == 3
    assert len(understanding.goal_candidates) == 3
    assert understanding.goal_candidates[0].intent_id in {
        intent.intent_id for intent in understanding.test_intents
    }
    assert understanding.unknown_areas == ()
    assert understanding.capability_gaps == ()


def test_scan_records_broken_python_and_unsupported_source() -> None:
    files = {
        "app/bad.py": b"def broken(:\n",
        "web/app.ts": b"export function createOrder() {}\n",
    }
    understanding = scan_sources(
        files=files,
        all_paths=tuple(files),
        source_snapshot_id="snap-1",
        understanding_id="understanding-1",
        revision=1,
        scope="project",
    )

    assert understanding.catalog.functions == ()
    assert understanding.unknown_areas
    assert "bad.py" in understanding.unknown_areas[0]
    gaps = {gap.scope: gap for gap in understanding.capability_gaps}
    assert "web/app.ts" in gaps
    assert gaps["web/app.ts"].blocks_full is True


def test_scan_honours_selected_paths() -> None:
    understanding = scan_sources(
        files=_SOURCES,
        all_paths=tuple(_SOURCES),
        source_snapshot_id="snap-1",
        understanding_id="understanding-1",
        revision=1,
        scope="project",
        selected_paths=("app/main.py",),
    )

    assert [function.name for function in understanding.catalog.functions] == [
        "GET /orders/{order_id}"
    ]
    assert all("app/main.py" in ref for ref in understanding.catalog.functions[0].source_refs)


def _service() -> tuple[ProjectUnderstandingService, MemoryUnitOfWork]:
    unit = MemoryUnitOfWork()
    unit.open("project-1")
    service = ProjectUnderstandingService(
        reader=MemoryReader(unit.store),
        unit_of_work=unit,
        snapshots=_FakeSnapshots(_SOURCES),
        workspace_id="workspace-1",
    )
    return service, unit


def test_service_is_idempotent_for_the_same_source_snapshot() -> None:
    service, _ = _service()

    first = service.understand(
        project_id="project-1",
        source_snapshot_id="snap-1",
        pinned_snapshot_id="pin-1",
        scope="project",
    )
    again = service.understand(
        project_id="project-1",
        source_snapshot_id="snap-1",
        pinned_snapshot_id="pin-1",
        scope="project",
    )

    assert first == again
    assert first["understanding_revision"] == 1
    assert first["function_count"] == 3


def test_service_creates_a_new_revision_for_a_new_source_snapshot() -> None:
    service, _ = _service()

    first = service.understand(
        project_id="project-1",
        source_snapshot_id="snap-1",
        pinned_snapshot_id="pin-1",
        scope="project",
    )
    second = service.understand(
        project_id="project-1",
        source_snapshot_id="snap-2",
        pinned_snapshot_id="pin-2",
        scope="project",
    )

    assert first["understanding_revision"] == 1
    assert second["understanding_revision"] == 2
    assert first["understanding_id"] == second["understanding_id"]


def test_service_rejects_a_pinned_digest_mismatch() -> None:
    class _BadSnapshots(_FakeSnapshots):
        def materialize(self, snapshot_id: str, destination: str) -> dict[str, object]:
            result = super().materialize(snapshot_id, destination)
            result["paths"][0]["sha256"] = "sha256:" + "0" * 64
            return result

    unit = MemoryUnitOfWork()
    unit.open("project-1")
    service = ProjectUnderstandingService(
        reader=MemoryReader(unit.store),
        unit_of_work=unit,
        snapshots=_BadSnapshots(_SOURCES),
        workspace_id="workspace-1",
    )

    with pytest.raises(ProjectUnderstandingError, match="digest changed"):
        service.understand(
            project_id="project-1",
            source_snapshot_id="snap-1",
            pinned_snapshot_id="pin-1",
            scope="project",
        )


def test_service_uses_separate_identity_for_a_different_scope() -> None:
    service, _ = _service()

    project = service.understand(
        project_id="project-1",
        source_snapshot_id="snap-1",
        pinned_snapshot_id="pin-1",
        scope="project",
    )
    module = service.understand(
        project_id="project-1",
        source_snapshot_id="snap-1",
        pinned_snapshot_id="pin-1",
        scope="app.main",
    )

    assert project["understanding_id"] != module["understanding_id"]
    assert project["understanding_revision"] == 1
    assert module["understanding_revision"] == 1
