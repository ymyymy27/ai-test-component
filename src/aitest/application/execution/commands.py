"""Adapt public saved identities to the existing authorization use cases."""

from collections.abc import Mapping

from aitest.application.execution.authorization import ExecutionAuthorizationService
from aitest.application.execution.registration import InitialRunRegistration
from aitest.application.execution.step_execution import SavedStepExecution
from aitest.contracts.commands import Command


class InvalidExecutionCommand(ValueError):
    code = "INVALID_REQUEST"


def _downstream_steps(facts: object, target: str) -> set[str]:
    """依赖 `target` 的传递下游步骤（用已提交事实的依赖边算，不猜）。"""

    steps = tuple(getattr(facts, "steps", ()))
    known = {step.step_id for step in steps}
    if target not in known:
        return set()
    downstream: set[str] = set()
    frontier = {target}
    while frontier:
        current = frontier.pop()
        for step in steps:
            if step.step_id in downstream or step.step_id == target:
                continue
            dependencies = set(getattr(step, "dependency_step_ids", ()) or ())
            if current in dependencies:
                downstream.add(step.step_id)
                frontier.add(step.step_id)
    return downstream


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

    def retry(self, command: Command) -> Mapping[str, object]:
        """重试测试：对**已有尝试**的步骤再执行一次，产生新尝试并立即成为当前尝试。

        与 `execute_step` 的差别只有前置条件：重试必须已解析出**新的一轮**执行动作
        （`attempt_index >= 2`），也就是该步骤此前确有尝试。新尝试与"把依赖旧尝试的
        下游依据标为需重跑"由执行提交路径在**同一业务提交**内完成
        （`commit.py` 的 `invalidate_downstream_attempts`），本动作不另造失效机制、
        也不绕过准备与授权：动作身份、修订与目标仍按 `execute_step` 的严格形状核对。
        """

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
            raise InvalidExecutionCommand("retry requires only an exact saved action reference")
        if self.execution is None:
            from aitest.application.errors import CapabilityUnavailable

            raise CapabilityUnavailable("saved step execution is not configured")
        assert command.target is not None
        resolver = self.execution.authorizations.resolver
        _, action = resolver.read(project, identity)
        previous_index = action.attempt.attempt_index - 1
        if action.request.step_id != command.target or previous_index < 1:
            raise InvalidExecutionCommand(
                "retry requires a step whose saved action is a later attempt "
                "(use execute_step for the first attempt)"
            )
        result = dict(
            self.execution.execute(
                project_id=project,
                intent_id=intent,
                action_id=identity,
                step_id=command.target,
            )
        )
        # 旧尝试保留为历史：只回执"从第几次尝试重试而来"，不改写旧事实。
        result["retried_from_attempt_index"] = previous_index
        result["previous_attempt_preserved"] = True
        return result

    def return_stage(self, command: Command) -> Mapping[str, object]:
        """退回阶段：对目标步骤发起新一轮尝试，并给出**下游需重跑清单**。

        分册已冻结的语义（不是本入口自定）：

        - 功能 §4/§221「退回阶段：形成新修订，将依赖下游标为需重跑，旧记录可查」；
        - 功能 §15「**退回阶段与普通上游重试使用同一失效机制**」；
        - 架构 02 §157「退回阶段生成修订与下游需重跑清单，**不把沿用证据记为重新执行**」；
        - 设计 §372「退回阶段先展示下游需重跑，不让用户误以为已有继承证据本轮已执行」。

        因此本入口与 `retry_step` 走**同一条**执行与失效路径，差别在于它必须
        如实回执影响面：执行前算出依赖该步骤的传递下游（来自已提交事实的依赖边），
        执行后按同一提交里实际登记的失效事实给出"已被标为需重跑"的集合；
        旧尝试、旧证据保留为历史，**绝不记为已重跑**。
        """

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
            raise InvalidExecutionCommand(
                "return_stage requires only an exact saved action reference"
            )
        if self.execution is None:
            from aitest.application.errors import CapabilityUnavailable

            raise CapabilityUnavailable("saved step execution is not configured")
        assert command.target is not None
        target = command.target
        resolver = self.execution.authorizations.resolver
        _, action = resolver.read(project, identity)
        previous_index = action.attempt.attempt_index - 1
        if action.request.step_id != target or previous_index < 1:
            raise InvalidExecutionCommand(
                "return_stage requires a step whose saved action is a later attempt "
                "(use execute_step for the first attempt)"
            )
        run_id = action.attempt.run_id
        before = self._facts(project, run_id)
        downstream = _downstream_steps(before, target)
        result = dict(
            self.execution.execute(
                project_id=project,
                intent_id=intent,
                action_id=identity,
                step_id=target,
            )
        )
        after = self._facts(project, run_id)
        marked = sorted(
            step_id
            for step_id in downstream
            if any(
                invalidation.affected_step_id == step_id
                for invalidation in getattr(after, "dependency_invalidations", ())
            )
        )
        # 旧尝试保留为历史：只回执影响面，不改写旧事实、不把沿用证据当重跑。
        result["returned_step_id"] = target
        result["retried_from_attempt_index"] = previous_index
        result["downstream_step_ids"] = sorted(downstream)
        result["downstream_requires_rerun"] = marked
        result["previous_attempt_preserved"] = True
        result["inherited_evidence_not_reexecuted"] = True
        if not downstream:
            result["downstream_note"] = "该步骤没有已登记的依赖下游：本次只产生新尝试。"
        elif not marked:
            result["downstream_note"] = (
                "依赖下游已登记但未出现在本次失效事实中：按事实如实显示，不代为标记。"
            )
        return result

    def _facts(self, project: str, run_id: str) -> object:
        """读已提交事实：退回阶段的影响面只能来自事实，不能来自调用方自报。"""

        coordinator = getattr(self.execution, "coordinator", None)
        reader = getattr(coordinator, "read_runtime_revision_facts", None)
        if not callable(reader):
            raise InvalidExecutionCommand(
                "return_stage requires the committed execution facts reader"
            )
        try:
            facts = reader(project_id=project, run_id=run_id)
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise InvalidExecutionCommand(
                f"return_stage cannot read the committed facts: {error}"
            ) from error
        if facts is None:
            raise InvalidExecutionCommand("return_stage requires a saved run snapshot")
        return facts

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
