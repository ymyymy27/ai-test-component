"""A normal restart validates its published roots without reading history bodies."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aitest.bootstrap import CoreAssemblyBlocked, assemble_workspace_core
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.integrity import check_workspace


def initialized_workspace(root: Path) -> None:
    core = assemble_workspace_core(root, instance_id="first-core")
    try:
        unit = core.unit_of_work
        unit.begin("save", "project", intent_id="save")
        unit.stage_record(
            aggregate_kind="case",
            record_id="saved",
            expected_revision=0,
            payload={"project_id": "project", "summary": "saved"},
        )
        unit.commit("save")
    finally:
        core.lifetime_lock.release()


def test_default_restart_does_not_read_unrelated_historical_json(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    initialized_workspace(root)
    history = root / "diagnostics" / "old-stage"
    history.mkdir(parents=True)
    files = {history / f"{number}.json" for number in range(150)}
    for file in files:
        file.write_text(json.dumps({"old_diagnostic": "unchanged"}), encoding="utf-8")
    visited: set[Path] = set()
    read_bytes, read_text = Path.read_bytes, Path.read_text

    def traced_bytes(path: Path):
        visited.add(path)
        return read_bytes(path)

    def traced_text(path: Path, *args, **kwargs):
        visited.add(path)
        return read_text(path, *args, **kwargs)

    with (
        patch.object(Path, "read_bytes", traced_bytes),
        patch.object(Path, "read_text", traced_text),
    ):
        restarted = assemble_workspace_core(root, instance_id="second-core")
    try:
        assert not files & visited, "normal startup read unrelated historical diagnostics"
        assert restarted.unit_of_work.repo.current_revision("case", "saved") == 1
        assert restarted.recovery.full_history_checked is False
        assert restarted.recovery.last_commit_sequence == 1
    finally:
        restarted.lifetime_lock.release()


def test_explicit_full_inspection_still_finds_unrelated_history_damage(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    initialized_workspace(root)
    diagnostic = root / "diagnostics" / "old-stage.json"
    diagnostic.parent.mkdir(parents=True, exist_ok=True)
    diagnostic.write_bytes(b"{broken historical JSON")
    report = check_workspace(root)
    assert report["ok"] is False
    assert any("old-stage.json" in error for error in report["errors"])


def test_obsolete_legacy_pointer_is_not_used_as_the_current_authority(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    initialized_workspace(root)
    legacy = root / "records.json"
    legacy.write_bytes(b"{damaged inactive legacy pointer")
    core = assemble_workspace_core(root, instance_id="second-core")
    try:
        assert core.unit_of_work.repo.current_revision("case", "saved") == 1
        assert legacy.read_bytes() == b"{damaged inactive legacy pointer"
        assert check_workspace(root)["ok"] is False
    finally:
        core.lifetime_lock.release()


def test_current_material_damage_still_blocks_normal_restart(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    initialized_workspace(root)
    current = FileCommitStore(root).read_current()
    (root / "manifests" / f"{current['pointer']['manifest_digest']}.json").write_bytes(b"{}")
    with pytest.raises(CoreAssemblyBlocked):
        assemble_workspace_core(root, instance_id="second-core")


@pytest.mark.parametrize(
    "damage", ["unknown_request", "wrong_intent", "wrong_commit", "unparseable"]
)
def test_unknown_transaction_marker_is_preserved_and_blocks_restart(
    tmp_path: Path, damage: str
) -> None:
    root = tmp_path / "workspace"
    initialized_workspace(root)
    marker = {
        "request_id": "save",
        "intent_id": "save",
        "project_id": "project",
        "commit_sequence": 1,
        "state": "in_progress",
    }
    if damage == "unknown_request":
        marker["request_id"] = "unknown"
    elif damage == "wrong_intent":
        marker["intent_id"] = "wrong"
    elif damage == "wrong_commit":
        marker["commit_sequence"] = 2
    path = root / "transactions" / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = b"{torn" if damage == "unparseable" else json.dumps(marker).encode("utf-8")
    path.write_bytes(raw)
    with pytest.raises(CoreAssemblyBlocked):
        assemble_workspace_core(root, instance_id="second-core")
    assert path.read_bytes() == raw


def test_confirmed_marker_is_cleared_after_permanent_recovery_fact_is_saved(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    initialized_workspace(root)
    marker = {
        "request_id": "save",
        "intent_id": "save",
        "project_id": "project",
        "commit_sequence": 1,
        "state": "in_progress",
    }
    path = root / "transactions" / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker), encoding="utf-8")
    core = assemble_workspace_core(root, instance_id="second-core")
    try:
        assert core.recovery.state == "repaired"
        assert not path.exists()
        facts = list((root / "diagnostics" / "recovery").glob("*.json"))
        assert len(facts) == 1
        fact = json.loads(facts[0].read_text(encoding="utf-8"))
        assert fact["instance_id"] == "second-core"
        assert fact["marker"]["request_id"] == "save"
        assert fact["marker"]["intent_id"] == "save"
        assert (
            fact["manifest_digest"]
            == FileCommitStore(root).read_current()["pointer"]["manifest_digest"]
        )
    finally:
        core.lifetime_lock.release()


def test_failed_recovery_fact_write_does_not_discard_marker(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    initialized_workspace(root)
    marker = {
        "request_id": "save",
        "intent_id": "save",
        "project_id": "project",
        "commit_sequence": 1,
        "state": "in_progress",
    }
    path = root / "transactions" / "active.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(marker).encode("utf-8")
    path.write_bytes(raw)
    with (
        patch(
            "aitest.infrastructure.file_store.publication_backend.FilePublicationBackend.publish_immutable",
            side_effect=OSError("recovery fact write failed"),
        ),
        pytest.raises(CoreAssemblyBlocked),
    ):
        assemble_workspace_core(root, instance_id="second-core")
    assert path.read_bytes() == raw
