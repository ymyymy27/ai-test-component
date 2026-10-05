"""A progress snapshot cannot stand in for an authorized runtime change."""

from pathlib import Path

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.contracts.execution_facts import ExecutionFacts
from tests.unit.test_frozen_step_publication import RecordingUnit


@pytest.fixture
def publication(monkeypatch):
    facts = ExecutionFacts.model_validate_json(
        (Path(__file__).parents[1] / "contracts/fixtures/execution_facts/success.json").read_text(
            encoding="utf-8"
        )
    )
    unit = RecordingUnit()
    coordinator = ExecutionCommitCoordinator(unit)
    monkeypatch.setattr(coordinator, "read_current_facts", lambda **kwargs: facts)
    return coordinator, unit, facts


@pytest.mark.parametrize("allow_current_change", [False, True])
@pytest.mark.parametrize(
    "field,value",
    [
        ("driver", "stepwise"),
        ("selected_scope", ["case-1", "unregistered-case"]),
        ("source_binding_digest", "sha256:other-source"),
        ("source_binding_digest", None),
        ("runtime_revision_refs", ["unreadable-revision"]),
    ],
)
def test_progress_cannot_replace_a_controlled_change(
    publication, field, value, allow_current_change
):
    coordinator, unit, facts = publication
    raw = facts.model_dump(mode="json")
    raw["run"][field] = value
    if field == "runtime_revision_refs":
        raw[field] = value  # A self-consistent view still proves no persisted change.
    if field == "selected_scope":
        raw["coverage"]["selected_case_ids"] = value
    with pytest.raises(ValueError, match="frozen run|runtime revision"):
        coordinator._stage_snapshot(
            ExecutionFacts.model_validate(raw), allow_current_change=allow_current_change
        )
    assert not unit.staged


@pytest.mark.parametrize("allow_current_change", [False, True])
@pytest.mark.parametrize("operation", ["remove", "replace", "reorder"])
def test_progress_cannot_remove_replace_or_reorder_saved_runtime_sequence(
    publication, monkeypatch, operation, allow_current_change
):
    coordinator, unit, initial = publication
    raw = initial.model_dump(mode="json")
    raw["runtime_revision_refs"] = raw["run"]["runtime_revision_refs"] = [
        "revision-1",
        "revision-2",
    ]
    previous = ExecutionFacts.model_validate(raw)
    monkeypatch.setattr(coordinator, "read_current_facts", lambda **kwargs: previous)
    value = {
        "remove": [],
        "replace": ["other-1", "other-2"],
        "reorder": ["revision-2", "revision-1"],
    }[operation]
    raw["runtime_revision_refs"] = raw["run"]["runtime_revision_refs"] = value
    with pytest.raises(ValueError, match="frozen run|runtime revision"):
        coordinator._stage_snapshot(
            ExecutionFacts.model_validate(raw), allow_current_change=allow_current_change
        )
    assert not unit.staged


@pytest.mark.parametrize("allow_current_change", [False, True])
def test_unverified_source_cannot_become_verified_by_adding_a_digest(
    publication, monkeypatch, allow_current_change
):
    coordinator, unit, initial = publication
    previous = initial.model_copy(
        update={"run": initial.run.model_copy(update={"source_binding_digest": None})}
    )
    monkeypatch.setattr(coordinator, "read_current_facts", lambda **kwargs: previous)
    with pytest.raises(ValueError, match="frozen run"):
        coordinator._stage_snapshot(initial, allow_current_change=allow_current_change)
    assert not unit.staged


@pytest.mark.parametrize("field", ["mandatory_case_ids", "selected_case_ids"])
@pytest.mark.parametrize("allow_current_change", [False, True])
def test_progress_cannot_edit_coverage_scope_alone(publication, field, allow_current_change):
    coordinator, unit, facts = publication
    raw = facts.model_dump(mode="json")
    raw["coverage"][field] = []
    with pytest.raises(ValueError, match="frozen.*scope"):
        coordinator._stage_snapshot(
            ExecutionFacts.model_validate(raw), allow_current_change=allow_current_change
        )
    assert not unit.staged


@pytest.mark.parametrize("mode", ["different", "empty", "duplicate"])
def test_inconsistent_runtime_sequence_cannot_be_initially_published(
    publication, monkeypatch, mode
):
    coordinator, unit, facts = publication
    monkeypatch.setattr(coordinator, "read_current_facts", lambda **kwargs: None)
    raw = facts.model_dump(mode="json")
    raw["run"]["runtime_revision_refs"] = {
        "different": ["other-1"],
        "empty": [""],
        "duplicate": ["revision-1", "revision-1"],
    }[mode]
    raw["runtime_revision_refs"] = (
        ["revision-1"] if mode == "different" else raw["run"]["runtime_revision_refs"]
    )
    with pytest.raises(ValueError, match="runtime revision"):
        coordinator._stage_snapshot(ExecutionFacts.model_validate(raw))
    assert not unit.staged


def test_unchanged_controlled_fields_allow_real_progress(publication):
    coordinator, unit, facts = publication
    changed = facts.model_copy(
        update={"coverage": facts.coverage.model_copy(update={"evidence_gap_count": 5})}
    )
    staged, published = coordinator._stage_snapshot(changed)
    assert len(staged) == len(unit.staged) == 2
    assert published.coverage.evidence_gap_count == 5
    assert published.run.driver == facts.run.driver
