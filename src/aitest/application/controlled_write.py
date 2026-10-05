"""Atomic controlled record writes and exact saved origin, shared by registered actions."""

from collections.abc import Mapping
from dataclasses import dataclass

from aitest.application.approval_service import ApprovalService, _digest
from aitest.application.planning.basis_approval import SavedBasisApprovalResolver
from aitest.application.planning.controlled_publication import publication_content
from aitest.application.planning.publish import payload_digest
from aitest.application.ports import (
    ApprovalRecords,
    ControlledWriteProof,
    StageableWorkspaceUnitOfWork,
)
from aitest.application.project.serialization import (
    binding_from_payload,
    binding_to_payload,
    environment_from_payload,
    environment_to_payload,
)
from aitest.domain.approvals import (
    ActionBasis,
    ApprovalConflict,
    ApprovalMaterialRef,
    ApprovalRequired,
)


@dataclass(frozen=True, slots=True)
class ControlledWrite:
    action: str
    aggregate_kind: str
    record_id: str
    parameters: Mapping[str, object]
    payload: Mapping[str, object]
    project_revision: int
    expected_revision: int
    materials: tuple[ApprovalMaterialRef, ...] = ()


def controlled_write(
    action: str,
    project_id: str,
    parameters: Mapping[str, object],
    records: ApprovalRecords | None = None,
    workspace_id: str = "",
) -> ControlledWrite:
    """Only registered business adapters define canonical input and record identity."""
    if action in {"publish_rules", "publish_plan"}:
        project_revision, expected = (
            parameters.get("project_revision"),
            parameters.get("expected_revision"),
        )
        if (
            type(project_revision) is not int
            or project_revision < 1
            or type(expected) is not int
            or expected < 0
        ):
            raise ApprovalRequired("publication needs exact owner and warehouse revisions")
        try:
            publication = publication_content(action, project_id, parameters, records, workspace_id)
        except (ValueError, TypeError, KeyError, OSError) as error:
            raise ApprovalRequired("publication input or saved basis cannot be verified") from error
        return ControlledWrite(
            action,
            publication.aggregate_kind,
            publication.record_id,
            publication.parameters,
            publication.payload,
            project_revision,
            expected,
            publication.materials,
        )
    if action not in {"save_binding", "save_environment"}:
        raise ApprovalRequired("this action has no controlled record write adapter")
    field = "binding" if action == "save_binding" else "environment"
    if set(parameters) != {"project_revision", "expected_revision", field}:
        raise ApprovalRequired("controlled confirmation needs its exact declared inputs")
    project_revision, expected = parameters["project_revision"], parameters["expected_revision"]
    raw = parameters[field]
    if (
        type(project_revision) is not int
        or project_revision < 1
        or type(expected) is not int
        or expected < 0
        or not isinstance(raw, Mapping)
    ):
        raise ApprovalRequired("controlled confirmation requires exact warehouse revisions")
    try:
        if action == "save_binding":
            binding = binding_from_payload(raw)
            payload = binding_to_payload(binding)
            identity, kind = binding.binding_id, "binding"
            if (
                binding.project_id != project_id
                or set(raw) - set(payload)
                or raw.get("schema_version") != payload["schema_version"]
            ):
                raise ApprovalRequired("binding has another owner or unknown fields")
        else:
            environment = environment_from_payload(raw)
            payload = environment_to_payload(environment, project_id=project_id)
            identity, kind = environment.environment_id, "environment"
            if dict(raw) != payload or environment.revision != expected + 1:
                raise ApprovalRequired("environment owner, declaration or revision differs")
    except (ValueError, TypeError, KeyError) as error:
        raise ApprovalRequired("controlled input cannot be verified") from error
    return ControlledWrite(
        action,
        kind,
        identity,
        {"project_revision": project_revision, "expected_revision": expected, field: payload},
        payload,
        project_revision,
        expected,
    )


