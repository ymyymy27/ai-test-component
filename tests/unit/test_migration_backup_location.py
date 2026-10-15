"""Explicit migration backup location and frozen-cycle restart boundaries."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.file_store.maintenance import FileMaintenanceService
from aitest.infrastructure.file_store.migrations import FileMigrationManager, MigrationError
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.infrastructure.security import KnownSecretRegistry

STEPS = ("0001-workspace-schema-version", "0002-records-intents-container")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    Workspace(tmp_path)
    (tmp_path / "records.json").write_text('{"records":{},"commit":0}', encoding="utf-8")
    return tmp_path


def _outside(root: Path, suffix: str = "backups") -> Path:
    return root.parent / (root.name + "-" + suffix)


def _pending(root: Path, *, backup_root: Path | None) -> tuple[str, Path]:
    manager = FileMigrationManager(root, backup_root=backup_root)
    plan = manager.plan(STEPS).plan_id
    report = manager.apply(plan)
    assert report.backup_path is not None
    registry = root / "migrations/registry.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["applied"].pop(STEPS[1])
    registry.write_text(json.dumps(data), encoding="utf-8")
    return plan, report.backup_path


def test_explicit_location_is_outside_recorded_and_not_created_by_inspect(root: Path) -> None:
    outside = _outside(root)
    audit = FileMaintenanceService(root)
    manager = FileMigrationManager(root, backup_root=outside, maintenance=audit)
    manager.inspect()
    assert not outside.exists()
    original = (root / "records.json").read_bytes()
    report = manager.apply(manager.plan(STEPS).plan_id)
    assert report.backup_path is not None
    assert report.backup_path.is_relative_to(outside)
    assert not report.backup_path.is_relative_to(root)
    assert report.backup_outside_active_workspace is True
    assert FileBackupStore(root).verify(report.backup_path)["ok"]
    assert (report.backup_path / "records.json").read_bytes() == original
    fact, = audit.backup_facts()
    assert fact["destination"] == str(report.backup_path)
    assert fact["manifest_digest"].startswith("sha256:")


@pytest.mark.parametrize("configuration", ["same", "changed", "unset"])
def test_restart_uses_frozen_location_even_if_configuration_changes(
    root: Path, configuration: str
) -> None:
    outside = _outside(root)
    plan, backup = _pending(root, backup_root=outside)
    original = {p: p.read_bytes() for p in backup.rglob("*") if p.is_file()}
    configured = outside if configuration == "same" else (
        _outside(root, "new-location") if configuration == "changed" else None
    )
    resumed = FileMigrationManager(root, backup_root=configured).resume(plan)
    assert resumed.executed == (STEPS[1],)
    assert resumed.backup_path == backup
    assert resumed.backup_outside_active_workspace is True
    assert {p: p.read_bytes() for p in original} == original
    if configuration == "changed":
        assert configured is not None and not configured.exists()


def test_old_local_cycle_keeps_original_location_after_external_configuration(root: Path) -> None:
    plan, backup = _pending(root, backup_root=None)
    cycle = root / "migrations/cycles" / f"{plan}.json"
    assert "backup_path" not in json.loads(cycle.read_text(encoding="utf-8"))
    outside = _outside(root)
    resumed = FileMigrationManager(root, backup_root=outside).resume(plan)
    assert resumed.backup_path == backup
    assert resumed.backup_outside_active_workspace is False
    assert not outside.exists()


def test_completed_rollback_uses_new_configuration_without_moving_old_backup(root: Path) -> None:
    old = FileMigrationManager(root, backup_root=_outside(root, "old-location"))
    plan = old.plan(STEPS).plan_id
    first = old.apply(plan)
    old.rollback(plan)
    outside = _outside(root, "new-location")
    second = FileMigrationManager(root, backup_root=outside).apply(plan)
    assert first.backup_path is not None and second.backup_path is not None
    assert first.backup_path != second.backup_path
    assert second.backup_path.is_relative_to(outside)
    assert FileBackupStore(root).verify(first.backup_path)["ok"]
    assert FileBackupStore(root).verify(second.backup_path)["ok"]


def test_shared_backup_storage_separates_workspace_identities(root: Path) -> None:
    other = _outside(root, "other-workspace")
    Workspace(other)
    outside = _outside(root)
    one = FileMigrationManager(root, backup_root=outside)
    two = FileMigrationManager(other, backup_root=outside)
    first = one.apply(one.plan(STEPS).plan_id)
    second = two.apply(two.plan(STEPS).plan_id)
    assert first.backup_path is not None and second.backup_path is not None
    assert first.plan_id == second.plan_id
    assert first.backup_path.parent != second.backup_path.parent
    assert FileBackupStore(root).verify(first.backup_path)["ok"]
    assert FileBackupStore(other).verify(second.backup_path)["ok"]


@pytest.mark.parametrize("kind", ["relative", "active", "nested", "file", "missing_identity"])
def test_invalid_configuration_is_rejected_before_backup_or_cycle_write(
    root: Path, kind: str
) -> None:
    outside = _outside(root)
    if kind == "relative":
        configured = Path("relative-backups")
    elif kind == "active":
        configured = root
    elif kind == "nested":
        configured = root / "nested-backups"
    elif kind == "file":
        outside.write_bytes(b"keep this file")
        configured = outside
    else:
        (root / "workspace.json").write_text('{"workspace_id":null}', encoding="utf-8")
        configured = outside
    original = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(MigrationError):
        FileMigrationManager(root, backup_root=configured)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == original
    if kind != "file":
        assert not outside.exists()
    else:
        assert outside.read_bytes() == b"keep this file"


def test_known_credential_in_location_is_rejected_without_persistence(root, monkeypatch) -> None:
    from aitest.infrastructure import security

    credential = "fictional-location-credential"
    registry = KnownSecretRegistry()
    registry.register(credential)
    monkeypatch.setattr(security, "_GLOBAL_REGISTRY", registry)
    outside = _outside(root, credential)
    with pytest.raises(MigrationError, match="敏感材料") as error:
        FileMigrationManager(root, backup_root=outside)
    assert credential not in str(error.value)
    assert not outside.exists() and not (root / "migrations").exists()


def test_derived_namespace_secret_is_rejected_before_directory_creation(root, monkeypatch) -> None:
    from aitest.infrastructure import security

    identity = Workspace(root).workspace_id
    namespace = "workspace-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    registry = KnownSecretRegistry()
    registry.register(namespace)
    monkeypatch.setattr(security, "_GLOBAL_REGISTRY", registry)
    outside = _outside(root)
    with pytest.raises(MigrationError, match="敏感材料"):
        FileMigrationManager(root, backup_root=outside)
    assert not outside.exists() and not (root / "migrations").exists()


@pytest.mark.parametrize("ancestor", [False, True])
def test_windows_junction_configuration_cannot_write_through_alias(root, ancestor) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows junction check")
    target = _outside(root, "target")
    target.mkdir()
    link = _outside(root, "junction")
    created = subprocess.run(  # noqa: S603 - fixed creation command, owned temp paths
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],  # noqa: S607
        capture_output=True,
    )
    assert created.returncode == 0, created.stderr
    try:
        configured = link / "backups" if ancestor else link
        with pytest.raises(MigrationError, match="无链接"):
            FileMigrationManager(root, backup_root=configured)
        assert list(target.iterdir()) == []
    finally:
        assert link.absolute().parent == root.parent.absolute()
        link.rmdir()  # removes the junction itself, never its target


@pytest.mark.parametrize("damage", ["relative", "foreign_namespace", "inside", "missing"])
def test_frozen_external_location_cannot_be_redirected_or_recreated(root, damage) -> None:
    plan, backup = _pending(root, backup_root=_outside(root))
    cycle_path = root / "migrations/cycles" / f"{plan}.json"
    cycle = json.loads(cycle_path.read_text(encoding="utf-8"))
    if damage == "relative":
        cycle["backup_path"] = f"relative/{plan}"
    elif damage == "foreign_namespace":
        cycle["backup_path"] = str(_outside(root, "foreign") / "workspace-other" / plan)
    elif damage == "inside":
        cycle["backup_path"] = str(root / "migrations/backups" / plan)
    else:
        saved = backup.with_name(backup.name + "-preserved")
        assert backup.resolve().is_relative_to(_outside(root).resolve())
        assert saved.resolve().is_relative_to(_outside(root).resolve())
        backup.rename(saved)
    cycle_path.write_text(json.dumps(cycle), encoding="utf-8")
    registry = root / "migrations/registry.json"
    journal = root / "migrations/journal.jsonl"
    before = {p: p.read_bytes() for p in (cycle_path, registry, journal)}
    with pytest.raises(MigrationError):
        FileMigrationManager(root).resume(plan)
    assert {p: p.read_bytes() for p in before} == before
    if damage == "missing":
        assert not backup.exists() and (saved / "backup.json").is_file()


@pytest.mark.parametrize("selection", ["environment", "explicit", "unset"])
def test_bootstrap_passes_the_selected_configuration_before_heavy_assembly(
    root, monkeypatch, selection
) -> None:
    from aitest import bootstrap

    environment = _outside(root, "environment")
    explicit = _outside(root, "explicit") if selection == "explicit" else None
    if selection == "unset":
        monkeypatch.delenv("AITEST_MIGRATION_BACKUP_ROOT", raising=False)
    else:
        monkeypatch.setenv("AITEST_MIGRATION_BACKUP_ROOT", str(environment))
    observed = []

    def probe(path, **kwargs):
        observed.append(kwargs["backup_root"])
        raise MigrationError("configuration probe stops before heavy assembly")

    monkeypatch.setattr(bootstrap, "FileMigrationManager", probe)
    with pytest.raises(bootstrap.CoreAssemblyBlocked, match="迁移无法核实"):
        bootstrap.assemble_workspace_core(
            root, instance_id="configuration-probe", migration_backup_root=explicit
        )
    expected = explicit if selection == "explicit" else (
        environment if selection == "environment" else None
    )
    assert observed == [expected]
    lock = Workspace(root).admit_lifetime()
    lock.release()


def test_worker_cli_passes_explicit_location_without_starting_a_core(root, monkeypatch) -> None:
    from aitest import bootstrap
    from aitest.interfaces.local import core_worker

    outside = _outside(root)
    observed = []

    def probe(path, **kwargs):
        observed.append(kwargs["migration_backup_root"])
        raise bootstrap.CoreAssemblyBlocked("configuration probe only")

    monkeypatch.setattr(bootstrap, "assemble_workspace_core", probe)
    monkeypatch.setattr(core_worker.sys, "platform", "win32")
    assert core_worker.main([
        "--workspace-root", str(root), "--workspace-id", "workspace", "--instance-id", "core",
        "--migration-backup-root", str(outside),
    ]) == 5
    assert observed == [outside]
    assert not outside.exists()
