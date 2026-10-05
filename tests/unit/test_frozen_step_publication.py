"""Execution progress never silently edits the frozen execution graph."""

from pathlib import Path

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.contracts.execution_facts import ExecutionFacts


class RecordingUnit:
    def __init__(self):
        self.staged = []

    def next_commit_seq(self):
        return "100"

    def current_revision(self, **kwargs):
        return 0

    def stage_record(self, **kwargs):
        self.staged.append(kwargs)
        return 1


@pytest.fixture
def publication(monkeypatch):
    facts = ExecutionFacts.model_validate_json(
        (
            Path(__file__).parents[1] / "contracts" / "fixtures/execution_facts/success.json"
        ).read_text(encoding="utf-8")
    )
    unit = RecordingUnit()
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
    staged, published = coordinator._stage_snapshot(ExecutionFacts.model_validate(raw))
    assert len(staged) == len(unit.staged) == 2
    assert published.steps[0].invalidated
    assert published.steps[0].case_id == facts.steps[0].case_id
