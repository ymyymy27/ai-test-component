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
    available = sorted(
        (
            *_BOTH_STEPS,
            "0003-sharded-record-authority",
            "0004-bounded-query-directory",
            "0005-complete-commit-closure",
            "0006-canonical-current-publication",
            "0007-canonical-manifest-and-business-changes",
        )
    )
    assert report["available"] == available
    assert report["pending"] == available
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


@pytest.mark.parametrize("duplicate_key", ["state", r"st\u0061te"])
def test_duplicate_checkpoint_cannot_hide_prepared_state(
    manager: FileMigrationManager, duplicate_key: str
) -> None:
    plan = manager.plan([_BOTH_STEPS[0]])
    path = manager._steps_dir / plan.plan_id / f"{_BOTH_STEPS[0]}.json"
    path.parent.mkdir(parents=True)
    material = (
        '{"plan_id":' + json.dumps(plan.plan_id)
        + ',"state":"prepared","' + duplicate_key + '":"rolled_back"}'
    ).encode("utf-8")
    path.write_bytes(material)
    registry = manager._registry_path.read_bytes()
    with pytest.raises(MigrationError):
        manager.maintenance_reason()
    assert path.read_bytes() == material
    assert manager._registry_path.read_bytes() == registry


@pytest.mark.parametrize("schema", [None, "aitest.migration-registry/999"])
def test_unknown_registry_schema_cannot_release_maintenance(
    manager: FileMigrationManager, schema: str | None
) -> None:
    material = json.dumps({"schema": schema, "applied": {}}).encode("utf-8")
    manager._registry_path.write_bytes(material)
    with pytest.raises(MigrationError):
        manager.maintenance_reason()
    assert manager._registry_path.read_bytes() == material


def test_duplicate_registry_cannot_forge_applied_step(manager: FileMigrationManager) -> None:
    plan = manager.plan([_BOTH_STEPS[0]])
    path = manager._steps_dir / plan.plan_id / f"{_BOTH_STEPS[0]}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"plan_id": plan.plan_id, "state": "prepared"}), encoding="utf-8")
    material = (
        '{"schema":"aitest.migration-registry/1.0","applied":{},"applied":{'
        + json.dumps(_BOTH_STEPS[0]) + ': {}}}'
    ).encode("utf-8")
    manager._registry_path.write_bytes(material)
    with pytest.raises(MigrationError):
        manager.maintenance_reason()
    assert manager._registry_path.read_bytes() == material


@pytest.mark.parametrize(
    "field,value", [("schema", None), ("schema", "unknown"), ("plan_id", "other")]
)
def test_unknown_plan_schema_or_identity_is_rejected_before_apply(
    manager: FileMigrationManager, field: str, value: str | None
) -> None:
    plan = manager.plan([_BOTH_STEPS[0]])
    payload = json.loads(plan.path.read_bytes())
    payload[field] = value
    material = json.dumps(payload).encode("utf-8")
    plan.path.write_bytes(material)
    with pytest.raises(MigrationError):
        manager.apply(plan.plan_id)
    assert plan.path.read_bytes() == material
    assert not list(manager._backups_dir.iterdir())
    assert manager._load_registry() == {}


def test_duplicate_commit_cannot_hide_rollback_watermark(manager: FileMigrationManager) -> None:
    material = b'{"commit":100,"commit":0,"records":{"r":{}}}'
    path = manager._root / "records.json"
    path.write_bytes(material)
    with pytest.raises(MigrationError, match="水位"):
        manager._records_watermark()
    assert path.read_bytes() == material


@pytest.mark.parametrize("registered_plan", [None, "different"])
def test_prepared_checkpoint_needs_registration_from_exact_plan(
    manager: FileMigrationManager, registered_plan: str | None
) -> None:
    plan = manager.plan([_BOTH_STEPS[0]])
    other = manager.plan(_BOTH_STEPS)
    path = manager._steps_dir / plan.plan_id / f"{_BOTH_STEPS[0]}.json"
    path.parent.mkdir(parents=True)
    material = json.dumps({"plan_id": plan.plan_id, "state": "prepared"}).encode("utf-8")
    path.write_bytes(material)
    manager._save_registry(
        applied={
            _BOTH_STEPS[0]: {
                "plan_id": other.plan_id if registered_plan else None,
                "changed": False,
                "added_keys": {},
                "records_commit_before": 0,
            }
        }
    )
    assert manager.maintenance_reason() is not None
    assert path.read_bytes() == material


