"""Unit tests for the file maintenance service (infrastructure/file_store/maintenance.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.maintenance import (
    FileMaintenanceService,
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


def test_duplicate_backup_audit_does_not_publish_fabricated_verified_fact(
    service: FileMaintenanceService,
) -> None:
    material = b'{"action":"backed_up","verified":false,"verified":true}\n'
    service._audit_path.write_bytes(material)
    assert service.backup_facts() == ()
    assert service._audit_path.read_bytes() == material


