"""Real file boundaries, fault injection and frozen source/result identity checks."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.queries import QuerySpec
from aitest.infrastructure import security
from aitest.infrastructure.file_store.commit_manifest import CommitMaterialError, FileCommitStore
from aitest.infrastructure.file_store.events import EventMaintenanceRequired, FileEventJournal
from aitest.infrastructure.file_store.index import FileQueryIndex
from aitest.infrastructure.file_store.migrations import FileMigrationManager, MigrationError
from aitest.infrastructure.file_store.ordered_events import OrderedEventStore
from aitest.infrastructure.file_store.publication_backend import FilePublicationBackend
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.sharded_records import AuthorityTree
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.file_store.workspace import Workspace


def legacy_workspace(root: Path):
    workspace = Workspace(root)
    journal = FileEventJournal(root, instance_id=workspace.workspace_id)
    manager = FileMigrationManager(root)
    plan = manager.plan(("0003-sharded-record-authority", "0004-bounded-query-directory"))
    manager.apply(plan.plan_id)
    unit = FileUnitOfWork(root, journal=journal)
    unit.begin("legacy", "project", intent_id="legacy")
    unit.stage_record(
        aggregate_kind="case",
        record_id="old",
        expected_revision=0,
        payload={"project_id": "project", "summary": "old"},
    )
    unit.commit("legacy")
    return manager, journal


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    identity = Workspace(root)
    FileEventJournal(root, instance_id=identity.workspace_id)
    migrations = FileMigrationManager(root)
    plan = migrations.plan(
        (
            "0003-sharded-record-authority",
            "0004-bounded-query-directory",
            "0005-complete-commit-closure",
        )
    )
    result = migrations.apply(plan.plan_id)
    assert result.state == "applied"
    assert result.backup_path is not None
    assert FileCommitStore(root).read_current(verify_material=True) is not None
    return root


def commit(root: Path, records: tuple[str, ...], *, request: str, intent: str | None = None):
    journal = FileEventJournal(root, instance_id="reader-or-new-core")
    unit = FileUnitOfWork(root, journal=journal)
    unit.begin(request, "project", intent_id=intent)
    for record in records:
        unit.stage_record(
            aggregate_kind="case",
            record_id=record,
            expected_revision=unit.current_revision(aggregate_kind="case", record_id=record),
            payload={"project_id": "project", "summary": record},
        )
    return unit, unit.commit(request)


def facts(root: Path, record: str):
    repository = FileRecordRepository(root)
    journal = FileEventJournal(root, instance_id="new-reader")
    result = FileQueryIndex(root, journal=journal).query_spec(
        QuerySpec(
            project_id="project",
            aggregate_kind="case",
            record_id=record,
            limit=1,
        )
    )
    return (
        repository.current_commit_sequence(),
        repository.current_revision("case", record),
        result.status,
        len(result.items),
        result.commit_id,
        result.event_cursor,
        tuple((e.record_id, e.commit_sequence, e.event_sequence) for e in journal.read().events),
    )


def test_shared_root_publishes_multirecord_intent_query_and_events(workspace: Path) -> None:
    _, result = commit(workspace, ("one", "two"), request="request", intent="intent")
    assert result["commit_sequence"] == 2
    proof = facts(workspace, "two")
    assert proof[:5] == (2, 1, "ok", 1, 2)
    assert proof[5] is not None
    assert proof[6] == (("one", 2, 1), ("two", 2, 2))
    assert FileCommitStore(workspace).read_current(verify_material=True) is not None
    # Old independent pointers remain historical material; readers use current.
    assert json.loads((workspace / "records.json").read_text())["commit"] == 0


@pytest.mark.parametrize(
    "seam",
    [
        "authority",
        "index",
        "events",
        "manifest",
        "publish",
        "replace",
    ],
)
def test_failure_before_root_switch_keeps_complete_old_boundary_and_later_commit(
    workspace: Path,
    seam: str,
) -> None:
    commit(workspace, ("first",), request="first", intent="first")
    old_pointer = (workspace / "current.json").read_bytes()
    targets = {
        "authority": (AuthorityTree, "_write"),
        "index": (FileQueryIndex, "publish"),
        "events": (OrderedEventStore, "prepare"),
        "manifest": (FileCommitStore, "prepare"),
        "publish": (FileCommitStore, "publish"),
        "replace": (FilePublicationBackend, "replace_current"),
    }
    owner, name = targets[seam]
    with (
        patch.object(
            owner,
            name,
            side_effect=OSError("controlled material preparation failure"),
        ),
        pytest.raises(OSError),
    ):
        commit(workspace, ("lost",), request="lost", intent="lost")
    assert (workspace / "current.json").read_bytes() == old_pointer
    proof = facts(workspace, "lost")
    assert proof[:5] == (1, 0, "ok", 0, 1)
    assert proof[5] is not None and proof[6] == (("first", 1, 1),)
    commit(workspace, ("third",), request="third", intent="third")
    assert facts(workspace, "lost")[:5] == (2, 0, "ok", 0, 2)
    # The failed intent can now be submitted and must create exactly one fact.
    commit(workspace, ("lost",), request="retry", intent="lost")
    proof = facts(workspace, "lost")
    assert proof[:5] == (3, 1, "ok", 1, 3)
    assert proof[6] == (("first", 1, 1), ("third", 2, 2), ("lost", 3, 3))


def test_lost_ack_after_switch_resolves_original_intent_without_second_revision(
    workspace: Path,
) -> None:
    original = FileCommitStore.publish
    unit = FileUnitOfWork(workspace)
    unit.begin("request", "project", intent_id="intent")
    unit.stage_record(
        aggregate_kind="case",
        record_id="one",
        expected_revision=0,
        payload={"project_id": "project", "summary": "one"},
    )

    def publish_then_lose_ack(self, digest):
        original(self, digest)
        raise OSError("controlled lost acknowledgement")

    with patch.object(FileCommitStore, "publish", publish_then_lose_ack), pytest.raises(OSError):
        unit.commit("request")
    assert unit.rollback("request")["state"] == "committed"
    _, retry = commit(workspace, ("one",), request="another-entry", intent="intent")
    assert retry["commit_sequence"] == 1
    assert facts(workspace, "one")[:5] == (1, 1, "ok", 1, 1)
    assert len(FileEventJournal(workspace, instance_id="new").read().events) == 1


@pytest.mark.parametrize("material", ["pointer", "manifest", "index", "event", "record"])
def test_corrupt_published_material_is_never_replaced_with_legacy_success(
    workspace: Path,
    material: str,
) -> None:
    commit(workspace, ("one",), request="one")
    store = FileCommitStore(workspace)
    current = store.read_current(verify_material=True)
    assert current is not None
    manifest = current["manifest"]
    path = {
        "pointer": workspace / "current.json",
        "manifest": workspace / "manifests" / f"{current['pointer']['manifest_digest']}.json",
        "index": workspace / "indexes/roots" / f"{manifest['index_root']['snapshot_root']}.json",
        "event": workspace / "event-log/pages" / manifest["event_root"]["root"]["file"],
        "record": workspace / "record-store" / f"{manifest['record_header']['root']}.json",
    }[material]
    path.write_bytes(b"{}\n")
    before = (workspace / "current.json").read_bytes()
    with pytest.raises((CommitMaterialError, ValueError)):
        store.read_current(verify_material=True)
    assert (workspace / "current.json").read_bytes() == before
    if material in {"pointer", "manifest", "index"}:
        assert (
            FileQueryIndex(workspace)
            .query_spec(
                QuerySpec(
                    project_id="project",
                    aggregate_kind="case",
                )
            )
            .status
            == "maintenance_required"
        )
    if material in {"pointer", "manifest", "event"}:
        assert (
            OrderedEventStore(workspace).read(cursor=None, limit=10).status
            == "maintenance_required"
        )


def test_old_query_snapshot_keeps_matching_event_boundary_across_new_commit(workspace: Path):
    commit(workspace, ("one", "two", "three"), request="first")
    journal = FileEventJournal(workspace, instance_id="reader")
    index = FileQueryIndex(workspace, journal=journal)
    first = index.query_spec(QuerySpec(project_id="project", aggregate_kind="case", limit=1))
    assert first.next_cursor is not None and first.event_cursor is not None
    commit(workspace, ("four",), request="second")
    second = index.query_spec(
        QuerySpec(project_id="project", aggregate_kind="case", limit=1, cursor=first.next_cursor)
    )
    assert second.status == "ok" and second.commit_id == 3
    assert second.items[0]["record_id"] != first.items[0]["record_id"]
    assert second.event_cursor == first.event_cursor
    events = journal.read(cursor=first.event_cursor)
    assert [event.record_id for event in events.events] == ["four"]


@pytest.mark.parametrize(
    "change", ["workspace", "generation", "event_generation", "bool", "future"]
)
def test_event_cursor_rejects_wrong_identity_generation_and_future(workspace: Path, change: str):
    commit(workspace, ("one",), request="one")
    journal = FileEventJournal(workspace, instance_id="reader")
    cursor = journal.snapshot_cursor(commit_sequence=1)
    assert cursor is not None
    body = json.loads(base64.urlsafe_b64decode(cursor))
    key, value = {
        "workspace": ("w", "other"),
        "generation": ("g", "other"),
        "event_generation": ("e", body["e"] + 1),
        "bool": ("s", True),
        "future": ("s", 100),
    }[change]
    body[key] = value
    token = base64.urlsafe_b64encode(json.dumps(body).encode()).decode()
    assert journal.read(cursor=token).status == "invalid_cursor"


def test_unpublished_new_root_cannot_overwrite_a_later_complete_commit(workspace: Path):
    store = FileCommitStore(workspace)
    current = store.read_current()
    assert current is not None
    candidate = dict(current["manifest"])
    candidate.update(operation="maintenance", parent_manifest=current["pointer"]["manifest_digest"])
    digest = store.prepare(candidate)
    commit(workspace, ("one",), request="one")
    saved = (workspace / "current.json").read_bytes()
    with pytest.raises(CommitMaterialError, match="different current root"):
        store.publish(digest)
    assert (workspace / "current.json").read_bytes() == saved


def test_verified_legacy_migration_preserves_event_bytes_and_invalidates_old_query_cursor(tmp_path):
    manager, journal = legacy_workspace(tmp_path / "legacy")
    root = journal._root
    record_bytes = (root / "records.json").read_bytes()
    event_bytes = (root / "event-log/journal.jsonl").read_bytes()
    event = journal.read().events[0]
    generation = FileQueryIndex(root)._read_raw()["generation"]
    plan = manager.plan(("0005-complete-commit-closure",))
    report = manager.apply(plan.plan_id)
    assert report.backup_path is not None
    assert (report.backup_path / "records.json").read_bytes() == record_bytes
    assert (report.backup_path / "event-log/journal.jsonl").read_bytes() == event_bytes
    assert (root / "records.json").read_bytes() == record_bytes
    assert (root / "event-log/journal.jsonl").read_bytes() == event_bytes
    assert journal.read().events == (event,)
    assert FileQueryIndex(root)._read_raw()["generation"] == generation + 1
    commit(root, ("new",), request="new")
    assert facts(root, "old")[:5] == (2, 1, "ok", 1, 2)


@pytest.mark.parametrize("damage", ["missing", "foreign_workspace", "foreign_record", "extra"])
def test_unproved_legacy_events_block_migration_with_verified_backup(tmp_path, damage):
    manager, journal = legacy_workspace(tmp_path / "legacy")
    path = journal._journal
    event = json.loads(path.read_text())
    if damage == "missing":
        path.write_bytes(b"")
    elif damage == "extra":
        path.write_text(json.dumps(event) + "\n" + json.dumps(event) + "\n", encoding="utf-8")
    else:
        event["workspace_id" if damage == "foreign_workspace" else "record_id"] = "foreign"
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    damaged = path.read_bytes()
    record_bytes = (journal._root / "records.json").read_bytes()
    plan = manager.plan(("0005-complete-commit-closure",))
    with pytest.raises(CommitMaterialError):
        manager.apply(plan.plan_id)
    assert not (journal._root / "current.json").exists()
    assert path.read_bytes() == damaged
    assert (journal._root / "records.json").read_bytes() == record_bytes
    assert list((journal._root / "migrations/backups").rglob("backup.json"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", "other"),
        ("commit_sequence", True),
        ("commit_sequence", -1),
        ("writer_epoch", True),
        ("writer_epoch", 0),
        ("operation", []),
        ("operation", "unknown"),
        ("parent_manifest", "../unsafe"),
        ("created", "bad"),
        ("record_header", {}),
        ("index_root", {}),
        ("event_root", {}),
    ],
)
def test_invalid_manifest_material_is_rejected_before_publication(workspace, field, value):
    store = FileCommitStore(workspace)
    current = store.read_current()
    candidate = dict(current["manifest"])
    candidate[field] = value
    saved = (workspace / "current.json").read_bytes()
    with pytest.raises(CommitMaterialError):
        store.prepare(candidate)
    assert (workspace / "current.json").read_bytes() == saved


@pytest.mark.parametrize("position", ["record_id", "request_id", "intent_id", "authority_key"])
def test_known_credentials_in_identity_never_reach_prepared_files(workspace, monkeypatch, position):
    registry = security.KnownSecretRegistry()
    secret = "fictional-commit-credential"
    registry.register(secret)
    monkeypatch.setattr(security, "_GLOBAL_REGISTRY", registry)
    saved = (workspace / "current.json").read_bytes()
    if position == "authority_key":
        with pytest.raises(ValueError):
            AuthorityTree(workspace).put(secret, "safe")
    else:
        unit = FileUnitOfWork(workspace)
        unit.begin(
            secret if position == "request_id" else "request",
            "project",
            intent_id=secret if position == "intent_id" else "intent",
        )
        unit.stage_record(
            aggregate_kind="case",
            record_id=secret if position == "record_id" else "one",
            expected_revision=0,
            payload={"project_id": "project"},
        )
        with pytest.raises(ValueError):
            unit.commit(unit.request_id)
        assert unit.rollback(unit.request_id)["state"] == "rolled_back"
    assert (workspace / "current.json").read_bytes() == saved
    for path in workspace.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_multiple_cached_workspace_instances_advance_the_saved_writer_epoch(workspace):
    earlier, later = Workspace(workspace), Workspace(workspace)
    start = earlier.identity["writer_epoch"]
    with earlier.acquire():
        assert earlier.identity["writer_epoch"] == start + 1
    with later.acquire():
        assert later.identity["writer_epoch"] == start + 2
    with earlier.acquire():
        assert earlier.identity["writer_epoch"] == start + 3


def test_default_core_restart_keeps_the_same_complete_commit(workspace):
    first = assemble_workspace_core(workspace, instance_id="core-one")
    try:
        commit(workspace, ("one", "two"), request="request", intent="intent")
        old = facts(workspace, "one")
        saved = (workspace / "current.json").read_bytes()
    finally:
        first.lifetime_lock.release()
    second = assemble_workspace_core(workspace, instance_id="core-two")
    try:
        assert second.recovery.state == "healthy"
        assert (workspace / "current.json").read_bytes() == saved
        assert facts(workspace, "one") == old
        assert FileMigrationManager(workspace).inspect()["records_commit"] == 2
        _, retry = commit(workspace, ("one", "two"), request="other-entry", intent="intent")
        assert retry["commit_sequence"] == 2
        assert facts(workspace, "one") == old
    finally:
        second.lifetime_lock.release()


@pytest.mark.parametrize("cut", ["before", "after"])
def test_real_process_exit_around_root_switch_preserves_exact_commit(workspace, cut):
    script = """
