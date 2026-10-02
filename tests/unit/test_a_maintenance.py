"""Unit tests for the file maintenance service (infrastructure/file_store/maintenance.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.maintenance import (
    FileMaintenanceService,
    ReclaimCandidate,
    SpaceReport,
)
from aitest.infrastructure.file_store.workspace import Workspace


@pytest.fixture
def service(tmp_path: Path) -> FileMaintenanceService:
    Workspace(tmp_path)
    FileEventJournal(tmp_path, instance_id="instance-1")
    return FileMaintenanceService(tmp_path)


def _make_atomic_leftover(tmp_path: Path, *, content: bytes = b"stale") -> Path:
    # A-07：回收资格需要“正式目标已发布且候选不新于目标”的事实，因此
    # 先放置已发布目标 records.json，再放置更旧的崩溃遗留暂存。
    target = tmp_path / "records.json"
    target.write_text(json.dumps({"records": {}, "commit": 0}), encoding="utf-8")
    path = tmp_path / ".records.json.tmp"
    path.write_bytes(content)
    older = target.stat().st_mtime_ns - 10_000_000
    os.utime(path, ns=(older, older))
    return path


def _make_empty_staging(tmp_path: Path) -> Path:
    path = tmp_path / "event-log" / "staging" / "42.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_diagnose_totals_and_integrity(service: FileMaintenanceService) -> None:
    report = service.diagnose()
    assert isinstance(report, SpaceReport)
    assert report.total_files >= 1
    assert report.total_bytes == report.business_bytes + report.temp_bytes + (
        report.backup_bytes + report.other_bytes
    )
    assert report.integrity_ok is True
    assert report.integrity_errors == ()


def test_reclaimable_finds_leftover_and_empty_staging(
    service: FileMaintenanceService, tmp_path: Path
) -> None:
    leftover = _make_atomic_leftover(tmp_path)
    staging = _make_empty_staging(tmp_path)

    candidates = service.reclaimable()
    paths = {candidate.relative_path for candidate in candidates}

    assert leftover.relative_to(tmp_path).as_posix() in paths
    assert staging.relative_to(tmp_path).as_posix() in paths
    assert all(isinstance(candidate, ReclaimCandidate) for candidate in candidates)


def test_business_files_are_not_reclaimable(service: FileMaintenanceService) -> None:
    paths = {candidate.relative_path for candidate in service.reclaimable()}
    for business in ("workspace.json", "records.json", "event-log/journal.jsonl"):
        assert business not in paths


def test_dry_run_preview_deletes_nothing(service: FileMaintenanceService, tmp_path: Path) -> None:
    leftover = _make_atomic_leftover(tmp_path)
    report = service.reclaim(dry_run=True)

    assert report.state == "preview"
    assert report.removed == ()
    assert leftover.exists()


def test_reclaim_deletes_file_frees_bytes_and_audits(
    service: FileMaintenanceService, tmp_path: Path
) -> None:
    leftover = _make_atomic_leftover(tmp_path, content=b"12345")
    relative = leftover.relative_to(tmp_path).as_posix()

    report = service.reclaim(relative_paths=(relative,), dry_run=False)

    assert report.state == "reclaimed"
    assert report.removed == (relative,)
    assert report.bytes_freed == 5
    assert not leftover.exists()

    audit = tmp_path / "maintenance" / "audit.jsonl"
    entry = json.loads(audit.read_text(encoding="utf-8"))
    assert entry["relative_path"] == relative
    assert entry["size_bytes"] == 5


def test_non_whitelist_path_is_refused(service: FileMaintenanceService, tmp_path: Path) -> None:
    report = service.reclaim(relative_paths=("workspace.json",), dry_run=False)
    assert report.refused == ("workspace.json",)
    assert report.removed == ()
    assert (tmp_path / "workspace.json").exists()


def test_path_traversal_is_refused(service: FileMaintenanceService) -> None:
    report = service.reclaim(relative_paths=("../escape.tmp",), dry_run=False)
    assert report.refused == ("../escape.tmp",)
