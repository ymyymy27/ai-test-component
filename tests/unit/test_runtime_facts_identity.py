"""Exact C fact relationships before B uses them for runtime revision decisions."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aitest.application.planning.run_mode import runtime_facts_from_execution_facts
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.planning.runtime_revision import AttemptRuntimeState

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "docs/接口对接/进行中/CD-001-ExecutionFacts/fixtures"
)


def payload(name="failure"):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "damage",
    [
        "runtime_refs",
        "runtime_order",
        "runtime_duplicate",
        "runtime_empty",
        "step_owner",
        "attempt_step",
        "attempt_owner",
        "non_current_attempt",
        "hidden_current",
        "run_revision",
        "duplicate_step",
        "duplicate_attempt",
        "extra_map_key",
        "missing_map_key",
        "unknown_current",
        "orphan_history",
    ],
)
def test_inconsistent_fact_identity_cannot_supply_revision_authority(damage):
    body = payload()
    if damage == "runtime_refs":
        body["runtime_revision_refs"] = ["revision-real"]
        body["run"]["runtime_revision_refs"] = ["revision-other"]
    elif damage == "runtime_order":
        body["runtime_revision_refs"] = ["revision-1", "revision-2"]
        body["run"]["runtime_revision_refs"] = ["revision-2", "revision-1"]
    elif damage in {"runtime_duplicate", "runtime_empty"}:
        refs = ["revision-1", "revision-1"] if damage == "runtime_duplicate" else [" "]
        body["runtime_revision_refs"] = refs
        body["run"]["runtime_revision_refs"] = refs
    elif damage == "step_owner":
        body["steps"][0]["run_id"] = "foreign-run"
    elif damage == "attempt_step":
        body["attempts"][0]["step_id"] = body["steps"][1]["step_id"]
    elif damage == "attempt_owner":
        body["attempts"][0]["run_id"] = "foreign-run"
    elif damage == "non_current_attempt":
        body["attempts"][0]["is_current"] = False
    elif damage == "hidden_current":
        step = body["steps"][0]
        step["current_attempt_id"] = None
        body["current_attempt_by_step"][step["step_id"]] = None
    elif damage == "run_revision":
        body["run_revision"] += 1
    elif damage == "duplicate_step":
        body["steps"].append(copy.deepcopy(body["steps"][0]))
    elif damage == "duplicate_attempt":
        body["attempts"].append(copy.deepcopy(body["attempts"][0]))
    elif damage == "extra_map_key":
        body["current_attempt_by_step"]["foreign-step"] = None
    elif damage == "missing_map_key":
        del body["current_attempt_by_step"][body["steps"][1]["step_id"]]
    elif damage == "unknown_current":
        body["steps"][0]["current_attempt_id"] = "attempt-unknown"
        body["current_attempt_by_step"][body["steps"][0]["step_id"]] = "attempt-unknown"
    elif damage == "orphan_history":
        historical = copy.deepcopy(body["attempts"][0])
        historical.update(attempt_id="old-attempt", step_id="unknown-step", is_current=False)
        body["attempts"].append(historical)
    facts = ExecutionFacts.model_validate(body)
    before = facts.model_dump(mode="json")
    with pytest.raises(ValueError):
        runtime_facts_from_execution_facts(facts)
    assert facts.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "name", ["success", "failure", "timeout", "unknown", "quick", "multistream", "non_utf8"]
)
def test_original_c_fixtures_keep_exact_current_attempts_and_states(name):
    facts = ExecutionFacts.model_validate(payload(name))
    view = runtime_facts_from_execution_facts(facts)
    assert view.run_id == facts.run_id
    assert view.run_revision == facts.run_revision
    assert view.runtime_revision_count == len(facts.runtime_revision_refs)
    attempts = {attempt.attempt_id: attempt for attempt in facts.attempts}
    for source, step in zip(facts.steps, view.steps, strict=True):
        expected = attempts.get(source.current_attempt_id)
        assert step.step_id == source.step_id
        assert step.current_attempt_state is None if expected is None else (
            step.current_attempt_state.value == expected.state.value
        )


def test_history_can_remain_without_becoming_current_or_changing_active_progress():
    body = payload()
    baseline = runtime_facts_from_execution_facts(ExecutionFacts.model_validate(body))
    historical = copy.deepcopy(body["attempts"][0])
    historical.update(attempt_id="old-attempt", is_current=False, state="completed")
    body["attempts"].append(historical)
    body["run"]["runtime_revision_refs"] = ["revision-1", "revision-2"]
    body["runtime_revision_refs"] = ["revision-1", "revision-2"]
    view = runtime_facts_from_execution_facts(ExecutionFacts.model_validate(body))
    for before, after in zip(baseline.steps, view.steps, strict=True):
        # Retaining history enriches the view, without changing any current field.
        assert replace(after, historical_attempt_states=()) == before
        assert after.progress() is before.progress()
    assert view.steps[0].historical_attempt_states == (AttemptRuntimeState.COMPLETED,)
    assert view.steps[1].historical_attempt_states == ()
    assert view.runtime_revision_count == 2


def test_not_started_steps_have_explicit_null_current_mapping_without_fabricated_attempts():
    body = payload()
    body["attempts"] = []
    for step in body["steps"]:
        step.update(current_attempt_id=None, state="pending")
        body["current_attempt_by_step"][step["step_id"]] = None
    view = runtime_facts_from_execution_facts(ExecutionFacts.model_validate(body))
    assert all(step.current_attempt_state is None for step in view.steps)
