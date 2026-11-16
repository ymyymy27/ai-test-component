"""Short transaction ownership for the controlled C runtime revision component."""

from __future__ import annotations

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.ports import (
    BasisConfirmationProof,
    ControlledWriteProof,
    ExecutionAuthorizationProof,
    RecordRepository,
    StageableWorkspaceUnitOfWork,
)
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.planning.plans import Plan
from aitest.domain.planning.runtime_revision import RuntimeRevisionRequest


class RuntimeRevisionService:
    """An internal component; trusted UI action registration is a separate boundary."""

    def __init__(
        self,
        *,
        unit: StageableWorkspaceUnitOfWork,
        records: RecordRepository,
        approvals: BasisConfirmationProof | None = None,
        controlled_writes: ControlledWriteProof | None = None,
        execution_authorizations: ExecutionAuthorizationProof | None = None,
    ) -> None:
        self.unit = unit
        self.coordinator = ExecutionCommitCoordinator(
            unit,
            records=records,
            approvals=approvals,
            controlled_writes=controlled_writes,
            execution_authorizations=execution_authorizations,
        )

    def apply(
        self,
        *,
        project_id: str,
        run_id: str,
        plan: Plan,
        request: RuntimeRevisionRequest,
        request_id: str,
        intent_id: str,
        confirmation_ids: tuple[str, ...] = (),
    ) -> ExecutionFacts:
        self.unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            result, created = self.coordinator.stage_runtime_revision(
                project_id=project_id,
                run_id=run_id,
                plan=plan,
                request=request,
                intent_id=intent_id,
                confirmation_ids=confirmation_ids,
            )
            if created:
                self.unit.commit(request_id)
            else:
                self.unit.rollback(request_id)
        except BaseException as error:
            try:
                self.unit.rollback(request_id)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise
        return result
