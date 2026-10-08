"""Default command source metadata must match its exact stored reference."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.application.execution.reuse_material import validate_source_material
from tests.unit.test_permanent_redaction_summary import saved_summary


def source(tmp_path):
    _, coordinator, unit, collector, checkpoint, _ = saved_summary(tmp_path)
    facts = coordinator.commit_checkpoint(project_id="project-1", checkpoint=checkpoint).facts
    selected = SimpleNamespace(facts=facts, steps=(SimpleNamespace(
        attempt=facts.attempts[0], checkpoint=checkpoint,
    ),))
    return selected, unit, collector


@pytest.mark.parametrize("field,value", [
    ("project_id", "other"), ("source_instance_id", "other"), ("run_id", "other"),
    ("step_id", "other"), ("attempt_id", "other"), ("object_size", True),
    ("evidence_revision", True), ("redaction_summary_ref", "another-summary"),
    ("projection_state", "blocked"), ("extra", "unknown"),
])
def test_inline_snapshot_and_readable_objects_cannot_hide_wrong_exact_reference(
    tmp_path, field, value,
):
    selected, unit, collector = source(tmp_path)
    original = unit.repo.read
    reader = Mock(wraps=unit.repo)

    def corrupt(**kwargs):
        record = original(**kwargs)
        if kwargs["aggregate_kind"] != "evidence_ref":
            return record
        raw = deepcopy(record.payload)
        raw[field] = value
        return SimpleNamespace(**kwargs, payload=raw)

    reader.read.side_effect = corrupt
    with pytest.raises(ValueError, match="evidence reference"):
        validate_source_material(selected, collector.objects, collector.spool, reader)


@pytest.mark.parametrize("field,value", [
    ("aggregate_kind", "another"), ("record_id", "another"), ("revision", True),
    ("revision", 2),
])
def test_wrong_exact_reference_envelope_is_rejected(tmp_path, field, value):
    selected, unit, collector = source(tmp_path)
    original = unit.repo.read
    reader = Mock(wraps=unit.repo)

    def corrupt(**kwargs):
        record = original(**kwargs)
        if kwargs["aggregate_kind"] != "evidence_ref":
            return record
        envelope = {**kwargs, "payload": record.payload, field: value}
        return SimpleNamespace(**envelope)

    reader.read.side_effect = corrupt
    with pytest.raises(ValueError, match="evidence reference"):
        validate_source_material(selected, collector.objects, collector.spool, reader)


def test_missing_exact_reference_is_not_filled_from_inline_metadata(tmp_path):
    selected, unit, collector = source(tmp_path)
    original = unit.repo.read
    reader = Mock(wraps=unit.repo)

    def absent(**kwargs):
        if kwargs["aggregate_kind"] == "evidence_ref":
            raise FileNotFoundError("synthetic missing exact reference")
        return original(**kwargs)

    reader.read.side_effect = absent
    with pytest.raises(ValueError, match="evidence reference is unreadable"):
        validate_source_material(selected, collector.objects, collector.spool, reader)


def test_saved_source_reference_is_exact_and_read_only_once(tmp_path):
    selected, unit, collector = source(tmp_path)
    reader = Mock(wraps=unit.repo)
    sequence = unit.current_commit_sequence()
    validate_source_material(selected, collector.objects, collector.spool, reader)
    calls = [call for call in reader.read.call_args_list
             if call.kwargs["aggregate_kind"] == "evidence_ref"]
    assert len(calls) == 1
    assert calls[0].kwargs["revision"] == selected.facts.evidence_refs[0].evidence_revision
    assert not any(call.kwargs["aggregate_kind"] == "evidence_ref"
                   for call in reader.current_revision.call_args_list)
    assert unit.current_commit_sequence() == sequence


def test_original_reference_does_not_fall_back_to_a_later_saved_version(tmp_path):
    selected, unit, collector = source(tmp_path)
    fact = selected.facts.evidence_refs[0]
    raw = dict(unit.repo.read(aggregate_kind="evidence_ref", record_id=fact.evidence_id,
                              revision=1).payload)
    raw["evidence_revision"] = 2
    raw["source_instance_id"] = "later-core"
    unit.begin("later-reference", "project-1")
    unit.stage_record_exact(aggregate_kind="evidence_ref", record_id=fact.evidence_id,
                            expected_revision=1, payload=raw)
    unit.commit()
    validate_source_material(selected, collector.objects, collector.spool, unit.repo)
    assert selected.facts.evidence_refs[0].source_instance_id == "first-core"


def test_published_namespace_cannot_attach_a_nonexistent_output_index(tmp_path):
    selected, unit, collector = source(tmp_path)
    fact = selected.facts.evidence_refs[0]
    selected.facts = selected.facts.model_copy(update={"evidence_refs": (fact.model_copy(update={
        "evidence_id": "evidence:attempt-1:stdout:999",
    }),)})
    with pytest.raises(ValueError, match="evidence reference lacks its original"):
        validate_source_material(selected, collector.objects, collector.spool, unit.repo)
