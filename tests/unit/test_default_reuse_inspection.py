"""Default API and real records; synthetic authority, no real reuse or Trae AC."""

from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
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
