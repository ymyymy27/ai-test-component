"""Durable run control: save admission before observing or stopping original executions."""

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.continuation import stage_existing_activity
from aitest.application.execution.control import boundary_pending
from aitest.application.execution.facts import execution_payload_digest
from aitest.application.execution.runner import SerialRunner
from aitest.application.execution.step_execution import SavedStepExecution
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts, RunControlStateFact
from aitest.domain.approvals import ApprovalConflict
from aitest.domain.execution.runs import Attempt, ExecutionInspectionState


class RunControlBlocked(ValueError):
    code = "RUN_CONTROL_BLOCKED"


class SavedRunControl:
    def __init__(
        self,
        coordinator: ExecutionCommitCoordinator,
        execution: SavedStepExecution,
        workspace_id: str,
    ) -> None:
        self.coordinator, self.execution, self.workspace_id = coordinator, execution, workspace_id
        self.unit = coordinator._uow

    def _identity(self, project: str, intent: str) -> str:
        return (
            "run-control-"
            + execution_payload_digest(
                {"workspace_id": self.workspace_id, "project_id": project, "intent_id": intent}
            )[7:]
        )

    def _pointer(self, project: str, run: str) -> str:
        return (
            "run-control-current-"
            + execution_payload_digest(
                {"workspace_id": self.workspace_id, "project_id": project, "run_id": run}
            )[7:]
        )

    def _read(self, kind: str, identity: str, project: str) -> Mapping[str, object] | None:
        raw = self.coordinator._read_payload(kind, identity)
        if raw is not None and raw.get("project_id") != project:
            raise RunControlBlocked("control material has a foreign or unknown owner")
        return raw

    def _facts(self, raw: Mapping[str, object], project: str, run: str) -> ExecutionFacts:
        identity = raw.get("snapshot_commit_id")
        if (
            not isinstance(identity, str)
            or self.coordinator._revision("execution_facts", identity) != 1
        ):
            raise RunControlBlocked("original control snapshot is not immutable")
        payload = self._read("execution_facts", identity, project)
        if payload is None or execution_payload_digest(payload) != raw.get("snapshot_digest"):
            raise RunControlBlocked("original control snapshot cannot be verified")
        facts = ExecutionFacts.model_validate(payload)
        if (
            (facts.project_id, facts.run_id, facts.snapshot_commit_id) != (project, run, identity)
            or facts.run.origin_workspace_id != self.workspace_id
            or facts.snapshot_revision != 1
        ):
            raise RunControlBlocked("original control snapshot has a different identity")
        return facts

    def _recall(
        self, identity: str, project: str, run: str, intent: str, action: str, base: str
    ) -> Mapping[str, object] | None:
        raw = self._read("run_control_intent", identity, project)
        if raw is None:
            return None
        attempt_ids = raw.get("attempt_ids")
        if (
            self.coordinator._revision("run_control_intent", identity) != 1
            or set(raw)
            != {
                "schema_version",
                "workspace_id",
                "project_id",
                "run_id",
                "intent_id",
                "action",
                "base_snapshot_commit_id",
                "snapshot_commit_id",
                "snapshot_digest",
                "complete",
                "attempt_ids",
            }
            or raw.get("schema_version") != "aitest.run-control-intent/1.0"
            or raw.get("workspace_id") != self.workspace_id
            or type(raw.get("complete")) is not bool
            or not isinstance(attempt_ids, list)
            or any(type(item) is not str or not item for item in attempt_ids)
            or len(attempt_ids) != len(set(attempt_ids))
        ):
            raise RunControlBlocked("saved control intent has invalid identity or fields")
        if (raw["run_id"], raw["intent_id"], raw["action"], raw["base_snapshot_commit_id"]) != (
            run,
            intent,
            action,
            base,
        ):
            raise ApprovalConflict("control intent has different input")
        original = self._facts(raw, project, run)
        if set(attempt_ids) != {item.attempt_id for item in original.attempts}:
            raise RunControlBlocked("saved control scope differs from its original facts")
        admitted = original.run.control_state
        allowed = {
            "pause_run": {RunControlStateFact.PAUSE_REQUESTED, RunControlStateFact.PAUSED},
            "resume_run": {RunControlStateFact.RUNNING},
            "cancel_run": {RunControlStateFact.CANCELLING, RunControlStateFact.CANCELLED},
        }
        complete = admitted not in {
            RunControlStateFact.PAUSE_REQUESTED,
            RunControlStateFact.CANCELLING,
        }
        if admitted not in allowed[action] or raw["complete"] is not complete:
            raise RunControlBlocked("saved control admission differs from its original state")
        return raw

    def _attempts(self, facts: ExecutionFacts) -> tuple[Attempt, ...]:
        return tuple(
            self.coordinator.read_checkpoint(
                project_id=facts.project_id, attempt_id=item.attempt_id
            ).attempt
            for item in facts.attempts
        )

    @staticmethod
    def _transition(previous: ExecutionFacts, state: RunControlStateFact) -> ExecutionFacts:
        revision = previous.run_revision + 1
        return previous.model_copy(
            update={
                "facts_id": "run-control-update-" + uuid4().hex,
                "run_revision": revision,
                "committed_at": datetime.now(UTC),
                "run": previous.run.model_copy(
                    update={"control_state": state, "run_revision": revision}
                ),
            }
        )

    def apply(self, command: Command) -> Mapping[str, object]:
        from aitest.application.execution.commands import ExecutionCommands, InvalidExecutionCommand

        project, intent = ExecutionCommands._identity(command)
        values = command.parameters
        base = values.get("base_snapshot_commit_id")
        if (
            command.action not in {"pause_run", "resume_run", "cancel_run"}
            or set(values) != {"run_id", "base_snapshot_commit_id"}
            or not isinstance(values["run_id"], str)
            or not values["run_id"].strip()
            or command.target != values["run_id"]
            or not isinstance(base, str)
            or not base.strip()
        ):
            raise InvalidExecutionCommand("run control requires an exact run and current snapshot")
        run = values["run_id"]
        identity = self._identity(project, intent)
        try:
            raw = self._recall(identity, project, run, intent, command.action, base)
            if raw is None:
                raw = self._admit(command, project, run, intent, base, identity)
            result = self._advance(raw, identity, project, run)
            return result.model_dump(mode="json")
        except ValueError as error:
            if getattr(error, "code", None):
                raise
            raise RunControlBlocked(str(error)) from error

    def _admit(
        self, command: Command, project: str, run: str, intent: str, base: str, identity: str
    ) -> Mapping[str, object]:
        self.unit.begin(command.request_id, project)
        try:
            recalled = self._recall(identity, project, run, intent, command.action, base)
            if recalled is not None:
                self.unit.rollback()
                return recalled
            current = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
            if current.run.origin_workspace_id != self.workspace_id:
                raise RunControlBlocked("registered run belongs to a different workspace")
            if current.snapshot_commit_id != base:
                raise ApprovalConflict("run control uses a stale current snapshot")
            state = current.run.control_state
            pending = any(boundary_pending(item) for item in self._attempts(current))
            if command.action == "pause_run":
                if state is not RunControlStateFact.RUNNING:
                    raise RunControlBlocked("only a running run can be paused")
                next_state = (
                    RunControlStateFact.PAUSE_REQUESTED if pending else RunControlStateFact.PAUSED
                )
            elif command.action == "resume_run":
                if state not in {RunControlStateFact.PAUSED, RunControlStateFact.PAUSE_REQUESTED}:
                    raise RunControlBlocked("only a paused run can be resumed")
                next_state = RunControlStateFact.RUNNING
            else:
                if state not in {
                    RunControlStateFact.NOT_STARTED,
                    RunControlStateFact.RUNNING,
                    RunControlStateFact.PAUSED,
                    RunControlStateFact.PAUSE_REQUESTED,
                    RunControlStateFact.PENDING_VERIFICATION,
                }:
                    raise RunControlBlocked("run cancellation is already terminal or controlled")
                next_state = (
                    RunControlStateFact.CANCELLING if pending else RunControlStateFact.CANCELLED
                )
                self.coordinator._stage_authorization_revocations(
                    before=current,
                    superseded_attempt_ids=(),
                    changed_step_ids=tuple(step.step_id for step in current.steps),
                )
            pointer_id = self._pointer(project, run)
            self.unit.stage_record(
                aggregate_kind="run_control_current",
                record_id=pointer_id,
                expected_revision=self.coordinator._revision("run_control_current", pointer_id),
                payload={"project_id": project, "run_id": run, "control_intent_id": identity},
            )
            _, facts = self.coordinator._stage_snapshot(self._transition(current, next_state))
            if next_state in {
                RunControlStateFact.RUNNING,
                RunControlStateFact.PAUSED,
                RunControlStateFact.CANCELLED,
            }:
                stage_existing_activity(
                    self.coordinator,
                    self.workspace_id,
                    project,
                    run,
                    next_state is RunControlStateFact.RUNNING,
                )
            raw: dict[str, object] = {
                "schema_version": "aitest.run-control-intent/1.0",
                "workspace_id": self.workspace_id,
                "project_id": project,
                "run_id": run,
                "intent_id": intent,
                "action": command.action,
                "base_snapshot_commit_id": base,
                "snapshot_commit_id": facts.snapshot_commit_id,
                "snapshot_digest": execution_payload_digest(facts.model_dump(mode="json")),
                "complete": next_state
                not in {RunControlStateFact.PAUSE_REQUESTED, RunControlStateFact.CANCELLING},
                "attempt_ids": [item.attempt_id for item in current.attempts],
            }
            self.unit.stage_record(
                aggregate_kind="run_control_intent",
                record_id=identity,
                expected_revision=0,
                payload=raw,
            )
            self.unit.commit()
            return raw
        except BaseException:
            self.unit.rollback()
            raise

    def _owns(self, identity: str, project: str, run: str) -> bool:
        pointer = self._read("run_control_current", self._pointer(project, run), project)
        if (
            pointer is None
            or set(pointer) != {"project_id", "run_id", "control_intent_id"}
            or pointer["run_id"] != run
        ):
            raise RunControlBlocked("current control reference cannot be verified")
        return pointer["control_intent_id"] == identity

    def advance_current(
        self, project: str, run: str, *, max_inspections: int = 1
    ) -> ExecutionFacts:
        if type(max_inspections) is not int or not 1 <= max_inspections <= 100:
            raise RunControlBlocked("control observation budget must be between one and 100")
        pointer = self._read("run_control_current", self._pointer(project, run), project)
        if pointer is None or type(pointer.get("control_intent_id")) is not str:
            raise RunControlBlocked("pending run has no exact saved control")
        identity = str(pointer["control_intent_id"])
        raw = self._read("run_control_intent", identity, project)
        if raw is None or not self._owns(identity, project, run):
            raise RunControlBlocked("original pending control is unavailable")
        if raw.get("action") not in {"pause_run", "cancel_run"} or any(
            type(raw.get(key)) is not str or not raw[key]
            for key in ("intent_id", "action", "base_snapshot_commit_id")
        ):
            raise RunControlBlocked("pending control input cannot be verified")
        verified = self._recall(
            identity,
            project,
            run,
            str(raw.get("intent_id")),
            str(raw.get("action")),
            str(raw.get("base_snapshot_commit_id")),
        )
        if verified is None:
            raise RunControlBlocked("original pending control cannot be verified")
        return self._advance(verified, identity, project, run, max_inspections=max_inspections)

    def _advance(
        self,
        raw: Mapping[str, object],
        identity: str,
        project: str,
        run: str,
        *,
        max_inspections: int = 100,
    ) -> ExecutionFacts:
        result = self._read("run_control_result", identity, project)
        if result is not None:
            if (
                self.coordinator._revision("run_control_result", identity) != 1
                or set(result)
                != {
                    "project_id",
                    "run_id",
                    "control_intent_id",
                    "snapshot_commit_id",
                    "snapshot_digest",
                }
                or result["run_id"] != run
                or result["control_intent_id"] != identity
            ):
                raise RunControlBlocked("original control result cannot be verified")
            facts = self._facts(result, project, run)
            expected = (
                RunControlStateFact.PAUSED
                if raw["action"] == "pause_run"
                else RunControlStateFact.CANCELLED
            )
            if facts.run.control_state is not expected or {
                item.attempt_id for item in facts.attempts
            } != {item.attempt_id for item in self._facts(raw, project, run).attempts}:
                raise RunControlBlocked(
                    "original control completion has a different scope or state"
                )
            return facts
        if raw["complete"] or not self._owns(identity, project, run):
            return self._facts(raw, project, run)
        current = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
        original = self._facts(raw, project, run)
        pending_state = (
            RunControlStateFact.PAUSE_REQUESTED
            if raw["action"] == "pause_run"
            else RunControlStateFact.CANCELLING
        )
        if current.run.control_state is not pending_state or {
            item.attempt_id for item in current.attempts
        } != {item.attempt_id for item in original.attempts}:
            raise RunControlBlocked("pending control differs from its admitted run scope or state")
        port = self.execution.execution_port
        if port is not None:
            runner = SerialRunner(port, self.execution.spool, commit_coordinator=self.coordinator)
            remaining = max_inspections
            for attempt in self._attempts(current):
                if not boundary_pending(attempt) or attempt.execution_handle_ref is None:
                    continue
                if remaining == 0:
                    break
                remaining -= 1
                # Original saved authority is proved before the adapter sees a handle.
                saved = self.coordinator.find_start(
                    project_id=project,
                    intent_id=attempt.intent_id,
                    fingerprint=attempt.intent_digest,
                )
                if saved != attempt:
                    raise RunControlBlocked(
                        "control target differs from its original saved execution"
                    )
                inspection = runner.inspect_attempt(attempt)
                stop_requested = (
                    raw["action"] == "cancel_run"
                    and inspection.state is ExecutionInspectionState.RUNNING
                )
                if stop_requested:
                    runner.request_stop(attempt)
                runner.observe_saved_attempt(
                    attempt,
                    project_id=project,
                    inspection=None if stop_requested else inspection,
                )
        self.unit.begin("settle-control-" + uuid4().hex, project)
        try:
            if not self._owns(identity, project, run):
                self.unit.rollback()
                return self._facts(raw, project, run)
            current = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=run)
            if current.run.control_state is not pending_state or {
                item.attempt_id for item in current.attempts
            } != {item.attempt_id for item in original.attempts}:
                raise RunControlBlocked("pending control changed during external observation")
            if any(boundary_pending(item) for item in self._attempts(current)):
                self.unit.rollback()
                return current
            state = (
                RunControlStateFact.PAUSED
                if raw["action"] == "pause_run"
                else RunControlStateFact.CANCELLED
            )
            _, facts = self.coordinator._stage_snapshot(self._transition(current, state))
            stage_existing_activity(self.coordinator, self.workspace_id, project, run, False)
            self.unit.stage_record(
                aggregate_kind="run_control_result",
                record_id=identity,
                expected_revision=0,
                payload={
                    "project_id": project,
                    "run_id": run,
                    "control_intent_id": identity,
                    "snapshot_commit_id": facts.snapshot_commit_id,
                    "snapshot_digest": execution_payload_digest(facts.model_dump(mode="json")),
                },
            )
            self.unit.commit()
            return facts
        except BaseException:
            self.unit.rollback()
            raise