import os,sys
from pathlib import Path
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
original=FileCommitStore.publish
def interrupt(self,digest):
    if sys.argv[2]=='after': original(self,digest)
    os._exit(77)
FileCommitStore.publish=interrupt
unit=FileUnitOfWork(Path(sys.argv[1]))
unit.begin('request','project',intent_id='intent')
unit.stage_record(aggregate_kind='case',record_id='one',expected_revision=0,
                  payload={'project_id':'project','summary':'one'})
unit.commit('request')
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script, str(workspace), cut],
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 77, result.stderr.decode(errors="replace")
    proof = facts(workspace, "one")
    expected = 0 if cut == "before" else 1
    assert proof[:5] == (expected, expected, "ok", expected, expected)
    assert proof[5] is not None
    assert len(proof[6]) == expected
    restart = assemble_workspace_core(workspace, instance_id="restart")
    try:
        assert restart.recovery.state == "healthy"
        assert facts(workspace, "one") == proof
        _, retried = commit(workspace, ("one",), request="retry", intent="intent")
        assert retried["commit_sequence"] == 1
        assert len(facts(workspace, "one")[6]) == 1
    finally:
        restart.lifetime_lock.release()


def test_shared_root_blocks_independent_rollback_of_old_record_format(workspace):
    manager = FileMigrationManager(workspace)
    old = manager.plan(("0003-sharded-record-authority",))
    pointer = (workspace / "current.json").read_bytes()
    with pytest.raises(MigrationError, match="完整提交根"):
        manager.rollback(old.plan_id)
    assert (workspace / "current.json").read_bytes() == pointer


