"""Exact warehouse refs and atomic publication; fixtures do not establish R or real AC."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aitest.application.execution.checkpoint_refs import (
    KIND,
    CheckpointRef,
    checkpoint_map_id,
    read_checkpoint_refs,
    read_referenced_checkpoint,
)
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import execution_payload_digest
from aitest.infrastructure.file_store.business_changes import BusinessChangeIndex
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.publication_backend import FilePublicationBackend
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.unit.test_complete_commit_closure import workspace as workspace
from tests.unit.test_current_attempt_transition_integrity import _saved_graph
from tests.unit.test_current_execution_snapshot import _batch, _publish
from tests.unit.test_saved_reuse_revocation import _runner
from tests.unit.test_serial_runner import _attempt, _request


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    root = tmp_path_factory.mktemp("checkpoint-refs")
    unit = FileUnitOfWork(root)
    first = _publish(unit, _batch(), "original-checkpoint")
    second = _publish(unit, _batch(), "checkpoint-advanced")
    return root, first, second


def test_body_counter_never_substitutes_for_warehouse_revision_after_restart(saved):
    root, first, second = saved
    repo = FileUnitOfWork(root).repo
    one, two = first.facts, second.facts
    assert one.attempts[0].attempt_revision == two.attempts[0].attempt_revision == 0
    refs1, refs2 = read_checkpoint_refs(repo, one), read_checkpoint_refs(repo, two)
    assert refs1["attempt-1"].revision == 1 and refs2["attempt-1"].revision == 2
    assert one.snapshot_cursor == first.committed["commit_sequence"]
    assert two.snapshot_cursor == second.committed["commit_sequence"]
    assert read_referenced_checkpoint(repo, refs1["attempt-1"], one.attempts[0], one).attempt == (
        _batch().checkpoint.attempt
    )
    assert repo.read(aggregate_kind="execution_facts", record_id=one.snapshot_commit_id,
                     revision=1).payload == one.model_dump(mode="json")
    assert BusinessChangeIndex.classification(KIND) == "business"
    assert [item[0] for item in first.committed["created"]][-3:] == [
        KIND, "execution_facts_current", "execution_facts",
    ]


class SubstituteRecords:
    def __init__(self, repo, *, mapping=None, checkpoint=None, envelope=None, revision=None):
        self.repo, self.mapping, self.checkpoint = repo, mapping, checkpoint
        self.envelope, self.revision = envelope, revision
        self.reads = []

    def current_revision(self, **kwargs):
        if kwargs["aggregate_kind"] == KIND and self.revision is not None:
            return self.revision
        return self.repo.current_revision(**kwargs)

    def read(self, **kwargs):
        self.reads.append(kwargs)
        record = self.repo.read(**kwargs)
        payload = self.mapping if kwargs["aggregate_kind"] == KIND else self.checkpoint
        return SimpleNamespace(
            aggregate_kind=record.aggregate_kind, record_id=record.record_id,
            revision=self.envelope if self.envelope is not None else record.revision,
            payload=deepcopy(record.payload if payload is None else payload),
        )


@pytest.mark.parametrize("change", [
    "schema", "project", "run", "workspace", "snapshot", "digest", "cursor_bool",
    "cursor_text", "snapshot_revision_bool", "missing_attempt", "extra_attempt",
    "record_id", "revision_bool", "revision_float", "revision_text", "revision_zero",
    "ref_digest", "unknown_field", "unknown_ref_field",
])
def test_reference_map_rejects_mismatched_material_before_any_checkpoint_read(saved, change):
    root, first, _ = saved
    repo, facts = FileUnitOfWork(root).repo, first.facts
    raw = deepcopy(repo.read(aggregate_kind=KIND, record_id=checkpoint_map_id(facts),
                             revision=1).payload)
    fields = {
        "schema": "schema_version", "project": "project_id", "run": "run_id",
        "workspace": "origin_workspace_id", "snapshot": "snapshot_commit_id",
        "digest": "snapshot_digest",
    }
    if change in fields:
        raw[fields[change]] = "another"
    elif change.startswith("cursor_"):
        raw["snapshot_cursor"] = True if change == "cursor_bool" else str(facts.snapshot_cursor)
    elif change == "snapshot_revision_bool":
        raw["snapshot_revision"] = True
    elif change == "missing_attempt":
        raw["checkpoint_refs"].clear()
    elif change == "extra_attempt":
        raw["checkpoint_refs"]["extra"] = dict(raw["checkpoint_refs"]["attempt-1"])
    elif change == "unknown_field":
        raw["extra"] = None
    elif change == "record_id":
        raw["checkpoint_refs"]["attempt-1"]["record_id"] = "another"
    elif change.startswith("revision_"):
        raw["checkpoint_refs"]["attempt-1"]["revision"] = {
            "revision_bool": True, "revision_float": 1.0, "revision_text": "1",
            "revision_zero": 0,
        }[change]
    elif change == "ref_digest":
        raw["checkpoint_refs"]["attempt-1"]["digest"] = "sha256:invalid"
    else:
        raw["checkpoint_refs"]["attempt-1"]["extra"] = None
    port = SubstituteRecords(repo, mapping=raw)
    with pytest.raises(ValueError):
        read_checkpoint_refs(port, facts)
    assert all(item["aggregate_kind"] == KIND for item in port.reads)


@pytest.mark.parametrize("revision", [0, 2, True, "1", 1.0])
def test_missing_or_changed_map_has_no_latest_fallback(saved, revision):
    root, first, _ = saved
    port = SubstituteRecords(FileUnitOfWork(root).repo, revision=revision)
    with pytest.raises(ValueError, match="immutable"):
        read_checkpoint_refs(port, first.facts)
    assert not port.reads


@pytest.mark.parametrize("change", [
    "envelope", "digest", "owner", "run", "step", "attempt", "body_revision",
    "checkpoint_identity", "input_identity", "typed_counter",
])
def test_exact_checkpoint_rejects_foreign_or_inconsistent_body_even_with_matching_digest(
    saved, change
):
    root, first, _ = saved
    repo, facts = FileUnitOfWork(root).repo, first.facts
    reference = read_checkpoint_refs(repo, facts)["attempt-1"]
    raw = deepcopy(repo.read(aggregate_kind="execution_checkpoint", record_id="attempt-1",
                             revision=1).payload)
    if change == "owner":
        raw["project_id"] = "foreign"
    elif change in {"run", "step", "attempt"}:
        raw["attempt"][change + "_id"] = "another"
    elif change == "body_revision":
        raw["attempt"]["revision"] += 1
    elif change == "checkpoint_identity":
        raw["checkpoint"]["attempt_id"] = "another"
    elif change == "input_identity":
        raw["checkpoint"]["resolved_input_digest"] = "another"
    elif change == "typed_counter":
        raw["attempt"]["revision"] = False
    elif change == "digest":
        raw["attempt"]["unknown_reason_ref"] = "another"
    port = SubstituteRecords(repo, checkpoint=raw, envelope=True if change == "envelope" else None)
    # Matching digest cannot turn foreign identities or a different projection into proof.
    if change != "digest":
        reference = replace(reference, digest=execution_payload_digest(raw))
    with pytest.raises(ValueError):
        read_referenced_checkpoint(port, reference, facts.attempts[0], facts)
    assert port.reads == [{
        "aggregate_kind": "execution_checkpoint", "record_id": "attempt-1", "revision": 1,
    }]


@pytest.mark.parametrize("kind", [KIND, "execution_facts_current", "execution_facts"])
def test_failed_publication_leaves_no_half_checkpoint_map_or_new_current(
    tmp_path, monkeypatch, kind
):
    unit = FileUnitOfWork(tmp_path)
    first = _publish(unit, _batch())
    before = unit.current_commit_sequence()
    stage, attempted = unit.stage_record, []

    def fault(**kwargs):
        attempted.append((kwargs["aggregate_kind"], kwargs["record_id"]))
        if kwargs["aggregate_kind"] == kind:
            raise OSError("injected checkpoint provenance publication failure")
        return stage(**kwargs)

    monkeypatch.setattr(unit, "stage_record", fault)
    with pytest.raises(OSError):
        _publish(unit, _batch(), "unpublished")
    restarted = FileUnitOfWork(tmp_path)
    assert restarted.current_commit_sequence() == before
    assert restarted.current_revision(
        aggregate_kind="execution_checkpoint", record_id="attempt-1"
    ) == 1
    assert ExecutionCommitCoordinator(restarted).read_current_facts(
        project_id="project-1", run_id="run-1"
    ) == first.facts
    for staged_kind, identity in attempted:
        if staged_kind == KIND:
            assert restarted.current_revision(aggregate_kind=KIND, record_id=identity) == 0
    assert read_checkpoint_refs(restarted.repo, first.facts)["attempt-1"].revision == 1


def test_original_start_replay_does_not_create_another_checkpoint_map(tmp_path):
    runner, coordinator, unit = _runner(tmp_path)
    runner.start_attempt(_attempt(), _request())
    facts = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    refs = read_checkpoint_refs(unit.repo, facts)
    assert refs["attempt-1"].revision >= 1
    before = unit.current_commit_sequence()
    recovered, _, _ = _runner(tmp_path)
    recovered.start_attempt(_attempt(), _request())
    assert unit.current_commit_sequence() == before
    assert read_checkpoint_refs(unit.repo, facts) == refs


def test_reference_requires_actual_positive_integer():
    for revision in [True, False, 0, -1, 1.0, "1"]:
        with pytest.raises(ValueError):
            CheckpointRef("attempt", revision, "sha256:" + "0" * 64)


def test_later_publication_preserves_unchanged_historical_checkpoint_revision(tmp_path):
    unit, coordinator, published, _ = _saved_graph(tmp_path, historical_middle=True)
    original = published.facts
    fact = next(item for item in original.attempts if item.attempt_id == "attempt-2")
    assert not fact.is_current
    refs = read_checkpoint_refs(unit.repo, original)
    checkpoint = read_referenced_checkpoint(unit.repo, refs[fact.attempt_id], fact, original)
    coordinator.commit_checkpoint(
        project_id=original.project_id,
        checkpoint=replace(checkpoint, attempt=replace(
            checkpoint.attempt, revision=checkpoint.attempt.revision + 1,
        )),
    )
    assert unit.current_revision(
        aggregate_kind="execution_checkpoint", record_id=fact.attempt_id,
    ) > (
        refs[fact.attempt_id].revision
    )
    unit.begin("unrelated-progress", original.project_id)
    _, updated = coordinator._stage_snapshot(original.model_copy(update={
        "coverage": original.coverage.model_copy(update={"evidence_gap_count": 9}),
    }))
    unit.commit()
    saved = read_checkpoint_refs(FileUnitOfWork(tmp_path).repo, updated)
    assert saved[fact.attempt_id] == refs[fact.attempt_id]
    assert read_referenced_checkpoint(
        unit.repo, saved[fact.attempt_id], fact, updated,
    ) == checkpoint


@pytest.mark.parametrize("published", [False, True])
def test_publication_fault_or_lost_reply_resolves_the_actual_complete_material(
    workspace, monkeypatch, published
):
    root = workspace
    unit = FileUnitOfWork(root)
    first = _publish(unit, _batch())
    before = unit.current_commit_sequence()
    replace_current = FilePublicationBackend.replace_current

    def fault(backend, data, *, previous):
        if published:
            replace_current(backend, data, previous=previous)
        raise OSError("injected fault around actual current publication")

    with monkeypatch.context() as patch:
        patch.setattr(FilePublicationBackend, "replace_current", fault)
        with pytest.raises(OSError):
            _publish(unit, _batch(), "reply-unknown")
    reopened = FileUnitOfWork(root)
    facts = ExecutionCommitCoordinator(reopened).read_current_facts(
        project_id="project-1", run_id="run-1",
    )
    assert FileCommitStore(root).read_current(verify_material=True) is not None
    refs = read_checkpoint_refs(reopened.repo, facts)
    checkpoint = read_referenced_checkpoint(
        reopened.repo, refs["attempt-1"], facts.attempts[0], facts,
    )
    assert checkpoint.attempt == _batch().checkpoint.attempt
    if published:
        assert reopened.current_commit_sequence() > before
        assert facts.snapshot_commit_id != first.facts.snapshot_commit_id
        assert facts.snapshot_cursor == reopened.current_commit_sequence()
        assert refs["attempt-1"].revision == 2
    else:
        assert reopened.current_commit_sequence() == before
        assert facts == first.facts and refs["attempt-1"].revision == 1
