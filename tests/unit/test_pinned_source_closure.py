"""Actual source blobs must remain provable at every publication/use boundary."""

import json

import pytest

from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore, SnapshotError
from aitest.infrastructure.file_store.commit_manifest import CommitMaterialError, FileCommitStore
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_source_analysis import analyze, dispatch, seed
from tests.unit.test_default_source_analysis import stack as stack


def blob_path(root, result):
    metadata = FileSourceSnapshotStore(root).read_pinned(result["pinned_snapshot_id"])
    return root / "snapshots/blobs" / metadata["files"][0]["sha256"]


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_default_check_source_rejects_lost_or_changed_fixed_bytes(stack, damage):
    core, source, root = stack
    seed(core, source)
    pinned = analyze(core)
    assert pinned.error is None
    path = blob_path(root, pinned.result)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"VALUE = 9\n")
    before = FileCommitStore(root).read_current()["pointer"]
    result = dispatch(
        core,
        "check_source",
        parameters={"snapshot_id": pinned.result["snapshot_id"], "revision": 1},
    )
    assert result.error is not None and result.error.code == "B_SOURCE_UNVERIFIED"
    assert FileCommitStore(root).read_current()["pointer"] == before


def test_loss_after_pin_before_business_publish_does_not_consume_intent(stack, monkeypatch):
    core, source, root = stack
    seed(core, source)
    original = FileSourceSnapshotStore.pin

    def lose_blob(self, **kwargs):
        metadata = original(self, **kwargs)
        (root / "snapshots/blobs" / metadata["files"][0]["sha256"]).unlink()
        return metadata

    monkeypatch.setattr(FileSourceSnapshotStore, "pin", lose_blob)
    before = FileCommitStore(root).read_current()["pointer"]
    result = analyze(core)
    assert result.error is not None and result.error.code == "B_SOURCE_UNVERIFIED"
    assert FileCommitStore(root).read_current()["pointer"] == before
    assert core.unit_of_work.repo._load()["intents"].get("pin-intent") is None


def test_current_manifest_rechecks_its_referenced_fixed_bytes(stack):
    core, source, root = stack
    seed(core, source)
    pinned = analyze(core)
    assert pinned.error is None
    path = blob_path(root, pinned.result)
    before = FileCommitStore(root).read_current()["pointer"]
    path.unlink()
    with pytest.raises(CommitMaterialError):
        FileCommitStore(root).read_current(verify_material=True)
    assert FileCommitStore(root).read_current()["pointer"] == before


def test_new_commit_digest_list_includes_pinned_manifest_and_blobs(stack):
    core, source, root = stack
    seed(core, source)
    pinned = analyze(core)
    assert pinned.error is None
    path = blob_path(root, pinned.result)
    current = FileCommitStore(root).read_current(verify_material=True)["manifest"]
    paths = {item["path"] for item in current["file_digests"]}
    assert path.relative_to(root).as_posix() in paths
    assert "snapshots/" + pinned.result["pinned_snapshot_id"] + ".json" in paths


def test_duplicate_snapshot_json_fields_are_rejected_even_if_last_value_is_correct(stack):
    core, source, root = stack
    seed(core, source)
    pinned = analyze(core)
    assert pinned.error is None
    snapshot_id = pinned.result["pinned_snapshot_id"]
    path = root / "snapshots" / (snapshot_id + ".json")
    original = path.read_text(encoding="utf-8")
    path.write_text('{"purpose":"false earlier value",' + original.lstrip()[1:], encoding="utf-8")
    raw = path.read_bytes()
    assert json.loads(raw)["purpose"] == "prepare"
    with pytest.raises(SnapshotError):
        FileSourceSnapshotStore(root).read_pinned(snapshot_id)
    assert path.read_bytes() == raw


