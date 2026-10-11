"""Advance an authorized saved action without accepting executable input."""

from collections.abc import Mapping

from aitest.application.errors import CapabilityUnavailable
from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import project_attempt_fact
from aitest.application.execution.runner import SerialRunner
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.application.ports import ExecutionPort, SpoolStore
from aitest.domain.approvals import ApprovalRequired
from aitest.domain.execution.runs import has_reliable_terminal_fact


class StepExecutionBlocked(ValueError):
    code = "EXECUTION_BLOCKED"


class SavedStepExecution:
    def __init__(
        self,
        authorizations: ExecutionAuthorizationService,
        coordinator: ExecutionCommitCoordinator,
        spool: SpoolStore,
        execution_port: ExecutionPort | None,
    ) -> None:
        self.authorizations, self.coordinator = authorizations, coordinator
        self.spool, self.execution_port = spool, execution_port

    def execute(
        self, *, project_id: str, intent_id: str, action_id: str, step_id: str, max_polls: int = 100
    ) -> Mapping[str, object]:
        _, action = self.authorizations.resolver.read(project_id, action_id)
        if (intent_id, step_id) != (action.request.intent_id, action.request.step_id):
            raise ApprovalRequired("execution intent or target differs from its frozen action")
        # Both new execution and recovery must prove the original core consent.
        self.authorizations._origin(project_id, action.request.authorization_ref.authorization_id)
        try:
            saved = self.coordinator.find_start(
                project_id=project_id,
                intent_id=intent_id,
                fingerprint=execution_start_fingerprint(action.attempt, action.request),
            )
            if saved is not None and has_reliable_terminal_fact(saved):
                attempt = saved
                self.coordinator.ensure_checkpoint_evidence(project_id=project_id, attempt=attempt)
            else:
                if self.execution_port is None:
                    raise CapabilityUnavailable("a trusted execution adapter is not configured")
                attempt = SerialRunner(
                    self.execution_port,
                    self.spool,
                    commit_coordinator=self.coordinator,
                ).execute_attempt(saved or action.attempt, action.request, max_polls=max_polls)
            current = self.coordinator.read_current_facts(
                project_id=project_id, run_id=action.request.run_id
            )
            if current is None:
                raise StepExecutionBlocked("the registered current execution facts are unavailable")
            return {
                "attempt": project_attempt_fact(
                    attempt,
                    is_current=current.current_attempt_by_step.get(step_id) == attempt.attempt_id,
                ).model_dump(mode="json"),
                "execution_facts": current.model_dump(mode="json"),
            }
        except ValueError as error:
            if getattr(error, "code", None):
                raise
            raise StepExecutionBlocked(str(error)) from error
