"""Default API and real records; synthetic authority, no real reuse or Trae AC."""

from dataclasses import replace
from types import SimpleNamespace

from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.contracts.prepared_run import AssertionBasisStateFact, ConfirmationRef
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_basis_confirmation import confirm
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_initial_run_registration import register


def test_default_read_inspection_survives_restart_and_never_mints_a_choice(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    source = register(core, prepared)
    target = register(core, prepared, request="reuse-target-request", intent="reuse-target-intent")
    case = prepared["selected_case_ids"][0]
    command = Command(
        action="inspect_case_reuse", request_id="reuse-inspection", project_id=inputs.project_id,
        target=case, parameters={
            "case_id": case, "source_run_id": source.run_id, "target_run_id": target.run_id,
            "source_snapshot": SnapshotContentRef.of(source).model_dump(mode="json"),
            "target_snapshot": SnapshotContentRef.of(target).model_dump(mode="json"),
        },
    )
    sequence = core.unit_of_work.current_commit_sequence()
    response = core.api.dispatch(command, RELAY)
    assert response.error is None, response.error
    result = response.result
    assert result["status"] == "unverified"
    assert "environment_dynamic_digest_unverified" in result["denial_reasons"]
    assert "dependency_digest_unverified" in result["denial_reasons"]
    assert "source_attempts_missing" in result["denial_reasons"]
    assert not any(x.endswith("_changed") for x in result["denial_reasons"])
    assert all(item["source_attempt_id"] is None for item in result["step_mapping"])
    assert {item["source_step_id"] for item in result["step_mapping"]}.isdisjoint(
        item["target_step_id"] for item in result["step_mapping"]
    )
    assert core.unit_of_work.current_commit_sequence() == sequence
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(core.workspace.root, instance_id="reuse-inspection-restart")
    try:
        assert restarted.gate is not None
        restarted.gate.degrade("source", "saved inspection does not need a new source probe")
        restored = restarted.api.dispatch(
            command.model_copy(update={"request_id": "restart-inspection"}), RELAY,
        )
        assert restored.error is None and restored.result == result
        assert restarted.unit_of_work.current_commit_sequence() == sequence
        doctor = restarted.api.dispatch(
            Command(action="doctor", request_id="inspect-doctor"), RELAY,
        )
        assert "inspect_case_reuse" in doctor.result["supported_actions"]
        altered = restarted.api.dispatch(command.model_copy(update={
            "request_id": "invalid-inspection",
            "parameters": {**command.parameters, "eligible": True},
        }), RELAY)
        assert altered.error.code == "CASE_REUSE_UNVERIFIED"
        assert restarted.unit_of_work.current_commit_sequence() == sequence
    finally:
        restarted.lifetime_lock.release()


def test_default_late_basis_confirmation_reads_controlled_original_after_restart(
    authoritative, monkeypatch,
):
    core, inputs, _ = authoritative
    source_prepared = prepare(core, inputs).result
    source = register(core, source_prepared)
    confirmed = confirm(core, inputs)
    assert confirmed.error is None, confirmed.error
    actual = confirmed.result
    entry = inputs.assertion_bases[0]
    ref = ConfirmationRef(**{key: actual[key] for key in (
        "confirmation_id", "case_id", "basis_revision", "confirmed_at_commit",
    )})
    target_inputs = replace(inputs, prepare_request_id="basis-target-business-preparation",
                           assertion_bases=(entry.model_copy(update={
        "assertion_basis_state": AssertionBasisStateFact.CONFIRMED, "confirmation_refs": (ref,),
    }), *inputs.assertion_bases[1:]))
    prepared = prepare(core, target_inputs, request="basis-target-preparation",
                       intent="basis-target-preparation-intent")
    assert prepared.error is None, prepared.error
    assert prepared.result["status"] == "prepared", prepared.result
    assert prepared.result["prepared_run_id"] != source_prepared["prepared_run_id"]
    assert prepared.result["assertion_bases"][0]["confirmation_refs"] == [
        ref.model_dump(mode="json"),
    ]
    target = register(core, prepared.result, request="basis-target-register",
                      intent="basis-target-register-intent")
    command = Command(
        action="inspect_case_reuse", request_id="basis-confirmation-inspection",
        project_id=inputs.project_id, target=entry.case_id, parameters={
            "case_id": entry.case_id, "source_run_id": source.run_id,
            "target_run_id": target.run_id,
            "source_snapshot": SnapshotContentRef.of(source).model_dump(mode="json"),
            "target_snapshot": SnapshotContentRef.of(target).model_dump(mode="json"),
        },
    )
    sequence = core.unit_of_work.current_commit_sequence()
    response = core.api.dispatch(command, RELAY)
    assert response.error is None, response.error
    assert response.result["schema_version"] == "aitest.case-reuse-inspection/1.1"
    for side in ("source", "target"):
        assert response.result["basis_confirmation"][side] == {
            "state": "confirmed", "confirmation_refs": [ref.model_dump(mode="json")],
        }
    assert "basis_confirmed_unverified" not in response.result["denial_reasons"]
    assert response.result["status"] == "unverified"
    assert "verification_valid_unverified" in response.result["denial_reasons"]
    assert source_prepared["assertion_bases"][0]["assertion_basis_state"] == "present_unconfirmed"
    assert core.unit_of_work.current_commit_sequence() == sequence
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(core.workspace.root, instance_id="basis-proof-restart")
    try:
        restored = restarted.api.dispatch(command.model_copy(update={
            "request_id": "basis-proof-reopened-inspection",
        }), RELAY)
        assert restored.error is None, restored.error
        assert restored.result == response.result
        assert restarted.unit_of_work.current_commit_sequence() == sequence
        read = restarted.unit_of_work.repo.read

        def missing_origin(**kw):
            record = read(**kw)
            if kw["aggregate_kind"] != "case_link" or kw["record_id"] != ref.confirmation_id:
                return record
            return SimpleNamespace(
                aggregate_kind=record.aggregate_kind, record_id=record.record_id,
                revision=record.revision,
                payload={**record.payload, "approval_confirmation_id": "missing-controlled-origin"},
            )

        monkeypatch.setattr(restarted.unit_of_work.repo, "read", missing_origin)
        damaged = restarted.api.dispatch(command.model_copy(update={
            "request_id": "damaged-basis-proof-inspection",
        }), RELAY)
        assert damaged.error.code == "CASE_REUSE_UNVERIFIED"
        assert restarted.unit_of_work.current_commit_sequence() == sequence
    finally:
        restarted.lifetime_lock.release()
