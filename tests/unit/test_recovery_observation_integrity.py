"""Recovery uses original process facts and verified, retained output material."""

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aitest.application.execution.control import RunControlService
from aitest.application.execution.facts import project_attempt_fact
from aitest.application.execution.recovery import RecoveryAction, recover_attempt
from aitest.application.execution.runner import SerialRunner
from aitest.application.execution.runtime_revision import require_runtime_boundary
from aitest.contracts.execution_facts import RunControlStateFact
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    ExecutionCollectionResult,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    ExitFact,
    OutputStreamName,
    ProcessTerminationReason,
    RecoveryCheckpoint,
    RunControlState,
    SideEffectClass,
    SpoolManifest,
)
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.recovery.test_execution_recovery import _attempt, _handle, _LostPort
from tests.support.execution_authority import fixture_coordinator
from tests.unit.test_current_attempt_transition_integrity import _new_start, _record, _saved_graph
from tests.unit.test_current_execution_snapshot import _batch
from tests.unit.test_execution_control import _run
from tests.unit.test_serial_runner import FakeExecutionPort, _request
from tests.unit.test_serial_runner import _attempt as _start_attempt


def _saved(state=AttemptState.RUNNING, side_effect=SideEffectClass.UNKNOWN):
    attempt = replace(
        _attempt(state=state, side_effect=side_effect), execution_handle_ref=_handle()
    )
    checkpoint = RecoveryCheckpoint(
        attempt.run_id,
        attempt.step_id,
        attempt.attempt_id,
        "saved",
        resolved_input_digest=attempt.resolved_input_digest,
        side_effect_class=side_effect,
        execution_handle_ref=attempt.execution_handle_ref,
    )
    return checkpoint, attempt


def _inspection(state=ExecutionInspectionState.RUNNING, **updates):
    return replace(
        ExecutionInspectionResult("handle-1", state, True, True, stop_confirmed=True), **updates
    )


@pytest.mark.parametrize(
    "state",
    [
        ExecutionInspectionState.RUNNING,
        ExecutionInspectionState.EXITED,
        ExecutionInspectionState.STOPPED,
    ],
)
@pytest.mark.parametrize(
    "change", [{"handle_id": "other"}, {"identity_matches": 1}, {"identity_matches": "false"}]
)
def test_foreign_or_unproven_observation_never_reattaches_or_confirms_stop(tmp_path, state, change):
    checkpoint, attempt = _saved()
    result = recover_attempt(
        checkpoint, attempt, FileSpoolStore(tmp_path), inspection=_inspection(state, **change)
    )
    assert result.action is RecoveryAction.PENDING_VERIFICATION
    assert result.attempt.state is AttemptState.PENDING_VERIFICATION
    assert result.attempt.execution_handle_ref == attempt.execution_handle_ref
    assert result.gaps


@pytest.mark.parametrize("confirmed", [False, "false", 1])
def test_stop_without_exact_confirmation_keeps_execution_pending(tmp_path, confirmed):
    checkpoint, attempt = _saved()
    result = recover_attempt(
        checkpoint,
        attempt,
        FileSpoolStore(tmp_path),
        inspection=_inspection(ExecutionInspectionState.STOPPED, stop_confirmed=confirmed),
    )
    assert result.attempt.state is AttemptState.PENDING_VERIFICATION
    assert result.action is RecoveryAction.PENDING_VERIFICATION


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_id": "foreign"},
        {"process_start_identity": "reused-pid"},
        {"termination_reason": ProcessTerminationReason.UNKNOWN},
        {"timed_out": True},
    ],
)
def test_exit_fact_presence_does_not_prove_a_reliable_completed_attempt(tmp_path, changes):
    checkpoint, attempt = _saved(AttemptState.COMPLETED)
    exit_fact = ExitFact(
        "attempt-1", "token", "start-1", 0, termination_reason=ProcessTerminationReason.NATURAL_EXIT
    )
    attempt = replace(attempt, exit_fact_ref=replace(exit_fact, **changes))
    result = recover_attempt(
        checkpoint,
        attempt,
        FileSpoolStore(tmp_path),
        inspection=_inspection(
            ExecutionInspectionState.LOST, process_reachable=False, identity_matches=False
        ),
    )
    assert result.action is RecoveryAction.PENDING_VERIFICATION
    assert result.attempt.state is AttemptState.PENDING_VERIFICATION


