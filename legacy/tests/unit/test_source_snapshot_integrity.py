"""Source pinning must never persist known credentials or certify corrupt material."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore, SnapshotError
from aitest.infrastructure.security import KnownSecretRegistry


def _source(root: Path, content: bytes) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "main.py").write_bytes(content)
    return source


@pytest.mark.parametrize("secret", ["fictional-source-key", "虚构凭据", "1234"])
@pytest.mark.parametrize("offset", [0, 65532, 65535, 65536, 131070])
def test_unsafe_source_never_reaches_even_a_temporary_blob_write(
    tmp_path, monkeypatch, secret, offset
):
    content = b"safe\n" * (offset // 5) + b" " * (offset % 5) + secret.encode() + b"\n"
    source, workspace = _source(tmp_path, content), tmp_path / "workspace"
    registry = KnownSecretRegistry()
    registry.register(secret)
    store = FileSourceSnapshotStore(workspace, registry=registry)
    writes = []
    original = Path.open

    class WriteSpy:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def write(self, value):
            writes.append(value)
            return self.handle.write(value)

    def open_spy(path, mode="r", *args, **kwargs):
        handle = original(path, mode, *args, **kwargs)
        if mode in {"wb", "xb"} and path.is_relative_to(workspace):
            return WriteSpy(handle)
        return handle

    monkeypatch.setattr(Path, "open", open_spy)
    with pytest.raises(SnapshotError, match="敏感材料"):
        store.pin(canonical_path=str(source), purpose="analysis")
    assert secret.encode() not in b"".join(writes)
    assert list((workspace / "snapshots/blobs").iterdir()) == []
    assert not list((workspace / "snapshots").glob("*.json"))


@pytest.mark.skipif(os.name != "nt", reason="Windows Junction contract")
@pytest.mark.parametrize("use", ["pin", "materialize", "workspace", "detect_changes"])
def test_snapshot_rejects_real_junction_ancestors_before_writing(tmp_path, use):
    source, workspace = _source(tmp_path, b"safe\n"), tmp_path / "workspace"
    store = FileSourceSnapshotStore(workspace)
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(source)], capture_output=True
    )
    assert result.returncode == 0 and compat.is_junction(junction)
    before = (source / "main.py").read_bytes()
    if use == "detect_changes":
        # Replace the original directory with a junction to identical bytes: it
        # must not be certified unchanged merely because the hashes still match.
        original = tmp_path / "original"
        source.rename(original)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(source), str(original)], capture_output=True
        )
        assert result.returncode == 0 and compat.is_junction(source)
        drift = store.detect_changes(pinned["snapshot_id"])
        assert drift["state"] == "unknown" and drift["changed"] is True
        assert (original / "main.py").read_bytes() == before
        return
    with pytest.raises(SnapshotError, match="链接"):
        if use == "pin":
            store.pin(canonical_path=str(junction), purpose="analysis")
        elif use == "workspace":
            FileSourceSnapshotStore(junction / "new-workspace")
        else:
            store.materialize(pinned["snapshot_id"], str(junction / "new-target"))
    assert (source / "main.py").read_bytes() == before
    assert not (source / "new-workspace").exists()
    assert not (source / "new-target").exists()


def test_source_replacement_between_hash_and_copy_cannot_persist_unfiltered_bytes(
    tmp_path, monkeypatch
):
    source = _source(tmp_path, b"safe\n")
    secret = "fictional-late-source-key"
    registry = KnownSecretRegistry()
    registry.register(secret)
    workspace = tmp_path / "workspace"
    store = FileSourceSnapshotStore(workspace, registry=registry)
    original = store._store_blob

    def replace_source(path, digest):
        path.write_bytes(secret.encode())
        return original(path, digest)

    monkeypatch.setattr(store, "_store_blob", replace_source)
    with pytest.raises(SnapshotError, match="敏感材料"):
        store.pin(canonical_path=str(source), purpose="prepare")
    assert list((workspace / "snapshots/blobs").iterdir()) == []


def test_existing_corrupt_blob_is_rejected_without_republishing_a_manifest(tmp_path):
    source = _source(tmp_path, b"safe\n")
    workspace = tmp_path / "workspace"
    store = FileSourceSnapshotStore(workspace)
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    blob = workspace / "snapshots/blobs" / pinned["files"][0]["sha256"]
    blob.write_bytes(b"corrupt\n")
    before = (workspace / "snapshots" / (pinned["snapshot_id"] + ".json")).read_bytes()
    with pytest.raises(SnapshotError, match="摘要"):
        store.pin(canonical_path=str(source), purpose="analysis")
    assert (workspace / "snapshots" / (pinned["snapshot_id"] + ".json")).read_bytes() == before


def test_newly_registered_credential_blocks_reusing_or_materializing_a_legacy_blob(tmp_path):
    secret = "fictional-legacy-source-key"
    source = _source(tmp_path, ("DEMO = '" + secret + "'\n").encode())
    workspace = tmp_path / "workspace"
    registry = KnownSecretRegistry()
    store = FileSourceSnapshotStore(workspace, registry=registry)
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    registry.register(secret)
    restarted = FileSourceSnapshotStore(workspace, registry=registry)
    with pytest.raises(SnapshotError, match="敏感材料"):
        restarted.pin(canonical_path=str(source), purpose="analysis")
    target = tmp_path / "materialized"
    with pytest.raises(SnapshotError, match="敏感材料"):
        restarted.materialize(pinned["snapshot_id"], str(target))
    assert not target.exists()


@pytest.mark.parametrize(
    "change", ["files_missing", "files_invalid", "purpose", "size", "duplicate"]
)
def test_corrupt_manifest_cannot_be_certified_as_an_empty_or_valid_source(tmp_path, change):
    source, workspace = _source(tmp_path, b"safe\n"), tmp_path / "workspace"
    store = FileSourceSnapshotStore(workspace)
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    manifest = workspace / "snapshots" / (pinned["snapshot_id"] + ".json")
    if change == "files_missing":
        pinned.pop("files")
    elif change == "files_invalid":
        pinned["files"] = [None]
    elif change == "purpose":
        pinned["purpose"] = "modified"
    elif change == "size":
        pinned["files"][0]["size"] += 1
    else:
        pinned["files"] *= 2
    manifest.write_text(json.dumps(pinned), encoding="utf-8")
    target = tmp_path / "target"
    with pytest.raises(SnapshotError):
        store.materialize(pinned["snapshot_id"], str(target))
    assert not target.exists()


def test_corrupt_blob_is_detected_before_any_materialization_write(tmp_path):
    source, workspace = _source(tmp_path, b"safe\n"), tmp_path / "workspace"
    store = FileSourceSnapshotStore(workspace)
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    (workspace / "snapshots/blobs" / pinned["files"][0]["sha256"]).write_bytes(b"bad\n")
    target = tmp_path / "target"
    result = store.materialize(pinned["snapshot_id"], str(target))
    assert result["state"] == "rejected" and result["verified"] is False
    assert not target.exists()


def test_source_filter_does_not_change_safe_bytes_or_their_existing_identity(tmp_path):
    content = "print('安全源码')\n".encode()
    source, workspace = _source(tmp_path, content), tmp_path / "workspace"
    registry = KnownSecretRegistry()
    registry.register("unused-fictional-key")
    pinned = FileSourceSnapshotStore(workspace, registry=registry).pin(
        canonical_path=str(source), purpose="analysis"
    )
    assert pinned["files"][0]["sha256"] == hashlib.sha256(content).hexdigest()
    target = tmp_path / "target"
    result = FileSourceSnapshotStore(workspace, registry=registry).materialize(
        pinned["snapshot_id"], str(target)
    )
    assert result["verified"] is True
    assert (target / "main.py").read_bytes() == content


def test_unresolved_source_segment_is_bounded_without_publishing_partial_material(tmp_path):
    source, workspace = _source(tmp_path, b"z" * (2 * 1024 * 1024)), tmp_path / "workspace"
    with pytest.raises(SnapshotError, match="安全内存范围"):
        FileSourceSnapshotStore(workspace).pin(canonical_path=str(source), purpose="analysis")
    assert list((workspace / "snapshots/blobs").iterdir()) == []
    assert not list((workspace / "snapshots").glob("*.json"))