def test_default_prepare_refuses_blob_loss_without_consuming_prepare_intent(authoritative):
    core, inputs, _source = authoritative
    saved = core.unit_of_work.repo.read(
        aggregate_kind="source_snapshot", record_id=inputs.snapshot.source_snapshot_id, revision=1
    )
    path = blob_path(core.workspace.root, saved.payload)
    path.write_bytes(b"VALUE = 9\n")
    bad = path.read_bytes()
    seq = core.unit_of_work.current_commit_sequence()
    result = prepare(core, inputs)
    assert result.error is None
    assert result.result["status"] == "blocked"
    assert "basis_unverified" in {r["code"] for r in result.result["blocking_reasons"]}
    assert core.unit_of_work.current_commit_sequence() == seq
    assert core.unit_of_work.repo._load()["intents"].get("prepare-intent") is None
    assert path.read_bytes() == bad


def test_loss_inside_transaction_before_new_authority_staging_is_refused(stack, monkeypatch):
    core, source, root = stack
    seed(core, source)
    original = core.unit_of_work.stage_record

    def lose_during_stage(**kwargs):
        if kwargs["aggregate_kind"] == "source_snapshot":
            path = blob_path(root, kwargs["payload"])
            path.unlink()
        return original(**kwargs)

    monkeypatch.setattr(core.unit_of_work, "stage_record", lose_during_stage)
    before = FileCommitStore(root).read_current()["pointer"]
    result = analyze(core)
    assert result.error is not None and result.error.code == "COMMIT_MATERIAL_UNVERIFIED"
    assert FileCommitStore(root).read_current()["pointer"] == before
    assert core.unit_of_work.repo._load()["intents"].get("pin-intent") is None


def test_new_source_file_marker_cannot_omit_a_fixed_blob(stack):
    core, source, root = stack
    seed(core, source)
    assert analyze(core).error is None
    store = FileCommitStore(root)
    current = store.read_current()["manifest"]
    candidate = {**current, "source_material_files": []}
    with pytest.raises(ValueError, match="source material list"):
        store.verify_material(candidate)


def test_legacy_v2_without_source_file_list_is_readable_but_cannot_certify_lost_bytes(stack):
    core, source, root = stack
    seed(core, source)
    pinned = analyze(core)
    assert pinned.error is None
    store = FileCommitStore(root)
    legacy = dict(store.read_current()["manifest"])
    legacy.pop("source_material_files")
    legacy["file_digests"] = [
        ref for ref in legacy["file_digests"] if not ref["path"].startswith("snapshots/")
    ]
    store.verify_material(legacy)
    blob_path(root, pinned.result).unlink()
    with pytest.raises(CommitMaterialError):
        store.verify_material(legacy)


def test_shared_blob_is_verified_once_without_touching_current_source(tmp_path, monkeypatch):
    from aitest.infrastructure.file_store import source_material

    source = tmp_path / "source"
    source.mkdir()
    for name in ("a.py", "b.py"):
        (source / name).write_bytes(b"VALUE = 1\n")
    store = FileSourceSnapshotStore(tmp_path / "workspace")
    pinned = store.pin(canonical_path=str(source), purpose="prepare")
    (source / "a.py").unlink()
    (source / "b.py").unlink()
    calls = []
    original = source_material.copy_unchanged_safe_bytes

    def count(stream, **kwargs):
        calls.append(stream.name)
        return original(stream, **kwargs)

    monkeypatch.setattr(source_material, "copy_unchanged_safe_bytes", count)
    assert store.verify_pinned(pinned["snapshot_id"])["files"] == pinned["files"]
    assert len(calls) == 1


def test_metadata_budget_is_checked_before_reading_oversized_material(tmp_path, monkeypatch):
    from aitest.infrastructure.file_store import source_material

    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_bytes(b"safe\n")
    root = tmp_path / "workspace"
    store = FileSourceSnapshotStore(root)
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    monkeypatch.setattr(source_material, "MAX_METADATA_BYTES", 1)
    with pytest.raises(SnapshotError, match="budget"):
        store.read_pinned(pinned["snapshot_id"])
