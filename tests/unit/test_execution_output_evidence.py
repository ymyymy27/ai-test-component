"""Real byte publication/transactions with explicitly synthetic source context."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from aitest.application.evidence.execution_outputs import SavedExecutionEvidence
from aitest.application.execution.runner import SerialRunner
from aitest.domain.evidence.evidence import EvidenceIntegrity, EvidenceRef
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    ExitFact,
    ProcessTerminationReason,
    RecoveryCheckpoint,
    RecoveryRecord,
)
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.security import KnownSecretRegistry, UnsafeMaterialError
from tests.support.execution_authority import fixture_coordinator
from tests.unit.test_evidence_publication_integrity import _capture, _context
from tests.unit.test_saved_reuse_revocation import _runner
from tests.unit.test_serial_runner import FakeExecutionPort, _attempt, _request


class References:
    def __init__(self):
        self.values = {}

    def current_revision(self, *, aggregate_kind, record_id):
        return len(self.values.get(record_id, ()))

    def read(self, *, aggregate_kind, record_id, revision):
        return SimpleNamespace(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision,
            payload=self.values[record_id][revision - 1],
        )

    def save(self, ref):
        self.values.setdefault(ref.evidence_id, []).append(
            TypeAdapter(EvidenceRef).dump_python(ref, mode="json")
        )


class FixtureEvidence(SavedExecutionEvidence):
    def _context(self, project, checkpoint):
        return replace(_context(), source_instance_id=self.instance_id)


def material(tmp_path, records=None):
    spool, objects = FileSpoolStore(tmp_path), FileObjectStore(tmp_path)
    manifest = spool.persist_blocks((_capture(b"safe command evidence\n"),))
    attempt = replace(
        _attempt(AttemptState.COLLECTING), output_block_refs=manifest.blocks,
        capture_completeness=CaptureCompleteness.PARTIAL,
    )
    checkpoint = RecoveryRecord(
        RecoveryCheckpoint("run-1", "step-1", "attempt-1", "collecting"),
        attempt, "project-1",
    )
    records = records or References()
    collector = FixtureEvidence(
        records, None, spool, objects, workspace_id="workspace-1", instance_id="first-core",
    )
    return collector, checkpoint, records


def test_actual_bytes_are_published_and_core_restart_preserves_original_reference(tmp_path):
    collector, checkpoint, records = material(tmp_path)
    one = collector.collect("project-1", checkpoint)
    assert len(one) == 1 and one[0].evidence_revision == 1
    collector.validate(one)
    records.save(one[0])
    collector.instance_id = "new-core"
    assert collector.collect("project-1", checkpoint) == one
    assert records.current_revision(
        aggregate_kind="evidence_ref", record_id=one[0].evidence_id,
    ) == 1


def test_block_completeness_appends_revision_instead_of_rewriting_history(tmp_path):
    collector, checkpoint, records = material(tmp_path)
    block = checkpoint.attempt.output_block_refs[0]
    # The stable bytes may first be associated as partial; immutable ref stays available.
    partial = replace(
        collector.publisher.publish_blocks(_context(), (block,))[0],
        integrity=EvidenceIntegrity.PARTIAL, gap_ids=("partial_spool_block",),
    )
    records.save(partial)
    result = collector.collect("project-1", checkpoint)
    assert result[0].evidence_revision == 2
    assert result[0].integrity.value == "complete"
    assert records.values[partial.evidence_id][0]["integrity"] == "partial"


@pytest.mark.parametrize("field,value", [
    ("evidence_revision", True), ("object_size", "22"), ("source_instance_id", None),
    ("project_id", "another-project"), ("object_digest", "sha256:" + "f" * 64),
    ("extra", "unknown"),
])
def test_wrong_saved_reference_cannot_be_a_replayed_success(tmp_path, field, value):
    collector, checkpoint, records = material(tmp_path)
    ref = collector.collect("project-1", checkpoint)[0]
    records.save(ref)
    records.values[ref.evidence_id][0][field] = value
    with pytest.raises(ValueError):
        collector.collect("project-1", checkpoint)


def test_missing_object_at_final_validation_is_not_published_success(tmp_path):
    collector, checkpoint, _ = material(tmp_path)
    references = collector.collect("project-1", checkpoint)
    path = tmp_path / "objects/project-1" / references[0].object_digest.removeprefix("sha256:")
    assert path.resolve().is_relative_to(tmp_path.resolve())
    path.unlink()
    with pytest.raises(OSError):
        collector.validate(references)


def test_permanent_published_material_does_not_need_the_old_spool(tmp_path):
    collector, checkpoint, records = material(tmp_path)
    refs = collector.collect("project-1", checkpoint)
    records.save(refs[0])
    path = (tmp_path / "spool/attempt-1/stdout.log").resolve()
    assert path.is_relative_to(tmp_path.resolve())
    path.unlink()
    assert collector.existing("project-1", checkpoint) == refs


def saved_output(tmp_path, *, registry=None):
    unit = FileUnitOfWork(tmp_path, registry=registry)
    runner, coordinator, _ = _runner(tmp_path, unit=unit)
    started = runner.start_attempt(_attempt(), _request())
    checkpoint = coordinator.read_checkpoint(project_id="project-1", attempt_id=started.attempt_id)
    collector, captured, _ = material(tmp_path, unit.repo)
    block = captured.attempt.output_block_refs[0]
    exit_fact = ExitFact(
        started.attempt_id, "synthetic-exit",
        started.execution_handle_ref.process_start_identity, 0,
        last_block_index_by_stream=((block.stream_name, block.block_index),),
        saved_bytes_by_stream=((block.stream_name, block.length),),
        capture_completeness=CaptureCompleteness.COMPLETE,
        termination_reason=ProcessTerminationReason.NATURAL_EXIT,
        published_at=datetime.now(UTC),
    )
    checkpoint = replace(checkpoint, attempt=replace(
        checkpoint.attempt, state=AttemptState.COMPLETED,
        capture_completeness=CaptureCompleteness.COMPLETE,
        output_block_refs=captured.attempt.output_block_refs,
        exit_fact_ref=exit_fact,
    ))
    return runner, coordinator, unit, collector, checkpoint


@pytest.mark.parametrize("kind", [
    "execution_checkpoint", "evidence_ref", "execution_facts_current",
    "execution_checkpoint_refs", "execution_facts", "object_validation",
])
def test_output_evidence_checkpoint_and_snapshot_publish_atomically(tmp_path, monkeypatch, kind):
    _, coordinator, unit, collector, checkpoint = saved_output(tmp_path)
    coordinator._evidence_collector = collector
    before = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    sequence = unit.current_commit_sequence()
    original_stage, original_exact = unit.stage_record, unit.stage_record_exact

    def fail(original, **kwargs):
        if kwargs["aggregate_kind"] == kind:
            raise OSError("injected publication stage failure")
        return original(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(unit, "stage_record", lambda **kw: fail(original_stage, **kw))
        patch.setattr(unit, "stage_record_exact", lambda **kw: fail(original_exact, **kw))
        if kind == "object_validation":
            patch.setattr(collector, "validate", lambda _: (_ for _ in ()).throw(
                OSError("injected permanent material failure"),
            ))
        with pytest.raises(OSError, match="injected"):
            coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    assert unit.current_commit_sequence() == sequence
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == before
    assert unit.current_revision(
        aggregate_kind="evidence_ref", record_id="evidence:attempt-1:stdout:0",
    ) == 0
    assert coordinator.read_checkpoint(
        project_id="project-1", attempt_id="attempt-1",
    ).attempt.state is AttemptState.RUNNING
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    assert result.facts.attempts[0].state.value == "completed"
    assert len(result.facts.evidence_refs) == 1
    assert result.facts.evidence_refs[0].evidence_revision == 1
    collector.validate(collector.existing("project-1", checkpoint))


def test_lost_publication_reply_and_restart_are_read_only_without_old_spool(tmp_path, monkeypatch):
    runner, coordinator, unit, collector, checkpoint = saved_output(tmp_path)
    coordinator._evidence_collector = collector
    original = unit.commit

    def lose_reply():
        original()
        raise OSError("injected saved publication lost reply")

    with monkeypatch.context() as patch:
        patch.setattr(unit, "commit", lose_reply)
        with pytest.raises(OSError, match="lost reply"):
            coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    sequence = unit.current_commit_sequence()
    starts = list(runner._execution_port.started)
    path = (tmp_path / "spool/attempt-1/stdout.log").resolve()
    assert path.is_relative_to(tmp_path.resolve())
    path.unlink()
    reloaded = fixture_coordinator(FileUnitOfWork(tmp_path), ((_attempt(), _request()),))
    collector.records = reloaded._uow.repo
    collector.instance_id = "restarted-core"
    reloaded._evidence_collector = collector
    reloaded.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    assert unit.current_commit_sequence() == sequence
    assert runner._execution_port.started == starts
    facts = reloaded.read_current_facts(project_id="project-1", run_id="run-1")
    assert facts.evidence_refs[0].source_instance_id == "first-core"
    assert collector.existing("project-1", checkpoint)[0].evidence_revision == 1


def test_terminal_before_feature_repairs_evidence_once_without_execution(tmp_path):
    runner, coordinator, unit, collector, checkpoint = saved_output(tmp_path)
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    assert not coordinator.read_current_facts(project_id="project-1", run_id="run-1").evidence_refs
    starts = list(runner._execution_port.started)
    coordinator._evidence_collector = collector
    coordinator.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    sequence = unit.current_commit_sequence()
    coordinator.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    assert unit.current_commit_sequence() == sequence
    assert runner._execution_port.started == starts
    assert len(coordinator.read_current_facts(
        project_id="project-1", run_id="run-1",
    ).evidence_refs) == 1


def test_late_secret_in_reference_metadata_rejects_entire_publication(tmp_path):
    registry = KnownSecretRegistry()
    _, coordinator, unit, collector, checkpoint = saved_output(tmp_path, registry=registry)
    coordinator._evidence_collector = collector
    sequence = unit.current_commit_sequence()
    registry.register(collector.instance_id)
    with pytest.raises(UnsafeMaterialError):
        coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    assert unit.current_commit_sequence() == sequence
    assert unit.current_revision(
        aggregate_kind="evidence_ref", record_id="evidence:attempt-1:stdout:0",
    ) == 0


def test_old_terminal_publication_preserves_the_new_current_attempt(tmp_path):
    _, coordinator, unit, collector, checkpoint = saved_output(tmp_path)
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    request = _request()
    authorization = replace(
        request.authorization_ref, authorization_id="replacement-authorization",
        intent_id="replacement-intent",
    )
    request = replace(request, intent_id="replacement-intent", attempt_id="replacement",
                      authorization_ref=authorization)
    attempt = replace(_attempt(), attempt_id="replacement", intent_id="replacement-intent",
                      attempt_index=2)
    coordinator = fixture_coordinator(unit, ((attempt, request),))
    runner = SerialRunner(FakeExecutionPort(), commit_coordinator=coordinator)
    runner.start_attempt(attempt, request)
    before = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    coordinator._evidence_collector = collector
    coordinator.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    after = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    assert after.current_attempt_by_step == before.current_attempt_by_step
    assert after.current_attempt_by_step["step-1"] == "replacement"
    assert after.attempts == before.attempts and after.steps == before.steps
    assert after.coverage == before.coverage and after.run == before.run
    assert len(after.evidence_refs) == 1
    assert unit.read(aggregate_kind="execution_facts",
                     record_id=before.snapshot_commit_id, revision=1).payload == (
        before.model_dump(mode="json")
    )
    sequence = unit.current_commit_sequence()
    coordinator.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    assert unit.current_commit_sequence() == sequence
    assert len(runner._execution_port.started) == 1
