from __future__ import annotations

import sys
from pathlib import Path

from aitest.application.project.test_discovery import TestDiscoveryService as DiscoveryService


def test_collect_reports_file_level_counts(tmp_path: Path) -> None:
    tests = tmp_path / "pkgA" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_order.py").write_text(
        "def test_create():\n    assert 1 == 1\n\ndef test_cancel():\n    assert 1 == 1\n",
        encoding="utf-8",
    )
    service = DiscoveryService(  # type: ignore[arg-type]
        source_analysis=None, environments=None, snapshots=None
    )
    inventory = service._collect(
        executable=sys.executable, cwd=tmp_path, scope=("pkgA/tests",), timeout_ms=30_000
    )
    assert inventory["summary"]["test_count"] == 2
    assert inventory["files"] == [
        {
            "path": "pkgA/tests/test_order.py",
            "test_count": 2,
            "markers": [],
            "collection_error": None,
            "mock_candidates": [],
            "nodes": [
                "pkgA/tests/test_order.py::test_cancel",
                "pkgA/tests/test_order.py::test_create",
            ],
        }
    ]
    assert inventory["collection_errors"] == []


def test_collect_reports_collection_error(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_bad.py").write_text("import missing_module_for_aitest\n", encoding="utf-8")
    service = DiscoveryService(  # type: ignore[arg-type]
        source_analysis=None, environments=None, snapshots=None
    )
    inventory = service._collect(
        executable=sys.executable, cwd=tmp_path, scope=("tests",), timeout_ms=30_000
    )
    assert inventory["summary"]["test_count"] == 0
    assert inventory["collection_errors"]
