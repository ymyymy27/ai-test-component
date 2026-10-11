"""Advance one registered planned Run using only saved per-action consent."""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

from aitest.application.execution.authorization_index import read_authorization_index
from aitest.application.execution.commands import ExecutionCommands, InvalidExecutionCommand
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.continuation import (
    read_continuation,
    stage_continuation,
    stage_existing_activity,
)
from aitest.application.execution.control import boundary_pending
from aitest.application.execution.facts import execution_payload_digest
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.application.execution.step_execution import SavedStepExecution
from aitest.application.ports import BackgroundWorkDirectory
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts, RunControlStateFact, StepStateFact
from aitest.contracts.prepared_run import RunDriverFact
from aitest.domain.approvals import ApprovalConflict
from aitest.domain.execution.authorization import AuthorizationState, ResolvedExecutionAction
from aitest.domain.execution.continuation import RunContinuation
from aitest.domain.execution.runs import (
    Attempt,
    DependencyEdge,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
    has_complete_capture,
    has_reliable_terminal_fact,
)
from aitest.domain.execution.scheduling import DispatchPlan, plan_serial_dispatch

_SLICE_BUDGET = 32
_AUTHORIZATION_LIMIT = 100
_OBSERVABLE = frozenset(
    {
        RunControlStateFact.NOT_STARTED,
        RunControlStateFact.RUNNING,
        RunControlStateFact.PENDING_VERIFICATION,
    }
)
_DISPATCHABLE = frozenset({RunControlStateFact.NOT_STARTED, RunControlStateFact.RUNNING})


class RunScheduleBlocked(ValueError):
    code = "RUN_SCHEDULE_BLOCKED"


def dispatch_plan(facts: ExecutionFacts) -> DispatchPlan:
    return plan_serial_dispatch(
        tuple(
            Step(
                step.step_id,
                step.run_id,
                step.ordinal,
                step.case_id,
                StepLevel(step.level.value),
                StepRevisionRef(**step.step_revision_ref.model_dump()),
                state=StepState(step.state.value),
                dependency_edges=tuple(
                    DependencyEdge(upstream, step.step_id) for upstream in step.dependency_step_ids
                ),
            )
            for step in sorted(facts.steps, key=lambda item: (item.ordinal, item.step_id))
        )
    )


