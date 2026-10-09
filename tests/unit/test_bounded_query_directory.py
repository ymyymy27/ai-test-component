"""Bounded directory/identity lookup, pinned roots and fault publication evidence."""

import json
import random
import uuid
from pathlib import Path

import pytest

from aitest.contracts.queries import QuerySpec
from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.file_store import atomic, ordered_index
from aitest.infrastructure.file_store.index import (
    _ALL_FAMILIES,
    _POINT_FAMILY,
    FileQueryIndex,
    IndexMissing,
    _key_cmp,
)
from aitest.infrastructure.file_store.migrations import FileMigrationManager, MigrationError
from aitest.infrastructure.file_store.ordered_index import OrderedIndexTree


def _row(number, *, project="p", kind="case", revision=1, sequence=None):
    result = dict(
        project_id=project,
        aggregate_kind=kind,
        record_id=f"r{number:05}",
        revision=revision,
        commit_sequence=sequence or number + 1,
    )
    if kind == "report":
        result.update(
            report_id=result["record_id"],
            content_revision=revision,
            published_sequence=result["commit_sequence"],
            business_outcome="passed",
        )
    if kind == "issue":
        result.update(
            updated_sequence=result["commit_sequence"],
            issue_index_entries=[
                dict(view="ALL", mask=0),
                dict(view="OPEN", mask=0),
            ],
        )
    return result


def test_page_and_commit_do_not_read_whole_directory_history(tmp_path, monkeypatch):
    index = FileQueryIndex(tmp_path, shard_size=8)
    index.rebuild([_row(i) for i in range(8)] + [_row(i, project="unrelated") for i in range(2048)])
    reads, writes = [], []
    original_text, original_bytes = Path.read_text, Path.read_bytes
    original_write = atomic.write_json

    def record_read(path):
        if path.is_relative_to(tmp_path):
            reads.append((path, path.stat().st_size))

    def read_text(path, *args, **kwargs):
        record_read(path)
        return original_text(path, *args, **kwargs)

    def read_bytes(path, *args, **kwargs):
        record_read(path)
        return original_bytes(path, *args, **kwargs)

    def write_json(path, value, **kwargs):
        writes.append((path, len(json.dumps(value).encode())))
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    original_material = ordered_index.read_material_bytes

    def read_material(path, maximum):
        raw = original_material(path, maximum)
        if path.is_relative_to(tmp_path):
            reads.append((path, len(raw)))
        return raw

    monkeypatch.setattr(ordered_index, "read_material_bytes", read_material)
    monkeypatch.setattr(atomic, "write_json", write_json)
    page = index.query_spec(QuerySpec(project_id="p", limit=5))
    assert page.status == "ok" and len(page.items) == 5
    assert sum(size for _, size in reads) < 40_000, reads
    reads.clear()
    index.publish([_row(9000, sequence=3000)], commit_sequence=3000)
    assert max(size for _, size in reads) < 24_000, reads
    assert max(size for _, size in writes) < 24_000, writes
    assert sum(size for _, size in reads) < 150_000, reads


@pytest.mark.parametrize("kind", ["report", "issue"])
def test_corrupt_current_key_ledger_cannot_be_treated_as_empty(tmp_path, kind):
    index = FileQueryIndex(tmp_path, shard_size=8)
    original = _row(1, kind=kind)
    index.rebuild([original])
    # Both old and new formats have a published ledger. Corrupt its authority,
    # rather than an unrelated abandoned file left by an interrupted writer.
    header = json.loads((tmp_path / "indexes.json").read_text())
    snapshot = json.loads(
        (tmp_path / "indexes/roots" / f"{header['snapshot_root']}.json").read_text()
    )
    ledger = snapshot.get("latest_keys")
    if ledger is None:
        path = tmp_path / "indexes/.latest-keys.json"
    else:
        path = tmp_path / "indexes/latest-keys" / f"{ledger['root']['file']}"
    path.write_text("{corrupt", encoding="utf-8")
    root_before = (tmp_path / "indexes.json").read_bytes()
    with pytest.raises(IndexMissing):
        index.publish([_row(1, kind=kind, revision=2, sequence=3)], commit_sequence=3)
    assert (tmp_path / "indexes.json").read_bytes() == root_before


