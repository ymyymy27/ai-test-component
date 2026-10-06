"""Real default API/UOW, with explicitly synthetic paused state and user gestures."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, Session
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_persisted_runtime_revision import (
    apply,
    initial_basis,
    revision_request,
    saved_next_case,
)

HUMAN = Session("runtime-human-fixture", EntryKind.HUMAN_UI, True)
RELAY = Session("runtime-relay-fixture", EntryKind.AGENT_RELAY)


def pending(authoritative):
    core, inputs, source = authoritative
    plan, cases, facts = initial_basis(core, inputs)
    case = saved_next_case(
        core,
        inputs,
        replace(cases[0], revision=2, steps=tuple(s + " revised" for s in cases[0].steps)),
    )
    return core, inputs, source, plan, case, facts


def command(
    facts,
    case=None,
    *,
    action="revise_pending_steps",
    intent="runtime-public",
    request="runtime-request",
):
    return Command(
        action=action,
        project_id=facts.project_id,
        request_id=request,
        intent_id=intent,
        expected_revision=0,
        target=facts.run_id,
        parameters={
            "run_id": facts.run_id,
            "base_snapshot_commit_id": facts.snapshot_commit_id,
            "reason": "revise saved pending steps",
            "confirmation_ids": [],
            "case_changes": []
            if case is None
            else [
                {"case_id": case.case_id, "record_revision": case.revision, "target_step_ids": []}
            ],
        },
    )


def review(core, value):
    challenge = core.api.dispatch(
        Command(
            action="prepare_approval",
            project_id=value.project_id,
            request_id="review-" + value.request_id,
            intent_id="review-" + value.intent_id,
            expected_revision=0,
            parameters={
                "action": value.action,
                "action_intent_id": value.intent_id,
                "target": value.target,
                "parameters": value.parameters,
            },
        ),
        HUMAN,
    )
    assert challenge.error is None, challenge.error
    approved = value.model_copy(
        update={
            "parameters": value.parameters
            | {"approval_challenge_id": challenge.result["challenge_id"]}
        }
    )
    return approved, challenge.result


def confirm(core, value, challenge):
    return core.api.dispatch_user_confirmation(
        value,
        HUMAN,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )


def test_default_successive_revision_history_and_restart(authoritative):
    core, inputs, source, plan, case2, before = pending(authoritative)
    value, challenge = review(core, command(before, case2))
    first = confirm(core, value, challenge)
    assert first.error is None, first.error
    assert first.result["plan_revision"] == before.plan_revision.model_dump(mode="json")
    assert first.result["run"]["required_scope"] == list(before.run.required_scope)
    assert len(first.result["runtime_revision_refs"]) == 1
    original_refs = {s.step_id: s.step_revision_ref.model_dump(mode="json") for s in before.steps}
    changed_steps = [s for s in first.result["steps"] if s["case_id"] == case2.case_id]
    assert changed_steps
    assert all(s["step_revision_ref"] != original_refs[s["step_id"]] for s in changed_steps)
    assert all(
        s["step_revision_ref"] == original_refs[s["step_id"]]
        for s in first.result["steps"]
        if s["case_id"] != case2.case_id
    )
    current = core.execution_coordinator.read_current_facts(
        project_id=inputs.project_id, run_id=before.run_id
    )
    core.runtime_actions.validate_runtime_origins(current)
    origin, _ = core.runtime_actions._origin(inputs.project_id, value.intent_id)
    proof = core.runtime_actions.approvals.read_confirmation(
        project_id=inputs.project_id, confirmation_id=origin["confirmation_id"]
    )
    for altered in (
        replace(proof, confirmation_intent_id="another-intent"),
        replace(proof, basis=replace(proof.basis, credential_scope_ref="another-scope")),
        replace(proof, basis=replace(proof.basis, materials=())),
    ):
        with (
            patch.object(core.runtime_actions.approvals, "read_confirmation", return_value=altered),
            pytest.raises(ValueError, match="exact controlled origin"),
        ):
            core.runtime_actions.validate_runtime_origins(current)
    from tests.unit.test_execution_authorization_origin import FixtureActionResolver

    service = core.execution_authorizations
    service.action_resolver = FixtureActionResolver(core.unit_of_work)
    target_step = next(s for s in current.steps if s.case_id == case2.case_id)
    prepared_action = service.prepare(
        project_id=inputs.project_id,
        run_id=current.run_id,
        step_id=target_step.step_id,
        intent_id="after-runtime-revision",
        request_id="prepare-after-runtime-revision",
    )
    _, resolved = service.resolver.read(inputs.project_id, prepared_action["execution_action_id"])
    from aitest.application.approval_service import _payload

    assert _payload(resolved.attempt.step_revision_ref) == target_step.step_revision_ref.model_dump(
        mode="json"
    )
    assert service.action_resolver.calls == 1
    assert not first.result["attempts"] and not first.result["verifications"]
    case3 = saved_next_case(
        core, inputs, replace(case2, revision=3, steps=tuple(s + " again" for s in case2.steps))
    )
    value2, challenge2 = review(
        core, command(current, case3, intent="runtime-second", request="runtime-second-request")
    )
    second = confirm(core, value2, challenge2)
    assert second.error is None, second.error
    assert len(second.result["runtime_revision_refs"]) == 2
    seq = core.unit_of_work.current_commit_sequence()
    recalled = core.api.dispatch(value.model_copy(update={"request_id": "readonly-first"}), HUMAN)
    assert recalled.error is None, recalled.error
    assert recalled.result == first.result and core.unit_of_work.current_commit_sequence() == seq
    assert core.execution_coordinator.read_current_facts(
        project_id=inputs.project_id, run_id=before.run_id
    ).runtime_revision_refs == tuple(second.result["runtime_revision_refs"])
    changed = value.model_copy(
        update={
            "request_id": "conflict-first",
            "parameters": value.parameters | {"reason": "different"},
        }
    )
    assert core.api.dispatch(changed, HUMAN).error.code == "INTENT_CONFLICT"
    core.lifetime_lock.release()
    (source / "main.py").write_text("changed after revision", encoding="utf-8")
    restarted = assemble_workspace_core(core.workspace.root, instance_id="runtime-restart")
    try:
        original = restarted.api.dispatch(
            value.model_copy(update={"request_id": "restart-first"}), HUMAN
        )
        assert original.error is None, original.error
        assert original.result == first.result
        current = restarted.execution_coordinator.read_current_facts(
            project_id=inputs.project_id, run_id=before.run_id
        )
        restarted.runtime_actions.validate_runtime_origins(current)
    finally:
        restarted.lifetime_lock.release()


def test_driver_only_narrowing_does_not_fake_case_change(authoritative):
    core, inputs, _ = authoritative
    _, _, before = initial_basis(core, inputs)
    from tests.unit.test_execution_authorization_origin import (
        FixtureActionResolver,
    )
    from tests.unit.test_execution_authorization_origin import (
        review as review_authorization,
    )
    from tests.unit.test_execution_authorization_origin import (
        save as save_authorization,
    )

    service = core.execution_authorizations
    service.action_resolver = FixtureActionResolver(core.unit_of_work)
    parameters = service.prepare(
        project_id=inputs.project_id,
        run_id=before.run_id,
        step_id=before.steps[0].step_id,
        intent_id="unused-before-narrow",
        request_id="prepare-unused-before-narrow",
    )
    _, action = service.resolver.read(inputs.project_id, parameters["execution_action_id"])
    actor, consent = review_authorization(service, inputs.project_id, action, parameters)
    save_authorization(service, inputs.project_id, action, parameters, actor, consent)
    grant_id = action.request.authorization_ref.authorization_id
    assert service._state(inputs.project_id, grant_id)[1].value == "unused"
    value, challenge = review(core, command(before, action="narrow_driver"))
    result = confirm(core, value, challenge)
    assert result.error is None, result.error
    assert result.result["run"]["driver"] == "stepwise"
    assert service._state(inputs.project_id, grant_id)[1].value == "revoked"
    assert result.result["steps"] == [s.model_dump(mode="json") for s in before.steps]
    record = core.runtime_actions.reader.read_record(
        project_id=inputs.project_id, reference=result.result["runtime_revision_refs"][0]
    )
    assert record.request_payload["case_changes"] == [] and not record.step_changes
    assert record.request_payload["operator_ref"] == "human-session:" + HUMAN.session_id
    current = core.execution_coordinator.read_current_facts(
        project_id=inputs.project_id, run_id=before.run_id
    )
    core.runtime_actions.validate_runtime_origins(current)
    retry = command(
        current, action="narrow_driver", intent="narrow-again", request="narrow-again-request"
    )
    denied = core.api.dispatch(
        Command(
            action="prepare_approval",
            project_id=inputs.project_id,
            request_id="review-again",
            intent_id="review-again-intent",
            expected_revision=0,
            parameters={
                "action": retry.action,
                "action_intent_id": retry.intent_id,
                "target": retry.target,
                "parameters": retry.parameters,
            },
        ),
        HUMAN,
    )
    assert denied.error.code == "RUNTIME_REVISION_BLOCKED"


def test_revision_rejects_untrusted_or_changed_input_without_effect(authoritative):
    core, inputs, _, _, case, facts = pending(authoritative)
    value, challenge = review(core, command(facts, case))
    seq = core.unit_of_work.current_commit_sequence()
    for index, fault in enumerate(
        [
            "relay",
            "no_gesture",
            "changed_snapshot",
            "unknown",
            "body",
            "boolean",
            "wrong_target",
            "another_challenge",
        ]
    ):
        value = value.model_copy(update={"request_id": f"negative-{index}"})
        if fault == "relay":
            result = core.api.dispatch(value, RELAY)
        elif fault == "no_gesture":
            result = core.api.dispatch(value, HUMAN)
        else:
            changed = deepcopy(value.parameters)
            if fault == "changed_snapshot":
                changed["base_snapshot_commit_id"] = "another-snapshot"
            elif fault == "unknown":
                changed["accepted"] = True
            elif fault == "body":
                changed["case_changes"][0] = dict(changed["case_changes"][0]) | {
                    "case_content": {"forged": True}
                }
            elif fault == "boolean":
                changed["case_changes"][0] = dict(changed["case_changes"][0]) | {
                    "record_revision": True
                }
            elif fault == "another_challenge":
                changed["approval_challenge_id"] = "another-challenge"
            altered = value.model_copy(
                update={
                    "parameters": changed,
                    "target": "another-run" if fault == "wrong_target" else value.target,
                }
            )
            result = confirm(core, altered, challenge)
        assert result.error is not None
        assert core.unit_of_work.current_commit_sequence() == seq
        assert (
            core.execution_coordinator.read_current_facts(
                project_id=inputs.project_id, run_id=facts.run_id
            )
            == facts
        )
        assert (
            core.execution_authorizations.approvals.read_challenge(
                project_id=inputs.project_id, challenge_id=challenge["challenge_id"]
            ).state.value
            == "pending"
        )


def test_revision_confirmation_and_effect_rollback_together(authoritative):
    core, inputs, _, _, case, facts = pending(authoritative)
    value, challenge = review(core, command(facts, case))
    seq = core.unit_of_work.current_commit_sequence()
    original = core.unit_of_work.stage_record

    for index, fault in enumerate(
        [
            "run_plan_revision",
            "execution_facts_current",
            "execution_facts",
            "approval_challenge",
            "approval_confirmation",
            "origin",
        ]
    ):
        value = value.model_copy(update={"request_id": f"rollback-{index}"})

        def fail(*, fault=fault, **kwargs):
            if (
                kwargs["aggregate_kind"] == fault
                or fault == "origin"
                and kwargs["record_id"].startswith("runtime-origin-")
            ):
                raise OSError("injected revision boundary")
            return original(**kwargs)

        with patch.object(core.unit_of_work, "stage_record", fail):
            result = confirm(core, value, challenge)
        assert result.error is not None
        assert core.unit_of_work.current_commit_sequence() == seq
        assert (
            core.execution_coordinator.read_current_facts(
                project_id=inputs.project_id, run_id=facts.run_id
            )
            == facts
        )
        assert (
            core.execution_authorizations.approvals.read_challenge(
                project_id=inputs.project_id, challenge_id=challenge["challenge_id"]
            ).state.value
            == "pending"
        )
    retried = confirm(core, value.model_copy(update={"request_id": "after-failure"}), challenge)
    assert retried.error is None, retried.error


def test_old_internal_revision_cannot_gain_default_consent(authoritative):
    core, inputs, _, plan, case, facts = pending(authoritative)
    internal = apply(core, plan, facts, revision_request(facts, case))
    with pytest.raises(ValueError, match="legacy runtime revision"):
        core.runtime_actions.validate_runtime_origins(internal)
    from tests.unit.test_execution_authorization_origin import FixtureActionResolver

    service = core.execution_authorizations
    service.action_resolver = FixtureActionResolver(core.unit_of_work)
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="legacy runtime revision"):
        service.prepare(
            project_id=inputs.project_id,
            run_id=internal.run_id,
            step_id=internal.steps[0].step_id,
            intent_id="legacy-execution",
            request_id="prepare-legacy-execution",
        )
    assert service.action_resolver.calls == 0
    assert core.unit_of_work.current_commit_sequence() == sequence


@pytest.mark.parametrize("action", ["revise_pending_steps", "narrow_driver"])
def test_default_runtime_handlers_cannot_be_replaced(tmp_path, action):
    with pytest.raises(ValueError, match="conflicts with built-in"):
        assemble_workspace_core(
            tmp_path / "workspace",
            instance_id="override",
            extra_handlers={action: lambda _: {"forged": True}},
        )
