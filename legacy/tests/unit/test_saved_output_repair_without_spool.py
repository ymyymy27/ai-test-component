"""Legacy metadata correction uses real permanent bytes, never another command."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.domain.evidence.evidence import EvidenceIntegrity, RedactionState
from tests.unit.test_execution_output_evidence import material, saved_output
from tests.unit.test_permanent_redaction_summary import saved_summary


def test_legacy_no_summary_state_can_be_corrected_using_only_permanent_bytes(tmp_path):
    collector, checkpoint, records = material(tmp_path)
    current = collector.collect("project-1", checkpoint)[0]
    records.save(replace(current, redaction_state=RedactionState.NOT_REQUIRED, gap_ids=()))
    path = (tmp_path / "spool/attempt-1/stdout.log").resolve()
    assert path.is_relative_to(tmp_path.resolve())
    path.unlink()
    objects = Mock(wraps=collector.objects)
    collector.objects = objects
    collector.publisher._object_store = objects
    corrected = collector.collect("project-1", checkpoint)[0]
    assert corrected.evidence_revision == 2
    assert corrected.redaction_state is RedactionState.UNKNOWN
    assert "redaction_summary_unavailable" in corrected.gap_ids
    assert corrected.object_digest == current.object_digest
    assert records.values[current.evidence_id][0]["redaction_state"] == "not_required"
    assert not objects.publish_bytes.called


def test_old_terminal_can_repair_current_projection_after_permanent_cleanup(tmp_path):
    runner, coordinator, unit, collector, checkpoint = saved_output(tmp_path)
    collector.collect("project-1", checkpoint)  # Publish verified actual bytes first.
    original_collect = collector.collect
    collector.collect = lambda project, checkpoint: tuple(replace(
        ref, redaction_state=RedactionState.NOT_REQUIRED, gap_ids=(),
    ) for ref in original_collect(project, checkpoint))
    coordinator._evidence_collector = collector
    old = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint).facts
    collector.collect = original_collect
    path = (tmp_path / "spool/attempt-1/stdout.log").resolve()
    assert path.is_relative_to(tmp_path.resolve())
    path.unlink()
    coordinator.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    result = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    assert result.evidence_refs[0].redaction_state.value == "unknown"
    assert result.evidence_refs[0].evidence_revision == 2
    assert unit.read(aggregate_kind="execution_facts", record_id=old.snapshot_commit_id,
                     revision=1).payload == old.model_dump(mode="json")
    sequence = unit.current_commit_sequence()
    coordinator.ensure_checkpoint_evidence(project_id="project-1", attempt=checkpoint.attempt)
    assert unit.current_commit_sequence() == sequence
    assert len(runner._execution_port.started) == 1


@pytest.mark.parametrize("damage", ["missing", "corrupt", "truncated"])
def test_metadata_repair_cannot_borrow_corrupt_permanent_bytes(tmp_path, damage):
    collector, checkpoint, records = material(tmp_path)
    ref = collector.collect("project-1", checkpoint)[0]
    records.save(replace(ref, redaction_state=RedactionState.NOT_REQUIRED, gap_ids=()))
    path = (tmp_path / "objects/project-1" / ref.object_digest.removeprefix("sha256:")).resolve()
    assert path.is_relative_to(tmp_path.resolve())
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"wrong" if damage == "corrupt" else b"")
    with pytest.raises((OSError, ValueError)):
        collector.collect("project-1", checkpoint)
    assert records.current_revision(aggregate_kind="evidence_ref", record_id=ref.evidence_id) == 1


@pytest.mark.parametrize("field,value", [
    ("project_id", "other"), ("run_id", "other"), ("step_id", "other"),
    ("attempt_id", "other"), ("object_size", 1), ("object_digest", "sha256:" + "f" * 64),
])
def test_metadata_repair_still_rejects_wrong_saved_basis(tmp_path, field, value):
    collector, checkpoint, records = material(tmp_path)
    ref = collector.collect("project-1", checkpoint)[0]
    records.save(replace(ref, redaction_state=RedactionState.NOT_REQUIRED, gap_ids=()))
    records.values[ref.evidence_id][0][field] = value
    with pytest.raises(ValueError):
        collector.collect("project-1", checkpoint)


def test_permanent_unknown_summary_is_not_duplicated_during_metadata_replay(tmp_path):
    _, coordinator, _, collector, checkpoint, _ = saved_summary(
        tmp_path, completeness="unknown", gaps=("secondary_filter_summary_unavailable",),
    )
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint)
    refs = collector.existing("project-1", checkpoint)
    assert collector.collect("project-1", checkpoint) == refs
    assert refs[0].gap_ids.count("redaction_summary_incomplete") == 1


def test_completeness_change_still_requires_actual_stream_material(tmp_path):
    collector, checkpoint, records = material(tmp_path)
    ref = collector.collect("project-1", checkpoint)[0]
    records.save(replace(ref, integrity=EvidenceIntegrity.PARTIAL))
    path = (tmp_path / "spool/attempt-1/stdout.log").resolve()
    assert path.is_relative_to(tmp_path.resolve())
    path.unlink()
    with pytest.raises(ValueError, match="output_material_unverified"):
        collector.collect("project-1", checkpoint)
    assert records.current_revision(aggregate_kind="evidence_ref", record_id=ref.evidence_id) == 1