@pytest.mark.parametrize("kind", ["report", "issue"])
def test_current_identity_update_reads_only_its_paths(tmp_path, monkeypatch, kind):
    index = FileQueryIndex(tmp_path, shard_size=8)
    index.rebuild([_row(i, kind=kind) for i in range(256)])
    reads = []
    original = Path.read_bytes

    def read_bytes(path, *args, **kwargs):
        if path.is_relative_to(tmp_path / "indexes"):
            reads.append((path, path.stat().st_size))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    original_material = ordered_index.read_material_bytes

    def read_material(path, maximum):
        raw = original_material(path, maximum)
        if path.is_relative_to(tmp_path / "indexes"):
            reads.append((path, len(raw)))
        return raw

    monkeypatch.setattr(ordered_index, "read_material_bytes", read_material)
    index.publish([_row(130, kind=kind, revision=2, sequence=300)], commit_sequence=300)
    ledger_reads = [size for path, size in reads if path.parent.name == "latest-keys"]
    assert 1 <= len(ledger_reads) <= 8
    assert sum(ledger_reads) < 15_000
    assert len(reads) < 100
    spec = (
        QuerySpec(project_id="p", report_id="r00130")
        if kind == "report"
        else QuerySpec(project_id="p", view="ALL", sort="updated_sequence", descending=True)
    )
    result = index.query_spec(spec)
    assert result.status == "ok"
    assert result.items[0]["record_id"] == "r00130" and result.items[0]["revision"] == 2
    assert not (tmp_path / "indexes/.latest-keys.json").exists()


@pytest.mark.parametrize("descending", [False, True])
@pytest.mark.parametrize("leaf_size", [8, 16])
def test_ordered_tree_split_delete_reinsert_and_old_snapshot(tmp_path, descending, leaf_size):
    tree = OrderedIndexTree(tmp_path, _key_cmp, leaf_size=leaf_size)
    numbers = list(range(260))
    random.Random(21).shuffle(numbers)
    for number in numbers:
        tree.replace([(("p", number), {"number": number})], [])
    old_root = tree.root
    for number in range(0, 260, 3):
        tree.replace([], [("p", number)])
    for number in range(260, 290):
        tree.replace([(("p", number), {"number": number})], [])
    after = ("p", 140)
    expected = [number for number in range(290) if number >= 260 or number % 3]
    expected = (
        [number for number in expected if number < 140]
        if descending
        else [number for number in expected if number > 140]
    )
    if descending:
        expected.reverse()
    assert [
        row["number"]
        for _, row in tree.scan(
            lower=("p", -1),
            upper=("p", 1000),
            after=after,
            descending=descending,
        )
    ] == expected
    old = OrderedIndexTree(tmp_path, _key_cmp, leaf_size=leaf_size, root=old_root)
    assert [
        row["number"]
        for _, row in old.scan(
            lower=("p", -1),
            upper=("p", 1000),
            after=None,
            descending=False,
        )
    ] == list(range(260))
    for number in range(290):
        tree.replace([], [("p", number)])
    assert tree.root is None
    tree.replace([(("p", 9), {"number": 9})], [])
    assert tree.get(("p", 9)) == {"number": 9} and tree.get(("p", 10)) is None


