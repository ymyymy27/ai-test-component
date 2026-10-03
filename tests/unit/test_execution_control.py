from aitest.application.execution.control import (
    RunControlAction,
    RunControlService,
    StepExecutionBasis,
    aggregate_case_execution,
)
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    CaptureCompleteness,
    ExecutionHandle,
    ExecutionInspectionState,
    PlanRevisionRef,
    Run,
    RunControlState,
    RunTier,
    SideEffectClass,
    StepRevisionRef,
    StopRequestResult,
)
from aitest.domain.project.context import IsolationMode


class _UnconfirmedStopPort:
    def start(self, request):
        raise AssertionError("not used")

    def inspect(self, handle):
        raise AssertionError("not used")

    def collect(self, handle, cursors=None):
        raise AssertionError("not used")

    def request_stop(self, handle):
        return StopRequestResult(
            handle_id=handle.handle_id,
            stop_confirmed=False,
            observed_state=ExecutionInspectionState.UNKNOWN,
            unknown_reason="process_group_stop_unconfirmed",
        )


def _handle() -> ExecutionHandle:
    return ExecutionHandle(
        handle_id="handle-1",
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="1.0",
        real_execution_id="1234",
        process_start_identity="start-1",
        workdir_ref="workspace-1",
    )


def _attempt(state: AttemptState) -> Attempt:
    return Attempt(
        attempt_id="attempt-1",
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
        side_effect_class=SideEffectClass.READ_ONLY,
        adapter_kind=AdapterKind.COMMAND,
        adapter_version="1.0",
        state=state,
        execution_handle_ref=_handle(),
    )


def _run() -> Run:
    return Run(
        run_id="run-1",
        project_id="project-1",
        origin_workspace_id="workspace-1",
        intent_id="intent-1",
        tier=RunTier.FULL,
        driver="planned",
        conclusion_ceiling="passable",
        plan_revision_ref=PlanRevisionRef(
            revision_id="plan-1",
            revision_no=1,
            digest="sha256:plan-1",
        ),
        environment_ref="environment-1",
        environment_isolation_mode=IsolationMode.VENV,
        rules_revision="rules-1",
    )


def test_cancel_keeps_unconfirmed_stop_in_pending_verification() -> None:
    decision = RunControlService(_UnconfirmedStopPort()).cancel(_attempt(AttemptState.RUNNING))

    assert decision.action is RunControlAction.CANCEL
    assert decision.run_state is RunControlState.CANCELLING
    assert decision.attempt_state is AttemptState.PENDING_VERIFICATION
    assert decision.stop_confirmed is False
    assert "stop_confirmation_unavailable" in decision.gaps
    assert decision.requires_verification is True


def test_mixed_current_and_inherited_steps_cannot_count_e_r_v() -> None:
    aggregate = aggregate_case_execution(
        case_id="case-1",
        required_step_ids=("step-1", "step-2"),
        steps=(
            StepExecutionBasis(
                step_id="step-1",
                attempt_id="attempt-current",
                from_current_run=True,
                state=AttemptState.COMPLETED,
                capture_completeness=CaptureCompleteness.COMPLETE,
                verification_valid=True,
            ),
            StepExecutionBasis(
                step_id="step-2",
                attempt_id="attempt-old",
                from_current_run=False,
                state=AttemptState.COMPLETED,
                capture_completeness=CaptureCompleteness.COMPLETE,
                verification_valid=True,
            ),
        ),
    )

    assert aggregate.mixed_inheritance is True
    assert aggregate.can_count_execution is False
    assert aggregate.can_count_reuse is False
    assert aggregate.can_count_verification is False
    assert aggregate.pending_step_ids == ("step-2",)


def test_decisive_failure_remains_visible_without_counting_pass() -> None:
    aggregate = aggregate_case_execution(
        case_id="case-1",
        required_step_ids=("step-1",),
        steps=(
            StepExecutionBasis(
                step_id="step-1",
                attempt_id="attempt-1",
                from_current_run=True,
                state=AttemptState.COMPLETED,
                capture_completeness=CaptureCompleteness.COMPLETE,
                verification_valid=True,
            ),
        ),
        decisive_failure_step_ids=("step-1",),
    )

    assert aggregate.has_decisive_failure is True
    assert aggregate.can_count_execution is False
    assert aggregate.can_count_reuse is False
    assert aggregate.can_count_verification is False
