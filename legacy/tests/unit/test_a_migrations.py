"""Unit tests for the file migration manager (infrastructure/file_store/migrations.py)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aitest.infrastructure.file_store.backup import FileBackupStore, manifest_facts
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


def _pending_second_step(manager: FileMigrationManager) -> tuple[str, Path]:
    plan_id = _plan(manager)
    report = manager.apply(plan_id)
    assert report.backup_path is not None
    registry_path = manager._dir / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["applied"].pop(_BOTH_STEPS[1])
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    return plan_id, report.backup_path


@pytest.mark.parametrize("kind", ["empty", "foreign", "partial"])
def test_unowned_backup_is_preserved_and_never_used_as_migration_prestate(
    manager: FileMigrationManager, kind: str
) -> None:
    root = manager._root
    (root / "records.json").write_text('{"records":{},"commit":0}', encoding="utf-8")
    before_workspace = (root / "workspace.json").read_bytes()
    before_records = (root / "records.json").read_bytes()
    plan_id = _plan(manager)
    unowned = manager._backups_dir / plan_id
    unowned.mkdir()
    if kind != "partial":
        files = {}
        if kind == "foreign":
            material = b'{"workspace_id":"another-workspace"}'
            (unowned / "workspace.json").write_bytes(material)
            files["workspace.json"] = hashlib.sha256(material).hexdigest()
        (unowned / "backup.json").write_text(
            json.dumps({"schema": "aitest.backup/1.0", "files": files, "excluded_active": []}),
            encoding="utf-8",
        )
        assert FileBackupStore(root).verify(unowned)["ok"]
    else:
        (unowned / "partial-output").write_bytes(b"retain unknown material")
    original = {p.name: p.read_bytes() for p in unowned.iterdir()}

    report = manager.apply(plan_id)

    assert report.backup_path is not None and report.backup_path != unowned
    assert FileBackupStore(root).verify(report.backup_path)["ok"]
    assert (report.backup_path / "workspace.json").read_bytes() == before_workspace
    assert (report.backup_path / "records.json").read_bytes() == before_records
    assert {p.name: p.read_bytes() for p in unowned.iterdir()} == original
    cycle = json.loads((manager._cycles_dir / f"{plan_id}.json").read_text(encoding="utf-8"))
    assert cycle["backup_manifest_digest"] == manifest_facts(report.backup_path)[0]


@pytest.mark.parametrize(
    "damage", ["changed", "missing", "legacy_digest", "cycle_missing", "invalid_manifest"]
)
def test_resume_cannot_replace_or_recapture_the_frozen_backup(
    manager: FileMigrationManager, damage: str
) -> None:
    plan_id, backup = _pending_second_step(manager)
    cycle_path = manager._cycles_dir / f"{plan_id}.json"
    if damage == "changed":
        path = backup / "workspace.json"
        path.write_bytes(b'{"workspace_id":"changed-after-freeze"}')
        manifest_path = backup / "backup.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["workspace.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        assert FileBackupStore(manager._root).verify(backup)["ok"]
    elif damage == "missing":
        backup.rename(manager._root / "original-backup-preserved")
    elif damage == "legacy_digest":
        cycle = json.loads(cycle_path.read_text(encoding="utf-8"))
        cycle.pop("backup_manifest_digest")
        cycle_path.write_text(json.dumps(cycle), encoding="utf-8")
    elif damage == "invalid_manifest":
        (backup / "backup.json").write_text("{torn", encoding="utf-8")
    else:
        cycle_path.rename(cycle_path.with_suffix(".preserved"))
    tracked = [manager._registry_path, manager._journal_path, manager._root / "workspace.json"]
    tracked += list(manager._steps_dir.rglob("*.json"))
    if cycle_path.exists():
        tracked.append(cycle_path)
    before = {path: path.read_bytes() for path in tracked}

    with pytest.raises(MigrationError, match="冻结|摘要|备份无法可靠核对"):
        FileMigrationManager(manager._root).resume(plan_id)

    assert {path: path.read_bytes() for path in tracked} == before
    if damage == "missing":
        assert not backup.exists()
        assert (manager._root / "original-backup-preserved" / "backup.json").is_file()


@pytest.mark.parametrize("name", ["..", "another-plan", "plan-72011b2348-invalid"])
def test_resume_rejects_backup_name_outside_its_exact_plan(
    manager: FileMigrationManager, name: str
) -> None:
    plan_id, backup = _pending_second_step(manager)
    cycle_path = manager._cycles_dir / f"{plan_id}.json"
    cycle = json.loads(cycle_path.read_text(encoding="utf-8"))
    cycle["backup_name"] = name
    cycle_path.write_text(json.dumps(cycle), encoding="utf-8")
    before_registry = manager._registry_path.read_bytes()
    before_journal = manager._journal_path.read_bytes()
    with pytest.raises(MigrationError, match="备份引用损坏"):
        manager.resume(plan_id)
    assert manager._registry_path.read_bytes() == before_registry
    assert manager._journal_path.read_bytes() == before_journal
    assert FileBackupStore(manager._root).verify(backup)["ok"]


def test_new_manager_resumes_with_the_original_frozen_backup(
    manager: FileMigrationManager,
) -> None:
    plan_id, backup = _pending_second_step(manager)
    before = manifest_facts(backup)
    resumed = FileMigrationManager(manager._root).resume(plan_id)
    assert resumed.executed == (_BOTH_STEPS[1],)
    assert resumed.backup_path == backup
    assert manifest_facts(backup) == before


def test_finished_rollback_before_cycle_publish_requires_a_new_backup(
    manager: FileMigrationManager,
) -> None:
    records = manager._root / "records.json"
    records.write_text('{"records":{},"commit":0}', encoding="utf-8")
    plan_id = _plan(manager)
    first = manager.apply(plan_id)
    assert first.backup_path is not None
    cycle_path = manager._cycles_dir / f"{plan_id}.json"
    active_cycle = cycle_path.read_bytes()
    manager.rollback(plan_id)
    # 模拟最后一个 rolled_back 已保存，但周期收尾写入前中断。
    cycle_path.write_bytes(active_cycle)
    records.write_text(
        '{"records":{"saved_after_rollback":{"retained":true}},"commit":1}',
        encoding="utf-8",
    )
    current_records = records.read_bytes()
    first_identity = manifest_facts(first.backup_path)

    second = FileMigrationManager(manager._root).apply(plan_id)

    assert second.backup_path is not None and second.backup_path != first.backup_path
    assert (second.backup_path / "records.json").read_bytes() == current_records
    assert manifest_facts(first.backup_path) == first_identity
    assert FileBackupStore(manager._root).verify(second.backup_path)["ok"]


@pytest.mark.parametrize(
    "damage",
    ["wrong_plan", "unknown_state", "structured_state", "wrong_step", "cycle_conflict",
     "structured_cycle"],
)
def test_controlled_resume_rejects_conflicting_checkpoint_identity(
    manager: FileMigrationManager, damage: str
) -> None:
    plan_id, _backup = _pending_second_step(manager)
    state_path = manager._steps_dir / plan_id / f"{_BOTH_STEPS[1]}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if damage == "wrong_plan":
        state["plan_id"] = "another-plan"
        state_path.write_text(json.dumps(state), encoding="utf-8")
    elif damage in {"unknown_state", "structured_state"}:
        state["state"] = "unknown" if damage == "unknown_state" else []
        state_path.write_text(json.dumps(state), encoding="utf-8")
    elif damage == "wrong_step":
        (state_path.parent / "unknown-step.json").write_text(json.dumps(state), encoding="utf-8")
    else:
        cycle_path = manager._cycles_dir / f"{plan_id}.json"
        cycle = json.loads(cycle_path.read_text(encoding="utf-8"))
        cycle["state"] = "rolled_back" if damage == "cycle_conflict" else {}
        cycle_path.write_text(json.dumps(cycle), encoding="utf-8")
    tracked = [manager._registry_path, manager._journal_path, manager._root / "workspace.json"]
    tracked += list(manager._steps_dir.rglob("*.json"))
    tracked += list(manager._cycles_dir.glob("*.json"))
    before = {path: path.read_bytes() for path in tracked}
    backup_names = sorted(path.name for path in manager._backups_dir.iterdir())
    with pytest.raises(MigrationError, match="检查点|冲突|周期"):
        FileMigrationManager(manager._root).resume(plan_id)
    assert {path: path.read_bytes() for path in tracked} == before
    assert sorted(path.name for path in manager._backups_dir.iterdir()) == backup_names


@pytest.mark.parametrize("status", [[], {}, False, None, 42])
def test_malformed_checkpoint_status_blocks_ordinary_writes_as_migration_error(
    manager: FileMigrationManager, status: object
) -> None:
    plan_id, _backup = _pending_second_step(manager)
    state_path = manager._steps_dir / plan_id / f"{_BOTH_STEPS[1]}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["state"] = status
    state_path.write_text(json.dumps(state), encoding="utf-8")
    original = state_path.read_bytes()
    with pytest.raises(MigrationError, match="检查点状态"):
        manager.maintenance_reason()
    assert state_path.read_bytes() == original


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
