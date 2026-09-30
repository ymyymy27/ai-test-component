"""Unit tests for the file migration manager (infrastructure/file_store/migrations.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aitest.infrastructure.file_store.migrations import (
    FileMigrationManager,
    MigrationError,
)
from aitest.infrastructure.file_store.workspace import Workspace

_BOTH_STEPS = (
    "0001-workspace-schema-version",
    "0002-records-intents-container",
)


@pytest.fixture
def manager(tmp_path: Path) -> FileMigrationManager:
    Workspace(tmp_path)  # initialize a real workspace identity
    return FileMigrationManager(tmp_path)


def _plan(manager: FileMigrationManager, steps: tuple[str, ...] = _BOTH_STEPS) -> str:
    return manager.plan(steps).plan_id


def test_inspect_reports_available_and_pending(manager: FileMigrationManager) -> None:
    report = manager.inspect()
    assert report["applied"] == []
    assert report["available"] == sorted(_BOTH_STEPS)
    assert report["pending"] == sorted(_BOTH_STEPS)
    assert report["workspace_schema_version"] == "1.0"


def test_plan_rejects_unknown_empty_unsorted_and_duplicates(manager: FileMigrationManager) -> None:
    with pytest.raises(MigrationError):
        manager.plan(("9999-does-not-exist",))
    with pytest.raises(MigrationError):
        manager.plan(())
    with pytest.raises(MigrationError):
        manager.plan((_BOTH_STEPS[1], _BOTH_STEPS[0]))
    with pytest.raises(MigrationError):
        manager.plan((_BOTH_STEPS[0], _BOTH_STEPS[0]))


def test_plan_is_persisted(manager: FileMigrationManager) -> None:
    plan_id = _plan(manager)
    files = list((manager._dir / "plans").glob(f"{plan_id}.json"))
    assert len(files) == 1


def test_apply_executes_all_and_creates_verifiable_backup(manager: FileMigrationManager) -> None:
    plan_id = _plan(manager)
    report = manager.apply(plan_id)

    assert report.state == "applied"
    assert report.executed == _BOTH_STEPS
    assert report.skipped == ()
    assert report.backup_path is not None
    assert (report.backup_path / "backup.json").exists()
    assert manager.inspect()["applied"] == sorted(_BOTH_STEPS)


def test_apply_twice_is_nothing_to_apply(manager: FileMigrationManager) -> None:
    plan_id = _plan(manager)
    manager.apply(plan_id)
    second = manager.apply(plan_id)
    assert second.state == "nothing_to_apply"
    assert second.executed == ()


def test_resume_completes_only_unapplied_step(manager: FileMigrationManager) -> None:
    plan_id = _plan(manager)
    manager.apply(plan_id)
    # Simulate a crash before the final registry persist of step two.
    registry_path = manager._dir / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["applied"].pop(_BOTH_STEPS[1])
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    resumed = manager.resume(plan_id)
    assert resumed.state == "resumed"
    assert resumed.executed == (_BOTH_STEPS[1],)
    assert resumed.skipped == (_BOTH_STEPS[0],)
    assert manager.inspect()["applied"] == sorted(_BOTH_STEPS)


def test_rollback_reverses_steps_and_clears_registry(manager: FileMigrationManager) -> None:
    plan_id = _plan(manager)
    manager.apply(plan_id)
    report = manager.rollback(plan_id)

    assert report.state == "rolled_back"
    assert report.rolled_back == (_BOTH_STEPS[1], _BOTH_STEPS[0])
    assert manager.inspect()["applied"] == []


def test_rollback_unknown_plan_is_error(manager: FileMigrationManager) -> None:
    with pytest.raises(MigrationError):
        manager.rollback("plan-does-not-exist")


def test_workspace_migration_has_real_file_effect_and_rollback(tmp_path: Path) -> None:
    workspace_file = tmp_path / "workspace.json"
    workspace_file.write_text(
        json.dumps({"workspace_id": "ws-1"}), encoding="utf-8"
    )
    manager = FileMigrationManager(tmp_path)
    plan_id = manager.plan((_BOTH_STEPS[0],)).plan_id

    report = manager.apply(plan_id)
    assert report.executed == (_BOTH_STEPS[0],)
    data = json.loads(workspace_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0"

    manager.rollback(plan_id)
    data = json.loads(workspace_file.read_text(encoding="utf-8"))
    assert "schema_version" not in data


def test_records_migration_adds_and_removes_intents_container(tmp_path: Path) -> None:
    records_file = tmp_path / "records.json"
    records_file.write_text(json.dumps({"records": {}, "commit": 0}), encoding="utf-8")
    manager = FileMigrationManager(tmp_path)
    plan_id = manager.plan((_BOTH_STEPS[1],)).plan_id

    manager.apply(plan_id)
    data = json.loads(records_file.read_text(encoding="utf-8"))
    assert data["intents"] == {}

    manager.rollback(plan_id)
    data = json.loads(records_file.read_text(encoding="utf-8"))
    assert "intents" not in data
