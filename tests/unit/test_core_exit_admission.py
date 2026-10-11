"""Exit requests cannot replace saved terminal execution and shutdown receipts."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

import aitest.bootstrap as bootstrap
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import project_attempt_fact
from aitest.application.execution.output_material import require_saved_output_material
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.errors import ErrorDTO
from aitest.contracts.responses import Response
from aitest.domain.execution.runs import (
    AdapterKind,
    AttemptState,
    CaptureCompleteness,
    ExecutionHandle,
    ExitFact,
    ProcessTerminationReason,
)
from aitest.infrastructure.adapters.execution.command import CommandAdapter, CommandRegistration
from aitest.infrastructure.file_store.core_exit import FileCoreExitGuard
from aitest.infrastructure.file_store.core_launch import FileCoreLaunchStore, probe_process
from aitest.infrastructure.file_store.execution_handles import FileExecutionHandleStore
from aitest.infrastructure.file_store.maintenance import detect_activity_blocker
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.interfaces.local.api import LocalAPI
from aitest.interfaces.local.core_worker import (
    ShutdownCoordinator,
    prepare_core_shutdown,
    serve_connection,
    shutdown_frame,
)
from aitest.interfaces.local.pipe import NamedPipeClient, NamedPipeServer, PipeUnavailable
from tests.unit.test_command_adapter import _request
from tests.unit.test_core_startup_integrity import INSTANCE, _saved
from tests.unit.test_current_execution_snapshot import _batch, _publish
from tests.unit.test_serial_runner import _attempt


@pytest.mark.parametrize("problem", ["activity", "missing_guard", "read_fault", "bad_type"])
def test_exit_never_seals_live_unknown_or_unreadable_material(problem):
    seal = Mock()
    check = (
        None
        if problem == "missing_guard"
        else Mock(
            return_value="pending"
            if problem == "activity"
            else False
            if problem == "bad_type"
            else None,
            side_effect=OSError("unreadable") if problem == "read_fault" else None,
        )
    )
    assert prepare_core_shutdown(check, seal) is not None
    seal.assert_not_called()


def test_verified_idle_exit_seals_and_save_failure_refuses_exit():
    seal = Mock(return_value=())
    assert prepare_core_shutdown(lambda: None, seal) is None
    seal.assert_called_once()
    seal.side_effect = OSError("save failed")
    assert prepare_core_shutdown(lambda: None, seal) is not None


def guard(root, coordinator):
    return FileCoreExitGuard(
        root,
        lambda project, attempt: coordinator.read_checkpoint(
            project_id=project, attempt_id=attempt
        ),
        lambda attempt: require_saved_output_material(
            FileSpoolStore(root), attempt, attempt.output_block_refs
        ),
    )


def test_completed_label_without_an_actual_exit_blocks_even_without_sidecars(tmp_path):
    unit = FileUnitOfWork(tmp_path)
    _publish(unit, _batch())
    assert not (tmp_path / "execution-handles").exists()
    assert guard(tmp_path, ExecutionCommitCoordinator(unit)).blocker() is not None


def test_saved_exact_terminal_attempt_allows_exit_without_recomputing_business(tmp_path):
    batch = _batch()
    attempt = replace(
        batch.checkpoint.attempt,
        execution_handle_ref=ExecutionHandle(
            "handle", AdapterKind.COMMAND, "command/1.0", "123", "birth", "workdir"
        ),
        exit_fact_ref=ExitFact(
            attempt_id="attempt-1",
            startup_token="token",
            process_start_identity="birth",
            real_exit_code=0,
            capture_completeness=CaptureCompleteness.COMPLETE,
            termination_reason=ProcessTerminationReason.NATURAL_EXIT,
        ),
        capture_completeness=CaptureCompleteness.COMPLETE,
    )
    batch = replace(
        batch,
        checkpoint=replace(batch.checkpoint, attempt=attempt),
        facts=batch.facts.model_copy(
            update={"attempts": (project_attempt_fact(attempt, is_current=True),)}
        ),
    )
    unit = FileUnitOfWork(tmp_path)
    _publish(unit, batch)
    before = unit.commit_seq()
    assert guard(tmp_path, ExecutionCommitCoordinator(unit)).blocker() is None
    assert unit.commit_seq() == before


@pytest.mark.parametrize(
    "state",
    [
        AttemptState.INTENT_RECORDED,
        AttemptState.STARTING,
        AttemptState.RUNNING,
        AttemptState.STOP_REQUESTED,
        AttemptState.COLLECTING,
    ],
)
def test_active_checkpoint_with_exit_fact_still_needs_control_and_capture_boundary(tmp_path, state):
    batch = _batch()
    unit = FileUnitOfWork(tmp_path)
    _publish(unit, batch)
    record = ExecutionCommitCoordinator(unit).read_checkpoint(
        project_id=batch.facts.project_id, attempt_id=batch.checkpoint.attempt.attempt_id
    )
    attempt = replace(
        record.attempt,
        state=state,
        execution_handle_ref=ExecutionHandle(
            "handle", AdapterKind.COMMAND, "command/1.0", "123", "birth", "workdir"
        ),
        exit_fact_ref=ExitFact(
            "attempt-1",
            "token",
            "birth",
            0,
            termination_reason=ProcessTerminationReason.NATURAL_EXIT,
        ),
    )
    # This boundary test supplies the exact reader result; it is not a genuine new execution.
    guarded = FileCoreExitGuard(
        tmp_path, lambda project, identity: replace(record, attempt=attempt), lambda _: None
    )
    assert guarded.blocker() is not None


def test_bad_authority_cannot_be_hidden_by_absent_physical_activity(tmp_path):
    unit = FileUnitOfWork(tmp_path)
    unit.begin("bad-checkpoint", "project")
    unit.stage_record(
        aggregate_kind="execution_checkpoint",
        record_id="unknown",
        expected_revision=0,
        payload={"project_id": "project"},
    )
    unit.commit()
    assert guard(tmp_path, ExecutionCommitCoordinator(unit)).blocker() is not None


def test_actual_completed_sidecar_without_core_authority_cannot_allow_exit(tmp_path):
    adapter = CommandAdapter(
        spool_store=FileSpoolStore(tmp_path), handle_store=FileExecutionHandleStore(tmp_path)
    )
    adapter.register(CommandRegistration("exit-command", sys.executable, tmp_path))
    handle = adapter.start(_request("exit-command", ("-c", "print('saved output')")))
    try:
        result = adapter.collect(handle)
        deadline = time.monotonic() + 5
        while not result.complete and time.monotonic() < deadline:
            time.sleep(0.01)
            result = adapter.collect(handle)
        assert result.complete is True
        assert detect_activity_blocker(tmp_path) is None
        unit = FileUnitOfWork(tmp_path)
        assert guard(tmp_path, ExecutionCommitCoordinator(unit)).blocker() is not None
        attempt = replace(
            _attempt(),
            execution_handle_ref=handle,
            output_block_refs=result.output_blocks,
            output_cursors=result.output_cursors,
            exit_fact_ref=result.exit_fact_ref,
        )
        require_saved_output_material(FileSpoolStore(tmp_path), attempt, attempt.output_block_refs)
        (tmp_path / "spool" / attempt.attempt_id / "stdout.log").write_bytes(b"tampered")
        with pytest.raises(ValueError, match="output_material_unverified"):
            require_saved_output_material(
                FileSpoolStore(tmp_path), attempt, attempt.output_block_refs
            )
    finally:
        adapter.request_stop(handle)


def test_denied_shutdown_keeps_connection_and_returns_a_truthful_receipt():
    incoming = [shutdown_frame(), b'{"action":"doctor","request_id":"after"}']
    output = []

    def read():
        if not incoming:
            raise OSError("closed")
        return incoming.pop(0)

    server = SimpleNamespace(read_message=read, write_message=output.append)
    api = LocalAPI(instance_id="core", workspace_id="workspace")
    assert (
        serve_connection(server, api, connection_no=1, shutdown_blocker=lambda: "pending")
        == "disconnected"
    )
    denied, doctor = map(Response.model_validate_json, output)
    assert denied.error.code == "CORE_DRAINING"
    assert denied.result is None and doctor.result["status"] == "READY"


def test_parent_loss_can_retain_activity_and_accept_new_connections():
    coordinator = ShutdownCoordinator(123, lambda _: False)
    coordinator.watch_forever()
    assert coordinator.exit_requested
    coordinator.retain_activity()
    assert not coordinator.exit_requested
    assert coordinator.reason == "parent_exited_activity_retained"


def test_parent_loss_before_listener_binding_still_cancels_the_new_wait():
    coordinator = ShutdownCoordinator(123, lambda _: False)
    coordinator.request_exit("parent_exited")
    cancel = Mock()
    coordinator.bind_accept(cancel)
    cancel.assert_called_once()


@pytest.mark.parametrize(
    "problem",
    [
        "denied",
        "lost",
        "instance",
        "workspace",
        "request",
        "wrong_status",
        "malformed",
        "good",
        "duplicate_request",
        "duplicate_instance",
        "duplicate_workspace",
        "duplicate_status",
    ],
)
def test_shutdown_caller_marks_stopping_only_after_exact_acknowledgement(
    tmp_path, monkeypatch, problem
):
    workspace = Workspace(tmp_path)
    original = _saved(tmp_path)
    response = Response(
        request_id="core-shutdown",
        instance_id=INSTANCE,
        workspace_id=workspace.workspace_id,
        result={"status": "shutting_down"},
    )
    if problem == "denied":
        response = response.model_copy(
            update={
                "result": None,
                "error": ErrorDTO(code="CORE_DRAINING", message="pending", next_step="核实原执行"),
            }
        )
    elif problem in {"instance", "workspace", "request"}:
        response = response.model_copy(update={problem + "_id": "other"})
    elif problem == "wrong_status":
        response = response.model_copy(update={"result": {"status": "running"}})

    def read(*, timeout_ms):
        assert 0 < timeout_ms <= 2000
        if problem == "lost":
            raise PipeUnavailable("unknown receipt")
        raw = response.model_dump_json().encode()
        if problem in {"duplicate_request", "duplicate_instance", "duplicate_workspace"}:
            field = problem.removeprefix("duplicate_") + "_id"
            raw = b'{"' + field.encode() + b'":"foreign",' + raw[1:]
        elif problem == "duplicate_status":
            raw = raw.replace(
                b'"status":"shutting_down"', b'"status":"running","status":"shutting_down"'
            )
        return b"{" if problem == "malformed" else raw

    client = SimpleNamespace(write_message=Mock(), read_message=read, close=Mock())
    monkeypatch.setattr(
        bootstrap, "make_pipe_connector", lambda *args, **kwargs: lambda _: (client, INSTANCE)
    )
    accepted = bootstrap.shutdown_endpoint(tmp_path, workspace_id="workspace")
    saved = FileCoreLaunchStore(tmp_path).read()
    assert accepted is (problem == "good")
    assert saved["state"] == ("stopping" if accepted else original["state"])
    client.close.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows partial pipe frame")
def test_partial_response_obeys_one_deadline_for_header_and_body():
    workspace, instance = "deadline" + uuid4().hex[:12], "core"
    ready, close = threading.Event(), threading.Event()
    errors = []

    def serve():
        server = NamedPipeServer(workspace, instance_id=instance)
        try:
            server.start()
            ready.set()
            server.wait_for_client()
            server.validate_peer()
            server._write_all(b"\x00\x00\x00\x02x")
            close.wait(2)
        except Exception as error:
            errors.append(error)
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(2)
    client = NamedPipeClient(workspace, instance_id=instance)
    client.connect()
    try:
        started = time.monotonic()
        with pytest.raises(PipeUnavailable, match="超时"):
            client.read_message(timeout_ms=50)
        assert time.monotonic() - started < 1
    finally:
        close.set()
        client.close()
        thread.join(2)
    assert not thread.is_alive() and not errors


def test_default_assembly_has_exit_guard_and_protocol_error_uses_safe_boundary(tmp_path):
    core = assemble_workspace_core(tmp_path, instance_id="exit-core")
    try:
        assert core.shutdown_blocker is not None and core.shutdown_blocker() is None
        from aitest.infrastructure.security import known_secrets
        from aitest.interfaces.local.core_worker import dispatch_frame

        secret = "synthetic-control-secret"
        known_secrets().register(secret)
        try:
            response = dispatch_frame(
                core.api, Mock(), json.dumps({"__aitest_control__": secret}).encode()
            )
            assert secret not in response.model_dump_json()
        finally:
            known_secrets().clear()
    finally:
        core.lifetime_lock.release()


@pytest.mark.skipif(sys.platform != "win32", reason="actual Windows parent exit and core pipe")
def test_actual_worker_retains_unknown_authority_after_parent_exit_and_refuses_shutdown(tmp_path):
    seed = assemble_workspace_core(tmp_path, instance_id="exit-seed")
    try:
        _publish(seed.unit_of_work, _batch())
    finally:
        seed.lifetime_lock.release()
    script = (
        "from pathlib import Path; import sys; from aitest.bootstrap import acquire_endpoint; "
        "endpoint=acquire_endpoint(Path(sys.argv[1]), workspace_id='exit-retain', "
        "wait_timeout_seconds=8); endpoint.connection.close()"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    saved = None
    parent = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        output, errors = parent.communicate(timeout=20)
        assert parent.returncode == 0, (output, errors)
        saved = FileCoreLaunchStore(tmp_path).read()
        assert saved is not None and saved["state"] == "created"
        # Allow the real one-second watchdog to observe that the actual parent has gone.
        time.sleep(1.2)
        connector = bootstrap.make_pipe_connector(tmp_path)
        connected = None
        deadline = time.monotonic() + 8
        while connected is None and time.monotonic() < deadline:
            connected = connector("exit-retain")
            if connected is not None:
                candidate, instance = connected
                try:
                    candidate.write_message(b'{"action":"doctor","request_id":"retained"}')
                    response = Response.model_validate_json(candidate.read_message(timeout_ms=2000))
                except PipeUnavailable:
                    candidate.close()
                    connected = None
            if connected is None:
                time.sleep(0.05)
        assert connected is not None
        client, instance = connected
        try:
            assert instance == saved["instance_id"]
            assert response.error is None and response.instance_id == instance
            client.write_message(shutdown_frame())
            denied = Response.model_validate_json(client.read_message(timeout_ms=2000))
            assert denied.error.code == "CORE_DRAINING" and denied.result is None
            client.write_message(b'{"action":"doctor","request_id":"after-denied"}')
            assert Response.model_validate_json(client.read_message(timeout_ms=2000)).error is None
        finally:
            client.close()
        assert not bootstrap.shutdown_endpoint(tmp_path, workspace_id="exit-retain")
        assert FileCoreLaunchStore(tmp_path).read()["state"] == "created"
        assert probe_process(saved["process_id"]).alive is True
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(5)
        saved = saved or FileCoreLaunchStore(tmp_path).read()
        if saved is not None and probe_process(saved["process_id"]) == bootstrap.ProcessFact(
            True, saved["process_identity"]
        ):
            # Test-only cleanup of our isolated synthetic unknown execution; no business stop claim.
            assert saved["process_id"] != os.getpid()
            os.kill(saved["process_id"], signal.SIGTERM)
