"""Stage a scheduling projection through the same business unit of work."""

from dataclasses import replace

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.domain.execution.continuation import RunContinuation, continuation_id


def read_continuation(
    coordinator: ExecutionCommitCoordinator, workspace: str, project: str, run: str
) -> RunContinuation | None:
    identity = continuation_id(workspace, project, run)
    raw = coordinator._read_payload("execution_schedule", identity)
    if raw is None:
        return None
    work = RunContinuation.read(raw)
    if (work.workspace_id, work.project_id, work.run_id, work.record_id) != (
        workspace,
        project,
        run,
        identity,
    ):
        raise ValueError("continuation scope differs from its exact run")
    return work


def stage_continuation(coordinator: ExecutionCommitCoordinator, work: RunContinuation) -> None:
    coordinator._uow.stage_record(
        aggregate_kind="execution_schedule",
        record_id=work.record_id,
        expected_revision=coordinator._revision("execution_schedule", work.record_id),
        payload=work.payload(),
    )


def stage_existing_activity(
    coordinator: ExecutionCommitCoordinator, workspace: str, project: str, run: str, active: bool
) -> None:
    work = read_continuation(coordinator, workspace, project, run)
    if work is not None and work.active is not active:
        stage_continuation(coordinator, replace(work, active=active))
