"""Canonical saved material must prove the producing core and every record."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.events import FileEventJournal


def stage(unit, request: str, kinds: tuple[str, ...] = ("case",)) -> None:
    unit.begin(request, "project", intent_id=request)
    for index, kind in enumerate(kinds):
        unit.stage_record(
            aggregate_kind=kind,
            record_id=f"{request}-{index}",
            expected_revision=0,
            payload={"project_id": "project", "summary": request},
        )
    unit.commit(request)


def test_default_commit_records_actual_core_instance(tmp_path: Path) -> None:
    first = assemble_workspace_core(tmp_path, instance_id="core-one")
    stage(first.unit_of_work, "one")
    first.lifetime_lock.release()
    second = assemble_workspace_core(tmp_path, instance_id="core-two")
    try:
        stage(second.unit_of_work, "two")
        current = FileCommitStore(tmp_path).read_current(verify_material=True)
        assert current["manifest"]["instance_id"] == "core-two"
        events = FileEventJournal(tmp_path, instance_id="reader").read(limit=20).events
        assert [event.instance_id for event in events] == ["core-one", "core-two"]
        assert all(event.instance_id != second.workspace.workspace_id for event in events)
    finally:
        second.lifetime_lock.release()


def test_manifest_has_canonical_aliases_and_exact_idempotency_result(tmp_path: Path) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="actual-core")
    try:
        stage(core.unit_of_work, "request", ("case", "task"))
        manifest = FileCommitStore(tmp_path).read_current(verify_material=True)["manifest"]
        assert manifest["schema"] == "aitest.commit-manifest/2"
        assert manifest["commit_id"] == manifest["commit_sequence"]
        assert manifest["generation"] == manifest["generation_id"]
        assert manifest["parent_commit"] == manifest["parent_manifest"]
        assert manifest["changed_records"] == manifest["created"]
        assert manifest["event_range"] == {"first": 1, "last": 2}
        ref = manifest["idempotency_result_ref"]
        raw = (tmp_path / ref["path"]).read_bytes()
        assert ref["size"] == len(raw)
        assert ref["sha256"] == hashlib.sha256(raw).hexdigest()
        result = json.loads(raw)
        assert result["request_id"] == result["intent_id"] == "request"
        assert result["commit_sequence"] == 2
        assert len(result["created"]) == 2
        assert result["instance_id"] == "actual-core"
        assert ref in manifest["file_digests"]
    finally:
        core.lifetime_lock.release()


def test_every_record_in_batch_is_in_same_published_business_index(tmp_path: Path) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="actual-core")
    try:
        stage(core.unit_of_work, "batch", ("case", "task", "case"))
        manifest = FileCommitStore(tmp_path).read_current(verify_material=True)["manifest"]
        root = manifest["business_change_index_root"]
        from aitest.infrastructure.file_store.business_changes import BusinessChangeIndex

        index = BusinessChangeIndex(tmp_path, root)
        rows = index.read(types=("case", "task"), high_water=3, limit=10)
        assert [(row["record_id"], row["revision"]) for row in rows] == [
            ("batch-0", 1),
            ("batch-1", 1),
            ("batch-2", 1),
        ]
        assert {row["commit_sequence"] for row in rows} == {3}
        assert {row["origin_workspace_id"] for row in rows} == {core.workspace.workspace_id}
    finally:
        core.lifetime_lock.release()


def test_diagnostic_commit_reuses_business_root(tmp_path: Path) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="actual-core")
    try:
        stage(core.unit_of_work, "business")
        original = FileCommitStore(tmp_path).read_current()["manifest"][
            "business_change_index_root"
        ]
        stage(core.unit_of_work, "diagnostic", ("diagnostic",))
        manifest = FileCommitStore(tmp_path).read_current(verify_material=True)["manifest"]
        assert manifest["commit_sequence"] == 2
        assert manifest["business_change_index_root"] == original
    finally:
        core.lifetime_lock.release()


def test_lost_idempotency_file_blocks_current_verification(tmp_path: Path) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="actual-core")
    try:
        stage(core.unit_of_work, "request")
        manifest = FileCommitStore(tmp_path).read_current()["manifest"]
        (tmp_path / manifest["idempotency_result_ref"]["path"]).unlink()
        with pytest.raises(ValueError, match="current commit"):
            FileCommitStore(tmp_path).read_current(verify_material=True)
    finally:
        core.lifetime_lock.release()


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit_id", True),
        ("generation", "other"),
        ("parent_commit", None),
        ("changed_records", []),
        ("event_range", {"first": 2, "last": 2}),
        ("instance_id", "other-core"),
        ("file_digests", []),
    ],
)
def test_canonical_identity_mismatch_cannot_be_prepared(tmp_path, field, value) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="actual-core")
    try:
        stage(core.unit_of_work, "request")
        store = FileCommitStore(tmp_path)
        saved = store.read_current()
        candidate = {**saved["manifest"], field: value}
        with pytest.raises(ValueError):
            store.prepare(candidate)
        assert store.read_current()["pointer"] == saved["pointer"]
    finally:
        core.lifetime_lock.release()


def test_sparse_query_continues_within_same_batch_and_freezes_high_water(tmp_path) -> None:
    from aitest.infrastructure.file_store.business_changes import BusinessChangeIndex

    core = assemble_workspace_core(tmp_path, instance_id="actual-core")
    try:
        stage(core.unit_of_work, "batch", ("case", "task", "case"))
        frozen = FileCommitStore(tmp_path).read_current()["manifest"]
        stage(core.unit_of_work, "later")
        index = BusinessChangeIndex(tmp_path, frozen["business_change_index_root"])
        rows = index.read(types=("case", "task"), high_water=3, limit=1)
        seen = []
        while rows:
            row = rows[0]
            seen.append(row["record_id"])
            after = (
                row["commit_sequence"],
                row["origin_workspace_id"],
                row["project_id"],
                row["record_id"],
                row["revision"],
                row["aggregate_kind"],
            )
            rows = index.read(types=("case", "task"), high_water=3, limit=1, after=after)
        assert seen == ["batch-0", "batch-1", "batch-2"]
    finally:
        core.lifetime_lock.release()


def test_sparse_query_reads_no_unrelated_manifest_or_other_type(tmp_path, monkeypatch) -> None:
    from aitest.infrastructure.file_store.business_changes import BusinessChangeIndex

    core = assemble_workspace_core(tmp_path, instance_id="actual-core")
    try:
        stage(core.unit_of_work, "batch", ("case", "task"))
        root = FileCommitStore(tmp_path).read_current()["manifest"]["business_change_index_root"]
        visited = []
        original = Path.open

        def trace(path, *args, **kwargs):
            visited.append(path)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", trace)
        index = BusinessChangeIndex(tmp_path, root)
        rows = index.read(types=("case",), high_water=2, limit=1)
        assert len(rows) == 1
        assert visited
        assert all(path.is_relative_to(index.tree("case").directory) for path in visited)
    finally:
        core.lifetime_lock.release()


def test_sparse_index_cannot_replace_history_while_preserving_count_and_new_batch(tmp_path):
    from aitest.infrastructure.file_store.business_changes import BusinessChangeIndex

    core = assemble_workspace_core(tmp_path, instance_id="historical-index-core")
    try:
        stage(core.unit_of_work, "old")
        old = FileCommitStore(tmp_path).read_current()["manifest"]
        stage(core.unit_of_work, "new")
        current = FileCommitStore(tmp_path).read_current()["manifest"]
        previous = BusinessChangeIndex(tmp_path, old["business_change_index_root"])
        index = BusinessChangeIndex(tmp_path, current["business_change_index_root"])
        row = index.read(types=("case",), high_water=2, limit=1)[0]
        key = (
            row["commit_sequence"],
            row["origin_workspace_id"],
            row["project_id"],
            row["record_id"],
            row["revision"],
        )
        tree = index.tree("case")
        tree.replace([], [key])
        tree.replace([(key, {**row, "body_sha256": "0" * 64})], [])
        header = {
            **index.header,
            "types": {
                **index.header["types"],
                "case": {
                    **index.header["types"]["case"],
                    "root": tree.root,
                },
            },
        }
        forged = BusinessChangeIndex(tmp_path, header)
        forged.verify_changes(
            current["created"],
            commit_sequence=2,
            workspace_id=core.workspace.workspace_id,
            project_id="project",
        )
        with pytest.raises(ValueError, match="historical"):
            forged.verify_extension(previous, current["created"])
    finally:
        core.lifetime_lock.release()


def test_canonical_migration_resumes_frozen_index_after_failed_switch(
    tmp_path, monkeypatch
) -> None:
    from aitest.infrastructure.file_store.migrations import FileMigrationManager
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

    unit = FileUnitOfWork(tmp_path)
    stage(unit, "one", ("case", "task"))
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(
        (
            "0003-sharded-record-authority",
            "0004-bounded-query-directory",
            "0005-complete-commit-closure",
            "0006-canonical-current-publication",
        )
    )
    manager.apply(plan.plan_id)
    store = FileCommitStore(tmp_path)
    saved = store.read_current(verify_material=True)
    old_file = tmp_path / "manifests" / f"{saved['pointer']['manifest_digest']}.json"
    old_bytes = old_file.read_bytes()
    plan = manager.plan(("0007-canonical-manifest-and-business-changes",))
    publish = FileCommitStore.publish

    def failed_publish(self, digest):
        if self.read_manifest(digest)["schema"] == "aitest.commit-manifest/2":
            raise OSError("injected before current switch")
        return publish(self, digest)

    with monkeypatch.context() as patch:
        patch.setattr(FileCommitStore, "publish", failed_publish)
        with pytest.raises(OSError):
            manager.apply(plan.plan_id)
    assert store.read_current()["pointer"] == saved["pointer"]
    assert (tmp_path / "migrations/business-index-progress.json").exists()
    report = manager.resume(plan.plan_id)
    assert report.state in {"applied", "resumed"}
    assert report.executed == ("0007-canonical-manifest-and-business-changes",)
    assert (
        store.read_current(verify_material=True)["manifest"]["schema"] == "aitest.commit-manifest/2"
    )
    assert old_file.read_bytes() == old_bytes