@pytest.mark.parametrize("field", ["resolved_input_digest", "execution_handle_ref"])
def test_checkpoint_cannot_rebind_execution_basis_before_spool_access(tmp_path, field):
    checkpoint, attempt = _saved()
    changed = (
        replace(_handle(), process_start_identity="foreign")
        if field == "execution_handle_ref"
        else "foreign-input"
    )
    with pytest.raises(ValueError, match="checkpoint"):
        recover_attempt(replace(checkpoint, **{field: changed}), attempt, FileSpoolStore(tmp_path))


def test_original_checkpoint_handle_is_retained_when_legacy_attempt_omits_it(tmp_path):
    checkpoint, attempt = _saved()
    result = recover_attempt(
        checkpoint,
        replace(attempt, execution_handle_ref=None),
        FileSpoolStore(tmp_path),
        inspection=_inspection(),
    )
    assert result.action is RecoveryAction.REATTACH
    assert result.attempt.execution_handle_ref == checkpoint.execution_handle_ref


@pytest.mark.parametrize("field", ["attempt_id", "run_id", "step_id"])
def test_recovery_checks_manifest_namespace_before_salvage(tmp_path, monkeypatch, field):
    checkpoint, attempt = _saved()
    store = FileSpoolStore(tmp_path)
    manifest = SpoolManifest(attempt.attempt_id, attempt.run_id, attempt.step_id)
    monkeypatch.setattr(
        store, "read_manifest", lambda identity: replace(manifest, **{field: "foreign"})
    )
    monkeypatch.setattr(
        store,
        "salvage_streams",
        lambda identity: pytest.fail("foreign manifest must not be salvaged"),
    )
    with pytest.raises(ValueError, match="manifest"):
        recover_attempt(
            checkpoint,
            attempt,
            store,
            inspection=_inspection(
                ExecutionInspectionState.LOST, process_reachable=False, identity_matches=False
            ),
        )


def test_spool_lookup_never_accepts_another_attempt_under_the_requested_path(tmp_path):
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
    )
    writer.close()
    path = tmp_path / "spool/attempt-1/manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["attempt_id"] = "foreign"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        store.read_manifest("attempt-1")


@pytest.mark.parametrize("field", ["step_id", "schema_version"])
def test_ambiguous_manifest_fields_are_rejected_instead_of_choosing_the_last(tmp_path, field):
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
    )
    writer.close()
    path = tmp_path / "spool/attempt-1/manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    encoded = json.dumps({field: payload[field]})[1:-1]
    raw = json.dumps(payload)
    assert encoded in raw
    path.write_text(raw.replace(encoded, encoded + ", " + encoded, 1), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate field"):
        store.read_manifest("attempt-1")


def _sealed_store(root):
    store = FileSpoolStore(root)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_size=1,
    )
    writer.append(b"safe")
    writer.close()
    return store


def test_recovery_verifies_sealed_bytes_before_treating_them_as_material(tmp_path):
    store = _sealed_store(tmp_path)
    (tmp_path / "spool/attempt-1/stdout.log").write_bytes(b"evil")
    checkpoint, attempt = _saved()
    with pytest.raises(ValueError, match="verification"):
        recover_attempt(
            checkpoint, attempt, store, inspection=_inspection(ExecutionInspectionState.EXITED)
        )


def test_complete_blocks_do_not_prove_complete_process_output(tmp_path):
    store = _sealed_store(tmp_path)
    checkpoint, attempt = _saved()
    result = recover_attempt(
        checkpoint, attempt, store, inspection=_inspection(ExecutionInspectionState.EXITED)
    )
    assert result.recovered_blocks
    assert result.capture_completeness is CaptureCompleteness.UNKNOWN
    assert result.attempt.capture_completeness is CaptureCompleteness.UNKNOWN


def test_unknown_write_is_not_safe_retry_just_because_it_declares_idempotence(tmp_path):
    checkpoint, attempt = _saved(side_effect=SideEffectClass.IDEMPOTENT_WRITE)
    result = recover_attempt(checkpoint, attempt, FileSpoolStore(tmp_path))
    assert result.action is RecoveryAction.PENDING_VERIFICATION
    assert result.attempt.state is AttemptState.PENDING_VERIFICATION


