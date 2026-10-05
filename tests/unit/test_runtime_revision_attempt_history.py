"""Runtime edits must preserve attempted content despite lagging step state."""

from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from aitest.application.execution.facts import project_attempt_fact
from aitest.application.planning.run_mode import runtime_facts_from_execution_facts
from aitest.contracts.execution_facts import ExecutionFacts, StepStateFact
from aitest.domain.execution.runs import AttemptState, RecoveryRecord
from aitest.domain.planning.runtime_revision import CaseRuntimeChange, StepRuntimeProgress
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_runtime_revision import (
    _case,
    _change,
    _codes,
    _decide,
    _failure_in_progress,
)
from tests.unit.test_saved_runtime_revision import (
    assess,
    save_current_attempt,
)
from tests.unit.test_saved_runtime_revision import (
    runtime as runtime,
)

RECORDED = (
    "completed",
    "cancelled",
    "pending_verification",
    "execution_error",
    "invalidated",
    "unknown",
)
ACTIVE = ("intent_recorded", "starting", "running", "stop_requested", "collecting")


def lagging_payload(state, step_state, *, historical=False):
    body = _failure_in_progress()
    step = body["steps"][0]
    step["state"] = step_state
    body["attempts"][0]["state"] = state
    if historical:
        step["current_attempt_id"] = None
        body["current_attempt_by_step"][step["step_id"]] = None
        body["attempts"][0]["is_current"] = False
    return body


@pytest.mark.parametrize("state", RECORDED)
@pytest.mark.parametrize("step_state", ["pending", "invalidated"])
def test_recorded_current_attempt_cannot_be_edited_via_lagging_step(state, step_state):
    body = lagging_payload(state, step_state)
    before = ExecutionFacts.model_validate(body).model_dump(mode="json")
    decision = _decide(body, change=_change(_case(), targets=("step-1",)))
    assert not decision.accepted
    assert "step_facts_already_recorded" in _codes(decision)
    assert not decision.affected_step_ids
    assert ExecutionFacts.model_validate(body).model_dump(mode="json") == before


@pytest.mark.parametrize("state", RECORDED)
@pytest.mark.parametrize("step_state", ["pending", "invalidated"])
def test_history_is_preserved_and_outdated_when_only_fresh_step_changes(state, step_state):
    body = lagging_payload(state, step_state, historical=True)
    case = _case()
    basis = replace(case.assertion_basis, revision=2, text="新依据", text_digest="sha256:new")
    decision = _decide(body, change=_change(case, basis=basis))
    assert decision.accepted
    assert decision.affected_step_ids == ("step-2",)
    assert decision.preserved_step_ids == ("step-1",)
    assert decision.invalidated_basis_step_ids == ("step-1",)
    assert decision.rejudge_case_ids == ("case-1",)
    assert decision.pause_required


@pytest.mark.parametrize("state", ACTIVE)
@pytest.mark.parametrize("historical", [False, True])
def test_unresolved_active_attempt_blocks_edit_even_when_not_current(state, historical):
    body = lagging_payload(state, "blocked", historical=historical)
    decision = _decide(body)
    assert not decision.accepted
    assert "step_is_executing" in _codes(decision)
    assert not decision.affected_step_ids


@pytest.mark.parametrize("step_state", ["pending", "ready", "blocked", "invalidated"])
def test_unattempted_step_remains_editable_without_changing_recorded_peer(step_state):
    body = _failure_in_progress()
    body["steps"][1]["state"] = step_state
    decision = _decide(body, change=_change(_case(), targets=("step-2",)))
    assert decision.accepted
    assert decision.affected_step_ids == ("step-2",)
    assert decision.preserved_step_ids == ("step-1",)
    view = runtime_facts_from_execution_facts(ExecutionFacts.model_validate(body))
    assert view.steps[1].progress() is StepRuntimeProgress.NOT_STARTED


@pytest.mark.parametrize(
    "field,value",
    [
        ("step_revision_id", "different-content"),
        ("revision_no", 99),
        ("digest", "sha256:different"),
        ("inherited", True),
        ("base_step_revision_id", "different-base"),
    ],
)
def test_current_attempt_must_use_the_exact_step_content_reference(field, value):
    body = _failure_in_progress()
    body["attempts"][0]["step_revision_ref"][field] = value
    with pytest.raises(ValueError, match="exact current step revision"):
        runtime_facts_from_execution_facts(ExecutionFacts.model_validate(body))


@pytest.mark.parametrize("state", ["completed", "invalidated", "unknown"])
def test_saved_checkpoint_overrides_lagging_step_without_any_new_execution(runtime, state):
    core, service, _, _, _, _ = runtime
    _, facts = save_current_attempt(runtime)
    fact = facts.attempts[0]
    checkpoint = service.execution.read_checkpoint(
        project_id=facts.project_id, attempt_id=fact.attempt_id
    )
    attempt = replace(checkpoint.attempt, state=AttemptState(state))
    checkpoint = replace(checkpoint, attempt=attempt)
    changed = facts.model_copy(
        update={
            "attempts": (project_attempt_fact(attempt, is_current=True),),
            "steps": tuple(
                s.model_copy(update={"state": StepStateFact.INVALIDATED})
                if s.step_id == attempt.step_id
                else s
                for s in facts.steps
            ),
        }
    )
    request_id = "save-recorded-attempt-" + state
    unit = core.unit_of_work
    unit.begin(request_id, facts.project_id, intent_id=request_id + "-intent")
    try:
        unit.stage_record(
            aggregate_kind="execution_checkpoint",
            record_id=fact.attempt_id,
            expected_revision=unit.current_revision(
                aggregate_kind="execution_checkpoint", record_id=fact.attempt_id
            ),
            payload=TypeAdapter(RecoveryRecord).dump_python(checkpoint, mode="json"),
        )
        _, saved = service.execution._stage_snapshot(changed)
        unit.commit(request_id)
    except BaseException:
        unit.rollback()
        raise
    request = replace(
        runtime[5],
        observed_snapshot_cursor=saved.snapshot_cursor,
        case_changes=(
            CaseRuntimeChange(
                runtime[5].case_changes[0].next_case, target_step_ids=(attempt.step_id,)
            ),
        ),
    )
    sequence = unit.current_commit_sequence()
    decision = assess(runtime, request=request)
    assert not decision.accepted
    assert "step_facts_already_recorded" in _codes(decision)
    assert not decision.affected_step_ids
    assert (
        service.execution.read_runtime_revision_facts(
            project_id=facts.project_id, run_id=facts.run_id
        )
        == saved
    )
    assert unit.current_commit_sequence() == sequence