class SavedRunSchedule:
    def __init__(
        self,
        coordinator: ExecutionCommitCoordinator,
        execution: SavedStepExecution,
        workspace_id: str,
        directory: BackgroundWorkDirectory | None = None,
    ) -> None:
        self.coordinator, self.execution, self.workspace_id = coordinator, execution, workspace_id
        self.unit = coordinator._uow
        self.directory = directory
        self._last_work_id = ""

    def _identity(self, project: str, intent: str) -> str:
        return (
            "run-schedule-"
            + execution_payload_digest(
                {"workspace_id": self.workspace_id, "project_id": project, "intent_id": intent}
            )[7:]
        )

    def _current(self, project: str, run: str) -> ExecutionFacts:
        facts = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
        if facts.run.origin_workspace_id != self.workspace_id:
            raise RunScheduleBlocked("registered run belongs to a different workspace")
        return facts

    def _recall(self, identity: str, project: str, run: str, intent: str, base: str) -> bool:
        raw = self.coordinator._read_payload("execution_intent", identity)
        if raw is None:
            return False
        if (
            self.coordinator._revision("execution_intent", identity) != 1
            or set(raw)
            != {
                "schema_version",
                "workspace_id",
                "project_id",
                "run_id",
                "intent_id",
                "base_snapshot_commit_id",
                "snapshot_digest",
            }
            or raw.get("schema_version") != "aitest.run-schedule-intent/1.0"
            or raw.get("workspace_id") != self.workspace_id
            or raw.get("project_id") != project
        ):
            raise RunScheduleBlocked("saved scheduling admission cannot be verified")
        if (raw["run_id"], raw["intent_id"], raw["base_snapshot_commit_id"]) != (run, intent, base):
            raise ApprovalConflict("scheduling intent has different input")
        snapshot = self.coordinator._read_payload("execution_facts", base)
        if (
            snapshot is None
            or self.coordinator._revision("execution_facts", base) != 1
            or execution_payload_digest(snapshot) != raw["snapshot_digest"]
        ):
            raise RunScheduleBlocked("original scheduling snapshot cannot be verified")
        original = ExecutionFacts.model_validate(snapshot)
        if (
            (original.project_id, original.run_id, original.snapshot_commit_id)
            != (project, run, base)
            or original.run.origin_workspace_id != self.workspace_id
            or original.snapshot_revision != 1
            or original.run.driver is not RunDriverFact.PLANNED
            or original.run.control_state not in _DISPATCHABLE
        ):
            raise RunScheduleBlocked("original scheduling scope or state differs")
        return True

    def _admit(self, command: Command, project: str, run: str, intent: str, base: str) -> str:
        identity = self._identity(project, intent)
        if self._recall(identity, project, run, intent, base):
            self._register_existing(project, run, intent, base, identity)
            return identity
        self.unit.begin(command.request_id, project)
        try:
            if self._recall(identity, project, run, intent, base):
                if (
                    self.directory is not None
                    and read_continuation(self.coordinator, self.workspace_id, project, run) is None
                ):
                    self._stage_work(project, run, intent, base, identity)
                    self.unit.commit()
                else:
                    self.unit.rollback()
                return identity
            facts = self._current(project, run)
            if facts.snapshot_commit_id != base:
                raise ApprovalConflict("scheduling uses a stale current snapshot")
            if facts.run.driver is not RunDriverFact.PLANNED:
                raise RunScheduleBlocked("automatic scheduling requires the planned driver")
            if facts.run.control_state not in _DISPATCHABLE:
                raise RunScheduleBlocked("a controlled or terminal run cannot start scheduling")
            self.unit.stage_record(
                aggregate_kind="execution_intent",
                record_id=identity,
                expected_revision=0,
                payload={
                    "schema_version": "aitest.run-schedule-intent/1.0",
                    "workspace_id": self.workspace_id,
                    "project_id": project,
                    "run_id": run,
                    "intent_id": intent,
                    "base_snapshot_commit_id": base,
                    "snapshot_digest": execution_payload_digest(facts.model_dump(mode="json")),
                },
            )
            self._stage_work(project, run, intent, base, identity)
            self.unit.commit()
            return identity
        except BaseException:
            self.unit.rollback()
            raise

    def _stage_work(self, project: str, run: str, intent: str, base: str, identity: str) -> None:
        if self.directory is None:
            return
        if read_continuation(self.coordinator, self.workspace_id, project, run) is not None:
            # Later schedule admissions never replace the Run's original basis.
            return
        stage_continuation(
            self.coordinator,
            RunContinuation(
                self.workspace_id,
                project,
                run,
                identity,
                intent,
                base,
                True,
            ),
        )

    def _register_existing(
        self, project: str, run: str, intent: str, base: str, identity: str
    ) -> None:
        if self.directory is None:
            return
        current = read_continuation(self.coordinator, self.workspace_id, project, run)
        if current is not None:
            # A completed/paused work record must not be rearmed by an old start.
            return
        self.unit.begin("schedule-registration-" + uuid4().hex, project)
        try:
            if read_continuation(self.coordinator, self.workspace_id, project, run) is None:
                self._recall(identity, project, run, intent, base)
                self._stage_work(project, run, intent, base, identity)
                self.unit.commit()
            else:
                self.unit.rollback()
        except BaseException:
            self.unit.rollback()
            raise

    def _attempts(self, facts: ExecutionFacts) -> tuple[Attempt, ...]:
        return tuple(
            self.coordinator.read_checkpoint(
                project_id=facts.project_id, attempt_id=fact.attempt_id
            ).attempt
            for fact in facts.attempts
        )

    def _original_action(
        self, project: str, attempt: Attempt
    ) -> tuple[str, ResolvedExecutionAction]:
        if attempt.authorization_ref is None:
            raise RunScheduleBlocked("original activity has no saved authorization")
        grant, action = self.execution.authorizations._origin(
            project, attempt.authorization_ref.authorization_id
        )
        saved = self.coordinator.find_start(
            project_id=project,
            intent_id=action.request.intent_id,
            fingerprint=execution_start_fingerprint(action.attempt, action.request),
        )
        if saved != attempt:
            raise RunScheduleBlocked("original activity differs from its saved execution intent")
        parameters = grant["parameters"]
        assert isinstance(parameters, Mapping)
        identity = parameters["execution_action_id"]
        assert isinstance(identity, str)
        return identity, action

    def _unused_actions(
        self, facts: ExecutionFacts
    ) -> dict[str, list[tuple[str, ResolvedExecutionAction]]]:
        service = self.execution.authorizations
        _, identities = read_authorization_index(
            service.records, self.workspace_id, facts.project_id, facts.run_id
        )
        if len(identities) > _AUTHORIZATION_LIMIT:
            raise RunScheduleBlocked(
                "unused authorization index exceeds the bounded schedule limit"
            )
        result: dict[str, list[tuple[str, ResolvedExecutionAction]]] = {}
        for identity in identities:
            grant, action = service._origin(facts.project_id, identity)
            _, state, occupied = service._state(facts.project_id, identity)
            if (
                action.request.run_id != facts.run_id
                or action.request.step_id not in {step.step_id for step in facts.steps}
                or state is not AuthorizationState.UNUSED
                or occupied is not None
            ):
                raise RunScheduleBlocked(
                    "unused authorization index differs from its original grants"
                )
            parameters = grant["parameters"]
            assert isinstance(parameters, Mapping)
            action_id = parameters["execution_action_id"]
            assert isinstance(action_id, str)
            result.setdefault(action.request.step_id, []).append((action_id, action))
        return result

    def _finish(self, previous: ExecutionFacts) -> ExecutionFacts:
        self.unit.begin("schedule-completion-" + uuid4().hex, previous.project_id)
        try:
            facts = self._current(previous.project_id, previous.run_id)
            if facts != previous:
                self.unit.rollback()
                return facts
            revision = facts.run_revision + 1
            completed = facts.model_copy(
                update={
                    "facts_id": "schedule-completion-" + uuid4().hex,
                    "committed_at": datetime.now(UTC),
                    "run_revision": revision,
                    "run": facts.run.model_copy(
                        update={
                            "control_state": RunControlStateFact.COMPLETED,
                            "run_revision": revision,
                            "ended_at": datetime.now(UTC),
                        }
                    ),
                }
            )
            _, saved = self.coordinator._stage_snapshot(completed)
            if self.directory is not None:
                stage_existing_activity(
                    self.coordinator, self.workspace_id, previous.project_id, previous.run_id, False
                )
            self.unit.commit()
            return saved
        except BaseException:
            self.unit.rollback()
            raise

    @staticmethod
    def _completion_basis(
        facts: ExecutionFacts, attempts: tuple[Attempt, ...]
    ) -> tuple[Attempt, ...] | None:
        if (
            not facts.steps
            or any(step.state is not StepStateFact.COMPLETED for step in facts.steps)
            or any(boundary_pending(item) for item in attempts)
        ):
            return None
        by_id = {item.attempt_id: item for item in attempts}
        result = []
        for step in facts.steps:
            basis = by_id.get(step.current_attempt_id or "")
            if (
                basis is None
                or not has_reliable_terminal_fact(basis)
                or not has_complete_capture(basis)
            ):
                return None
            result.append(basis)
        return tuple(result)

    def _advance(
        self, project: str, run: str, identity: str, *, budget: int | None = None
    ) -> Mapping[str, object]:
        observed: list[str] = []
        dispatched: list[str] = []
        status = "slice_exhausted"
        for _ in range(_SLICE_BUDGET if budget is None else budget):
            facts = self._current(project, run)
            attempts = self._attempts(facts)
            if facts.run.control_state is RunControlStateFact.COMPLETED:
                basis = self._completion_basis(facts, attempts)
                if basis is None:
                    status = "unverified_completion"
                else:
                    for item in basis:
                        self._original_action(project, item)
                    status = "execution_completed"
                break
            if (
                facts.run.driver is not RunDriverFact.PLANNED
                or facts.run.control_state not in _OBSERVABLE
            ):
                status = "controlled"
                break
            pending = tuple(item for item in attempts if boundary_pending(item))
            unobserved = next(
                (
                    item
                    for item in pending
                    if item.attempt_id not in observed and item.execution_handle_ref is not None
                ),
                None,
            )
            if unobserved is not None:
                action_id, action = self._original_action(project, unobserved)
                self.execution.execute(
                    project_id=project,
                    intent_id=action.request.intent_id,
                    action_id=action_id,
                    step_id=action.request.step_id,
                    max_polls=1,
                )
                observed.append(unobserved.attempt_id)
                continue
            if pending:
                status = (
                    "active"
                    if all(item.state.value == "running" for item in pending)
                    else "pending_verification"
                )
                break
            if facts.run.control_state not in _DISPATCHABLE:
                status = "pending_verification"
                break
            plan = dispatch_plan(facts)
            if facts.steps and all(step.state is StepStateFact.COMPLETED for step in facts.steps):
                basis = self._completion_basis(facts, attempts)
                if basis is not None:
                    for item in basis:
                        self._original_action(project, item)
                    facts = self._finish(facts)
                    status = (
                        "execution_completed"
                        if facts.run.control_state is RunControlStateFact.COMPLETED
                        else "controlled"
                    )
                else:
                    status = "unverified_completion"
                break
            actions = self._unused_actions(facts)
            chosen = next((step for step in plan.ready_step_ids if actions.get(step)), None)
            if chosen is None:
                status = (
                    "waiting_authorization"
                    if plan.ready_step_ids
                    else "blocked_dependencies"
                    if plan.blocked_step_ids or plan.terminal_step_ids
                    else "waiting_dependencies"
                )
                break
            if len(actions[chosen]) != 1:
                status = "ambiguous_authorization"
                break
            action_id, action = actions[chosen][0]
            self.execution.execute(
                project_id=project,
                intent_id=action.request.intent_id,
                action_id=action_id,
                step_id=chosen,
                max_polls=1,
            )
            dispatched.append(action.attempt.attempt_id)
            observed.append(action.attempt.attempt_id)
        facts = self._current(project, run)
        plan = dispatch_plan(facts)
        return {
            "schedule_intent_id": identity,
            "status": status,
            "observed_attempt_ids": observed,
            "dispatched_attempt_ids": dispatched,
            "ready_step_ids": list(plan.ready_step_ids),
            "blocked_step_ids": list(plan.blocked_step_ids),
            "waiting_step_ids": list(plan.waiting_step_ids),
            "execution_facts": facts.model_dump(mode="json"),
        }

    def tick(self, control: object) -> Mapping[str, object] | None:
        """One saved run per turn; no new admission, consent or history discovery."""
        if self.directory is None:
            return None
        entries = self.directory.active_execution_schedules(workspace_id=self.workspace_id)
        if not entries:
            return None
        if len(entries) > 64 or any(
            set(item) != {"record_id", "workspace_id", "project_id", "run_id", "revision"}
            or any(
                type(item[key]) is not str or not item[key]
                for key in ("record_id", "workspace_id", "project_id", "run_id")
            )
            or item["workspace_id"] != self.workspace_id
            or type(item["revision"]) is not int
            or item["revision"] < 1
            for item in entries
        ):
            raise RunScheduleBlocked("active inventory shape, scope or budget differs")
        entry = next(
            (item for item in entries if str(item["record_id"]) > self._last_work_id), entries[0]
        )
        identity = str(entry["record_id"])
        # Advance the fairness marker before doing any external operation: a bad
        # saved entry cannot monopolize every subsequent tick.
        self._last_work_id = identity
        project, run = str(entry["project_id"]), str(entry["run_id"])
        work = read_continuation(self.coordinator, self.workspace_id, project, run)
        if (
            work is None
            or not work.active
            or work.record_id != identity
            or self.coordinator._revision("execution_schedule", identity) != entry["revision"]
            or not self._recall(
                work.schedule_intent_id, project, run, work.intent_id, work.base_snapshot_commit_id
            )
        ):
            raise RunScheduleBlocked("active inventory differs from its original admission")
        facts = self._current(project, run)
        if facts.run.control_state in {
            RunControlStateFact.PAUSE_REQUESTED,
            RunControlStateFact.CANCELLING,
        }:
            from aitest.application.execution.saved_control import SavedRunControl

            if not isinstance(control, SavedRunControl):
                raise RunScheduleBlocked("saved control continuation is unavailable")
            control.advance_current(project, run, max_inspections=1)
            result: Mapping[str, object] = {"status": "controlled"}
        elif facts.run.driver is RunDriverFact.STEPWISE:
            active = next(
                (
                    item
                    for item in self._attempts(facts)
                    if boundary_pending(item) and item.execution_handle_ref is not None
                ),
                None,
            )
            if active is not None:
                action_id, action = self._original_action(project, active)
                self.execution.execute(
                    project_id=project,
                    intent_id=action.request.intent_id,
                    action_id=action_id,
                    step_id=action.request.step_id,
                    max_polls=1,
                )
            result = {"status": "controlled"}
        else:
            result = self._advance(project, run, work.schedule_intent_id, budget=1)
        self._retire_quiescent(work)
        return result

    def _retire_quiescent(self, work: RunContinuation) -> None:
        from dataclasses import replace

        facts = self._current(work.project_id, work.run_id)
        if (
            facts.run.control_state
            not in {
                RunControlStateFact.PAUSED,
                RunControlStateFact.CANCELLED,
                RunControlStateFact.COMPLETED,
            }
            and facts.run.driver is not RunDriverFact.STEPWISE
        ):
            return
        self.unit.begin("schedule-retirement-" + uuid4().hex, work.project_id)
        try:
            latest = read_continuation(
                self.coordinator, self.workspace_id, work.project_id, work.run_id
            )
            facts = self._current(work.project_id, work.run_id)
            attempts = self._attempts(facts)
            inactive = (
                facts.run.control_state
                in {
                    RunControlStateFact.PAUSED,
                    RunControlStateFact.CANCELLED,
                }
                or facts.run.driver is RunDriverFact.STEPWISE
            )
            if facts.run.control_state is RunControlStateFact.COMPLETED:
                basis = self._completion_basis(facts, attempts)
                if basis is not None:
                    for item in basis:
                        self._original_action(work.project_id, item)
                    inactive = True
            if latest != work or not inactive or any(boundary_pending(item) for item in attempts):
                self.unit.rollback()
                return
            stage_continuation(self.coordinator, replace(work, active=False))
            self.unit.commit()
        except BaseException:
            self.unit.rollback()
            raise

    def apply(self, command: Command) -> Mapping[str, object]:
        project, intent = ExecutionCommands._identity(command)
        values = command.parameters
        run, base = values.get("run_id"), values.get("base_snapshot_commit_id")
        if (
            command.action != "start_run"
            or set(values) != {"run_id", "base_snapshot_commit_id"}
            or not isinstance(run, str)
            or not run.strip()
            or not isinstance(base, str)
            or not base.strip()
            or command.target != run
        ):
            raise InvalidExecutionCommand(
                "scheduling requires only an exact run and current snapshot"
            )
        try:
            identity = self._admit(command, project, run, intent, base)
            return self._advance(project, run, identity)
        except ValueError as error:
            if getattr(error, "code", None):
                raise
            raise RunScheduleBlocked(str(error)) from error