@pytest.mark.parametrize(
    "changes",
    [
        {"attempt_id": "foreign"},
        {"process_start_identity": "foreign"},
        {"termination_reason": ProcessTerminationReason.EXECUTOR_LOST},
    ],
)
def test_pause_and_runtime_revision_share_original_exit_identity_checks(changes):
    _checkpoint, attempt = _saved(AttemptState.COMPLETED)
    fact = ExitFact(
        "attempt-1", "token", "start-1", 0, termination_reason=ProcessTerminationReason.NATURAL_EXIT
    )
    attempt = replace(attempt, exit_fact_ref=replace(fact, **changes))
    decision = RunControlService(FakeExecutionPort()).pause(
        replace(_run(), control_state=RunControlState.RUNNING), (attempt,)
    )
    assert decision.run_state is RunControlState.PAUSE_REQUESTED
    assert decision.requires_verification
    facts = _batch().facts.model_copy(
        update={"attempts": (project_attempt_fact(attempt, is_current=True),)}
    )
    with pytest.raises(ValueError, match="verified non-active"):
        require_runtime_boundary(facts)


@pytest.mark.parametrize(
    "reason", [ProcessTerminationReason.UNKNOWN, ProcessTerminationReason.EXECUTOR_LOST]
)
def test_saved_uncertain_exit_blocks_replacement_without_consuming_authorization(tmp_path, reason):
    unit, coordinator, _published, domain = _saved_graph(tmp_path)
    port = FakeExecutionPort()
    old = replace(
        domain[1],
        execution_handle_ref=port.handle,
        exit_fact_ref=ExitFact(
            "attempt-2", "token", port.handle.process_start_identity, 0, termination_reason=reason
        ),
    )
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=_record(old))
    sequence = unit.current_commit_sequence()
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    with pytest.raises(ValueError, match="stop and verify"):
        runner.start_attempt(attempt, request)
    assert unit.current_commit_sequence() == sequence
    assert not port.started


@pytest.mark.parametrize(
    "change",
    [{"termination_reason": ProcessTerminationReason.EXECUTOR_LOST}, {"real_exit_code": None}],
)
def test_inconclusive_exit_cannot_become_completion_or_allow_serial_progress(change):
    _checkpoint, attempt = _saved()
    fact = ExitFact(
        "attempt-1", "token", "start-1", 0, termination_reason=ProcessTerminationReason.NATURAL_EXIT
    )
    fact = replace(fact, **change)
    collection = ExecutionCollectionResult(attempt.attempt_id, exit_fact_ref=fact, complete=True)
    state = SerialRunner._attempt_state_for(
        attempt, _inspection(ExecutionInspectionState.EXITED), collection
    )
    assert state is AttemptState.PENDING_VERIFICATION
    assert SerialRunner._blocks_serial_progress(replace(attempt, state=state, exit_fact_ref=fact))


@pytest.mark.parametrize("field", ["offset", "last_committed_digest"])
def test_cursor_must_match_its_sealed_block_before_salvage(tmp_path, monkeypatch, field):
    store = _sealed_store(tmp_path)
    manifest = store.read_manifest("attempt-1")
    changed = 99 if field == "offset" else "sha256:foreign"
    cursor = replace(manifest.cursors[0], **{field: changed})
    monkeypatch.setattr(
        store, "read_manifest", lambda identity: replace(manifest, cursors=(cursor,))
    )
    monkeypatch.setattr(
        store, "salvage_streams", lambda identity: pytest.fail("bad cursor must not be salvaged")
    )
    checkpoint, attempt = _saved()
    with pytest.raises(ValueError, match="cursor"):
        recover_attempt(
            checkpoint, attempt, store, inspection=_inspection(ExecutionInspectionState.EXITED)
        )


