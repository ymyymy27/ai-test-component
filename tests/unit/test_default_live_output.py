"""Saved live output through default API; environment/user evidence remains synthetic."""

from dataclasses import replace
from time import monotonic, sleep
from unittest.mock import patch

from aitest.domain.execution.runs import OutputStreamName
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_default_active_control import LongCommandResolver, configure, control
from tests.unit.test_default_execution_authorization import RELAY


def test_default_active_large_output_stays_running_and_preserves_pause(authoritative):
    original = LongCommandResolver.resolve

    def large(self, **kwargs):
        action = original(self, **kwargs)
        return replace(
            action,
            request=replace(
                action.request,
                registered_entry=replace(
                    action.request.registered_entry,
                    arguments=(
                        "-c",
                        "import sys,time; print('alpha '*30000,flush=True); "
                        "print('beta '*30000,file=sys.stderr,flush=True); time.sleep(300)",
                    ),
                ),
            ),
        )

    with patch.object(LongCommandResolver, "resolve", large):
        core, inputs, _, action, value, port = configure(authoritative)
    started = core.api.dispatch(value, RELAY)
    handle = core.execution_coordinator.read_checkpoint(
        project_id=inputs.project_id, attempt_id=action.attempt.attempt_id
    ).attempt.execution_handle_ref
    try:
        assert started.error is None, started.error
        # The first bounded slice may end before the child has produced output.
        # Wait for actual saved material, then observe that same original execution.
        spool = FileSpoolStore(core.workspace.root)
        deadline = monotonic() + 5
        while monotonic() < deadline:
            manifest = spool.read_manifest(action.attempt.attempt_id)
            if {cursor.stream_name for cursor in manifest.cursors} == {
                OutputStreamName.STDOUT,
                OutputStreamName.STDERR,
            }:
                break
            sleep(0.01)
        assert manifest.blocks and len(manifest.cursors) == 2
        started = core.api.dispatch(
            value.model_copy(update={"request_id": "observe-saved-live-output"}), RELAY
        )
        assert started.error is None, started.error
        attempt = started.result["attempt"]
        assert attempt["state"] == "running"
        assert attempt["output_blocks"] and attempt["output_cursors"]
        assert attempt["capture_completeness"] == "partial"
        assert attempt["exit_fact"] is None
        assert not started.result["execution_facts"]["coverage"]["executed_attempt_ids"]
        checkpoint = core.execution_coordinator.read_checkpoint(
            project_id=inputs.project_id, attempt_id=action.attempt.attempt_id
        ).attempt
        assert all(spool.read_block(ref) for ref in checkpoint.output_block_refs)
        paused = control(core, started.result["execution_facts"], "pause_run", "pause-live-large")
        assert paused.error is None, paused.error
        assert paused.result["run"]["control_state"] == "pause_requested"
        collected = core.api.dispatch(
            value.model_copy(update={"request_id": "collect-live-large"}), RELAY
        )
        assert collected.error is None, collected.error
        assert collected.result["attempt"]["state"] == "running"
        assert collected.result["execution_facts"]["run"]["control_state"] == "pause_requested"
        cancelled = control(
            core, collected.result["execution_facts"], "cancel_run", "cancel-live-large"
        )
        assert cancelled.error is None, cancelled.error
        assert cancelled.result["run"]["control_state"] == "cancelled"
        assert not cancelled.result["coverage"]["executed_attempt_ids"]
    finally:
        port.request_stop(handle)
