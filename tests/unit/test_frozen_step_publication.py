"""Execution progress never silently edits the frozen execution graph."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.execution.runs import AttemptState, RecoveryRecord
from tests.unit.test_current_execution_snapshot import _batch


class RecordingUnit:
    def __init__(self, checkpoint):
        self.staged = []
        self.checkpoint = replace(checkpoint, project_id="project-1")

    def next_commit_seq(self):
        return "100"

    def current_revision(self, **kwargs):
        return int(kwargs["aggregate_kind"] == "execution_checkpoint")

    def read(self, *, aggregate_kind, record_id, revision):
        assert (aggregate_kind, record_id, revision) == (
            "execution_checkpoint", self.checkpoint.attempt.attempt_id, 1,
        )
        return SimpleNamespace(
            aggregate_kind=aggregate_kind, record_id=record_id, revision=revision,
            payload=TypeAdapter(RecoveryRecord).dump_python(self.checkpoint, mode="json"),
        )

    def stage_record(self, **kwargs):
        self.staged.append(kwargs)
        return kwargs["expected_revision"] + 1


@pytest.fixture
def publication(monkeypatch):
    batch = _batch()
    facts = batch.facts
    unit = RecordingUnit(batch.checkpoint)
    coordinator = ExecutionCommitCoordinator(unit)
    monkeypatch.setattr(coordinator, "read_current_facts", lambda **kwargs: facts)
    return coordinator, unit, facts


@pytest.mark.parametrize(
    "field,value",
    [
        ("case_id", "foreign-case"),
        ("required_for_case", False),
        ("ordinal", 99),
        ("level", "L3"),
        ("dependency_step_ids", ["foreign-upstream"]),
        ("registered_entry_ref", "foreign-entry"),
        ("assertion_refs", ["foreign-basis"]),
        ("evidence_requirement_ids", ["foreign-requirement"]),
    ],
)
@pytest.mark.parametrize("allow_current_change", [False, True])
def test_progress_and_attempt_claim_cannot_edit_frozen_step_content(
    publication, field, value, allow_current_change
):
    coordinator, unit, facts = publication
    raw = facts.model_dump(mode="json")
    raw["steps"][0][field] = value
    with pytest.raises(ValueError, match="frozen step execution basis"):
        coordinator._stage_snapshot(
            ExecutionFacts.model_validate(raw), allow_current_change=allow_current_change
        )
    assert not unit.staged


@pytest.mark.parametrize("allow_current_change", [False, True])
def test_step_revision_cannot_be_rewritten_with_a_matching_attempt_projection(
    publication, allow_current_change
):
    coordinator, unit, facts = publication
    raw = facts.model_dump(mode="json")
    raw["steps"][0]["step_revision_ref"]["digest"] = "sha256:forged-step"
    for attempt in raw["attempts"]:
        if attempt["step_id"] == raw["steps"][0]["step_id"]:
            attempt["step_revision_ref"]["digest"] = "sha256:forged-step"
    with pytest.raises(ValueError, match="frozen step execution basis"):
        coordinator._stage_snapshot(
            ExecutionFacts.model_validate(raw), allow_current_change=allow_current_change
        )
    assert not unit.staged


def test_new_step_cannot_hide_behind_the_attempt_claim_flag(publication):
    coordinator, unit, facts = publication
    raw = facts.model_dump(mode="json")
    step = {**raw["steps"][0], "step_id": "unregistered-step", "current_attempt_id": None}
    raw["steps"].append(step)
    raw["current_attempt_by_step"][step["step_id"]] = None
    with pytest.raises(ValueError, match="add or remove frozen steps"):
        coordinator._stage_snapshot(ExecutionFacts.model_validate(raw), allow_current_change=True)
    assert not unit.staged


def test_execution_progress_fields_remain_publishable(publication):
    coordinator, unit, facts = publication
    raw = facts.model_dump(mode="json")
    raw["steps"][0].update(
        state="invalidated",
        invalidated=True,
        invalidated_by="actual-dependency-change",
        gap_ids=["basis-outdated"],
    )
    raw["attempts"][0]["state"] = "invalidated"
    checkpoint = replace(
        unit.checkpoint, attempt=replace(unit.checkpoint.attempt, state=AttemptState.INVALIDATED)
    )
    payload = TypeAdapter(RecoveryRecord).dump_python(checkpoint, mode="json")
    revision = unit.stage_record(
        aggregate_kind="execution_checkpoint", record_id=checkpoint.attempt.attempt_id,
        expected_revision=1, payload=payload,
    )
    staged, published = coordinator._stage_snapshot(
        ExecutionFacts.model_validate(raw),
        checkpoint_refs={checkpoint.attempt.attempt_id: (revision, payload)},
    )
    assert len(staged) == 3 and len(unit.staged) == 4
    assert published.steps[0].invalidated
    assert published.steps[0].case_id == facts.steps[0].case_id
