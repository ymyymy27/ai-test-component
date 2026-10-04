"""A published record must never claim an unverified permanent object reference."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure import security
from aitest.infrastructure.file_store.commit_manifest import CommitMaterialError, FileCommitStore
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.references import verify_record_objects


@pytest.fixture
def core(tmp_path: Path):
    assembly = assemble_workspace_core(tmp_path / "workspace", instance_id="actual-core")
    try:
        yield assembly
    finally:
        assembly.lifetime_lock.release()


def stage_reference(core, reference: dict[str, object]) -> None:
    unit = core.unit_of_work
    unit.begin("request", "project", intent_id="intent")
    unit.stage_record(
        aggregate_kind="evidence",
        record_id="evidence",
        expected_revision=0,
        payload={"project_id": "project", "attachment": {"object_ref": reference}},
    )


@pytest.mark.parametrize(
    "damage", ["missing", "different_project", "different_size", "changed_bytes"]
)
def test_invalid_permanent_reference_cannot_publish_or_consume_intent(core, damage: str) -> None:
    store = FileObjectStore(core.workspace.root)
    reference = asdict(store.publish_bytes("project", b"observed output"))
    path = core.workspace.root / str(reference["relative_path"])
    if damage == "missing":
        path.unlink()
    elif damage == "different_project":
        reference = asdict(store.publish_bytes("other-project", b"observed output"))
    elif damage == "different_size":
        reference["size"] = int(reference["size"]) + 1
    else:
        path.write_bytes(b"corrupt bytes")
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    stage_reference(core, reference)
    with pytest.raises((ValueError, OSError), match="object|reference|digest|size|project"):
        core.unit_of_work.commit("request")
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert core.unit_of_work.repo.current_revision("evidence", "evidence") == 0
    assert core.unit_of_work.repo._load().get("intents", {}).get("intent") is None


def test_valid_nested_reference_is_published_and_verified_on_readback(core) -> None:
    store = FileObjectStore(core.workspace.root)
    reference = asdict(store.publish_bytes("project", b"observed output"))
    stage_reference(core, reference)
    result = core.unit_of_work.commit("request")
    assert result["commit_sequence"] == 1
    assert FileCommitStore(core.workspace.root).read_current(verify_material=True) is not None


def test_attachment_loss_after_publication_is_reported_without_altering_saved_root(core) -> None:
    store = FileObjectStore(core.workspace.root)
    reference = asdict(store.publish_bytes("project", b"observed output"))
    stage_reference(core, reference)
    core.unit_of_work.commit("request")
    pointer = FileCommitStore(core.workspace.root).read_current()["pointer"]
    (core.workspace.root / str(reference["relative_path"])).unlink()
    with pytest.raises(CommitMaterialError, match="current commit") as error:
        FileCommitStore(core.workspace.root).read_current(verify_material=True)
    assert "object reference" in str(error.value.__cause__)
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == pointer


@pytest.mark.parametrize("size", [True, -1, 1.0, "1"])
def test_object_size_requires_an_exact_nonnegative_integer(tmp_path: Path, size: object) -> None:
    reference = asdict(FileObjectStore(tmp_path).publish_bytes("project", b"x"))
    reference["size"] = size
    with pytest.raises(ValueError, match="size"):
        verify_record_objects(tmp_path, {"object_ref": reference}, "project")


@pytest.mark.parametrize("field", ["project_id", "digest", "media_type", "relative_path"])
def test_partial_structured_reference_cannot_silently_drop_its_object(
    tmp_path: Path, field: str
) -> None:
    reference = asdict(FileObjectStore(tmp_path).publish_bytes("project", b"x"))
    del reference[field]
    with pytest.raises(ValueError, match="reference"):
        verify_record_objects(tmp_path, {"object_ref": reference}, "project")


def test_repeated_structured_and_digest_references_verify_one_file(tmp_path: Path) -> None:
    reference = asdict(FileObjectStore(tmp_path).publish_bytes("project", b"observed output"))
    body = {
        "first": {"object_ref": reference},
        "second": {"object_ref": reference},
        "output_object_digest": [reference["digest"], reference["digest"]],
        "artifact_digest": None,
    }
    with patch(
        "aitest.infrastructure.file_store.references.copy_unchanged_safe_bytes",
        wraps=security.copy_unchanged_safe_bytes,
    ) as scanner:
        verify_record_objects(tmp_path, body, "project")
    assert scanner.call_count == 1


def test_conflicting_duplicate_sizes_are_rejected_before_any_file_read(tmp_path: Path) -> None:
    reference = asdict(FileObjectStore(tmp_path).publish_bytes("project", b"x"))
    with (
        patch("aitest.infrastructure.file_store.references.copy_unchanged_safe_bytes") as scanner,
        pytest.raises(ValueError, match="conflicting"),
    ):
        verify_record_objects(
            tmp_path,
            {"refs": [reference, {**reference, "size": 2}]},
            "project",
        )
    scanner.assert_not_called()


def test_inline_hashes_remain_inline_and_do_not_require_named_objects(tmp_path: Path) -> None:
    body = {key: "sha256:" + "0" * 64 for key in ("digest", "content_digest", "projection_digest")}
    verify_record_objects(tmp_path, body, "project")


def test_existing_object_with_known_credential_cannot_be_adopted(tmp_path: Path) -> None:
    # A synthetic credential represents externally injected old material. The
    # publisher must not adopt it under an otherwise correct digest/path/size.
    reference = asdict(FileObjectStore(tmp_path).publish_bytes("project", b"synthetic-credential"))
    registry = security.KnownSecretRegistry()
    registry.register("synthetic-credential")
    with (
        patch.object(security, "_GLOBAL_REGISTRY", registry),
        pytest.raises(ValueError, match="safe byte identity"),
    ):
        verify_record_objects(tmp_path, {"object_ref": reference}, "project")