def test_two_publishers_serialize_parent_check_and_only_one_can_switch(workspace):
    store = FileCommitStore(workspace)
    current = store.read_current()
    candidate = dict(current["manifest"])
    candidate.update(operation="maintenance", parent_manifest=current["pointer"]["manifest_digest"])
    digest = store.prepare(candidate)

    def publish():
        try:
            FileCommitStore(workspace).publish(digest)
            return True
        except (CommitMaterialError, RuntimeError):
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert sorted(results) == [False, True]
    assert (
        FileCommitStore(workspace).read_current(verify_material=True)["pointer"]["manifest_digest"]
        == digest
    )
    commit(workspace, ("one",), request="one")
    assert facts(workspace, "one")[:5] == (1, 1, "ok", 1, 1)


@pytest.mark.parametrize("change", ["different_identity", "different_body", "exact_same"])
def test_legacy_event_recovery_compares_saved_identity_and_whole_body(tmp_path, change):
    identity = Workspace(tmp_path)
    journal = FileEventJournal(tmp_path, instance_id="legacy-core")
    events = []
    for number in (1, 2):
        journal.begin_boundary(
            commit_sequence=number,
            request_id=f"r{number}",
            intent_id=None,
            workspace_id=identity.workspace_id,
            project_id="project",
            writer_epoch=1,
        )
        events.append(
            journal.record_event(
                commit_sequence=number,
                event_type="record_created",
                project_id="project",
                aggregate_kind="case",
                record_id=f"record-{number}",
                revision=1,
                request_id=f"r{number}",
                intent_id=None,
                workspace_id=identity.workspace_id,
                writer_epoch=1,
            )
        )
        if number == 1:
            journal.commit_boundary(commit_sequence=1)
    saved = events[1]
    if change != "exact_same":
        saved = saved.model_copy(
            update={
                "event_id" if change == "different_identity" else "request_id": "foreign",
            }
        )
    with journal._journal.open("ab") as handle:
        handle.write(journal._serialize(saved))
    before = journal._journal.read_bytes()
    staging = journal._staging_path(2).read_bytes()
    if change == "exact_same":
        report = journal.reconcile(committed_sequences={1, 2})
        assert report.completed_boundaries == (2,)
        assert journal.read().events == tuple(events)
    else:
        with pytest.raises(EventMaintenanceRequired, match="different saved material"):
            journal.reconcile(committed_sequences={1, 2})
        assert journal._staging_path(2).read_bytes() == staging
        assert journal._load_boundary(2) is None
    assert journal._journal.read_bytes() == before