@pytest.mark.parametrize("kind", ["report", "issue"])
@pytest.mark.parametrize("failure", ["snapshot", "switch"])
def test_interrupted_publish_cannot_leak_into_a_later_root(tmp_path, monkeypatch, kind, failure):
    index = FileQueryIndex(tmp_path, shard_size=8)
    index.rebuild([_row(i, kind=kind) for i in range(5)])
    spec = (
        QuerySpec(project_id="p", aggregate_kind="report", descending=True, limit=1)
        if kind == "report"
        else QuerySpec(
            project_id="p", view="ALL", sort="updated_sequence", descending=True, limit=1
        )
    )
    first = index.query_spec(spec)
    before = index.meta_path.read_bytes()
    write = atomic.write_json

    def fail(path, value, **kwargs):
        if (
            failure == "switch"
            and path == index.meta_path
            or failure == "snapshot"
            and path.parent == tmp_path / "indexes/roots"
        ):
            raise OSError("injected final root publication failure")
        return write(path, value, **kwargs)

    monkeypatch.setattr(atomic, "write_json", fail)
    with pytest.raises(OSError):
        index.publish([_row(1, kind=kind, revision=2, sequence=6)], commit_sequence=6)
    assert index.meta_path.read_bytes() == before
    monkeypatch.undo()
    # A different later commit must start from the published root, not from
    # family meta files or identity keys left by the interrupted publication.
    index.publish([_row(9, kind=kind, sequence=7)], commit_sequence=7)
    full_spec = spec.model_copy(update={"limit": 50})
    current = index.query_spec(full_spec)
    assert current.status == "ok"
    assert [
        (row["record_id"], row["revision"]) for row in current.items if row["record_id"] == "r00001"
    ] == [("r00001", 1)]
    pinned = index.query_spec(full_spec.model_copy(update={"cursor": first.next_cursor}))
    assert pinned.status == "ok" and pinned.commit_id == 5
    assert all(row["revision"] == 1 and row["record_id"] != "r00009" for row in pinned.items)


@pytest.mark.parametrize("kind", ["report", "issue"])
def test_late_older_rank_cannot_restore_old_current_keys(tmp_path, kind):
    index = FileQueryIndex(tmp_path)
    index.rebuild([_row(1, kind=kind, revision=3, sequence=10)])
    late = _row(1, kind=kind, revision=2, sequence=11)
    late["published_sequence" if kind == "report" else "updated_sequence"] = 5
    index.publish([late], commit_sequence=11)
    spec = (
        QuerySpec(project_id="p", report_id="r00001")
        if kind == "report"
        else QuerySpec(project_id="p", view="ALL", sort="updated_sequence")
    )
    page = index.query_spec(spec)
    assert page.status == "ok" and [row["revision"] for row in page.items] == [3]


def test_conflicting_immutable_generic_key_blocks_publication(tmp_path):
    index = FileQueryIndex(tmp_path)
    index.rebuild([_row(1)])
    before = index.meta_path.read_bytes()
    with pytest.raises(IndexMissing):
        index.publish([{**_row(1), "summary": "different"}], commit_sequence=3)
    assert index.meta_path.read_bytes() == before


def _legacy_index(root, rows):
    index = FileQueryIndex(root, shard_size=8)
    buckets, _ = index._derive_buckets(rows)
    families = {}
    for family in _ALL_FAMILIES:
        ordered = sorted(buckets[family], key=lambda item: item[0])
        refs = []
        for start in range(0, len(ordered), 8):
            chunk = ordered[start : start + 8]
            file = uuid.uuid4().hex[:16] + ".json"
            atomic.write_json(
                root / "indexes" / family / file,
                {
                    "entries": [{"k": list(key), "v": value} for key, value in chunk],
                },
            )
            refs.append(
                dict(file=file, count=len(chunk), first=list(chunk[0][0]), last=list(chunk[-1][0]))
            )
        meta = dict(
            family=family,
            generation=1,
            commit_id=max(row["commit_sequence"] for row in rows),
            shard_size=8,
            shards=refs,
        )
        families[family] = meta
        atomic.write_json(root / "indexes" / family / "meta.json", meta)
    root_id = uuid.uuid4().hex
    atomic.write_json(
        root / "indexes/roots" / f"{root_id}.json",
        {
            "generation": 1,
            "commit_id": max(row["commit_sequence"] for row in rows),
            "families": families,
        },
    )
    atomic.write_json(
        root / "indexes.json",
        dict(
            schema="aitest.index-root/3",
            version=3,
            generation=1,
            last_commit_sequence=max(row["commit_sequence"] for row in rows),
            snapshot_root=root_id,
        ),
    )
    return index


