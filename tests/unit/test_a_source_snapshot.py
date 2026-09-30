"""Unit tests for the file source snapshot adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aitest.infrastructure.adapters.source_snapshot import (
    FileSourceSnapshotStore,
    SnapshotError,
)
from aitest.infrastructure.file_store.workspace import Workspace


@pytest.fixture
def source(tmp_path: Path) -> Path:
    directory = tmp_path / "project-src"
    directory.mkdir()
    (directory / "main.py").write_text("print('hello')", encoding="utf-8")
    sub = directory / "pkg"
    sub.mkdir()
    (sub / "util.py").write_text("VALUE = 1", encoding="utf-8")
    return directory


@pytest.fixture
def store(tmp_path: Path) -> FileSourceSnapshotStore:
    Workspace(tmp_path)
    return FileSourceSnapshotStore(tmp_path)


def test_pin_has_real_hashes(store: FileSourceSnapshotStore, source: Path) -> None:
    record = store.pin(canonical_path=str(source), purpose="analysis")
    files = {item["relative_path"]: item for item in record["files"]}
    expected = hashlib.sha256(b"print('hello')").hexdigest()
    assert files["main.py"]["sha256"] == expected
    assert files["main.py"]["size"] == len(b"print('hello')")
    assert "pkg/util.py" in files
    assert record["snapshot_id"].startswith("snap-")


def test_pin_idempotent_same_id(store: FileSourceSnapshotStore, source: Path) -> None:
    first = store.pin(canonical_path=str(source), purpose="analysis")
    second = store.pin(canonical_path=str(source), purpose="prepare")
    assert first["snapshot_id"] == second["snapshot_id"]


def test_pin_default_excludes_git(store: FileSourceSnapshotStore, source: Path) -> None:
    git_dir = source / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref", encoding="utf-8")
    record = store.pin(canonical_path=str(source), purpose="analysis")
    paths = {item["relative_path"] for item in record["files"]}
    assert ".git/HEAD" not in paths


def test_pin_with_selected_paths(store: FileSourceSnapshotStore, source: Path) -> None:
    record = store.pin(
        canonical_path=str(source), purpose="analysis", selected_paths=("pkg",)
    )
    paths = {item["relative_path"] for item in record["files"]}
    assert paths == {"pkg/util.py"}


def test_read_pinned(store: FileSourceSnapshotStore, source: Path) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    loaded = store.read_pinned(str(pinned["snapshot_id"]))
    assert loaded["snapshot_id"] == pinned["snapshot_id"]


def test_materialize_copies_and_verifies(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    target = tmp_path / "out"
    result = store.materialize(str(pinned["snapshot_id"]), str(target))

    assert result["verified"] is True
    assert (target / "main.py").read_text(encoding="utf-8") == "print('hello')"
    assert (target / "pkg" / "util.py").exists()


def test_materialize_refuses_non_empty(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    target = tmp_path / "out"
    target.mkdir()
    (target / "x").write_text("x", encoding="utf-8")
    with pytest.raises(SnapshotError):
        store.materialize(str(pinned["snapshot_id"]), str(target))


def test_detect_changes_unchanged(store: FileSourceSnapshotStore, source: Path) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    result = store.detect_changes(str(pinned["snapshot_id"]))
    assert result["state"] == "unchanged"


def test_detect_changes_reports_modification(
    store: FileSourceSnapshotStore, source: Path
) -> None:
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    (source / "main.py").write_text("print('changed')", encoding="utf-8")
    result = store.detect_changes(str(pinned["snapshot_id"]))
    assert result["state"] == "changed"
    assert result["modified"] == ["main.py"]


def test_pin_missing_path_raises(store: FileSourceSnapshotStore) -> None:
    with pytest.raises(SnapshotError):
        store.pin(canonical_path="C:/does-not-exist-xyz", purpose="analysis")


def test_illegal_snapshot_id(store: FileSourceSnapshotStore) -> None:
    with pytest.raises(SnapshotError):
        store.read_pinned("../escape")
