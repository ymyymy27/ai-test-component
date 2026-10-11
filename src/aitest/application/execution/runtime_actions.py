"""Default controlled revision commands accept saved identities, never replacement facts."""

import json
from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter

from aitest.application.approval_service import ApprovalService, _digest, _payload
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.runtime_revision import (
    RunRevisionRecord,
    RuntimePlanningRecordReader,
    SavedRuntimeRevisionReader,
    SnapshotContentRef,
    require_runtime_boundary,
    revision_request_payload,
)
from aitest.application.planning.basis_approval import SavedBasisApprovalResolver
from aitest.application.planning.saved_runtime_revision import SavedRuntimeRevisionAssessment
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
)
from aitest.contracts.commands import Command
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.approvals import (
    ActionBasis,
    ApprovalConflict,
    ApprovalMaterialRef,
    ApprovalRequired,
)
from aitest.domain.planning.plans import Plan, RunDriver
from aitest.domain.planning.runtime_revision import (
    CaseRuntimeChange,
    RuntimeRevisionRefused,
    RuntimeRevisionRequest,
)

_ACTIONS = frozenset({"revise_pending_steps", "narrow_driver"})
_PARAMETERS = {"run_id", "base_snapshot_commit_id", "case_changes", "confirmation_ids", "reason"}
_ORIGIN_FIELDS = {
    "schema_version",
    "workspace_id",
    "project_id",
    "run_id",
    "intent_id",
    "action",
    "parameters",
    "input_digest",
    "challenge_id",
    "confirmation_id",
    "confirmed_at_commit",
    "revision_ref",
    "revision_digest",
    "result_snapshot",
}


class RuntimeRevisionBlocked(ApprovalRequired):
    code = "RUNTIME_REVISION_BLOCKED"


