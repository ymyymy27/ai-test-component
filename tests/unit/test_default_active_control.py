"""Real commands through default API; environment and user events remain synthetic."""

import sys
from dataclasses import replace

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_execution_authorization import RELAY
from tests.unit.test_default_step_execution import ActualCommandResolver, execution_command
from tests.unit.test_execution_authorization_origin import review, save
from tests.unit.test_initial_run_registration import register


class LongCommandResolver(ActualCommandResolver):
    def __init__(self, unit, *, timeout=600000):
        super().__init__(unit)
        self.timeout = timeout

    def resolve(self, **kwargs):
        action = super().resolve(**kwargs)
        return replace(
            action,
            attempt=replace(action.attempt, timeout_ms=self.timeout),
            request=replace(
                action.request,
                timeout_ms=self.timeout,
                registered_entry=replace(
                    action.request.registered_entry,
                    arguments=(
                        "-c",
                        "import time; print('running-api',flush=True); time.sleep(300)",
                    ),
                ),
            ),
        )


def configure(authoritative, *, timeout=600000):
    core, inputs, source = authoritative
    facts = register(core, prepare(core, inputs).result)
    service = core.execution_authorizations
    service.action_resolver = LongCommandResolver(core.unit_of_work, timeout=timeout)
    parameters = service.prepare(
        project_id=inputs.project_id,
        run_id=facts.run_id,
        step_id=facts.steps[0].step_id,
        intent_id="actual-long-intent",
        request_id="resolve-long",
    )
    _, action = service.resolver.read(inputs.project_id, parameters["execution_action_id"])
    actor, challenge = review(service, inputs.project_id, action, parameters)
    save(service, inputs.project_id, action, parameters, actor, challenge)
    spool = FileSpoolStore(core.workspace.root)
    port = CommandAdapter(
        spool_store=spool, handle_store=FileExecutionHandleStore(core.workspace.root)
    )
    port.register(CommandRegistration("public-python", sys.executable, source))
    core.step_execution.execution_port = port
    return (
        core,
        inputs,
        source,
        action,
        execution_command(inputs.project_id, action, parameters),
        port,
    )


def control(core, current, action, intent):
    return core.api.dispatch(
        Command(
            action=action,
            project_id=current["project_id"],
            request_id="req-" + intent,
            intent_id=intent,
            target=current["run_id"],
            expected_revision=0,
            parameters={
                "run_id": current["run_id"],
                "base_snapshot_commit_id": current["snapshot_commit_id"],
            },
        ),
        RELAY,
    )


def test_default_long_step_pause_resume_and_actual_cancel(authoritative):
    core, inputs, _, action, value, port = configure(authoritative)
    started = core.api.dispatch(value, RELAY)
    assert started.error is None, started.error
    assert started.result["attempt"]["state"] == "running"
    handle = core.execution_coordinator.read_checkpoint(
        project_id=inputs.project_id, attempt_id=action.attempt.attempt_id
    ).attempt.execution_handle_ref
    try:
        paused = control(core, started.result["execution_facts"], "pause_run", "pause-long")
        assert paused.error is None, paused.error
        assert paused.result["run"]["control_state"] == "pause_requested"
        collected = core.api.dispatch(
            value.model_copy(update={"request_id": "collect-paused"}), RELAY
        )
        assert collected.error is None, collected.error
        assert collected.result["execution_facts"]["run"]["control_state"] == "pause_requested"
        resumed = control(core, collected.result["execution_facts"], "resume_run", "resume-long")
        assert resumed.error is None, resumed.error
        assert resumed.result["run"]["control_state"] == "running"
        cancelled = control(core, resumed.result, "cancel_run", "cancel-long")
        assert cancelled.error is None, cancelled.error
        assert cancelled.result["run"]["control_state"] == "cancelled"
        assert cancelled.result["attempts"][0]["state"] == "cancelled"
        assert (
            not cancelled.result["verifications"] and cancelled.result["run"]["result_ref"] is None
        )
        manifest = FileSpoolStore(core.workspace.root).read_manifest(action.attempt.attempt_id)
        assert b"running-api" in b"".join(
            FileSpoolStore(core.workspace.root).read_block(b) for b in manifest.blocks
        )
    finally:
        port.request_stop(handle)


def test_default_new_core_and_adapter_use_saved_stop_without_inventing_exit_code(authoritative):
    core, inputs, source, action, value, original_port = configure(authoritative)
    started = core.api.dispatch(value, RELAY)
    assert started.error is None, started.error
    handle = core.execution_coordinator.read_checkpoint(
        project_id=inputs.project_id, attempt_id=action.attempt.attempt_id
    ).attempt.execution_handle_ref
    core.lifetime_lock.release()
    (source / "main.py").write_bytes(b"VALUE=8\n")
    port = CommandAdapter(
        spool_store=FileSpoolStore(core.workspace.root),
        handle_store=FileExecutionHandleStore(core.workspace.root),
    )
    restarted = assemble_workspace_core(
        core.workspace.root, instance_id="active-recovery", execution_port=port
    )
    try:
        cancelled = control(
            restarted, started.result["execution_facts"], "cancel_run", "cancel-after-restart"
        )
        assert cancelled.error is None, cancelled.error
        assert (
            FileExecutionHandleStore(core.workspace.root).load_stop(handle).stop_confirmed is True
        )
        assert cancelled.result["run"]["control_state"] == "cancelled"
        attempt = cancelled.result["attempts"][0]
        assert attempt["state"] == "cancelled"
        assert attempt["exit_fact"]["termination_reason"] == "confirmed_stop"
        assert attempt["exit_fact"]["real_exit_code"] is None
        assert attempt["capture_completeness"] in {"partial", "gap"}
        assert not cancelled.result["coverage"]["executed_attempt_ids"]
        assert (
            not cancelled.result["verifications"] and cancelled.result["run"]["result_ref"] is None
        )
    finally:
        original_port.request_stop(handle)
        restarted.lifetime_lock.release()