class SavedControlledWriteResolver:
    actions = frozenset({"save_binding", "save_environment", "publish_rules", "publish_plan"})

    def __init__(self, records: ApprovalRecords, workspace_id: str) -> None:
        self.records, self.workspace_id = records, workspace_id
        self.materials = SavedBasisApprovalResolver(records, workspace_id)
        self.proof: ControlledWriteProof | None = None

    def normalize(
        self, action: str, project: str, parameters: Mapping[str, object]
    ) -> ControlledWrite:
        return controlled_write(action, project, parameters, self.records, self.workspace_id)

    def resolve(
        self,
        *,
        project_id: str,
        intent_id: str,
        action: str,
        target: str,
        parameters: Mapping[str, object],
    ) -> ActionBasis:
        write = self.normalize(action, project_id, parameters)
        if target != write.record_id:
            raise ApprovalRequired("controlled write target differs from its business record")
        try:
            current = self.records.current_revision(
                aggregate_kind=write.aggregate_kind, record_id=write.record_id
            )
            owner = self.records.current_revision(aggregate_kind="project", record_id=project_id)
            if (
                type(current) is not int
                or current != write.expected_revision
                or type(owner) is not int
                or owner != write.project_revision
            ):
                raise ApprovalRequired("record or owner changed; review current saved material")
            project = self.materials._read("project", project_id, owner, project_id)
            if project.get("workspace_id") != self.workspace_id:
                raise ApprovalRequired("controlled write owner belongs to another workspace")
            references = [ApprovalMaterialRef("project", project_id, owner, _digest(project))]
            if current:
                previous = self.materials._read(
                    write.aggregate_kind, write.record_id, current, project_id
                )
                references.append(
                    ApprovalMaterialRef(
                        write.aggregate_kind, write.record_id, current, _digest(previous)
                    )
                )
            for reference in write.materials:
                if reference.aggregate_kind == "rule_version":
                    if self.proof is None:
                        raise ApprovalRequired("published rule confirmation reader is unavailable")
                    raw = self.materials._read(
                        reference.aggregate_kind,
                        reference.record_id,
                        reference.record_revision,
                        project_id,
                    )
                    self.proof.validate_saved_write(
                        project_id=project_id,
                        action="publish_rules",
                        aggregate_kind="rule_version",
                        record_id=reference.record_id,
                        record_revision=reference.record_revision,
                        payload=raw,
                    )
                references.append(reference)
        except (ValueError, OSError, TypeError, KeyError) as error:
            raise ApprovalRequired("controlled write's saved basis is unavailable") from error
        return ActionBasis(
            self.workspace_id,
            project_id,
            intent_id,
            action,
            target,
            _digest(dict(write.parameters)),
            "none",
            tuple(references),
        )


_ORIGIN_FIELDS = frozenset(
    {
        "workspace_id",
        "approval_action",
        "approval_confirmation_id",
        "approval_intent_id",
        "approval_project_revision",
        "approval_expected_revision",
        "approval_commit_seq",
    }
)


