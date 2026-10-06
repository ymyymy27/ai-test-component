"""Original single-action consent, unused state and saved occupation authority."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter

from aitest.application.approval_service import ApprovalService, _digest, _payload
from aitest.application.execution.authorization_index import (
    authorization_index_id,
    read_authorization_index,
    stage_authorization_index,
)
from aitest.application.execution.commit import ExecutionCommitCoordinator, _run_pointer_id
from aitest.application.execution.facts import _run_fact, _step_fact
from aitest.application.execution.run_record import read_run_record
from aitest.application.execution.runtime_revision import SavedRuntimeRevisionReader
from aitest.application.execution.start_identity import execution_start_fingerprint
from aitest.application.execution.step_content import StepContentReader
from aitest.application.planning.basis_approval import SavedBasisApprovalResolver
from aitest.application.planning.basis_validation import validate_prepared_material
from aitest.application.planning.preparation_origin import (
    load_saved_preparation,
    validate_preparation_origin,
)
from aitest.application.planning.serialization import (
    acceptance_scope_from_payload,
    case_from_payload,
)
from aitest.application.planning.substrate import RecordReader
from aitest.application.ports import (
    ControlledWriteProof,
    ExecutionActionResolver,
    RecordRepository,
    StageableWorkspaceUnitOfWork,
)
from aitest.application.project.environment_resolution import EnvironmentResolutionService
from aitest.application.project.source_analysis import SourceAnalysisService
from aitest.contracts.prepared_run import PreparedRun
from aitest.domain.approvals import (
    ActionBasis,
    ApprovalConflict,
    ApprovalMaterialRef,
    ApprovalRequired,
)
from aitest.domain.execution.authorization import (
    AuthorizationState,
    ResolvedExecutionAction,
    require_unused,
)
from aitest.domain.execution.runs import (
    Attempt,
    Run,
    RunControlState,
    Step,
    StepLevel,
    StepRevisionRef,
    StepState,
    attempt_start_basis,
)
from aitest.domain.planning.plans import Plan


def _identity(prefix: str, *values: object) -> str:
    return prefix + _digest(list(values))[7:]


class ExecutionResolutionUnavailable(ApprovalRequired):
    code = "CAPABILITY_UNAVAILABLE"


class SavedExecutionAuthorizationResolver:
    """Confirmation accepts saved action references, never an executable or grant body."""

    def __init__(self, records: RecordRepository, workspace_id: str) -> None:
        self.records, self.workspace_id = records, workspace_id
        self.materials = SavedBasisApprovalResolver(records, workspace_id)

    def read(self, project: str, identity: str) -> tuple[dict[str, Any], ResolvedExecutionAction]:
        revision = self.records.current_revision(
            aggregate_kind="execution_authorization", record_id=identity
        )
        if type(revision) is not int or revision != 1:
            raise ApprovalRequired("the exact immutable resolved action is unavailable")
        raw = self.materials._read("execution_authorization", identity, 1, project)
        if (
            set(raw)
            != {
                "schema_version",
                "workspace_id",
                "project_id",
                "action",
                "materials",
                "input_digest",
            }
            or raw["schema_version"] != "aitest.resolved-execution-action/1.0"
            or raw["workspace_id"] != self.workspace_id
        ):
            raise ApprovalRequired("resolved action has unknown fields or workspace")
        action = TypeAdapter(ResolvedExecutionAction).validate_json(
            json.dumps(raw["action"]), strict=True
        )
        if (
            identity
            != _identity("execution-action-", self.workspace_id, project, action.request.intent_id)
            or action.request.project_id != project
            or raw["input_digest"] != _digest(raw["action"])
            or raw["action"] != _payload(action)
            or action.request.authorization_ref.authorization_id
            != _identity("execution-grant-", self.workspace_id, project, action.request.intent_id)
            or action.attempt.attempt_id
            != _identity("execution-attempt-", self.workspace_id, project, action.request.intent_id)
        ):
            raise ApprovalRequired("resolved action identity or content differs")
        return raw, action

    def resolve(
        self,
        *,
        project_id: str,
        intent_id: str,
        action: str,
        target: str,
        parameters: Mapping[str, object],
    ) -> ActionBasis:
        if action != "authorize_step" or set(parameters) != {
            "execution_action_id",
            "record_revision",
        }:
            raise ApprovalRequired("execution consent requires an exact saved action reference")
        identity = parameters["execution_action_id"]
        if (
            not isinstance(identity, str)
            or type(parameters["record_revision"]) is not int
            or parameters["record_revision"] != 1
        ):
            raise ApprovalRequired("execution action reference cannot be verified")
        raw, resolved = self.read(project_id, identity)
        if (intent_id, target) != (resolved.request.intent_id, resolved.request.step_id):
            raise ApprovalRequired("execution consent intent or step differs")
        references = tuple(
            TypeAdapter(tuple[ApprovalMaterialRef, ...]).validate_json(
                json.dumps(raw["materials"]), strict=True
            )
        )
        if not references:
            raise ApprovalRequired("resolved execution has no saved origin material")
        for ref in references:
            revision = self.records.current_revision(
                aggregate_kind=ref.aggregate_kind, record_id=ref.record_id
            )
            body = self.materials._read(
                ref.aggregate_kind, ref.record_id, ref.record_revision, project_id
            )
            if (
                type(revision) is not int
                or (
                    ref.aggregate_kind != "execution_facts_current"
                    and revision != ref.record_revision
                )
                or _digest(body) != ref.digest
            ):
                raise ApprovalRequired("execution basis changed; resolve and review a new action")
        return ActionBasis(
            self.workspace_id,
            project_id,
            intent_id,
            action,
            target,
            _digest(dict(parameters)),
            resolved.request.authorization_ref.credential_scope_ref,
            (ApprovalMaterialRef("execution_authorization", identity, 1, _digest(raw)),)
            + references,
        )


class ExecutionAuthorizationService:
    def __init__(
        self,
        *,
        unit: StageableWorkspaceUnitOfWork,
        records: RecordRepository,
        reader: RecordReader,
        workspace_id: str,
        resolver: SavedExecutionAuthorizationResolver,
        approvals: ApprovalService,
        source: SourceAnalysisService,
        environment: EnvironmentResolutionService,
        action_resolver: ExecutionActionResolver | None,
        controlled_writes: ControlledWriteProof,
    ) -> None:
        self.unit, self.records, self.workspace_id = unit, records, workspace_id
        self.reader = reader
        self.resolver, self.approvals = resolver, approvals
        self.source, self.environment, self.action_resolver = source, environment, action_resolver
        self.controlled_writes = controlled_writes

    def _revision(self, identity: str) -> int:
        result = self.records.current_revision(
            aggregate_kind="execution_authorization", record_id=identity
        )
        if type(result) is not int or result < 0:
            raise ApprovalRequired("authorization warehouse revision cannot be verified")
        return result

    def _read(self, project: str, identity: str, revision: int) -> dict[str, Any]:
        raw = self.resolver.materials._read("execution_authorization", identity, revision, project)
        if raw.get("workspace_id") != self.workspace_id:
            raise ApprovalRequired("authorization belongs to a different workspace")
        return raw

    def _context(
        self, project: str, run_id: str, step_id: str
    ) -> tuple[Run, Step, PreparedRun, tuple[ApprovalMaterialRef, ...]]:
        references: list[ApprovalMaterialRef] = []

        def read(kind: str, identity: str) -> dict[str, Any]:
            revision = self.records.current_revision(aggregate_kind=kind, record_id=identity)
            if type(revision) is not int or revision < 1:
                raise ApprovalRequired("execution material requires an exact warehouse revision")
            if kind in {"run", "step"} and revision != 1:
                raise ApprovalRequired("initial execution basis cannot be rewritten")
            raw = self.resolver.materials._read(kind, identity, revision, project)
            references.append(ApprovalMaterialRef(kind, identity, revision, _digest(raw)))
            return raw

        run_raw, step_raw = read("run", run_id), read("step", step_id)
        run = read_run_record(run_raw)
        step = TypeAdapter(Step).validate_json(
            json.dumps({key: value for key, value in step_raw.items() if key != "project_id"}),
            strict=True,
        )
        coordinator = ExecutionCommitCoordinator(self.unit, records=self.records)
        current = coordinator.read_current_facts(project_id=project, run_id=run_id)
        if (
            step_raw != {"project_id": project, **_payload(step)}
            or (run.project_id, run.origin_workspace_id, run.run_id, step.run_id, step.step_id)
            != (project, self.workspace_id, run_id, run_id, step_id)
            or current is None
        ):
            raise ApprovalRequired("execution requires the exact registered current run and step")
        content = StepContentReader(self.records).read(run=run, step=step)
        prepared, _ = load_saved_preparation(
            reader=self.reader,
            project_id=project,
            workspace_id=self.workspace_id,
            prepared_run_id=content.prepared_run_ref.prepared_run_id,
        )
        validate_preparation_origin(prepared, reader=self.reader)
        if validate_prepared_material(
            prepared,
            reader=self.reader,
            workspace_id=self.workspace_id,
            approvals=self.approvals,
            controlled_writes=self.controlled_writes,
            environment_resolution=self.environment,
        ):
            raise ApprovalRequired(
                "execution preparation or original publication proof is unavailable"
            )
        receipt = read("execution_intent", "run-registration:" + run_id)
        registration_intent = receipt.get("intent_id")
        if not isinstance(registration_intent, str) or not registration_intent:
            raise ApprovalRequired("original registration intent cannot be verified")
        initial = coordinator.recall_initial_run(
            run_id=run_id,
            project_id=project,
            fingerprint=content.prepared_run_ref.digest,
            intent_id=registration_intent,
            prepared_run_id=prepared.prepared_run_id,
            workspace_id=self.workspace_id,
        )
        if (
            initial is None
            or initial.run != _run_fact(run)
            or next((item for item in initial.steps if item.step_id == step_id), None)
            != _step_fact(step)
        ):
            raise ApprovalRequired("initial run and step differ from their registration proof")
        plan_raw = self.resolver.materials._read(
            "plan", prepared.plan_revision.revision_id, prepared.plan_revision.revision_no, project
        )
        scope_raw = self.resolver.materials._read(
            "acceptance_scope",
            prepared.scope_id or "missing",
            prepared.acceptance_scope_revision,
            project,
        )
        plan = TypeAdapter(Plan).validate_json(
            json.dumps(
                {
                    "plan_id": prepared.plan_revision.revision_id,
                    "revision": plan_raw["revision"],
                    "record_revision": prepared.plan_revision.revision_no,
                    "scope": _payload(acceptance_scope_from_payload(scope_raw)),
                    "case_revisions": plan_raw["case_revisions"],
                    "rule_revisions": plan_raw["rule_revisions"],
                    "template_versions": plan_raw["template_versions"],
                    "run_tier": plan_raw["run_tier"],
                    "initial_driver": plan_raw["initial_driver"],
                    # The record has no domain status field. Its exact controlled
                    # publish_plan origin was verified above, before projecting it.
                    "status": "published",
                    "confirmation_id": plan_raw["approval_commit_seq"],
                }
            ),
            strict=True,
        )
        cases = tuple(
            case_from_payload(
                self.resolver.materials._read("case", ref.case_id, ref.revision, project)
            )
            for ref in prepared.case_revisions
        )
        # Current progress is mutable; every change to frozen content or driver must
        # instead have the already-defined, independently readable revision chain.
        SavedRuntimeRevisionReader(self.records).read_effective_cases(
            facts=current, plan=plan, initial_cases=cases
        )
        fact = next(item for item in current.steps if item.step_id == step_id)
        run = replace(
            run,
            revision=current.run.run_revision,
            driver=current.run.driver.value,
            control_state=RunControlState(current.run.control_state.value),
            evidence_level=(
                current.run.evidence_level.value if current.run.evidence_level else None
            ),
            primary_gap_ids=current.run.primary_gap_ids,
            coverage_summary=current.run.coverage_summary,
            runtime_revision_refs=current.runtime_revision_refs,
            result_ref=current.run.result_ref,
            started_at=current.run.started_at,
            ended_at=current.run.ended_at,
        )
        step = replace(
            step,
            step_revision_ref=StepRevisionRef(**fact.step_revision_ref.model_dump()),
            level=StepLevel(fact.level.value),
            state=StepState(fact.state.value),
            current_attempt_id=fact.current_attempt_id,
            invalidated_by=fact.invalidated_by,
        )
        pointer = read("execution_facts_current", _run_pointer_id(project, run_id))
        if (
            pointer["snapshot_commit_id"] != current.snapshot_commit_id
            or coordinator.read_current_facts(project_id=project, run_id=run_id) != current
        ):
            raise ApprovalRequired("current execution changed while resolving its frozen basis")
        for kind, identity, revision in (
            (
                "step_revision",
                step.step_revision_ref.step_revision_id,
                step.step_revision_ref.revision_no,
            ),
            ("prepared_run", prepared.prepared_run_id, 1),
        ):
            raw = self.resolver.materials._read(kind, identity, revision, project)
            references.append(ApprovalMaterialRef(kind, identity, revision, _digest(raw)))
        return run, step, prepared, tuple(references)

    def _current_action(
        self, project: str, raw: Mapping[str, Any], action: ResolvedExecutionAction
    ) -> PreparedRun:
        """Retain exact historical provenance; recheck this action's live basis."""
        run, step, prepared, current_refs = self._context(
            project, action.request.run_id, action.request.step_id
        )
        original_refs = TypeAdapter(tuple[ApprovalMaterialRef, ...]).validate_json(
            json.dumps(raw["materials"]), strict=True
        )

        def action_refs(refs: tuple[ApprovalMaterialRef, ...]) -> tuple[ApprovalMaterialRef, ...]:
            return tuple(ref for ref in refs if ref.aggregate_kind != "execution_facts_current")

        if (
            action_refs(current_refs) != action_refs(original_refs)
            or step.step_revision_ref != action.attempt.step_revision_ref
            or run.plan_revision_ref != action.attempt.expected_plan_revision_ref
        ):
            raise ApprovalRequired("authorized action no longer uses its exact current material")
        current, _ = ExecutionCommitCoordinator(
            self.unit, records=self.records
        )._prepare_current_start(project, action.attempt)
        if current is None:
            raise ApprovalRequired("authorized action requires a registered current run")
        return prepared

    def _actual(self, prepared: PreparedRun) -> None:
        self.environment.validate_current(prepared)
        source = self.source.check(
            project_id=prepared.project_id,
            snapshot_id=prepared.snapshot.source_snapshot_id,
            revision=prepared.snapshot.record_revision,
        )
        changes = source.get("changes")
        if (
            not isinstance(changes, dict)
            or changes.get("state") != "unchanged"
            or source.get("binding_state") != "unchanged"
            or source.get("git_state") not in {"unchanged", "not_applicable"}
        ):
            raise ApprovalRequired("actual execution source changed or cannot be verified")

    def prepare(
        self,
        *,
        project_id: str,
        run_id: str,
        step_id: str,
        intent_id: str,
        request_id: str,
    ) -> Mapping[str, object]:
        """Called only through core assembly; the public caller supplies saved identities."""
        identity = _identity("execution-action-", self.workspace_id, project_id, intent_id)
        if self._revision(identity):
            _, old = self.resolver.read(project_id, identity)
            if (old.request.run_id, old.request.step_id) != (run_id, step_id):
                raise ApprovalConflict("execution resolution intent has different run or step")
            return {"execution_action_id": identity, "record_revision": 1}
        if self.action_resolver is None:
            raise ExecutionResolutionUnavailable(
                "a trusted actual execution resolver is not configured"
            )
        run, step, prepared, references = self._context(project_id, run_id, step_id)
        self._actual(prepared)
        content = StepContentReader(self.records).read(run=run, step=step)
        resolved = self.action_resolver.resolve(
            run=run,
            step=step,
            intent_id=intent_id,
            prepared=prepared.model_dump(mode="json"),
            step_content=content.model_dump(mode="json"),
        )
        detail = prepared.environment.resolution
        if (
            not isinstance(resolved, ResolvedExecutionAction)
            or detail is None
            or resolved.environment_content_identity != detail.content_identity
            or resolved.source_content_identity != prepared.snapshot.content_identity
            or (
                resolved.request.project_id,
                resolved.request.run_id,
                resolved.request.step_id,
                resolved.request.intent_id,
            )
            != (project_id, run_id, step_id, intent_id)
            or resolved.attempt.step_revision_ref != step.step_revision_ref
            or resolved.attempt.expected_plan_revision_ref != run.plan_revision_ref
        ):
            raise ApprovalRequired("registered resolver returned another frozen execution basis")
        authorization = replace(
            resolved.request.authorization_ref,
            authorization_id=_identity(
                "execution-grant-", self.workspace_id, project_id, intent_id
            ),
        )
        attempt_id = _identity("execution-attempt-", self.workspace_id, project_id, intent_id)
        resolved = replace(
            resolved,
            attempt=replace(
                resolved.attempt, attempt_id=attempt_id, authorization_ref=authorization
            ),
            request=replace(
                resolved.request, attempt_id=attempt_id, authorization_ref=authorization
            ),
        )
        current, _ = ExecutionCommitCoordinator(
            self.unit, records=self.records
        )._prepare_current_start(project_id, resolved.attempt)
        if current is None:
            raise ApprovalRequired("resolved execution has no registered current run")
        self._actual(prepared)
        raw = {
            "schema_version": "aitest.resolved-execution-action/1.0",
            "workspace_id": self.workspace_id,
            "project_id": project_id,
            "action": _payload(resolved),
            "materials": [_payload(ref) for ref in references],
            "input_digest": _digest(_payload(resolved)),
        }
        self.unit.begin(request_id, project_id)
        try:
            if self._revision(identity):
                existing, _ = self.resolver.read(project_id, identity)
                if existing != raw:
                    raise ApprovalConflict(
                        "execution intent was resolved with different actual input"
                    )
                self.unit.rollback(request_id)
            else:
                if self._context(project_id, run_id, step_id)[3] != references:
                    raise ApprovalRequired("execution saved input changed before publication")
                self.unit.stage_record(
                    aggregate_kind="execution_authorization",
                    record_id=identity,
                    expected_revision=0,
                    payload=raw,
                )
                self.unit.commit(request_id)
        except BaseException:
            self.unit.rollback(request_id)
            raise
        return {"execution_action_id": identity, "record_revision": 1}

    def grant(
        self,
        *,
        project_id: str,
        intent_id: str,
        request_id: str,
        parameters: Mapping[str, object],
        challenge_id: str,
    ) -> Mapping[str, object]:
        self.approvals.require_actor(project_id)
        if (
            set(parameters) != {"execution_action_id", "record_revision"}
            or not isinstance(parameters["execution_action_id"], str)
            or type(parameters["record_revision"]) is not int
            or parameters["record_revision"] != 1
        ):
            raise ApprovalRequired("grant requires an exact saved execution action reference")
        raw, action = self.resolver.read(project_id, parameters["execution_action_id"])
        identity = action.request.authorization_ref.authorization_id
        if intent_id != action.request.intent_id:
            raise ApprovalRequired("grant business intent differs from its resolved action")
        if self._revision(identity):
            return self._recall_grant(project_id, identity, parameters, challenge_id)
        self._actual(self._current_action(project_id, raw, action))
        self.unit.begin(request_id, project_id)
        try:
            if self._revision(identity):
                recalled = self._recall_grant(project_id, identity, parameters, challenge_id)
                self.unit.rollback(request_id)
                return recalled
            basis = self.resolver.resolve(
                project_id=project_id,
                intent_id=intent_id,
                action="authorize_step",
                target=action.request.step_id,
                parameters=parameters,
            )
            self._current_action(project_id, raw, action)
            index_revision, unused = read_authorization_index(
                self.records, self.workspace_id, project_id, action.request.run_id
            )
            if identity in unused:
                raise ApprovalRequired("unused authority already exists without its original grant")
            confirmation = self.approvals.stage_confirmation(
                project_id=project_id,
                challenge_id=challenge_id,
                confirmation_intent_id=intent_id,
                parameters=parameters,
                trailing_records=3,
            )
            if confirmation.basis != basis:
                raise ApprovalRequired("execution confirmation differs from its saved origin")
            if str(int(self.unit.next_commit_seq()) + 2) != confirmation.confirmed_at_commit:
                raise ApprovalRequired("execution confirmation must belong to this atomic batch")
            self.unit.stage_record(
                aggregate_kind="execution_authorization",
                record_id=identity,
                expected_revision=0,
                payload={
                    "schema_version": "aitest.action-authorization/1.1",
                    "project_id": project_id,
                    "workspace_id": self.workspace_id,
                    "parameters": dict(parameters),
                    "authorization_ref": _payload(action.request.authorization_ref),
                    "confirmation_id": confirmation.confirmation_id,
                    "confirmed_at_commit": confirmation.confirmed_at_commit,
                    "input_digest": _digest(raw),
                    "authorization_index_id": authorization_index_id(
                        self.workspace_id, project_id, action.request.run_id
                    ),
                },
            )
            self.unit.stage_record(
                aggregate_kind="execution_authorization",
                record_id="authorization-state:" + identity,
                expected_revision=0,
                payload=self._state_payload(project_id, identity, AuthorizationState.UNUSED, None),
            )
            stage_authorization_index(
                self.unit,
                self.workspace_id,
                project_id,
                action.request.run_id,
                index_revision,
                tuple(sorted((*unused, identity))),
            )
            self.unit.commit(request_id)
        except BaseException:
            self.unit.rollback(request_id)
            raise
        return _payload(action.request.authorization_ref)

    def _recall_grant(
        self, project: str, identity: str, parameters: Mapping[str, object], challenge_id: str
    ) -> Mapping[str, object]:
        original, action = self._origin(project, identity)
        confirmation = self.approvals.read_confirmation(
            project_id=project, confirmation_id=original["confirmation_id"]
        )
        if original["parameters"] != dict(parameters) or confirmation.challenge_id != challenge_id:
            raise ApprovalConflict("authorization intent has different saved input or challenge")
        return _payload(action.request.authorization_ref)

    def _origin(
        self, project: str, identity: str
    ) -> tuple[dict[str, Any], ResolvedExecutionAction]:
        if self._revision(identity) != 1:
            raise ApprovalRequired("original immutable execution authorization is unavailable")
        grant = self._read(project, identity, 1)
        indexed = grant.get("schema_version") == "aitest.action-authorization/1.1"
        if set(grant) != {
            "schema_version",
            "project_id",
            "workspace_id",
            "parameters",
            "authorization_ref",
            "confirmation_id",
            "confirmed_at_commit",
            "input_digest",
        } | ({"authorization_index_id"} if indexed else set()) or grant["schema_version"] not in {
            "aitest.action-authorization/1.0",
            "aitest.action-authorization/1.1",
        }:
            raise ApprovalRequired("original execution authorization has unknown fields")
        parameters = grant["parameters"]
        if (
            not isinstance(parameters, dict)
            or set(parameters) != {"execution_action_id", "record_revision"}
            or type(parameters["record_revision"]) is not int
            or parameters["record_revision"] != 1
        ):
            raise ApprovalRequired("original authorization action reference is malformed")
        raw, action = self.resolver.read(project, parameters["execution_action_id"])
        confirmation = self.approvals.read_confirmation(
            project_id=project, confirmation_id=grant["confirmation_id"]
        )
        references = tuple(
            TypeAdapter(tuple[ApprovalMaterialRef, ...]).validate_json(
                json.dumps(raw["materials"]), strict=True
            )
        )
        expected = ActionBasis(
            self.workspace_id,
            project,
            action.request.intent_id,
            "authorize_step",
            action.request.step_id,
            _digest(parameters),
            action.request.authorization_ref.credential_scope_ref,
            (
                ApprovalMaterialRef(
                    "execution_authorization", parameters["execution_action_id"], 1, _digest(raw)
                ),
            )
            + references,
        )
        if (
            identity != action.request.authorization_ref.authorization_id
            or grant["authorization_ref"] != _payload(action.request.authorization_ref)
            or grant["input_digest"] != _digest(raw)
            or confirmation.basis != expected
            or confirmation.confirmation_intent_id != action.request.intent_id
            or confirmation.confirmed_at_commit != grant["confirmed_at_commit"]
            or (
                indexed
                and grant["authorization_index_id"]
                != authorization_index_id(self.workspace_id, project, action.request.run_id)
            )
        ):
            raise ApprovalRequired("original grant differs from its exact core user confirmation")
        for ref in references:
            body = self.resolver.materials._read(
                ref.aggregate_kind, ref.record_id, ref.record_revision, project
            )
            if _digest(body) != ref.digest:
                raise ApprovalRequired("original execution material cannot be verified")
        return grant, action

    def _state_payload(
        self, project: str, identity: str, state: AuthorizationState, attempt: str | None
    ) -> dict[str, Any]:
        return {
            "schema_version": "aitest.authorization-state/1.0",
            "workspace_id": self.workspace_id,
            "project_id": project,
            "authorization_id": identity,
            "state": state.value,
            "occupied_by_attempt_id": attempt,
        }

    def _state(self, project: str, identity: str) -> tuple[int, AuthorizationState, str | None]:
        key = "authorization-state:" + identity
        revision = self._revision(key)
        if revision < 1:
            raise ApprovalRequired("original authorization state is unavailable")
        raw = self._read(project, key, revision)
        value = raw.get("state")
        if not isinstance(value, str):
            raise ApprovalRequired("authorization state is unknown")
        state = AuthorizationState(value)
        attempt = raw.get("occupied_by_attempt_id")
        if (
            raw != self._state_payload(project, identity, state, attempt)
            or (state is AuthorizationState.OCCUPIED)
            != (isinstance(attempt, str) and bool(attempt))
            or (state is AuthorizationState.UNUSED and revision != 1)
            or (state is not AuthorizationState.UNUSED and revision != 2)
            or self._read(project, key, 1)
            != self._state_payload(project, identity, AuthorizationState.UNUSED, None)
        ):
            raise ApprovalRequired("authorization state has unknown fields or history")
        return revision, state, attempt

    def validate_new(self, *, project_id: str, attempt: Attempt) -> None:
        if attempt.authorization_ref is None:
            raise ApprovalRequired("new execution needs original authorization")
        grant, action = self._origin(project_id, attempt.authorization_ref.authorization_id)
        if attempt_start_basis(replace(attempt, intent_digest="")) != attempt_start_basis(
            action.attempt
        ) or attempt.intent_digest != execution_start_fingerprint(action.attempt, action.request):
            raise ApprovalRequired("execution attempt differs from its original authorized action")
        _, state, occupied = self._state(project_id, attempt.authorization_ref.authorization_id)
        require_unused(state, occupied)
        self._require_indexed(project_id, grant, action)
        action_id = _identity("execution-action-", self.workspace_id, project_id, attempt.intent_id)
        parameters = {
            "execution_action_id": action_id,
            "record_revision": 1,
        }
        self.resolver.resolve(
            project_id=project_id,
            intent_id=attempt.intent_id,
            action="authorize_step",
            target=attempt.step_id,
            parameters=parameters,
        )
        raw, _ = self.resolver.read(project_id, action_id)
        prepared = self._current_action(project_id, raw, action)
        self._actual(prepared)

    def stage_occupation(
        self,
        *,
        project_id: str,
        attempt: Attempt,
        superseded_attempt_ids: tuple[str, ...] = (),
        changed_step_ids: tuple[str, ...] = (),
    ) -> Mapping[str, object]:
        """Saved reads only; caller owns the start transaction and external checks."""
        if attempt.authorization_ref is None:
            raise ApprovalRequired("original authorization is unavailable")
        identity = attempt.authorization_ref.authorization_id
        grant, action = self._origin(project_id, identity)
        index_revision, unused = self._require_indexed(project_id, grant, action)
        if attempt_start_basis(replace(attempt, intent_digest="")) != attempt_start_basis(
            action.attempt
        ) or attempt.intent_digest != execution_start_fingerprint(action.attempt, action.request):
            raise ApprovalRequired("start attempt differs from its original authorization")
        revision, state, occupied = self._state(project_id, identity)
        require_unused(state, occupied)
        self.resolver.resolve(
            project_id=project_id,
            intent_id=attempt.intent_id,
            action="authorize_step",
            target=attempt.step_id,
            parameters=grant["parameters"],
        )
        raw, _ = self.resolver.read(project_id, grant["parameters"]["execution_action_id"])
        self._current_action(project_id, raw, action)
        removed = self._stage_revocations(
            project_id=project_id,
            run_id=attempt.run_id,
            unused=unused,
            superseded_attempt_ids=superseded_attempt_ids,
            changed_step_ids=tuple(sorted({attempt.step_id, *changed_step_ids})),
            except_id=identity,
        )
        self.unit.stage_record(
            aggregate_kind="execution_authorization",
            record_id="authorization-state:" + identity,
            expected_revision=revision,
            payload=self._state_payload(
                project_id, identity, AuthorizationState.OCCUPIED, attempt.attempt_id
            ),
        )
        stage_authorization_index(
            self.unit,
            self.workspace_id,
            project_id,
            attempt.run_id,
            index_revision,
            tuple(value for value in unused if value not in removed and value != identity),
        )
        return {
            "grant_id": identity,
            "grant_revision": 1,
            "grant_digest": _digest(grant),
            "state_revision": revision + 1,
        }

    def _require_indexed(
        self, project: str, grant: Mapping[str, Any], action: ResolvedExecutionAction
    ) -> tuple[int, tuple[str, ...]]:
        if grant.get("schema_version") != "aitest.action-authorization/1.1":
            raise ApprovalRequired("legacy authorization requires new resolution and consent")
        revision, unused = read_authorization_index(
            self.records, self.workspace_id, project, action.request.run_id, required=True
        )
        if action.request.authorization_ref.authorization_id not in unused:
            raise ApprovalRequired("authorization is absent from its exact unused index")
        return revision, unused

    def _stage_revocations(
        self,
        *,
        project_id: str,
        run_id: str,
        unused: tuple[str, ...],
        superseded_attempt_ids: tuple[str, ...],
        changed_step_ids: tuple[str, ...],
        except_id: str | None = None,
    ) -> set[str]:
        removed: set[str] = set()
        for identity in unused:
            if identity == except_id:
                continue
            grant, action = self._origin(project_id, identity)
            if action.request.run_id != run_id or grant.get(
                "authorization_index_id"
            ) != authorization_index_id(self.workspace_id, project_id, run_id):
                raise ApprovalRequired("unused index contains foreign or legacy authorization")
            revision, state, occupied = self._state(project_id, identity)
            require_unused(state, occupied)
            consumed = {value.upstream_attempt_id for value in action.attempt.consumed_outputs} | {
                value.upstream_attempt_id for value in action.attempt.consumed_conditions
            }
            if action.request.step_id in changed_step_ids or consumed.intersection(
                superseded_attempt_ids
            ):
                self.unit.stage_record(
                    aggregate_kind="execution_authorization",
                    record_id="authorization-state:" + identity,
                    expected_revision=revision,
                    payload=self._state_payload(
                        project_id, identity, AuthorizationState.REVOKED, None
                    ),
                )
                removed.add(identity)
        return removed

    def stage_revoke_affected(
        self,
        *,
        project_id: str,
        run_id: str,
        superseded_attempt_ids: tuple[str, ...],
        changed_step_ids: tuple[str, ...],
    ) -> None:
        revision, unused = read_authorization_index(
            self.records, self.workspace_id, project_id, run_id
        )
        removed = self._stage_revocations(
            project_id=project_id,
            run_id=run_id,
            unused=unused,
            superseded_attempt_ids=superseded_attempt_ids,
            changed_step_ids=changed_step_ids,
        )
        if removed:
            stage_authorization_index(
                self.unit,
                self.workspace_id,
                project_id,
                run_id,
                revision,
                tuple(value for value in unused if value not in removed),
            )

    def validate_occupation(
        self, *, project_id: str, attempt: Attempt, proof: Mapping[str, object]
    ) -> None:
        if attempt.authorization_ref is None:
            raise ApprovalRequired("original authorization occupation is unavailable")
        identity = attempt.authorization_ref.authorization_id
        grant, action = self._origin(project_id, identity)
        revision, state, occupied = self._state(project_id, identity)
        if (
            type(proof.get("grant_revision")) is not int
            or type(proof.get("state_revision")) is not int
            or proof
            != {
                "grant_id": identity,
                "grant_revision": 1,
                "grant_digest": _digest(grant),
                "state_revision": revision,
            }
            or state is not AuthorizationState.OCCUPIED
            or occupied != attempt.attempt_id
            or attempt_start_basis(replace(attempt, intent_digest=""))
            != attempt_start_basis(action.attempt)
            or attempt.intent_digest != execution_start_fingerprint(action.attempt, action.request)
        ):
            raise ApprovalRequired("saved occupation differs from its original grant and attempt")

    def revoke(
        self, *, project_id: str, authorization_id: str, intent_id: str, request_id: str
    ) -> None:
        self.approvals.require_actor(project_id)
        receipt = _identity("authorization-revoke-", self.workspace_id, project_id, intent_id)
        self.unit.begin(request_id, project_id)
        try:
            receipt_revision = self._revision(receipt)
            if receipt_revision:
                if receipt_revision != 1:
                    raise ApprovalRequired("revocation receipt requires its original revision")
                raw = self._read(project_id, receipt, 1)
                if raw != {
                    "schema_version": "aitest.authorization-revocation/1.0",
                    "workspace_id": self.workspace_id,
                    "project_id": project_id,
                    "authorization_id": authorization_id,
                    "intent_id": intent_id,
                }:
                    raise ApprovalConflict("authorization revocation intent has different input")
                self.unit.rollback(request_id)
                return
            grant, action = self._origin(project_id, authorization_id)
            index = (
                self._require_indexed(project_id, grant, action)
                if grant["schema_version"] == "aitest.action-authorization/1.1"
                else None
            )
            revision, state, occupied = self._state(project_id, authorization_id)
            require_unused(state, occupied)
            self.unit.stage_record(
                aggregate_kind="execution_authorization",
                record_id="authorization-state:" + authorization_id,
                expected_revision=revision,
                payload=self._state_payload(
                    project_id, authorization_id, AuthorizationState.REVOKED, None
                ),
            )
            self.unit.stage_record(
                aggregate_kind="execution_authorization",
                record_id=receipt,
                expected_revision=0,
                payload={
                    "schema_version": "aitest.authorization-revocation/1.0",
                    "workspace_id": self.workspace_id,
                    "project_id": project_id,
                    "authorization_id": authorization_id,
                    "intent_id": intent_id,
                },
            )
            if index is not None:
                revision, unused = index
                stage_authorization_index(
                    self.unit,
                    self.workspace_id,
                    project_id,
                    action.request.run_id,
                    revision,
                    tuple(value for value in unused if value != authorization_id),
                )
            self.unit.commit(request_id)
        except BaseException:
            self.unit.rollback(request_id)
            raise