def test_successful_exact_plan_registration_releases_prepared_checkpoint(
    manager: FileMigrationManager,
) -> None:
    plan = manager.plan([_BOTH_STEPS[0]])
    report = manager.apply(plan.plan_id)
    assert report.state == "applied"
    assert manager.maintenance_reason() is None
    checkpoint = manager._steps_dir / plan.plan_id / f"{_BOTH_STEPS[0]}.json"
    assert json.loads(checkpoint.read_bytes())["state"] == "prepared"


@pytest.mark.parametrize("damage", ["duplicate_checkpoint", "unknown_registry", "other_plan"])
def test_uncertain_migration_blocks_adapter_write_without_blocking_doctor(
    manager: FileMigrationManager, damage: str
) -> None:
    from aitest.contracts.commands import Command
    from aitest.interfaces.local.api import EntryKind, LocalAPI, Session

    plan = manager.plan([_BOTH_STEPS[0]])
    path = manager._steps_dir / plan.plan_id / f"{_BOTH_STEPS[0]}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"plan_id": plan.plan_id, "state": "prepared"}), encoding="utf-8")
    if damage == "duplicate_checkpoint":
        path.write_text(
            '{"plan_id":' + json.dumps(plan.plan_id)
            + ',"state":"prepared","state":"rolled_back"}', encoding="utf-8"
        )
    elif damage == "unknown_registry":
        manager._registry_path.write_text('{"schema":"unknown","applied":{}}', encoding="utf-8")
    else:
        other = manager.plan(_BOTH_STEPS)
        manager._save_registry(applied={_BOTH_STEPS[0]: {"plan_id": other.plan_id}})
    before = {file: file.read_bytes() for file in (path, manager._registry_path)}
    called = []

    def save_project(command):
        called.append(command)
        return {"called": True}

    api = LocalAPI("migration-adapter", "workspace", {"save_project": save_project},
                   maintenance=manager.maintenance_reason)
    session = Session("component-seam", EntryKind.AGENT_RELAY)
    response = api.dispatch(
        Command(action="save_project", project_id="p", request_id="blocked-write",
                intent_id="blocked-intent", expected_revision=0, parameters={}),
        session,
    )
    assert response.error.code == "maintenance_required"
    assert called == []
    doctor = api.dispatch(Command(action="doctor", request_id="diagnostic"), session)
    assert doctor.error is None
    assert all(file.read_bytes() == content for file, content in before.items())


@pytest.mark.parametrize(
    "material",
    [
        b'{"schema":"aitest.migration-registry/1.0","applied":{},"applied":{}}',
        b'{"schema":"unknown","applied":{}}',
        b'{broken json',
    ],
)
def test_corrupt_registry_blocks_core_start_and_releases_lifetime_lock(
    manager: FileMigrationManager, material: bytes
) -> None:
    from aitest.bootstrap import CoreAssemblyBlocked, assemble_workspace_core

    manager._registry_path.write_bytes(material)
    with pytest.raises(CoreAssemblyBlocked, match="核心拒绝启动"):
        assemble_workspace_core(manager._root, instance_id="invalid-registry-core")
    assert manager._registry_path.read_bytes() == material
    lock = Workspace(manager._root).admit_lifetime()
    lock.release()


def test_migration_error_is_translated_during_core_assembly(manager: FileMigrationManager) -> None:
    from aitest.bootstrap import CoreAssemblyBlocked, assemble_workspace_core

    before = manager._registry_path.read_bytes()

    def reject(self, plan_id):
        raise MigrationError("unreadable migration material")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FileMigrationManager, "apply", reject)
        with pytest.raises(CoreAssemblyBlocked, match="提交材料迁移"):
            assemble_workspace_core(manager._root, instance_id="migration-error-core")
    assert manager._registry_path.read_bytes() == before
    lock = Workspace(manager._root).admit_lifetime()
    lock.release()


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
    workspace_file.write_text(json.dumps({"workspace_id": "ws-1"}), encoding="utf-8")
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
