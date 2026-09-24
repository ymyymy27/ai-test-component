import pytest

from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    PlanRevisionRef,
    RunControlState,
    SideEffectClass,
    StepRevisionRef,
    StepState,
)
from aitest.domain.execution.sources import (
    ExecutionSourceVerification,
    SourceVerificationState,
)


def test_execution_state_enums_keep_unknown_separate_from_failure() -> None:
    assert RunControlState.PENDING_VERIFICATION.value == "pending_verification"
    assert StepState.BLOCKED.value == "blocked"
    assert AttemptState.PENDING_VERIFICATION.value != AttemptState.EXECUTION_ERROR.value
    assert AttemptState.UNKNOWN.value == "unknown"


def test_attempt_index_drives_retry_count() -> None:
    attempt = Attempt(
        attempt_id="attempt-2",
        run_id="run-1",
        step_id="step-1",
        attempt_index=2,
        resolved_input_digest="sha256:input",
        step_revision_ref=StepRevisionRef(
            step_revision_id="step-rev-1", revision_no=1, digest="sha256:step"
        ),
        source_binding_digest="sha256:source",
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="1.0",
    )
    assert attempt.retry_count == 1


def test_attempt_index_rejects_zero() -> None:
    with pytest.raises(ValueError, match="attempt_index"):
        Attempt(
            attempt_id="attempt-0",
            run_id="run-1",
            step_id="step-1",
            attempt_index=0,
            resolved_input_digest="sha256:input",
            step_revision_ref=StepRevisionRef(
                step_revision_id="step-rev-1", revision_no=1, digest="sha256:step"
            ),
            source_binding_digest="sha256:source",
            side_effect_class=SideEffectClass.UNKNOWN,
            adapter_kind=AdapterKind.COMMAND,
            adapter_version="1.0",
        )


def test_source_verification_records_actual_observation_only() -> None:
    verification = ExecutionSourceVerification(
        verification_id="verify-1",
        project_id="project-1",
        plan_revision_ref=PlanRevisionRef(
            revision_id="plan-1", revision_no=1, digest="sha256:plan"
        ),
        expected_source_binding_digest="sha256:expected",
        materialized_snapshot_ref="snapshot-1",
        observed_source_digest="sha256:observed",
        state=SourceVerificationState.MISMATCH,
    )
    assert verification.state is SourceVerificationState.MISMATCH
    assert verification.observed_source_digest == "sha256:observed"
