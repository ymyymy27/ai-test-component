from pathlib import Path

from aitest.application.execution.recovery import (
    RecoveryAction,
    invalidate_downstream_attempts,
    recover_attempt,
)
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    ConsumedOutput,
    ExecutionHandle,
    ExecutionInspectionResult,
    ExecutionInspectionState,
    OutputStreamName,
    PlanRevisionRef,
    RecoveryCheckpoint,
    SideEffectClass,
    StepRevisionRef,
)
from aitest.infrastructure.file_store.spool import FileSpoolStore


def _handle() -> ExecutionHandle:
    return ExecutionHandle(
        handle_id="handle-1",
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="1.0",
        real_execution_id="1234",
        process_start_identity="start-1",
        workdir_ref="workspace-1",
    )


def _attempt(
    attempt_id: str = "attempt-1",
    *,
    state: AttemptState = AttemptState.RUNNING,
    upstream_attempt_ids: tuple[str, ...] = ("upstream-1",),
    side_effect: SideEffectClass = SideEffectClass.UNKNOWN,
) -> Attempt:
    return Attempt(
        attempt_id=attempt_id,
        run_id="run-1",
        step_id="step-1",
        attempt_index=1,
        resolved_input_digest="sha256:input-1",
        step_revision_ref=StepRevisionRef(
            step_revision_id="step-revision-1",
            revision_no=1,
            digest="sha256:step-revision-1",
        ),
        source_binding_digest="sha256:source-1",
        side_effect_class=side_effect,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="1.0",
        state=state,
        consumed_outputs=tuple(
            ConsumedOutput(
                upstream_attempt_id=upstream,
                output_object_digest=f"sha256:output:{upstream}",
                value_ref=f"value:{upstream}",
            )
            for upstream in upstream_attempt_ids
        ),
    )


def test_recovery_restores_spool_cursors_after_process_loss(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_size=1,
    )
    writer.append(b"captured-before-loss\n")
    writer.close()
    manifest = store.read_manifest("attempt-1")

    checkpoint = RecoveryCheckpoint(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        last_committed_stage="collecting",
        output_cursors=manifest.cursors,
        output_block_refs=manifest.blocks,
        resolved_input_digest="sha256:input-1",
        side_effect_class=SideEffectClass.UNKNOWN,
        execution_handle_ref=_handle(),
    )
    result = recover_attempt(
        checkpoint,
        _attempt(),
        store,
        inspection=ExecutionInspectionResult(
            handle_id="handle-1",
            state=ExecutionInspectionState.LOST,
            process_reachable=False,
            identity_matches=False,
            unknown_reason="process_lost",
        ),
    )

    assert result.action is RecoveryAction.PENDING_VERIFICATION
    assert result.attempt.state is AttemptState.PENDING_VERIFICATION
    assert result.recovered_blocks == manifest.blocks
    assert result.recovered_cursors == manifest.cursors
    assert "execution_result_unknown" in result.gaps


def test_recovery_salvages_unsealed_tail_after_crash(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDERR,
        block_size=100,
    )
    writer.append(b"unsealed-tail")
    writer.abort()

    manifest = store.salvage_streams("attempt-1")
    assert len(manifest.blocks) == 1
    assert manifest.blocks[0].complete is False
    assert manifest.blocks[0].capture_source == "recovery"
    assert store.read_block(manifest.blocks[0]) == b"unsealed-tail"

    checkpoint = RecoveryCheckpoint(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        last_committed_stage="streaming",
        output_cursors=(),
        output_block_refs=(),
        resolved_input_digest="sha256:input-1",
        side_effect_class=SideEffectClass.UNKNOWN,
        execution_handle_ref=_handle(),
    )
    result = recover_attempt(
        checkpoint,
        _attempt(),
        store,
        inspection=ExecutionInspectionResult(
            handle_id="handle-1",
            state=ExecutionInspectionState.LOST,
            process_reachable=False,
            identity_matches=False,
        ),
    )
    assert "partial_spool_block" in result.gaps
    assert result.recovered_blocks[0].complete is False
    assert result.capture_completeness.value == "partial"


def test_plan_change_invalidates_only_precise_downstream_dependencies() -> None:
    old_revision = PlanRevisionRef(
        revision_id="plan-1",
        revision_no=1,
        digest="sha256:plan-old",
    )
    new_revision = PlanRevisionRef(
        revision_id="plan-1",
        revision_no=2,
        digest="sha256:plan-new",
    )
    affected = _attempt("executing-affected", upstream_attempt_ids=("upstream-1",))
    unrelated = _attempt("executing-unrelated", upstream_attempt_ids=("upstream-2",))
    completed = _attempt(
        "completed-dependent",
        state=AttemptState.COMPLETED,
        upstream_attempt_ids=("upstream-1",),
    )

    invalidations = invalidate_downstream_attempts(
        (affected, unrelated, completed),
        previous_plan_revision=old_revision,
        current_plan_revision=new_revision,
        affected_upstream_attempt_ids=("upstream-1",),
    )
    assert [item.attempt.attempt_id for item in invalidations] == ["executing-affected"]
    assert invalidations[0].attempt.state is AttemptState.INVALIDATED
    assert invalidations[0].upstream_attempt_ids == ("upstream-1",)
    assert unrelated.state is AttemptState.RUNNING
    assert completed.state is AttemptState.COMPLETED


def test_same_plan_revision_has_no_false_invalidation() -> None:
    revision = PlanRevisionRef(
        revision_id="plan-1",
        revision_no=1,
        digest="sha256:plan-1",
    )
    attempt = _attempt("executing", upstream_attempt_ids=("upstream-1",))
    invalidations = invalidate_downstream_attempts(
        (attempt,),
        previous_plan_revision=revision,
        current_plan_revision=revision,
        affected_upstream_attempt_ids=("upstream-1",),
    )
    assert invalidations == ()
    assert attempt.state is AttemptState.RUNNING
