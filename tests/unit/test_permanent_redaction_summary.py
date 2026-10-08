"""Actual files/UOW with synthetic frozen source and execution authority."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.domain.evidence.evidence import RedactionSummary
from aitest.domain.execution.runs import AttemptState, OutputStreamName
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import fixture_coordinator
from tests.unit.test_execution_output_evidence import saved_output
from tests.unit.test_serial_runner import _attempt, _request


def saved_summary(tmp_path, *, completeness="complete", gaps=()):
    runner, coordinator, unit, collector, checkpoint = saved_output(tmp_path)
    collector.workspace_id = coordinator.read_current_facts(
        project_id="project-1", run_id="run-1",
    ).run.origin_workspace_id
    block = checkpoint.attempt.output_block_refs[0]
    summary_id = f"redaction:{block.attempt_id}:{block.stream_name.value}"
    path = (tmp_path / "spool/attempt-1/stdout.log").resolve()
    assert path.is_relative_to(tmp_path.resolve())
    content = path.read_bytes()
    # Rebuild only this test's spool before any publication, with the original bytes.
    from aitest.domain.execution.runs import CapturedOutputBlock
    from aitest.infrastructure.file_store.spool import FileSpoolStore
    manifest_path = tmp_path / "spool/attempt-1/manifest.json"
    assert manifest_path.resolve().is_relative_to(tmp_path.resolve())
    manifest_path.unlink()
    path.unlink()
    manifest = FileSpoolStore(tmp_path).persist_blocks((CapturedOutputBlock(
        "run-1", "step-1", "attempt-1", OutputStreamName.STDOUT, 0, 0,
        content, redaction_summary_id=summary_id,
    ),))
    checkpoint = replace(checkpoint, attempt=replace(
        checkpoint.attempt, output_block_refs=manifest.blocks,
    ))
    summary = RedactionSummary(
        policy_version="aitest.redaction/1.0", filtered_streams=("stdout",),
        filtered_ranges=(f"stdout:0-{len(content)}",),
        completeness=completeness, gap_reasons=gaps,
    )
    collector.spool.persist_redaction_summary("attempt-1", OutputStreamName.STDOUT, summary)
    coordinator._evidence_collector = collector
    return runner, coordinator, unit, collector, checkpoint, summary


def test_default_publication_freezes_actual_summary_in_the_same_commit(tmp_path):
    _, coordinator, unit, _, checkpoint, summary = saved_summary(tmp_path)
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    fact = result.facts.evidence_refs[0]
    assert fact.redaction_summary.policy_version == summary.policy_version
    assert fact.redaction_summary.filtered_ranges == summary.filtered_ranges
    assert "redaction_summary_provenance_unverified" not in fact.gap_ids
    record = unit.read(
        aggregate_kind="execution_redaction_summary", record_id="redaction:attempt-1:stdout",
        revision=1,
    )
    assert record.payload["summary"]["completeness"] == "complete"


def remove_temp_material(tmp_path):
    for relative in ("spool/attempt-1/stdout.log", "spool/attempt-1/redaction-stdout.json"):
        path = (tmp_path / relative).resolve()
        assert path.is_relative_to(tmp_path.resolve())
        path.unlink()


def test_lost_reply_restart_and_cleaned_spool_read_exact_permanent_summary(tmp_path, monkeypatch):
    runner, coordinator, unit, collector, checkpoint, summary = saved_summary(tmp_path)
    original = unit.commit

    def lose_reply():
        original()
        raise OSError("saved reply lost")

    with monkeypatch.context() as patch:
        patch.setattr(unit, "commit", lose_reply)
        with pytest.raises(OSError, match="reply lost"):
            coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    sequence = unit.current_commit_sequence()
    remove_temp_material(tmp_path)
    reloaded = fixture_coordinator(FileUnitOfWork(tmp_path), ((_attempt(), _request()),))
    collector.records = reloaded._uow.repo
    collector.instance_id = "restarted-core"
    reloaded._evidence_collector = collector
    reloaded.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    facts = reloaded.read_current_facts(project_id="project-1", run_id="run-1")
    assert facts.evidence_refs[0].redaction_summary.policy_version == summary.policy_version
    assert facts.evidence_refs[0].redaction_summary.completeness == "complete"
    assert unit.current_commit_sequence() == sequence
    assert len(runner._execution_port.started) == 1
    assert unit.current_revision(
        aggregate_kind="execution_redaction_summary", record_id="redaction:attempt-1:stdout",
    ) == 1


@pytest.mark.parametrize("kind", [
    "execution_redaction_summary", "evidence_ref", "execution_checkpoint_refs",
    "execution_facts", "execution_facts_current", "summary_object_validation",
])
def test_summary_record_object_ref_and_snapshot_are_one_atomic_batch(tmp_path, monkeypatch, kind):
    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    sequence = unit.current_commit_sequence()
    before = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    original_exact, original_stage = unit.stage_record_exact, unit.stage_record

    def fail(original, **kwargs):
        if kwargs["aggregate_kind"] == kind:
            raise OSError("injected summary batch failure")
        return original(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(unit, "stage_record_exact", lambda **kw: fail(original_exact, **kw))
        patch.setattr(unit, "stage_record", lambda **kw: fail(original_stage, **kw))
        if kind == "summary_object_validation":
            def fail_validation(_):
                raise OSError("injected summary batch failure")

            patch.setattr(collector, "validate_redaction_materials", fail_validation)
        with pytest.raises(OSError, match="injected"):
            coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    assert unit.current_commit_sequence() == sequence
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == before
    for kind in ("execution_redaction_summary", "evidence_ref"):
        assert unit.current_revision(aggregate_kind=kind, record_id=(
            "redaction:attempt-1:stdout" if kind == "execution_redaction_summary"
            else "evidence:attempt-1:stdout:0"
        )) == 0
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    assert result.facts.evidence_refs[0].redaction_summary.completeness == "complete"


@pytest.mark.parametrize("completeness,gaps", [
    ("unknown", ("secondary_filter_summary_unavailable",)),
    ("gap", ("reader_error",)), ("partial", ()),
])
def test_saved_summary_is_not_automatically_complete_statistics(tmp_path, completeness, gaps):
    _, coordinator, _, collector, checkpoint, _ = saved_summary(
        tmp_path, completeness=completeness, gaps=gaps,
    )
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    fact = result.facts.evidence_refs[0]
    assert fact.redaction_summary.completeness == completeness
    assert fact.redaction_summary.gap_reasons == gaps
    assert "redaction_summary_incomplete" in fact.gap_ids
    assert "redaction_summary_provenance_unverified" not in fact.gap_ids
    remove_temp_material(tmp_path)
    assert collector.existing("project-1", checkpoint)[0].gap_ids == fact.gap_ids


def test_active_stream_never_freezes_an_intermediate_summary(tmp_path):
    _, coordinator, unit, _, checkpoint, _ = saved_summary(tmp_path)
    checkpoint = replace(checkpoint, attempt=replace(
        checkpoint.attempt, state=AttemptState.COLLECTING, exit_fact_ref=None,
    ))
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    assert "redaction_summary_provenance_unverified" in result.facts.evidence_refs[0].gap_ids
    assert unit.current_revision(
        aggregate_kind="execution_redaction_summary", record_id="redaction:attempt-1:stdout",
    ) == 0


def test_terminal_missing_summary_stays_unknown_and_can_repair_once(tmp_path):
    runner, coordinator, unit, collector, checkpoint, summary = saved_summary(tmp_path)
    path = (tmp_path / "spool/attempt-1/redaction-stdout.json").resolve()
    assert path.is_relative_to(tmp_path.resolve())
    path.unlink()
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    old = result.facts
    assert "redaction_summary_provenance_unverified" in old.evidence_refs[0].gap_ids
    collector.spool.persist_redaction_summary("attempt-1", OutputStreamName.STDOUT, summary)
    coordinator.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    repaired = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    assert repaired.evidence_refs[0].evidence_revision == 2
    assert repaired.evidence_refs[0].redaction_summary.completeness == "complete"
    assert unit.read(aggregate_kind="execution_facts", record_id=old.snapshot_commit_id,
                     revision=1).payload == old.model_dump(mode="json")
    sequence = unit.current_commit_sequence()
    coordinator.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    assert unit.current_commit_sequence() == sequence
    assert len(runner._execution_port.started) == 1


@pytest.mark.parametrize("field,value", [
    ("project_id", "other"), ("origin_workspace_id", "other"), ("run_id", "other"),
    ("step_id", "other"), ("attempt_id", "other"), ("stream_name", "stderr"),
    ("schema_version", "unknown"), ("extra", "unknown"),
    ("code_identity.workspace_ref", "other"), ("output_blocks.0.block_index", False),
    ("summary.replacement_count", True), ("summary.completeness", "unknown"),
    ("object_ref.size", True), ("object_ref.relative_path", "other"),
])
def test_permanent_summary_replay_rejects_tampered_exact_body(tmp_path, field, value):
    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    reader = Mock(wraps=unit.repo)
    original = unit.repo.read

    def corrupt(**kwargs):
        record = original(**kwargs)
        if kwargs["aggregate_kind"] != "execution_redaction_summary":
            return record
        raw = deepcopy(record.payload)
        target = raw
        parts = field.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        target[parts[-1]] = value
        return SimpleNamespace(**kwargs, payload=raw)

    reader.read.side_effect = corrupt
    collector.records = reader
    with pytest.raises(ValueError):
        collector.existing("project-1", checkpoint)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "truncated"])
def test_permanent_summary_object_damage_is_not_read_success(tmp_path, damage):
    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    raw = unit.read(aggregate_kind="execution_redaction_summary",
                    record_id="redaction:attempt-1:stdout", revision=1).payload
    path = (tmp_path / raw["object_ref"]["relative_path"]).resolve()
    assert path.is_relative_to(tmp_path.resolve())
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"wrong" if damage == "corrupt" else b"")
    with pytest.raises((ValueError, OSError)):
        collector.existing("project-1", checkpoint)


def test_second_summary_revision_is_not_a_latest_fallback(tmp_path):
    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    reader = Mock(wraps=unit.repo)
    original = unit.repo.current_revision
    reader.current_revision.side_effect = lambda **kw: (
        2 if kw["aggregate_kind"] == "execution_redaction_summary" else original(**kw)
    )
    collector.records = reader
    with pytest.raises(ValueError, match="exactly one"):
        collector.existing("project-1", checkpoint)
    assert not any(call.kwargs["aggregate_kind"] == "execution_redaction_summary"
                   for call in reader.read.call_args_list)


def test_historical_source_checks_permanent_summary_after_temp_cleanup(tmp_path):
    from aitest.application.execution.reuse_material import validate_source_material

    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    source = SimpleNamespace(facts=result.facts, steps=(SimpleNamespace(
        attempt=result.facts.attempts[0], checkpoint=checkpoint,
    ),))
    remove_temp_material(tmp_path)
    reader = Mock(wraps=unit.repo)
    objects = Mock(wraps=collector.objects)
    validate_source_material(source, objects, collector.spool, reader)
    assert sum(call.kwargs["aggregate_kind"] == "execution_redaction_summary"
               for call in reader.read.call_args_list) == 1
    assert sum(call.args[0].media_type == "application/json"
               for call in objects.read_bytes.call_args_list) == 1
    assert not objects.publish_bytes.called


@pytest.mark.parametrize("damage", ["inline", "missing_record", "summary_object", "code_identity"])
def test_historical_source_does_not_trust_only_inline_summary(tmp_path, damage):
    from aitest.application.execution.reuse_material import validate_source_material

    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    facts = result.facts
    reader = Mock(wraps=unit.repo)
    if damage == "inline":
        fact = facts.evidence_refs[0]
        facts = facts.model_copy(update={"evidence_refs": (fact.model_copy(update={
            "redaction_summary": fact.redaction_summary.model_copy(update={
                "replacement_count": 99,
            }),
        }),)})
    elif damage == "missing_record":
        original = unit.repo.current_revision
        reader.current_revision.side_effect = lambda **kw: (
            0 if kw["aggregate_kind"] == "execution_redaction_summary" else original(**kw)
        )
    elif damage == "code_identity":
        fact = facts.evidence_refs[0]
        facts = facts.model_copy(update={"evidence_refs": (fact.model_copy(update={
            "code_identity": fact.code_identity.model_copy(update={"workspace_ref": "other"}),
        }),)})
    else:
        raw = unit.read(aggregate_kind="execution_redaction_summary",
                        record_id="redaction:attempt-1:stdout", revision=1).payload
        path = (tmp_path / raw["object_ref"]["relative_path"]).resolve()
        assert path.is_relative_to(tmp_path.resolve())
        path.write_bytes(b"wrong")
    source = SimpleNamespace(facts=facts, steps=(SimpleNamespace(
        attempt=facts.attempts[0], checkpoint=checkpoint,
    ),))
    with pytest.raises(ValueError, match="redaction summary|object digest"):
        validate_source_material(source, collector.objects, collector.spool, reader)


def test_late_secret_in_summary_cannot_publish_raw_metadata(tmp_path):
    from aitest.infrastructure.file_store.objects import FileObjectStore
    from aitest.infrastructure.security import KnownSecretRegistry

    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    registry = KnownSecretRegistry()
    registry.register("aitest.redaction/1.0")
    collector.objects = FileObjectStore(tmp_path, registry=registry)
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="object reference"):
        coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    assert unit.current_commit_sequence() == before
    assert unit.current_revision(aggregate_kind="execution_redaction_summary",
                                 record_id="redaction:attempt-1:stdout") == 0
    for path in (tmp_path / "objects").rglob("*"):
        if path.is_file():
            assert b"aitest.redaction/1.0" not in path.read_bytes()
