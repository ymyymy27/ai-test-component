"""Consuming old source in a new commit must prove the fixed material again."""

import pytest

from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_initial_run_registration import register
from tests.unit.test_pinned_source_closure import blob_path


def saved_source(core, inputs):
    return core.unit_of_work.repo.read(
        aggregate_kind="source_snapshot",
        record_id=inputs.snapshot.source_snapshot_id,
        revision=inputs.snapshot.record_revision,
    ).payload


def test_blob_loss_during_prepare_after_a_later_unrelated_commit_cannot_publish(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    unit = core.unit_of_work
    unit.begin("unrelated-request", inputs.project_id, intent_id="unrelated-intent")
    unit.stage_record(
        aggregate_kind="diagnostic",
        record_id="unrelated",
        expected_revision=0,
        payload={"project_id": inputs.project_id, "message": "controlled fixture"},
    )
    unit.commit("unrelated-request")
    path = blob_path(core.workspace.root, saved_source(core, inputs))
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    original = unit.stage_record

    def lose_blob(**kwargs):
        if kwargs["aggregate_kind"] == "prepared_run":
            path.unlink()
        return original(**kwargs)

    monkeypatch.setattr(unit, "stage_record", lose_blob)
    result = prepare(core, inputs)
    assert result.error is not None and result.error.code == "COMMIT_MATERIAL_UNVERIFIED"
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert unit.repo._load()["intents"].get("prepare-intent") is None


def test_saved_preparation_consumed_by_initial_run_rechecks_blob_inside_publication(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    path = blob_path(core.workspace.root, saved_source(core, inputs))
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    original = core.unit_of_work.stage_record

    def lose_blob(**kwargs):
        if kwargs["aggregate_kind"] == "run":
            path.unlink()
        return original(**kwargs)

    monkeypatch.setattr(core.unit_of_work, "stage_record", lose_blob)
    with pytest.raises(ValueError, match="source"):
        register(core, prepared)
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert core.unit_of_work.repo._load()["intents"].get("register-intent") is None


def test_new_preparation_manifest_includes_its_consumed_historical_source_files(authoritative):
    core, inputs, _ = authoritative
    source = saved_source(core, inputs)
    path = blob_path(core.workspace.root, source)
    assert prepare(core, inputs).error is None
    manifest = FileCommitStore(core.workspace.root).read_current(verify_material=True)["manifest"]
    assert path.relative_to(core.workspace.root).as_posix() in manifest["source_material_files"]
    assert (
        "snapshots/" + source["pinned_snapshot_id"] + ".json" in manifest["source_material_files"]
    )


@pytest.mark.parametrize("damage", ["revision_bool", "identity", "purpose", "foreign_owner"])
def test_frozen_preparation_reference_tampering_cannot_publish(authoritative, damage):
    import copy

    core, inputs, _ = authoritative
    original = prepare(core, inputs).result
    candidate = copy.deepcopy(original)
    candidate["prepared_run_id"] = "invalid-reference-preparation"
    project = inputs.project_id
    if damage == "revision_bool":
        candidate["snapshot"]["record_revision"] = True
    elif damage == "identity":
        candidate["snapshot"]["content_identity"] = "sha256:" + "0" * 64
    elif damage == "purpose":
        candidate["snapshot"]["purpose"] = "analysis"
    else:
        project = "foreign-project"
        candidate["project_id"] = project
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    unit = core.unit_of_work
    unit.begin("tamper-request", project, intent_id="tamper-intent")
    unit.stage_record(
        aggregate_kind="prepared_run",
        record_id="invalid-reference-preparation",
        expected_revision=0,
        payload=candidate,
    )
    with pytest.raises(ValueError, match="source"):
        unit.commit("tamper-request")
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert unit.repo._load()["intents"].get("tamper-intent") is None


def test_run_registration_intent_cannot_rewrite_preparation_fingerprint(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    unit = core.unit_of_work
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    unit.begin("fake-intent-request", inputs.project_id, intent_id="fake-intent")
    unit.stage_record(
        aggregate_kind="execution_intent",
        record_id="run-registration:fake",
        expected_revision=0,
        payload={
            "schema_version": "aitest.run-registration-intent/1.0",
            "project_id": inputs.project_id,
            "prepared_run_id": prepared["prepared_run_id"],
            "fingerprint": "sha256:" + "0" * 64,
        },
    )
    with pytest.raises(ValueError, match="source.*digest"):
        unit.commit("fake-intent-request")
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert unit.repo._load()["intents"].get("fake-intent") is None
