"""Authority migration and bounded read/write evidence on the real filesystem."""

import json
from pathlib import Path

import pytest

from aitest.application.planning.substrate import RecordQuery
from aitest.infrastructure.file_store import atomic
from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.file_store.integrity import check_workspace
from aitest.infrastructure.file_store.migrations import FileMigrationManager, MigrationError
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.sharded_records import (
    SCHEMA,
    AuthorityTree,
    materialize_legacy,
    migrate_to_shards,
)
from aitest.infrastructure.file_store.workspace import Workspace


def _legacy(root: Path, count: int = 80) -> dict:
    workspace_id = Workspace(root).workspace_id
    records = {
        f"record-{index}": [{"project_id": "project-a", "name": f"name-{index}",
                             "large_detail": "x" * 16384}]
        for index in range(count)
    }
    commits = [{"commit_sequence": index + 1, "project_id": "project-a",
                "workspace_id": workspace_id,
                "request_id": f"request-{index}", "intent_id": f"intent-{index}",
                "created": [{"aggregate_kind": "project", "record_id": f"record-{index}",
                             "revision": 1}], "state": "committed"}
               for index in range(count)]
    legacy = {"records": {"project": records}, "commits": commits, "commit": count,
              "intents": {"old-intent": {"commit_sequence": 1}},
              "legacy_metadata": {"keep": "原始字段"}}
    atomic.write_json(root / "records.json", legacy)
    return legacy


def _migrate(root: Path) -> tuple[FileMigrationManager, str]:
    manager = FileMigrationManager(root)
    plan = manager.plan(("0003-sharded-record-authority",))
    report = manager.apply(plan.plan_id)
    assert report.state == "applied"
    assert report.backup_path is not None
    assert FileBackupStore(root).verify(report.backup_path)["ok"]
    return manager, plan.plan_id


def test_explicit_maintenance_rebuild_advances_generation_and_keeps_queries(tmp_path):
    _legacy(tmp_path, count=4)
    _migrate(tmp_path)
    repo = FileRecordRepository(tmp_path)
    generation = repo.rebuild_query_index()
    after = repo.query(RecordQuery(project_id="project-a"))
    assert after.status == "ok" and len(after.items) == 4
    assert generation >= 1


def test_explicit_rebuild_refuses_workspace_without_committed_entries(tmp_path):
    Workspace(tmp_path)
    with pytest.raises(ValueError, match="no commit"):
        FileRecordRepository(tmp_path).rebuild_query_index()


def test_migration_preserves_every_revision_intent_commit_owner_and_unknown_field(tmp_path):
    legacy = _legacy(tmp_path)
    manager, plan = _migrate(tmp_path)
    header = json.loads((tmp_path / "records.json").read_text(encoding="utf-8"))
    assert header["schema"] == SCHEMA
    assert materialize_legacy(tmp_path, header) == legacy
    repo = FileRecordRepository(tmp_path)
    assert repo.read(aggregate_kind="project", record_id="record-2", revision=1).payload == (
        legacy["records"]["project"]["record-2"][0]
    )
    assert repo.find_committed_request(
        project_id="project-a", request_id="request-2", intent_id="intent-2"
    ) == legacy["commits"][2]
    assert manager.rollback(plan).state == "rolled_back"
    assert json.loads((tmp_path / "records.json").read_text(encoding="utf-8")) == legacy


def test_migration_publication_failure_resumes_original_before_state(tmp_path, monkeypatch):
    legacy = _legacy(tmp_path, 8)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0003-sharded-record-authority",))
    original = atomic.write_json

    def fail_root(path, value):
        if path == tmp_path / "records.json" and value.get("schema") == SCHEMA:
            raise OSError("injected migration root failure")
        original(path, value)

    monkeypatch.setattr(atomic, "write_json", fail_root)
    with pytest.raises(OSError, match="root failure"):
        manager.apply(plan.plan_id)
    assert json.loads((tmp_path / "records.json").read_text(encoding="utf-8")) == legacy
    monkeypatch.setattr(atomic, "write_json", original)
    assert manager.resume(plan.plan_id).state == "applied"
    manager.rollback(plan.plan_id)
    assert json.loads((tmp_path / "records.json").read_text(encoding="utf-8")) == legacy


def test_backup_restores_sharded_authority_and_integrity_detects_missing_node(tmp_path):
    root = tmp_path / "source"
    _legacy(root, 4)
    _migrate(root)
    backup = tmp_path / "backup"
    FileBackupStore(root).create(backup)
    target = tmp_path / "restored"
    assert FileBackupStore(root).restore(backup=backup, target=target).state == "restored"
    assert FileRecordRepository(target).current_revision("project", "record-2") == 1
    assert check_workspace(target)["ok"]
    header = json.loads((target / "records.json").read_text(encoding="utf-8"))
    (target / "record-store" / f"{header['root']}.json").unlink()
    result = check_workspace(target)
    assert not result["ok"]
    assert any("authority reference closure" in error for error in result["errors"])


def test_legacy_owner_conflict_leaves_authority_unchanged(tmp_path):
    legacy = _legacy(tmp_path, 4)
    legacy["records"]["project"]["record-2"].append({"project_id": "project-b"})
    atomic.write_json(tmp_path / "records.json", legacy)
    before = (tmp_path / "records.json").read_bytes()
    with pytest.raises(ValueError, match="conflicting project ownership"):
        migrate_to_shards(tmp_path)
    assert (tmp_path / "records.json").read_bytes() == before


@pytest.mark.parametrize("corrupt", [None, "x" * 64, "empty_tree"])
def test_corrupt_authority_root_never_becomes_an_empty_workspace(tmp_path, corrupt):
    _legacy(tmp_path, 2)
    _migrate(tmp_path)
    path = tmp_path / "records.json"
    header = json.loads(path.read_text(encoding="utf-8"))
    header["root"] = (
        AuthorityTree(tmp_path)._write({"entries": {}}) if corrupt == "empty_tree" else corrupt
    )
    atomic.write_json(path, header)
    before = path.read_bytes()
    repo = FileRecordRepository(tmp_path)
    with pytest.raises(ValueError, match="root (header|commit)"):
        repo.current_revision("project", "record-1")
    with pytest.raises(ValueError, match="root (header|commit)"):
        repo.find_committed_request(
            project_id="project-a", request_id="request-1", intent_id="intent-1"
        )
    assert not check_workspace(tmp_path)["ok"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("corrupt", [None, True, -1, "invalid_json"])
def test_unknown_business_watermark_prevents_migration_without_changing_authority(
    tmp_path, corrupt,
):
    legacy = _legacy(tmp_path, 2)
    path = tmp_path / "records.json"
    if corrupt == "invalid_json":
        path.write_text('{"records":', encoding="utf-8")
    else:
        legacy["commit"] = corrupt
        atomic.write_json(path, legacy)
    before = path.read_bytes()
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0003-sharded-record-authority",))
    with pytest.raises(MigrationError, match="水位"):
        manager.apply(plan.plan_id)
    assert path.read_bytes() == before
    assert not (tmp_path / "record-store").exists()
