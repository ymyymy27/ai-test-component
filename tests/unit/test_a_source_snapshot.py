"""Unit tests for the file source snapshot adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

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
    record: dict[str, Any] = store.pin(
        canonical_path=str(source), purpose="analysis"
    )
    files = {item["relative_path"]: item for item in record["files"]}
    expected = hashlib.sha256(b"print('hello')").hexdigest()
    assert files["main.py"]["sha256"] == expected
    assert files["main.py"]["size"] == len(b"print('hello')")
    assert "pkg/util.py" in files
    assert record["snapshot_id"].startswith("snap-")


def test_pin_idempotent_same_id(store: FileSourceSnapshotStore, source: Path) -> None:
    first = store.pin(canonical_path=str(source), purpose="analysis")
    second = store.pin(canonical_path=str(source), purpose="analysis")
    assert first["snapshot_id"] == second["snapshot_id"]
    # A-15：用途不同即使字节相同也是不同快照身份（blob 仍内容去重）。
    other_purpose = store.pin(canonical_path=str(source), purpose="prepare")
    assert other_purpose["snapshot_id"] != first["snapshot_id"]


def test_pin_default_excludes_git(store: FileSourceSnapshotStore, source: Path) -> None:
    git_dir = source / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref", encoding="utf-8")
    record: dict[str, Any] = store.pin(
        canonical_path=str(source), purpose="analysis"
    )
    paths = {item["relative_path"] for item in record["files"]}
    assert ".git/HEAD" not in paths


def test_pin_with_selected_paths(store: FileSourceSnapshotStore, source: Path) -> None:
    record: dict[str, Any] = store.pin(
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


def test_materialize_returns_expected_to_actual_mapping_and_digest(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    """AB-001 1.34：物化成功时返回期望→实际路径映射与映射摘要。"""
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    target = tmp_path / "out-map"
    result = store.materialize(str(pinned["snapshot_id"]), str(target))

    manifest = {
        item["relative_path"]: item
        for item in store.read_pinned(str(pinned["snapshot_id"]))["files"]
    }
    paths = result["paths"]
    assert {item["relative_path"] for item in paths} == set(manifest)
    for item in paths:
        pinned_item = manifest[item["relative_path"]]
        assert item["sha256"] == "sha256:" + pinned_item["sha256"]
        assert item["size"] == pinned_item["size"]
        actual = Path(item["actual_path"])
        assert actual.is_file() and actual.is_relative_to(target)
        assert (
            hashlib.sha256(actual.read_bytes()).hexdigest()
            == item["sha256"].removeprefix("sha256:")
        )
    encoded = json.dumps(
        paths, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert result["content_digest"] == "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_materialize_mapping_digest_changes_with_actual_destination(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    """映射摘要绑定实际 workdir：同一快照物化到不同目录得到不同摘要。"""
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    first = store.materialize(str(pinned["snapshot_id"]), str(tmp_path / "out-a"))
    second = store.materialize(str(pinned["snapshot_id"]), str(tmp_path / "out-b"))
    assert first["content_digest"] != second["content_digest"]
    assert [item["relative_path"] for item in first["paths"]] == [
        item["relative_path"] for item in second["paths"]
    ]


def test_materialize_rejection_keeps_refused_and_no_mapping_digest(
    store: FileSourceSnapshotStore, source: Path, tmp_path: Path
) -> None:
    """AB-001 1.34：拒绝时保持 refused，且不返回 paths 的 content_digest。"""
    pinned = store.pin(canonical_path=str(source), purpose="analysis")
    record = store.read_pinned(str(pinned["snapshot_id"]))
    victim = next(item for item in record["files"] if item["relative_path"] == "main.py")
    blob = store._blobs / victim["sha256"]  # noqa: SLF001 - 直击固定字节缺失的真实场景
    blob.unlink()

    result = store.materialize(str(pinned["snapshot_id"]), str(tmp_path / "out-refused"))

    assert result["state"] == "rejected"
    assert result["verified"] is False
    assert result["refused"] == ["main.py"]
    assert result["materialized"] == []
    assert "content_digest" not in result
    assert "paths" not in result


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