def test_salvaged_tail_is_saved_in_authoritative_attempt_and_survives_restart(tmp_path):
    unit = FileUnitOfWork(tmp_path)
    first = replace(_start_attempt(), side_effect_class=SideEffectClass.UNKNOWN)
    request = replace(_request(), side_effect_class=SideEffectClass.UNKNOWN)
    coordinator = fixture_coordinator(unit, ((first, request),))
    sidecars = FileCheckpointStore(tmp_path)
    spool = FileSpoolStore(tmp_path)
    SerialRunner(
        FakeExecutionPort(), spool, checkpoint_store=sidecars, commit_coordinator=coordinator
    ).start_attempt(first, request)
    writer = spool.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_size=10000,
    )
    writer.append(b"survived-tail\n" * 100)
    # The filter's unresolved tail stays in memory; only already-safe disk bytes
    # are recovery material, so compare against those actual bytes.
    safe_bytes = (tmp_path / "spool/attempt-1/stdout.log").read_bytes()
    assert safe_bytes
    writer.abort()
    (result,) = SerialRunner(
        _LostPort(), spool, checkpoint_store=sidecars, commit_coordinator=coordinator
    ).recover_pending()
    assert result.action is RecoveryAction.PENDING_VERIFICATION
    assert result.recovered_blocks and not result.recovered_blocks[0].complete
    saved = coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-1")
    assert saved.attempt.output_block_refs == result.recovered_blocks
    assert saved.attempt.output_cursors == result.recovered_cursors
    assert saved.attempt.capture_completeness is CaptureCompleteness.PARTIAL
    rebuilt = FileCheckpointStore(tmp_path).load("attempt-1")
    assert rebuilt.attempt == saved.attempt
    assert spool.read_block(saved.attempt.output_block_refs[0]) == safe_bytes


def test_rechecking_reliable_terminal_history_does_not_erase_saved_run_result(tmp_path):
    unit = FileUnitOfWork(tmp_path)
    first, request = _start_attempt(), _request()
    coordinator = fixture_coordinator(unit, ((first, request),))
    sidecars = FileCheckpointStore(tmp_path)
    active = SerialRunner(FakeExecutionPort(), commit_coordinator=coordinator).start_attempt(
        first, request
    )
    completed = replace(
        active,
        state=AttemptState.COMPLETED,
        exit_fact_ref=ExitFact(
            "attempt-1",
            "token",
            active.execution_handle_ref.process_start_identity,
            0,
            termination_reason=ProcessTerminationReason.NATURAL_EXIT,
        ),
        capture_completeness=CaptureCompleteness.COMPLETE,
    )
    record = _record(completed)
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=record)
    sidecars.persist(record)
    current = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    unit.begin("fixture-completed-result", "project-1")
    coordinator._stage_snapshot(
        current.model_copy(
            update={
                "run": current.run.model_copy(
                    update={
                        "control_state": RunControlStateFact.COMPLETED,
                        "result_ref": "fixture-saved-result",
                        "ended_at": datetime.now(UTC),
                    }
                )
            }
        )
    )
    unit.commit()
    saved = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    sequence = unit.current_commit_sequence()
    (result,) = SerialRunner(
        _LostPort(),
        FileSpoolStore(tmp_path),
        checkpoint_store=sidecars,
        commit_coordinator=coordinator,
    ).recover_pending()
    assert result.action is RecoveryAction.TERMINAL_PRESERVED
    assert result.attempt == completed
    assert unit.current_commit_sequence() == sequence
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == saved
    assert saved.run.result_ref == "fixture-saved-result"


def test_reclaimed_temporary_spool_does_not_delete_reliable_historical_refs(tmp_path):
    store = _sealed_store(tmp_path)
    manifest = store.read_manifest("attempt-1")
    checkpoint, attempt = _saved(AttemptState.COMPLETED)
    attempt = replace(
        attempt,
        exit_fact_ref=ExitFact(
            "attempt-1",
            "token",
            "start-1",
            0,
            termination_reason=ProcessTerminationReason.NATURAL_EXIT,
        ),
        output_block_refs=manifest.blocks,
        output_cursors=manifest.cursors,
        capture_completeness=CaptureCompleteness.COMPLETE,
    )
    (tmp_path / "spool/attempt-1/stdout.log").unlink()
    result = recover_attempt(checkpoint, attempt, store)
    assert result.action is RecoveryAction.TERMINAL_PRESERVED
    assert result.attempt == attempt
    assert not result.recovered_blocks
    assert "spool_material_unavailable" in result.gaps
