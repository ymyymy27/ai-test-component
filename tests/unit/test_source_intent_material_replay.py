"""Actual fixed bytes with an in-memory read port; no real human/host acceptance claim."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from aitest.application.planning.substrate import CommittedRecord
from aitest.application.project.serialization import binding_to_payload
from aitest.application.project.source_analysis import (
    SourceAnalysisError,
    SourceAnalysisService,
    _digest,
)
from aitest.domain.project.context import BindingForm, LocalProjectBinding
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.file_store.source_material import (
    SourceMaterialError,
    prospective_source_authority,
    source_record_files,
)
from tests.unit.test_delivery_source_closure import material as material


class Reader:
    def __init__(self, records):
        self.records = records
        self.reads = []

    def current_revision(self, *, aggregate_kind, record_id):
        return max(
            (
                revision
                for kind, identity, revision in self.records
                if (kind, identity) == (aggregate_kind, record_id)
            ),
            default=0,
        )

    def read(self, *, aggregate_kind, record_id, revision):
        key = aggregate_kind, record_id, revision
        self.reads.append(key)
        return CommittedRecord(aggregate_kind, record_id, revision, self.records[key])

    def query(self, *args, **kwargs):
        raise AssertionError("exact replay must not scan history")


@pytest.fixture
def replay(material, monkeypatch):
    root, pinned, source, _, _ = material
    binding = LocalProjectBinding(
        "binding",
        1,
        "project",
        pinned["canonical_path"],
        BindingForm.PLAIN,
        manifest_digest="declared",
        confirmed=True,
    )
    inputs = {
        "project_id": "project",
        "binding_id": "binding",
        "binding_revision": 1,
        "expected_revision": 0,
        "purpose": "prepare",
        "source_scope": ".",
        "selected_paths": (),
        "exclusion_rules": (),
        "refetch_dependencies": (),
        "refetch_scope": None,
    }
    operation = "source-intent-" + _digest(["project", "original-intent"])[7:]
    current = "source-current-" + _digest(["project", "binding"])[7:]
    result = {
        "snapshot_id": "snapshot",
        "record_revision": 1,
        "content_identity": source["content_identity"],
        "pinned_snapshot_id": pinned["snapshot_id"],
        "binding_id": "binding",
        "binding_revision": 1,
        "purpose": "prepare",
        "source_current_ref": {"record_id": current, "record_revision": 1},
    }
    intent = {
        "schema_version": "aitest.source-pin-intent/1.0",
        "project_id": "project",
        "intent_id": "original-intent",
        "request_id": "original-request",
        "digest": _digest(inputs),
        "inputs": deepcopy(inputs),
        "result": deepcopy(result),
    }
    records = {
        ("source_pin_intent", operation, 1): intent,
        ("source_snapshot", "snapshot", 1): source,
        ("binding", "binding", 1): binding_to_payload(binding),
        ("source_binding_current", current, 1): {"project_id": "project", **deepcopy(result)},
        ("source_binding_current", current, 2): {
            "project_id": "project",
            "snapshot_id": "later-snapshot",
        },
    }
    reader, unit, snapshots = Reader(records), Mock(), FileSourceSnapshotStore(root)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "historical replay cannot scan, repin, materialize or inspect today's directory"
        )

    for method in ("pin", "detect_changes", "materialize"):
        monkeypatch.setattr(snapshots, method, forbidden)
    service = SourceAnalysisService(
        reader=reader,
        unit_of_work=unit,
        snapshots=snapshots,
        source_control=None,
        source_available=lambda: False,
    )
    return service, reader, unit, root, pinned, inputs, operation, current, intent, result


def recall(context):
    service, _, _, _, _, inputs, operation, *_ = context
    return service._original("project", operation, _digest(inputs), inputs)


def test_original_replay_reads_exact_pointer_and_fixed_bytes_under_paused_source(replay):
    _, reader, unit, _, _, _, _, current, _, result = replay
    assert recall(replay) == result | {"reused": True}
    assert ("source_binding_current", current, 1) in reader.reads
    assert ("source_binding_current", current, 2) not in reader.reads
    assert not unit.method_calls


def test_legacy_replay_keeps_missing_optional_fields_without_guessing(replay):
    _, reader, unit, _, _, _, _, _, intent, result = replay
    intent.pop("inputs")
    intent.pop("schema_version")
    intent["result"].pop("source_current_ref")
    legacy = {key: value for key, value in result.items() if key != "source_current_ref"}
    assert recall(replay) == legacy | {"reused": True}
    assert not any(kind == "source_binding_current" for kind, _, _ in reader.reads)
    assert not unit.method_calls


@pytest.mark.parametrize(
    "damage",
    [
        "missing_source",
        "missing_blob",
        "truncated_result",
        "content",
        "boolean_revision",
        "foreign_source",
        "pointer_material",
        "pointer_latest",
        "missing_pointer",
        "intent_identity",
        "frozen_inputs",
        "fixed_scope",
        "binding_root",
        "snapshot_purpose",
    ],
)
def test_invalid_original_material_blocks_without_new_effects(replay, damage):
    _, reader, unit, root, pinned, _, operation, current, intent, _ = replay
    source = reader.records[("source_snapshot", "snapshot", 1)]
    if damage == "missing_source":
        del reader.records[("source_snapshot", "snapshot", 1)]
    elif damage == "missing_blob":
        (root / "snapshots/blobs" / pinned["files"][0]["sha256"]).unlink()
    elif damage == "truncated_result":
        intent["result"] = {"snapshot_id": "snapshot"}
    elif damage == "content":
        intent["result"]["content_identity"] = "another-source"
    elif damage == "boolean_revision":
        intent["result"]["record_revision"] = True
    elif damage == "foreign_source":
        source["project_id"] = "another-project"
    elif damage == "pointer_material":
        reader.records[("source_binding_current", current, 1)]["snapshot_id"] = "another-source"
    elif damage == "pointer_latest":
        intent["result"]["source_current_ref"]["record_revision"] = 2
    elif damage == "missing_pointer":
        del reader.records[("source_binding_current", current, 1)]
    elif damage == "intent_identity":
        intent["intent_id"] = "another-intent"
    elif damage == "frozen_inputs":
        intent["inputs"]["source_scope"] = "another-scope"
    elif damage == "fixed_scope":
        source["exclusion_rules"] = []
    elif damage == "binding_root":
        reader.records[("binding", "binding", 1)]["canonical_path"] = (
            root / "another-root"
        ).as_posix()
    else:
        source["purpose"] = "analysis"
    before = deepcopy(reader.records)
    with pytest.raises(SourceAnalysisError):
        recall(replay)
    assert reader.records == before
    assert not unit.method_calls
    assert ("source_binding_current", current, 2) not in reader.reads


def authority(reader):
    rows = {}
    for (kind, identity, revision), body in sorted(reader.records.items()):
        records = rows.setdefault(kind, {}).setdefault(identity, [])
        assert len(records) == revision - 1
        records.append(body)
    return {"records": rows}


def test_new_intent_reusing_an_existing_snapshot_still_closes_actual_source_files(replay):
    _, reader, _, root, pinned, _, _, _, intent, _ = replay
    paths = source_record_files(
        root, "source_pin_intent", intent, "project", authority(reader), require_verified=True
    )
    assert paths == {
        "snapshots/" + pinned["snapshot_id"] + ".json",
        *("snapshots/blobs/" + file["sha256"] for file in pinned["files"]),
    }


def test_same_batch_source_lookup_does_not_publish_authority_before_byte_validation(replay):
    _, reader, _, root, pinned, _, _, current, intent, _ = replay
    original = authority(reader)
    source = original["records"].pop("source_snapshot")["snapshot"][0]
    pointer = original["records"].pop("source_binding_current")[current][0]
    before = deepcopy(original)
    pending = [
        ("source_snapshot", "snapshot", 0, source),
        ("source_binding_current", current, 0, pointer),
    ]
    candidate = prospective_source_authority(original, pending, "project")
    paths = source_record_files(
        root, "source_pin_intent", intent, "project", candidate, require_verified=True
    )
    assert paths and original == before
    missing = root / "snapshots/blobs" / pinned["files"][0]["sha256"]
    missing.unlink()
    with pytest.raises(SourceMaterialError):
        source_record_files(
            root, "source_pin_intent", intent, "project", candidate, require_verified=True
        )
    assert original == before


def test_candidate_source_uses_exact_lazy_reads_without_membership_or_enumeration(replay):
    _, reader, _, root, _, inputs, _, current, intent, result = replay
    inputs["expected_revision"] = 1
    result["source_current_ref"]["record_revision"] = 2
    intent["result"] = deepcopy(result)
    intent["inputs"] = deepcopy(inputs)
    intent["digest"] = _digest(inputs)
    original = authority(reader)
    original["records"]["source_binding_current"][current].pop()

    class Lazy(dict):
        def __init__(self, values):
            self.values = values

        def get(self, key, default=None):
            return self.values.get(key, default)

        def items(self):
            raise AssertionError("candidate checks must not enumerate saved history")

    original["records"] = Lazy({kind: Lazy(values) for kind, values in original["records"].items()})
    pending = [("source_binding_current", current, 1, {"project_id": "project", **result})]
    candidate = prospective_source_authority(original, pending, "project")
    assert source_record_files(
        root, "source_pin_intent", intent, "project", candidate, require_verified=True
    )
    assert len(original["records"].get("source_binding_current").get(current)) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "missing_inputs",
        "input_digest",
        "input_binding",
        "pointer_alias",
        "boolean_pointer",
        "content",
        "missing_blob",
    ],
)
def test_new_schema_source_intent_cannot_publish_an_unverified_reference(replay, damage):
    _, reader, _, root, pinned, _, _, _, intent, _ = replay
    if damage == "missing_inputs":
        intent.pop("inputs")
    elif damage == "input_digest":
        intent["digest"] = "wrong-input"
    elif damage == "input_binding":
        intent["inputs"]["binding_id"] = "another-binding"
        intent["digest"] = _digest(intent["inputs"])
    elif damage == "pointer_alias":
        intent["result"]["source_current_ref"]["record_id"] = "another-pointer"
    elif damage == "boolean_pointer":
        intent["result"]["source_current_ref"]["record_revision"] = True
    elif damage == "content":
        intent["result"]["content_identity"] = "another-source"
    else:
        (root / "snapshots/blobs" / pinned["files"][0]["sha256"]).unlink()
    with pytest.raises(SourceMaterialError):
        source_record_files(
            root, "source_pin_intent", intent, "project", authority(reader), require_verified=True
        )
