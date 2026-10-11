"""Actual controlled producer and C transactions; synthetic gestures/execution only."""

from dataclasses import replace

import pytest

from aitest.application.execution.authorization_index import read_authorization_index
from aitest.application.execution.runner import SerialRunner
from aitest.application.execution.runtime_revision_service import RuntimeRevisionService
from aitest.domain.execution.authorization import AuthorizationState
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_execution_authorization_origin import resolved as resolved
from tests.unit.test_execution_authorization_origin import review, save
from tests.unit.test_persisted_runtime_revision import (
    initial_basis,
    revision_request,
    saved_next_case,
)
from tests.unit.test_serial_runner import FakeExecutionPort


def test_actual_start_revokes_other_saved_grant_before_external_call_and_replays(resolved):
    core, inputs, _, authority, parameters, action = resolved
    actor, challenge = review(authority, inputs.project_id, action, parameters)
    save(authority, inputs.project_id, action, parameters, actor, challenge)
    second_parameters = authority.prepare(
        project_id=inputs.project_id,
        run_id=action.request.run_id,
        step_id=action.request.step_id,
        intent_id="alternate-real-origin-intent",
        request_id="resolve-alternate-origin",
    )
    _, alternate = authority.resolver.read(
        inputs.project_id, second_parameters["execution_action_id"]
    )
    actor2, challenge2 = review(authority, inputs.project_id, alternate, second_parameters)
    actor2 = replace(
        actor2, interaction=replace(actor2.interaction, interaction_id="second-fixture-gesture")
    )
    save(
        authority,
        inputs.project_id,
        alternate,
        second_parameters,
        actor2,
        challenge2,
        request="save-alternate",
    )
    own_id, other_id = (
        action.request.authorization_ref.authorization_id,
        alternate.request.authorization_ref.authorization_id,
    )
    assert (
        len(
            read_authorization_index(
                core.unit_of_work.repo,
                core.workspace.workspace_id,
                inputs.project_id,
                action.request.run_id,
            )[1]
        )
        == 2
    )
    port = FakeExecutionPort()
    original = port.start

    def start(request):
        assert core.unit_of_work.project is None
        assert authority._state(inputs.project_id, other_id) == (
            2,
            AuthorizationState.REVOKED,
            None,
        )
        assert authority._state(inputs.project_id, own_id) == (
            2,
            AuthorizationState.OCCUPIED,
            request.attempt_id,
        )
        assert (
            read_authorization_index(
                core.unit_of_work.repo,
                core.workspace.workspace_id,
                inputs.project_id,
                request.run_id,
            )[1]
            == ()
        )
        return original(request)

    port.start = start
    runner = SerialRunner(port, commit_coordinator=core.execution_coordinator)
    first = runner.start_attempt(action.attempt, action.request)
    before = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="revoked"):
        runner.start_attempt(alternate.attempt, alternate.request)
    assert core.unit_of_work.current_commit_sequence() == before and len(port.started) == 1
    assert runner.start_attempt(action.attempt, action.request) == first
    assert core.unit_of_work.current_commit_sequence() == before and len(port.started) == 1


def test_actual_runtime_revision_requires_consumer_and_rolls_back_revocation_with_snapshot(
    resolved, monkeypatch
):
    core, inputs, _, authority, parameters, action = resolved
    actor, challenge = review(authority, inputs.project_id, action, parameters)
    save(authority, inputs.project_id, action, parameters, actor, challenge)
    plan, cases, before = initial_basis(core, inputs)
    case = next(value for value in cases if value.case_id == before.steps[0].case_id)
    changed = saved_next_case(
        core,
        inputs,
        replace(
            case, revision=case.revision + 1, steps=tuple(text + " revised" for text in case.steps)
        ),
    )
    request = revision_request(before, changed)
    unit = core.unit_of_work
    identity = action.request.authorization_ref.authorization_id
    common = dict(
        unit=unit,
        records=unit.repo,
        approvals=authority.approvals,
        controlled_writes=authority.controlled_writes,
    )
    arguments = dict(
        project_id=inputs.project_id,
        run_id=before.run_id,
        plan=plan,
        request=request,
        request_id="apply-with-authority",
        intent_id="runtime-authority-change",
    )
    sequence = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="unused authorization consumer"):
        RuntimeRevisionService(**common).apply(**arguments)
    assert unit.current_commit_sequence() == sequence
    actual = RuntimeRevisionService(**common, execution_authorizations=authority)
    original_stage = unit.stage_record

    def fail(**kwargs):
        if kwargs["aggregate_kind"] == "execution_facts":
            raise OSError("injected runtime snapshot failure")
        return original_stage(**kwargs)

    monkeypatch.setattr(unit, "stage_record", fail)
    with pytest.raises(OSError, match="injected runtime"):
        actual.apply(**arguments)
    assert unit.current_commit_sequence() == sequence
    assert authority._state(inputs.project_id, identity) == (1, AuthorizationState.UNUSED, None)
    assert read_authorization_index(
        unit.repo, core.workspace.workspace_id, inputs.project_id, before.run_id
    )[1] == (identity,)
    monkeypatch.setattr(unit, "stage_record", original_stage)
    after = actual.apply(**arguments)
    assert (
        after.runtime_revision_refs
        and after.steps[0].step_revision_ref != before.steps[0].step_revision_ref
    )
    assert authority._state(inputs.project_id, identity) == (2, AuthorizationState.REVOKED, None)
    assert (
        read_authorization_index(
            unit.repo, core.workspace.workspace_id, inputs.project_id, before.run_id
        )[1]
        == ()
    )
    sequence = unit.current_commit_sequence()
    assert actual.apply(**arguments) == after
    assert unit.current_commit_sequence() == sequence