@dataclass(frozen=True, slots=True)
class ControlledWriteService:
    unit: StageableWorkspaceUnitOfWork
    approvals: ApprovalService
    resolver: SavedControlledWriteResolver

    def identity(self, project: str, intent: str) -> str:
        return "controlled-write-" + _digest([self.approvals.workspace_id, project, intent])[7:]

    def _result(self, write: ControlledWrite, revision: int) -> Mapping[str, object]:
        if write.action in {"publish_rules", "publish_plan"}:
            payload = self.resolver.materials._read(
                write.aggregate_kind, write.record_id, revision, str(write.payload["project_id"])
            )
            result = {
                "published": True,
                "kind": write.aggregate_kind,
                "revision": payload["revision"],
                "record_revision": revision,
                "confirmation_id": payload["approval_commit_seq"],
            }
            if write.action == "publish_rules":
                result.update(rule_id=write.record_id, digest=payload_digest(payload))
            else:
                result.update(
                    {
                        key: payload[key]
                        for key in (
                            "plan_id",
                            "scope_id",
                            "scope_revision",
                            "case_revisions",
                            "rule_revisions",
                            "template_versions",
                        )
                    }
                )
            return result
        return {
            "aggregate_kind": write.aggregate_kind,
            "record_id": write.record_id,
            "revision": revision,
        }

    def save(
        self,
        *,
        project_id: str,
        request_id: str,
        intent_id: str,
        action: str,
        expected_revision: int,
        parameters: Mapping[str, object],
        challenge_id: str | None,
    ) -> Mapping[str, object]:
        self.approvals.require_actor(project_id)
        write = self.resolver.normalize(action, project_id, parameters)
        if type(expected_revision) is not int or expected_revision != write.expected_revision:
            raise ApprovalRequired("command warehouse revision differs from reviewed input")
        original = self._original(project_id, intent_id, write)
        if original is not None:
            return original
        self.unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            original = self._original(project_id, intent_id, write)
            if original is not None:
                self.unit.rollback(request_id)
                return original
            if not challenge_id:
                raise ApprovalRequired("review this controlled write's core challenge")
            challenge = self.approvals.read_challenge(
                project_id=project_id, challenge_id=challenge_id
            )
            if (challenge.basis.intent_id, challenge.basis.action, challenge.basis.target) != (
                intent_id,
                action,
                write.record_id,
            ):
                raise ApprovalRequired("this challenge belongs to another business write")
            confirmation = self.approvals.stage_confirmation(
                project_id=project_id,
                challenge_id=challenge_id,
                confirmation_intent_id=intent_id,
                parameters=write.parameters,
                trailing_records=2,
            )
            payload = dict(write.payload) | {
                "workspace_id": self.approvals.workspace_id,
                "approval_action": action,
                "approval_confirmation_id": confirmation.confirmation_id,
                "approval_intent_id": intent_id,
                "approval_project_revision": write.project_revision,
                "approval_expected_revision": write.expected_revision,
                "approval_commit_seq": confirmation.confirmed_at_commit,
            }
            if action in {"publish_rules", "publish_plan"}:
                payload["approval_parameters"] = dict(write.parameters)
            revision = self.unit.stage_record(
                aggregate_kind=write.aggregate_kind,
                record_id=write.record_id,
                expected_revision=write.expected_revision,
                payload=payload,
            )
            if type(revision) is not int or revision != write.expected_revision + 1:
                raise ApprovalRequired("staged controlled record revision cannot be verified")
            if self.unit.next_commit_seq() != confirmation.confirmed_at_commit:
                raise ApprovalRequired("the controlled write's atomic batch layout changed")
            self.unit.stage_record(
                aggregate_kind="approval_intent",
                record_id=self.identity(project_id, intent_id),
                expected_revision=0,
                payload={
                    "schema_version": "aitest.controlled-write-intent/1.0",
                    "workspace_id": self.approvals.workspace_id,
                    "project_id": project_id,
                    "action": action,
                    "intent_id": intent_id,
                    "input_digest": _digest(dict(write.parameters)),
                    "aggregate_kind": write.aggregate_kind,
                    "record_id": write.record_id,
                    "record_revision": revision,
                    "record_digest": _digest(payload),
                    "confirmation_id": confirmation.confirmation_id,
                    "created_at_commit": confirmation.confirmed_at_commit,
                },
            )
            self.unit.commit(request_id)
            return self._result(write, revision)
        except BaseException as error:
            try:
                self.unit.rollback(request_id)
            except Exception as cleanup:
                raise error from cleanup
            raise

    def _original(
        self, project: str, intent: str, write: ControlledWrite
    ) -> Mapping[str, object] | None:
        receipt = self.approvals._current(
            "approval_intent", self.identity(project, intent), project
        )
        if receipt is None:
            return None
        if (receipt.get("schema_version"), receipt.get("intent_id")) != (
            "aitest.controlled-write-intent/1.0",
            intent,
        ):
            raise ApprovalRequired("controlled write receipt has another original intent")
        if receipt.get("input_digest") != _digest(dict(write.parameters)):
            raise ApprovalConflict("controlled write intent has different reviewed input")
        revision = receipt.get("record_revision")
        if (
            (receipt.get("action"), receipt.get("aggregate_kind"), receipt.get("record_id"))
            != (write.action, write.aggregate_kind, write.record_id)
            or type(revision) is not int
            or revision != write.expected_revision + 1
        ):
            raise ApprovalRequired("original controlled write result reference differs")
        payload = self.resolver.materials._read(
            write.aggregate_kind, write.record_id, revision, project
        )
        if payload.get("approval_intent_id") != intent:
            raise ApprovalRequired("original controlled result belongs to another saved intent")
        self.validate_saved_write(
            project_id=project,
            action=write.action,
            aggregate_kind=write.aggregate_kind,
            record_id=write.record_id,
            record_revision=revision,
            payload=payload,
        )
        return self._result(write, revision)

    def validate_saved_write(
        self,
        *,
        project_id: str,
        action: str,
        aggregate_kind: str,
        record_id: str,
        record_revision: int,
        payload: Mapping[str, object],
    ) -> None:
        try:
            self._validate(project_id, action, aggregate_kind, record_id, record_revision, payload)
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise ApprovalRequired("exact controlled record origin is unavailable") from error

    def _validate(
        self,
        project: str,
        action: str,
        kind: str,
        identity: str,
        revision: int,
        payload: Mapping[str, object],
    ) -> None:
        origin_fields = _ORIGIN_FIELDS
        if action in {"publish_rules", "publish_plan"}:
            origin_fields = origin_fields | {"approval_parameters"}
            parameters = payload.get("approval_parameters")
            if not isinstance(parameters, Mapping):
                raise ApprovalRequired("publication has no exact frozen input")
        else:
            parameters = {
                "project_revision": payload.get("approval_project_revision"),
                "expected_revision": payload.get("approval_expected_revision"),
                ("environment" if action == "save_environment" else "binding"): {
                    key: value for key, value in payload.items() if key not in _ORIGIN_FIELDS
                },
            }
        write = self.resolver.normalize(action, project, parameters)
        intent, confirmation_id = (
            payload.get("approval_intent_id"),
            payload.get("approval_confirmation_id"),
        )
        if (
            set(payload) != set(write.payload) | origin_fields
            or {key: value for key, value in payload.items() if key not in origin_fields}
            != dict(write.payload)
            or (kind, identity) != (write.aggregate_kind, write.record_id)
            or type(revision) is not int
            or revision != write.expected_revision + 1
            or payload.get("workspace_id") != self.approvals.workspace_id
            or payload.get("approval_action") != action
            or not isinstance(intent, str)
            or not intent
            or not isinstance(confirmation_id, str)
            or not confirmation_id
        ):
            raise ApprovalRequired("controlled record differs from its declared saved origin")
        saved = self.resolver.materials._read(kind, identity, revision, project)
        if dict(payload) != saved:
            raise ApprovalRequired("controlled record differs from its exact warehouse material")
        confirmation = self.approvals.read_confirmation(
            project_id=project, confirmation_id=confirmation_id
        )
        receipt = self.approvals._current(
            "approval_intent", self.identity(project, intent), project
        )
        expected_refs = {("project", project, write.project_revision)}
        if write.expected_revision:
            expected_refs.add((kind, identity, write.expected_revision))
        expected_refs.update(
            (r.aggregate_kind, r.record_id, r.record_revision) for r in write.materials
        )
        if (
            receipt is None
            or receipt.get("schema_version") != "aitest.controlled-write-intent/1.0"
            or (
                receipt.get("intent_id"),
                receipt.get("action"),
                receipt.get("aggregate_kind"),
                receipt.get("record_id"),
                receipt.get("record_revision"),
            )
            != (intent, action, kind, identity, revision)
            or type(receipt.get("record_revision")) is not int
            or receipt.get("input_digest") != _digest(dict(write.parameters))
            or receipt.get("record_digest") != _digest(dict(payload))
            or receipt.get("confirmation_id") != confirmation_id
            or receipt.get("created_at_commit") != confirmation.confirmed_at_commit
            or payload.get("approval_commit_seq") != confirmation.confirmed_at_commit
            or (
                confirmation.confirmation_intent_id,
                confirmation.basis.intent_id,
                confirmation.basis.action,
                confirmation.basis.target,
            )
            != (intent, intent, action, identity)
            or confirmation.basis.input_digest != _digest(dict(write.parameters))
            or confirmation.basis.credential_scope_ref != "none"
            or {
                (r.aggregate_kind, r.record_id, r.record_revision)
                for r in confirmation.basis.materials
            }
            != expected_refs
        ):
            raise ApprovalRequired(
                "controlled record, exact receipt and frozen confirmation differ"
            )
