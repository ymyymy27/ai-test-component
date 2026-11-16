"""Formal source consumers must bring exact, real fixed bytes into publication closure."""

import hashlib
import json

import pytest

from aitest.application.project.serialization import source_manifest_to_payload
from aitest.domain.project.context import SourceFileDigest, SourceForm, SourceManifest
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.file_store.source_material import (
    SourceMaterialError,
    source_record_files,
)


@pytest.fixture
def material(tmp_path):
    source_path = tmp_path / "tested"
    source_path.mkdir()
    (source_path / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    root = tmp_path / "workspace"
    pinned = FileSourceSnapshotStore(root).pin(
        canonical_path=source_path.as_posix(),
        purpose="prepare",
        selected_paths=(),
        exclusion_rules=(),
    )
    files = tuple(
        SourceFileDigest(f["relative_path"], f["size"], "sha256:" + f["sha256"])
        for f in pinned["files"]
    )
    manifest = SourceManifest(
        source_scope=".",
        source_form=SourceForm.PLAIN,
        files=files,
        exclusion_rules=tuple(pinned["exclusion_rules"]),
        manifest_digest="sha256:"
        + hashlib.sha256(json.dumps(pinned["files"]).encode()).hexdigest(),
    )
    source = source_manifest_to_payload(
        manifest, project_id="project", snapshot_id="snapshot", purpose="prepare"
    )
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                pinned, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
    )
    source.update(
        binding_id="binding",
        binding_revision=1,
        pinned_snapshot_id=pinned["snapshot_id"],
        pinned_manifest_digest=digest,
        selected_paths=[],
    )
    submission = {
        "schema_version": "aitest.delivery-submission/1.0",
        "status": "submitted",
        "project_id": "project",
        "snapshot_id": "snapshot",
        "snapshot_record_revision": 1,
        "binding_id": "binding",
        "binding_record_revision": 1,
        "content_identity": source["content_identity"],
    }
    authority = {"records": {"source_snapshot": {"snapshot": [source]}}}
    return root, pinned, source, submission, authority


def test_publication_closure_contains_actual_manifest_and_blob(material):
    root, pinned, _, body, authority = material
    paths = source_record_files(
        root, "delivery_submission", body, "project", authority, require_verified=True
    )
    assert paths == {
        "snapshots/" + pinned["snapshot_id"] + ".json",
        *("snapshots/blobs/" + f["sha256"] for f in pinned["files"]),
    }
    assert all((root / path).is_file() for path in paths)


@pytest.mark.parametrize(
    "damage",
    [
        "content",
        "snapshot",
        "binding",
        "body_boolean",
        "source_boolean",
        "foreign_source",
        "missing_blob",
    ],
)
def test_formal_source_alias_types_ownership_and_missing_bytes_reject(material, damage):
    root, pinned, source, body, authority = material
    if damage == "content":
        body["content_identity"] = "another-source"
    elif damage == "snapshot":
        body["snapshot_id"] = "missing-snapshot"
    elif damage == "binding":
        body["binding_id"] = "another-binding"
    elif damage == "body_boolean":
        body["binding_record_revision"] = True
    elif damage == "source_boolean":
        source["binding_revision"] = True
    elif damage == "foreign_source":
        source["project_id"] = "another-project"
    else:
        (root / "snapshots/blobs" / pinned["files"][0]["sha256"]).unlink()
    with pytest.raises(SourceMaterialError):
        source_record_files(
            root, "delivery_submission", body, "project", authority, require_verified=True
        )
