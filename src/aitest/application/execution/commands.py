"""Adapt public saved identities to the existing authorization use cases."""

from collections.abc import Mapping

from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.application.execution.registration import InitialRunRegistration
from aitest.application.execution.step_execution import SavedStepExecution
from aitest.contracts.commands import Command


class InvalidExecutionCommand(ValueError):
    code = "INVALID_REQUEST"


class RunRegistrationBlocked(ValueError):
    code = "RUN_REGISTRATION_BLOCKED"


class ExecutionCommands:
    def __init__(
        self,
        service: ExecutionAuthorizationService,
        registration: InitialRunRegistration | None = None,
        execution: SavedStepExecution | None = None,
    ) -> None:
        self.service = service
        self.registration = registration
        self.execution = execution

    @staticmethod
    def _identity(command: Command) -> tuple[str, str]:
        if (
            not command.project_id
            or not command.project_id.strip()
            or not command.intent_id
            or not command.intent_id.strip()
            or not command.request_id.strip()
            or command.request_id == command.intent_id
            or type(command.expected_revision) is not int
            or command.expected_revision != 0
            or not command.target
            or not command.target.strip()
        ):
            raise InvalidExecutionCommand(
                "registration/preparation/consent requires project, intent, target and revision 0"
            )
        return command.project_id, command.intent_id

    def register(self, command: Command) -> Mapping[str, object]:
        project, intent = self._identity(command)
        values = command.parameters
        identity = values.get("prepared_run_id")
        if (
            set(values) != {"prepared_run_id", "record_revision"}
            or not isinstance(identity, str)
            or not identity.strip()
            or type(values.get("record_revision")) is not int
            or values["record_revision"] != 1
            or command.target != identity
        ):
            raise InvalidExecutionCommand(
                "registration requires an exact saved preparation reference"
            )
        if self.registration is None:
            raise RunRegistrationBlocked("initial registration is not configured")
        try:
            return self.registration.register(
                project_id=project,
                prepared_run_id=identity,
                request_id=command.request_id,
                intent_id=intent,
            ).model_dump(mode="json")
        except ValueError as error:
            if getattr(error, "code", None):
                raise
            raise RunRegistrationBlocked(str(error)) from error

    def prepare(self, command: Command) -> Mapping[str, object]:
        project, intent = self._identity(command)
        values = command.parameters
        if set(values) != {"run_id", "step_id"} or any(
            not isinstance(value, str) or not value.strip() for value in values.values()
        ):
            raise InvalidExecutionCommand("preparation requires only saved run_id and step_id")
        run, step = values["run_id"], values["step_id"]
        assert isinstance(run, str) and isinstance(step, str)
        if command.target != step:
            raise InvalidExecutionCommand("preparation target differs from its step_id")
        return self.service.prepare(
            project_id=project,
            run_id=run,
            step_id=step,
            intent_id=intent,
            request_id=command.request_id,
        )

    def grant(self, command: Command) -> Mapping[str, object]:
        project, intent = self._identity(command)
        values = command.parameters
        identity, challenge = values.get("execution_action_id"), values.get("approval_challenge_id")
        if (
            set(values) != {"execution_action_id", "record_revision", "approval_challenge_id"}
            or not isinstance(identity, str)
            or not identity.strip()
            or type(values.get("record_revision")) is not int
            or values["record_revision"] != 1
            or not isinstance(challenge, str)
            or not challenge.strip()
        ):
            raise InvalidExecutionCommand("consent requires an exact saved action and challenge")
        _, action = self.service.resolver.read(project, identity)
        if command.target != action.request.step_id:
            raise InvalidExecutionCommand("consent target differs from the frozen step")
        return self.service.grant(
            project_id=project,
            intent_id=intent,
            request_id=command.request_id,
            parameters={"execution_action_id": identity, "record_revision": 1},
            challenge_id=challenge,
        )

    def execute(self, command: Command) -> Mapping[str, object]:
        project, intent = self._identity(command)
        values = command.parameters
        identity = values.get("execution_action_id")
        if (
            set(values) != {"execution_action_id", "record_revision"}
            or not isinstance(identity, str)
            or not identity.strip()
            or type(values.get("record_revision")) is not int
            or values["record_revision"] != 1
        ):
            raise InvalidExecutionCommand("execution requires only an exact saved action reference")
        if self.execution is None:
            from aitest.application.errors import CapabilityUnavailable

            raise CapabilityUnavailable("saved step execution is not configured")
        assert command.target is not None
        return self.execution.execute(
            project_id=project, intent_id=intent, action_id=identity, step_id=command.target
        )
