"""Saved C facts must not be repaired by coercion at the consumer boundary."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import execution_payload_digest
from aitest.application.execution.runtime_revision import (
    SavedRuntimeRevisionReader,
    SnapshotContentRef,
)
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.unit.test_current_execution_snapshot import _batch, _publish


@pytest.fixture
def saved(tmp_path):
    unit = FileUnitOfWork(tmp_path)
    facts = _publish(unit, _batch()).facts
    return unit, facts


def substitute(unit, monkeypatch, facts, change):
    payload = deepcopy(facts.model_dump(mode="json"))
    change(payload)
    digest = execution_payload_digest(payload)
    original = unit.read

    def read(**kwargs):
        record = original(**kwargs)
        raw = record.payload
        if kwargs["aggregate_kind"] == "execution_facts":
            raw = payload
        elif kwargs["aggregate_kind"] == "execution_facts_current":
            raw = {**raw, "digest": digest}
        return SimpleNamespace(
            aggregate_kind=record.aggregate_kind, record_id=record.record_id,
            revision=record.revision, payload=raw,
        )

    monkeypatch.setattr(unit, "read", read)
    return payload, digest


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("snapshot_cursor",), True),
        (("snapshot_cursor",), 1.0),
        (("snapshot_revision",), "1"),
        (("steps", 0, "ordinal"), "1"),
        (("steps", 0, "required_for_case"), "false"),
        (("attempts", 0, "is_current"), 1),
        (("attempts", 0, "timed_out"), "false"),
        (("attempts", 0, "attempt_index"), True),
    ],
)
def test_current_consumer_rejects_noncanonical_saved_types(saved, monkeypatch, path, value):
    unit, facts = saved

    def change(payload):
        target = payload
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value

    substitute(unit, monkeypatch, facts, change)
    with pytest.raises(ValueError):
        ExecutionCommitCoordinator(unit).read_current_facts(project_id="project-1", run_id="run-1")


@pytest.mark.parametrize("change", ["run", "step", "current", "history", "revision"])
def test_frozen_runtime_source_rejects_inconsistent_original_facts(saved, monkeypatch, change):
    unit, facts = saved

    def damage(payload):
        if change == "run":
            payload["run"]["run_id"] = "foreign-run"
        elif change == "step":
            payload["steps"][0]["run_id"] = "foreign-run"
        elif change == "current":
            payload["current_attempt_by_step"] = {}
        elif change == "history":
            payload["attempts"][0]["is_current"] = False
        else:
            payload["snapshot_revision"] = 2

    payload, digest = substitute(unit, monkeypatch, facts, damage)
    reference = SnapshotContentRef(
        snapshot_commit_id=facts.snapshot_commit_id,
        snapshot_cursor=payload["snapshot_cursor"], digest=digest,
    )
    with pytest.raises(ValueError):
        SavedRuntimeRevisionReader(unit).read_snapshot(
            project_id="project-1", run_id="run-1", reference=reference
        )


def test_frozen_source_reads_original_material_after_current_advances_and_restart(saved):
    unit, first = saved
    second = _publish(unit, _batch(), request="later")
    assert second.facts.snapshot_commit_id != first.snapshot_commit_id
    restarted = FileUnitOfWork(unit.workspace.root)
    assert SavedRuntimeRevisionReader(restarted).read_snapshot(
        project_id="project-1", run_id="run-1", reference=SnapshotContentRef.of(first)
    ) == first


@pytest.mark.parametrize("field", ["project", "run", "cursor", "digest", "missing"])
def test_wrong_frozen_reference_never_substitutes_latest_success(saved, monkeypatch, field):
    unit, first = saved
    _publish(unit, _batch(), request="later")
    ref = SnapshotContentRef.of(first)
    project, run = "project-1", "run-1"
    if field == "project":
        project = "another-project"
    elif field == "run":
        run = "another-run"
    else:
        ref = ref.model_copy(update={
            {"cursor": "snapshot_cursor", "digest": "digest", "missing": "snapshot_commit_id"}[
                field
            ]: {"cursor": first.snapshot_cursor + 1, "digest": "sha256:" + "0" * 64,
                "missing": "absent-snapshot"}[field]
        })
    original = unit.read
    reads = []

    def read(**kwargs):
        reads.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(unit, "read", read)
    with pytest.raises(ValueError):
        SavedRuntimeRevisionReader(unit).read_snapshot(
            project_id=project, run_id=run, reference=ref
        )
    assert all(
        item == {"aggregate_kind": "execution_facts", "record_id": ref.snapshot_commit_id,
                 "revision": 1}
        for item in reads
    )


@pytest.mark.parametrize("revision", [True, 1.0, "1", 0, 2])
def test_frozen_source_requires_immutable_warehouse_revision_before_read(
    saved, monkeypatch, revision
):
    unit, facts = saved
    original = unit.current_revision

    def current(**kwargs):
        return revision if kwargs["aggregate_kind"] == "execution_facts" else original(**kwargs)

    monkeypatch.setattr(unit, "current_revision", current)
    reads = []
    monkeypatch.setattr(unit, "read", lambda **kwargs: reads.append(kwargs))
    with pytest.raises(ValueError, match="immutable"):
        SavedRuntimeRevisionReader(unit).read_snapshot(
            project_id="project-1", run_id="run-1", reference=SnapshotContentRef.of(facts)
        )
    assert reads == []


@pytest.mark.parametrize("field", ["kind", "identity", "revision", "material"])
def test_frozen_source_checks_actual_envelope_and_original_body(saved, monkeypatch, field):
    unit, facts = saved
    original = unit.read

    def read(**kwargs):
        record = original(**kwargs)
        raw = deepcopy(record.payload)
        if field == "material":
            raw["run"]["intent_id"] = "different-material"
        return SimpleNamespace(
            aggregate_kind="run" if field == "kind" else record.aggregate_kind,
            record_id="another-snapshot" if field == "identity" else record.record_id,
            revision=True if field == "revision" else record.revision, payload=raw,
        )

    monkeypatch.setattr(unit, "read", read)
    with pytest.raises(ValueError):
        SavedRuntimeRevisionReader(unit).read_snapshot(
            project_id="project-1", run_id="run-1", reference=SnapshotContentRef.of(facts)
        )


def test_frozen_source_does_not_read_current_pointer(saved, monkeypatch):
    unit, facts = saved
    original = unit.read

    def read(**kwargs):
        assert kwargs["aggregate_kind"] == "execution_facts"
        assert kwargs["record_id"] == facts.snapshot_commit_id and kwargs["revision"] == 1
        return original(**kwargs)

    monkeypatch.setattr(unit, "read", read)
    assert SavedRuntimeRevisionReader(unit).read_snapshot(
        project_id="project-1", run_id="run-1", reference=SnapshotContentRef.of(facts)
    ) == facts
