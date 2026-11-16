"""The writer epoch identifies admission of a core, not individual transactions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore


def write_case(unit, request: str, record: str) -> None:
    unit.begin(request, "project", intent_id=request)
    unit.stage_record(
        aggregate_kind="case",
        record_id=record,
        expected_revision=0,
        payload={"project_id": "project", "summary": record},
    )
    unit.commit(request)


def test_one_core_keeps_one_epoch_for_multiple_business_transactions(tmp_path: Path) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="first-core")
    admitted_epoch = core.workspace.identity["writer_epoch"]
    try:
        write_case(core.unit_of_work, "first", "first")
        write_case(core.unit_of_work, "second", "second")
        stored = json.loads((tmp_path / "workspace.json").read_text(encoding="utf-8"))
        assert stored["writer_epoch"] == admitted_epoch
        assert (
            FileCommitStore(tmp_path).read_current()["manifest"]["writer_epoch"] == admitted_epoch
        )
    finally:
        core.lifetime_lock.release()


def test_old_core_unit_cannot_write_after_new_core_admission(tmp_path: Path) -> None:
    first = assemble_workspace_core(tmp_path, instance_id="first-core")
    first_epoch = first.workspace.identity["writer_epoch"]
    first.lifetime_lock.release()
    second = assemble_workspace_core(tmp_path, instance_id="second-core")
    try:
        assert second.workspace.identity["writer_epoch"] == first_epoch + 1
        with pytest.raises(ValueError, match="epoch"):
            first.unit_of_work.begin("late-old-result", "project", intent_id="late-old-result")
        assert first.unit_of_work.project is None
        write_case(second.unit_of_work, "new-result", "new-result")
        assert second.unit_of_work.repo.current_revision("case", "new-result") == 1
    finally:
        if first.unit_of_work.project is not None:
            first.unit_of_work.rollback("late-old-result")
        second.lifetime_lock.release()


def test_epoch_changed_after_begin_cannot_publish_or_consume_intent(tmp_path: Path) -> None:
    core = assemble_workspace_core(tmp_path, instance_id="first-core")
    unit = core.unit_of_work
    try:
        unit.begin("request", "project", intent_id="intent")
        unit.stage_record(
            aggregate_kind="case",
            record_id="case",
            expected_revision=0,
            payload={"project_id": "project", "summary": "case"},
        )
        pointer = FileCommitStore(tmp_path).read_current()["pointer"]
        identity = tmp_path / "workspace.json"
        saved = json.loads(identity.read_text(encoding="utf-8"))
        saved["writer_epoch"] += 1
        identity.write_text(json.dumps(saved), encoding="utf-8")
        with pytest.raises(ValueError, match="epoch"):
            unit.commit("request")
        assert FileCommitStore(tmp_path).read_current()["pointer"] == pointer
        assert unit.repo.current_revision("case", "case") == 0
        assert unit.repo._load().get("intents", {}).get("intent") is None
    finally:
        if unit.project is not None:
            unit.rollback("request")
        core.lifetime_lock.release()