def test_legacy_migration_backup_resume_rollback_and_refresh(tmp_path, monkeypatch):
    from aitest.infrastructure.file_store.backup import FileBackupStore
    from aitest.infrastructure.file_store.index import encode_cursor

    rows = [_row(i, kind="report") for i in range(20)]
    index = _legacy_index(tmp_path, rows)
    header_before = index.meta_path.read_bytes()
    spec = QuerySpec(project_id="p", aggregate_kind="report", limit=1)
    old_cursor = encode_cursor("a" * 32, spec=spec, generation=1, commit_id=20)
    assert index.query_spec(spec).status == "maintenance_required"
    with pytest.raises(IndexMissing):
        index.publish([_row(21)], commit_sequence=22, all_rows=rows)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0004-bounded-query-directory",))
    write = atomic.write_json

    def fail(path, value, **kwargs):
        if path == index.meta_path:
            raise OSError("injected format root switch failure")
        return write(path, value, **kwargs)

    monkeypatch.setattr(atomic, "write_json", fail)
    with pytest.raises(OSError):
        manager.apply(plan.plan_id)
    assert index.meta_path.read_bytes() == header_before
    backup = tmp_path / "migrations/backups" / plan.plan_id
    assert (backup / "indexes.json").read_bytes() == header_before
    assert FileBackupStore(tmp_path).verify(backup)["ok"]
    monkeypatch.undo()
    manager.resume(plan.plan_id)
    assert index.is_healthy() and index._read_meta()["generation"] == 2
    result = index.query_spec(spec.model_copy(update={"limit": 50}))
    assert result.status == "ok" and len(result.items) == 20
    assert (
        index.query_spec(spec.model_copy(update={"cursor": old_cursor})).status == "invalid_cursor"
    )
    manager.rollback(plan.plan_id)
    assert index.meta_path.read_bytes() == header_before
    manager.apply(plan.plan_id)
    assert index.is_healthy() and index._read_meta()["generation"] == 2


def test_corrupt_legacy_point_shard_is_not_migrated_as_empty(tmp_path):
    index = _legacy_index(tmp_path, [_row(1)])
    meta = json.loads((tmp_path / "indexes" / _POINT_FAMILY / "meta.json").read_text())
    (tmp_path / "indexes" / _POINT_FAMILY / meta["shards"][0]["file"]).write_text("{}")
    before = index.meta_path.read_bytes()
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0004-bounded-query-directory",))
    with pytest.raises(IndexMissing):
        manager.apply(plan.plan_id)
    assert index.meta_path.read_bytes() == before


def test_migration_rollback_blocks_new_business_write(tmp_path):
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

    index = _legacy_index(tmp_path, [_row(0, sequence=1)])
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(("0004-bounded-query-directory",))
    manager.apply(plan.plan_id)
    unit = FileUnitOfWork(tmp_path)
    unit.begin("new", "p")
    unit.stage_record(
        aggregate_kind="case", record_id="new", expected_revision=0, payload={"project_id": "p"}
    )
    unit.commit("new")
    with pytest.raises(MigrationError, match="新的业务写入"):
        manager.rollback(plan.plan_id)
    assert index.is_healthy()


