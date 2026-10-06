"""Unrelated progress remains legal; changed action authority never gains consent."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.current import project_current_update
from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.authorization import AuthorizationState
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_execution_authorization_origin import resolved as resolved
from tests.unit.test_execution_authorization_origin import review, save, started
from tests.unit.test_persisted_runtime_revision import (
    apply,
    initial_basis,
    revision_request,
    saved_next_case,
)


def test_current_attempt_replacement_blocks_grant_and_occupation(resolved):
    core, inputs, _, service, parameters, action = resolved
    actor, challenge = review(service, inputs.project_id, action, parameters)
    save(service, inputs.project_id, action, parameters, actor, challenge)
    coordinator = ExecutionCommitCoordinator(core.unit_of_work, records=core.unit_of_work.repo)
    # Controlled progress fixture simulates a prior Attempt without consuming this
    # unused grant, so the separate current-action check must reject it.
    current = coordinator.read_current_facts(
        project_id=inputs.project_id, run_id=action.request.run_id
    )
    attempt = started(action)
    core.unit_of_work.begin("controlled-progress", inputs.project_id)
    checkpoint = SerialRunner._checkpoint_record(attempt, stage="intent_recorded")
    core.unit_of_work.stage_record(
        aggregate_kind="execution_checkpoint",
        record_id=attempt.attempt_id,
        expected_revision=0,
        payload=TypeAdapter(type(checkpoint)).dump_python(checkpoint, mode="json")
        | {"project_id": inputs.project_id},
    )
    coordinator._stage_snapshot(
        project_current_update(current, attempt, committed_at=datetime.now(UTC)),
        allow_current_change=True,
    )
    core.unit_of_work.commit("controlled-progress")
    before = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="index must follow"):
        service.validate_new(project_id=inputs.project_id, attempt=started(action))
    core.unit_of_work.begin("stale-occupation", inputs.project_id)
    try:
        with pytest.raises(ValueError, match="index must follow"):
            service.stage_occupation(project_id=inputs.project_id, attempt=started(action))
    finally:
        core.unit_of_work.rollback("stale-occupation")
    assert core.unit_of_work.current_commit_sequence() == before
    assert service._state(inputs.project_id, action.request.authorization_ref.authorization_id) == (
        1,
        AuthorizationState.UNUSED,
        None,
    )


def test_saved_step_revision_change_cannot_consume_prepared_challenge(resolved):
    core, inputs, _, service, parameters, action = resolved
    actor, challenge = review(service, inputs.project_id, action, parameters)
    plan, cases, before = initial_basis(core, inputs)
    case = next(case for case in cases if case.case_id == before.steps[0].case_id)
    changed = saved_next_case(
        core,
        inputs,
        replace(case, revision=2, steps=tuple(text + " changed" for text in case.steps)),
    )
    after = apply(core, plan, before, revision_request(before, changed))
    current_step = next(step for step in after.steps if step.step_id == action.request.step_id)
    assert current_step.step_revision_ref.digest != action.attempt.step_revision_ref.digest
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="exact current material"):
        save(service, inputs.project_id, action, parameters, actor, challenge)
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert service._revision(action.request.authorization_ref.authorization_id) == 0
    assert (
        service.approvals.read_challenge(
            project_id=inputs.project_id, challenge_id=challenge.challenge_id
        ).state.value
        == "pending"
    )


def test_actual_source_change_blocks_new_grant_without_consuming_challenge(resolved):
    core, inputs, source, service, parameters, action = resolved
    actor, challenge = review(service, inputs.project_id, action, parameters)
    (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="actual execution source changed"):
        save(service, inputs.project_id, action, parameters, actor, challenge)
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert service._revision(action.request.authorization_ref.authorization_id) == 0
    assert (
        service.approvals.read_challenge(
            project_id=inputs.project_id, challenge_id=challenge.challenge_id
        ).state.value
        == "pending"
    )
