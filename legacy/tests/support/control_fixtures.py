"""共享的执行控制测试夹具（原 test_execution_control.py 的 helper）。"""

from __future__ import annotations

from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    AttemptState,
    ExecutionHandle,
    PlanRevisionRef,
    Run,
    RunTier,
    SideEffectClass,
    StepRevisionRef,
)
from aitest.domain.project.context import IsolationMode


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