class SavedRuntimeRevisionActions:
    def __init__(self, coordinator: ExecutionCommitCoordinator, approvals: ApprovalService) -> None:
        self.coordinator, self.approvals = coordinator, approvals
        self.unit = approvals.unit
        self.workspace_id = approvals.workspace_id
        self.materials = SavedBasisApprovalResolver(approvals.records, self.workspace_id)
        assert coordinator._records is not None
        self.reader = SavedRuntimeRevisionReader(coordinator._records)

    def _read(self, kind: str, identity: str, revision: int, project: str) -> dict[str, Any]:
        return self.materials._read(kind, identity, revision, project)

    @staticmethod
    def _parameters(parameters: Mapping[str, object], target: str, action: str) -> dict[str, Any]:
        if action not in _ACTIONS or set(parameters) != _PARAMETERS:
            raise RuntimeRevisionBlocked("revision accepts only exact declared saved references")
        result: dict[str, Any] = dict(parameters)
        if (
            any(
                not isinstance(result[key], str) or not result[key].strip()
                for key in ("run_id", "base_snapshot_commit_id", "reason")
            )
            or result["run_id"] != target
        ):
            raise RuntimeRevisionBlocked("revision target, basis or reason differs")
        changes, confirmations = result["case_changes"], result["confirmation_ids"]
        if (
            not isinstance(changes, list)
            or not isinstance(confirmations, list)
            or any(not isinstance(value, str) or not value.strip() for value in confirmations)
            or confirmations != sorted(set(confirmations))
        ):
            raise RuntimeRevisionBlocked("revision references must be unique canonical lists")
        ids = []
        for change in changes:
            if not isinstance(change, dict) or set(change) != {
                "case_id",
                "record_revision",
                "target_step_ids",
            }:
                raise RuntimeRevisionBlocked("revision case input has unknown fields")
            steps = change["target_step_ids"]
            if (
                not isinstance(change["case_id"], str)
                or not change["case_id"].strip()
                or (type(change["record_revision"]) is not int or change["record_revision"] < 1)
                or not isinstance(steps, list)
                or any(not isinstance(s, str) or not s.strip() for s in steps)
                or (steps != sorted(set(steps)))
            ):
                raise RuntimeRevisionBlocked(
                    "revision needs exact case warehouse revision and steps"
                )
            ids.append(change["case_id"])
        if (
            ids != sorted(set(ids))
            or (action == "narrow_driver" and (changes or confirmations))
            or (action == "revise_pending_steps" and not changes)
        ):
            raise RuntimeRevisionBlocked("case changes do not match the selected action")
        return result

    def _plan(self, facts: ExecutionFacts) -> Plan:
        project, ref = facts.project_id, facts.plan_revision
        raw = self._read("plan", ref.revision_id, ref.revision_no, project)
        scope = acceptance_scope_from_payload(
            self._read("acceptance_scope", raw["scope_id"], raw["scope_revision"], project)
        )
        return TypeAdapter(Plan).validate_json(
            json.dumps(
                {
                    "plan_id": ref.revision_id,
                    "revision": raw["revision"],
                    "record_revision": ref.revision_no,
                    "scope": _payload(scope),
                    "case_revisions": raw["case_revisions"],
                    "rule_revisions": raw["rule_revisions"],
                    "template_versions": raw["template_versions"],
                    "run_tier": raw["run_tier"],
                    "initial_driver": raw["initial_driver"],
                    "status": "published",
                    "confirmation_id": raw["approval_commit_seq"],
                }
            ),
            strict=True,
        )

    def _request(
        self, facts: ExecutionFacts, parameters: Mapping[str, Any], action: str, session: str
    ) -> RuntimeRevisionRequest:
        changes = tuple(
            CaseRuntimeChange(
                case_from_payload(
                    self._read(
                        "case", change["case_id"], change["record_revision"], facts.project_id
                    )
                ),
                tuple(change["target_step_ids"]),
            )
            for change in parameters["case_changes"]
        )
        if any(
            (change.next_case.case_id, change.next_case.revision)
            != (raw["case_id"], raw["record_revision"])
            for change, raw in zip(changes, parameters["case_changes"], strict=True)
        ):
            raise RuntimeRevisionBlocked("saved case differs from its requested identity")
        return RuntimeRevisionRequest(
            base_plan_revision_id=facts.plan_revision.revision_id,
            base_plan_revision_no=facts.plan_revision.revision_no,
            base_plan_revision_digest=facts.plan_revision.digest,
            observed_snapshot_cursor=facts.snapshot_cursor,
            case_changes=changes,
            reason=parameters["reason"],
            operator_ref="human-session:" + session,
            requested_driver=RunDriver.STEPWISE if action == "narrow_driver" else None,
        )

    def _describe(
        self, project: str, intent: str, action: str, target: str, parameters: Mapping[str, object]
    ) -> tuple[ActionBasis, Plan, RuntimeRevisionRequest]:
        values = self._parameters(parameters, target, action)
        self.approvals.require_actor(project)
        before = self.coordinator.read_runtime_revision_facts(project_id=project, run_id=target)
        if (
            before.run.origin_workspace_id != self.workspace_id
            or before.snapshot_commit_id != values["base_snapshot_commit_id"]
        ):
            raise RuntimeRevisionBlocked("current run changed since the reviewed snapshot")
        self.validate_runtime_origins(before)
        require_runtime_boundary(before)
        if action == "narrow_driver" and before.run.driver.value != "planned":
            raise RuntimeRevisionBlocked("driver is already narrowed; no new revision is needed")
        plan = self._plan(before)
        request = self._request(before, values, action, self.approvals.actors.current().session_id)
        decision = SavedRuntimeRevisionAssessment(
            reader=RuntimePlanningRecordReader(self.reader, project),
            execution=self.coordinator,
            runtime_basis=self.reader,
            approvals=self.coordinator._approvals,
            controlled_writes=self.coordinator._controlled_writes,
        ).assess(
            project_id=project,
            run_id=target,
            plan=plan,
            request=request,
            confirmation_ids=tuple(values["confirmation_ids"]),
        )
        if not decision.accepted:
            raise RuntimeRevisionRefused(decision)
        basis = self._basis(before, intent, action, target, values)
        return basis, plan, request

    def _basis(
        self,
        before: ExecutionFacts,
        intent: str,
        action: str,
        target: str,
        values: Mapping[str, Any],
    ) -> ActionBasis:
        project = before.project_id
        references = [
            ApprovalMaterialRef(
                "execution_facts",
                before.snapshot_commit_id,
                1,
                _digest(before.model_dump(mode="json")),
            )
        ]
        for change in values["case_changes"]:
            raw = self._read("case", change["case_id"], change["record_revision"], project)
            references.append(
                ApprovalMaterialRef(
                    "case", change["case_id"], change["record_revision"], _digest(raw)
                )
            )
        for identity in values["confirmation_ids"]:
            raw = self._read("case_link", identity, 1, project)
            references.append(ApprovalMaterialRef("case_link", identity, 1, _digest(raw)))
        return ActionBasis(
            self.workspace_id,
            project,
            intent,
            action,
            target,
            _digest(values),
            "local-runtime-revision",
            tuple(references),
        )

    def resolve(
        self,
        *,
        project_id: str,
        intent_id: str,
        action: str,
        target: str,
        parameters: Mapping[str, object],
    ) -> ActionBasis:
        try:
            return self._describe(project_id, intent_id, action, target, parameters)[0]
        except (ValueError, KeyError, TypeError, OSError) as error:
            if getattr(error, "code", None):
                raise
            raise RuntimeRevisionBlocked(
                "saved revision basis or activity cannot be verified"
            ) from error

    def _origin_id(self, project: str, intent: str) -> str:
        return "runtime-origin-" + _digest([self.workspace_id, project, intent])[7:]

    def _origin(self, project: str, intent: str) -> tuple[dict[str, Any], RunRevisionRecord] | None:
        identity = self._origin_id(project, intent)
        current = self.approvals.records.current_revision(
            aggregate_kind="approval_intent", record_id=identity
        )
        if type(current) is not int or current not in {0, 1}:
            raise RuntimeRevisionBlocked("runtime origin warehouse revision differs")
        if current == 0:
            return None
        raw = self._read("approval_intent", identity, 1, project)
        if set(raw) != _ORIGIN_FIELDS or (
            raw["schema_version"],
            raw["workspace_id"],
            raw["intent_id"],
        ) != ("aitest.runtime-revision-origin/1.0", self.workspace_id, intent):
            raise RuntimeRevisionBlocked("runtime origin schema or identity differs")
        values = self._parameters(raw["parameters"], raw["run_id"], raw["action"])
        if raw["input_digest"] != _digest(values):
            raise RuntimeRevisionBlocked("runtime origin input digest differs")
        record = self.reader.read_record(project_id=project, reference=raw["revision_ref"])
        proof = self.approvals.read_confirmation(
            project_id=project, confirmation_id=raw["confirmation_id"]
        )
        before = self.reader.read_snapshot(
            project_id=project, run_id=raw["run_id"], reference=record.base_snapshot
        )
        if (
            (record.run_id, record.intent_id, record.origin_workspace_id)
            != (raw["run_id"], intent, self.workspace_id)
            or raw["revision_digest"] != _digest(record.model_dump(mode="json"))
            or raw["result_snapshot"] != record.result_snapshot.model_dump(mode="json")
            or values["base_snapshot_commit_id"] != before.snapshot_commit_id
            or record.confirmation_ids != tuple(values["confirmation_ids"])
            or record.request_payload
            != revision_request_payload(
                self._request(before, values, raw["action"], proof.origin_session_id), project
            )
            or (
                proof.basis.intent_id,
                proof.basis.action,
                proof.basis.target,
                proof.basis.input_digest,
            )
            != (intent, raw["action"], raw["run_id"], raw["input_digest"])
            or proof.challenge_id != raw["challenge_id"]
            or proof.confirmed_at_commit != raw["confirmed_at_commit"]
            or proof.confirmation_intent_id != intent
            or proof.basis != self._basis(before, intent, raw["action"], raw["run_id"], values)
        ):
            raise RuntimeRevisionBlocked("runtime revision has no exact controlled origin")
        return raw, record

    def validate_runtime_origins(self, facts: ExecutionFacts) -> None:
        for reference in facts.runtime_revision_refs:
            record = self.reader.read_record(project_id=facts.project_id, reference=reference)
            original = self._origin(facts.project_id, record.intent_id)
            if original is None or original[1] != record or record.run_id != facts.run_id:
                raise RuntimeRevisionBlocked(
                    "legacy runtime revision needs controlled new preparation"
                )

    def _recall(
        self, project: str, intent: str, action: str, values: Mapping[str, Any], challenge: str
    ) -> ExecutionFacts | None:
        original = self._origin(project, intent)
        if original is None:
            return None
        raw, record = original
        if (
            raw["parameters"] != dict(values)
            or raw["action"] != action
            or raw["challenge_id"] != challenge
        ):
            raise ApprovalConflict("runtime revision intent has different input or challenge")
        return self.reader.read_snapshot(
            project_id=project, run_id=record.run_id, reference=record.result_snapshot
        )

    def apply(self, command: Command) -> Mapping[str, object]:
        try:
            return self._apply(command)
        except (ValueError, KeyError, TypeError, OSError) as error:
            if getattr(error, "code", None):
                raise
            raise RuntimeRevisionBlocked(
                "runtime revision material or boundary cannot be verified"
            ) from error

    def _apply(self, command: Command) -> Mapping[str, object]:
        project, intent = command.project_id, command.intent_id
        if (
            not project
            or not intent
            or not command.target
            or type(command.expected_revision) is not int
            or command.expected_revision != 0
        ):
            raise RuntimeRevisionBlocked("revision requires project/intent/run and revision 0")
        self.approvals.require_actor(project)
        raw = dict(command.parameters)
        challenge = raw.pop("approval_challenge_id", None)
        values = self._parameters(raw, command.target, command.action)
        if not isinstance(challenge, str) or not challenge.strip():
            raise ApprovalRequired("review the exact runtime revision challenge")
        original = self._recall(project, intent, command.action, values, challenge)
        if original is not None:
            return original.model_dump(mode="json")
        self.unit.begin(command.request_id, project, intent_id=intent)
        try:
            original = self._recall(project, intent, command.action, values, challenge)
            if original is not None:
                self.unit.rollback(command.request_id)
                return original.model_dump(mode="json")
            basis, plan, request = self._describe(
                project, intent, command.action, command.target, values
            )
            staged: list[RunRevisionRecord] = []
            facts, created = self.coordinator.stage_runtime_revision(
                project_id=project,
                run_id=command.target,
                plan=plan,
                request=request,
                intent_id=intent,
                confirmation_ids=tuple(values["confirmation_ids"]),
                on_revision_staged=staged.append,
            )
            if not created or len(staged) != 1:
                raise RuntimeRevisionBlocked("existing component revision has no controlled origin")
            confirmation = self.approvals.stage_confirmation(
                project_id=project,
                challenge_id=challenge,
                confirmation_intent_id=intent,
                parameters=values,
                trailing_records=1,
            )
            if (
                confirmation.basis != basis
                or self.unit.next_commit_seq() != confirmation.confirmed_at_commit
            ):
                raise RuntimeRevisionBlocked("revision confirmation differs from its atomic effect")
            reference = facts.runtime_revision_refs[-1]
            self.unit.stage_record(
                aggregate_kind="approval_intent",
                record_id=self._origin_id(project, intent),
                expected_revision=0,
                payload={
                    "schema_version": "aitest.runtime-revision-origin/1.0",
                    "workspace_id": self.workspace_id,
                    "project_id": project,
                    "run_id": command.target,
                    "intent_id": intent,
                    "action": command.action,
                    "parameters": values,
                    "input_digest": basis.input_digest,
                    "challenge_id": challenge,
                    "confirmation_id": confirmation.confirmation_id,
                    "confirmed_at_commit": confirmation.confirmed_at_commit,
                    "revision_ref": reference,
                    "revision_digest": _digest(staged[0].model_dump(mode="json")),
                    "result_snapshot": SnapshotContentRef.of(facts).model_dump(mode="json"),
                },
            )
            self.unit.commit(command.request_id)
        except BaseException as error:
            try:
                self.unit.rollback(command.request_id)
            except Exception as cleanup:
                raise error from cleanup
            raise
        return facts.model_dump(mode="json")