def test_default_core_migrates_only_after_backup_and_preserves_snapshot_events(tmp_path):
    from aitest.bootstrap import assemble_workspace_core
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

    unit = FileUnitOfWork(tmp_path)
    rows = []
    for number in range(3):
        unit.begin(f"r{number}", "p")
        unit.stage_record(
            aggregate_kind="case",
            record_id=f"r{number:05}",
            expected_revision=0,
            payload={"project_id": "p"},
        )
        unit.commit(f"r{number}")
        rows.append(_row(number))
    index = _legacy_index(tmp_path, rows)
    before = index.meta_path.read_bytes()
    first = assemble_workspace_core(tmp_path, instance_id="index-migration")
    try:
        registry = json.loads((tmp_path / "migrations/registry.json").read_text())
        assert "0004-bounded-query-directory" in registry["applied"]
        plans = list((tmp_path / "migrations/backups").glob("*/indexes.json"))
        assert len(plans) == 1 and plans[0].read_bytes() == before
        assert "0005-complete-commit-closure" in registry["applied"]
        assert index.is_healthy() and index._read_meta()["generation"] == 3
        from aitest.infrastructure.file_store.events import FileEventJournal

        journal = FileEventJournal(tmp_path, instance_id="index-migration")
        page = FileQueryIndex(tmp_path, journal=journal).query_spec(
            QuerySpec(project_id="p", limit=1)
        )
        assert page.status == "ok" and page.commit_id == 3
        assert page.event_cursor is not None
        assert page.event_cursor == journal.snapshot_cursor(commit_sequence=3)
    finally:
        first.lifetime_lock.release()
    saved = index.meta_path.read_bytes()
    second = assemble_workspace_core(tmp_path, instance_id="index-after-restart")
    try:
        assert index.meta_path.read_bytes() == saved
        assert index.query_spec(QuerySpec(project_id="p", limit=10)).items == tuple(rows)
    finally:
        second.lifetime_lock.release()


@pytest.mark.parametrize("damage", ["digest", "missing", "oversized"])
def test_ordered_node_damage_returns_maintenance_without_record_scan(tmp_path, damage):
    index = FileQueryIndex(tmp_path, shard_size=8)
    index.rebuild([_row(number) for number in range(20)])
    header = index._read_meta()
    reference = index._snapshot(header)["families"]["records-kind"]["root"]
    path = tmp_path / "indexes/records-kind" / reference["file"]
    if damage == "missing":
        path.unlink()
    elif damage == "digest":
        path.write_text('{"children": []}')
    else:
        path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
    (tmp_path / "records.json").write_text("must never read authority to disguise a query failure")
    result = index.query_spec(QuerySpec(project_id="p", limit=1))
    assert result.status == "maintenance_required" and result.items == ()


@pytest.mark.parametrize(
    "invalid",
    [
        {"commit_sequence": 99},
        {"commit_sequence": True},
        {"revision": False},
        {"project_id": ""},
    ],
)
def test_invalid_row_cannot_publish_another_commit_boundary(tmp_path, invalid):
    index = FileQueryIndex(tmp_path)
    index.rebuild([_row(1)])
    before = index.meta_path.read_bytes()
    with pytest.raises(IndexMissing):
        index.publish([{**_row(2), **invalid}], commit_sequence=3)
    assert index.meta_path.read_bytes() == before


def test_index_directory_link_is_rejected_before_any_tree_write(tmp_path, monkeypatch):
    from aitest.infrastructure.file_store.index import _ShardDirectory

    directory = tmp_path / "indexes/records-kind"
    actual = Path.is_junction
    monkeypatch.setattr(compat, "is_junction", lambda path: path == directory or actual(path))
    with pytest.raises(ValueError, match="filesystem link"):
        _ShardDirectory(tmp_path, "records-kind", 8).bulk_build(
            [(("p",), {"record_id": "a"})],
            generation=1,
            commit_id=1,
        )
    assert not directory.exists()


@pytest.mark.parametrize(
    "invalid",
    [
        {"commit_sequence": 99},
        {"commit_sequence": True},
        {"revision": False},
        {"project_id": ""},
    ],
)
def test_first_publication_validates_rows_before_creating_index_files(tmp_path, invalid):
    index = FileQueryIndex(tmp_path)
    bad = {**_row(1), **invalid}
    with pytest.raises(IndexMissing):
        index.publish([bad], commit_sequence=3, all_rows=[bad])
    assert not index.meta_path.exists() and not index.indexes_dir.exists()
