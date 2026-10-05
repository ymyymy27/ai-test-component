"""Save and consume an outbound policy's exact controlled confirmation atomically."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace

from pydantic import TypeAdapter

from aitest.application.approval_service import ApprovalService, _digest
from aitest.application.controlled_write import SavedControlledWriteResolver
from aitest.application.planning.basis_approval import SavedBasisApprovalResolver
from aitest.application.planning.model_orchestration import policy_record_id
from aitest.application.ports import ApprovalRecords, StageableWorkspaceUnitOfWork
from aitest.domain.approvals import (
    ActionBasis,
    ActionConfirmation,
    ApprovalConflict,
    ApprovalMaterialRef,
    ApprovalRequired,
)
from aitest.domain.planning.model_outbound import (
    ModelEndpoint,
    ModelOutboundPolicy,
    OutboundConfirmation,
    endpoint_digest,
    material_kinds_digest,
)


def policy_payload(policy: ModelOutboundPolicy) -> dict[str, object]:
    payload = dict(TypeAdapter(ModelOutboundPolicy).dump_python(policy, mode="json"))
    payload["allowed_material_kinds"] = sorted(kind.value for kind in policy.allowed_material_kinds)
    payload["revoked_material_kinds"] = sorted(kind.value for kind in policy.revoked_material_kinds)
    return payload


def policy_parameters(project_id: str, parameters: Mapping[str, object]) -> dict[str, object]:
    """Normalize actual policy meaning; a client confirmation is never permission."""
    if set(parameters) != {"project_revision", "policy"}:
        raise ApprovalRequired("model policy confirmation needs its exact declared inputs")
    project_revision, raw = parameters["project_revision"], parameters["policy"]
    if type(project_revision) is not int or project_revision < 1 or not isinstance(raw, Mapping):
        raise ApprovalRequired("model policy needs an exact saved project revision")
    if set(raw) - {field.name for field in fields(ModelOutboundPolicy)}:
        raise ApprovalRequired("model policy input has unknown fields")
    for name, record_type in (("endpoint", ModelEndpoint), ("confirmation", OutboundConfirmation)):
        nested = raw.get(name)
        if nested is not None and (
            not isinstance(nested, Mapping) or set(nested) - {f.name for f in fields(record_type)}
        ):
            raise ApprovalRequired("model policy input has unknown nested fields")
    try:
        policy = TypeAdapter(ModelOutboundPolicy).validate_json(json.dumps(dict(raw)), strict=True)
    except (TypeError, ValueError) as error:
        raise ApprovalRequired("model policy input cannot be verified") from error
    if policy.project_id != project_id:
        raise ApprovalRequired("model policy belongs to another project")
    if policy.endpoint.purpose != "model":
        raise ApprovalRequired("the model policy credential purpose differs")
    return {
        "project_revision": project_revision,
        "policy": policy_payload(replace(policy, confirmation=None)),
    }


class SavedModelPolicyApprovalResolver:
    def __init__(
        self, records: ApprovalRecords, workspace_id: str, credential_scope_ref: str
    ) -> None:
        self.records, self.workspace_id = records, workspace_id
        self.credential_scope_ref = credential_scope_ref
        self.materials = SavedBasisApprovalResolver(records, workspace_id)

    def resolve(
        self,
        *,
        project_id: str,
        intent_id: str,
        action: str,
        target: str,
        parameters: Mapping[str, object],
    ) -> ActionBasis:
        if action != "save_model_outbound_policy":
            raise ApprovalRequired("this action has no model policy confirmation adapter")
        normalized = policy_parameters(project_id, parameters)
        raw = normalized["policy"]
        assert isinstance(raw, dict)
        policy = TypeAdapter(ModelOutboundPolicy).validate_python(raw)
        expected = policy.revision - 1
        identity = policy_record_id(project_id)
        if target != identity:
            raise ApprovalRequired("the model policy target differs")
        try:
            current = self.records.current_revision(
                aggregate_kind="model_outbound_policy", record_id=identity
            )
            project_current = self.records.current_revision(
                aggregate_kind="project", record_id=project_id
            )
            if type(current) is not int or current != expected or type(project_current) is not int:
                raise ApprovalRequired("model policy changed; review its current revision")
            if project_current != normalized["project_revision"]:
                raise ApprovalRequired("the saved project changed; review the current policy owner")
            project = self.materials._read("project", project_id, project_current, project_id)
            if project.get("workspace_id") != self.workspace_id:
                raise ApprovalRequired("model policy owner belongs to another workspace")
            references = [
                ApprovalMaterialRef("project", project_id, project_current, _digest(project))
            ]
            if expected:
                previous = self.materials._read(
                    "model_outbound_policy", identity, expected, project_id
                )
                references.append(
                    ApprovalMaterialRef(
                        "model_outbound_policy", identity, expected, _digest(previous)
                    )
                )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ApprovalRequired("saved policy approval material is unavailable") from error
        return ActionBasis(
            self.workspace_id,
            project_id,
            intent_id,
            action,
            target,
            _digest(normalized),
            self.credential_scope_ref,
            tuple(references),
        )


class SavedHumanActionResolver:
    def __init__(
        self,
        basis: SavedBasisApprovalResolver,
        policies: SavedModelPolicyApprovalResolver,
        writes: SavedControlledWriteResolver,
    ) -> None:
        self.basis, self.policies = basis, policies
        self.writes = writes

    def resolve(
        self,
        *,
        project_id: str,
        intent_id: str,
        action: str,
        target: str,
        parameters: Mapping[str, object],
    ) -> ActionBasis:
        resolver = (
            self.writes
            if action in self.writes.actions
            else self.policies
            if action == "save_model_outbound_policy"
            else self.basis
        )
        return resolver.resolve(
            project_id=project_id,
            intent_id=intent_id,
            action=action,
            target=target,
            parameters=parameters,
        )


_POLICY_METADATA = frozenset(
    {"workspace_id", "approval_confirmation_id", "approval_intent_id", "approval_project_revision"}
)


@dataclass(frozen=True, slots=True)
class ModelPolicyConfirmationService:
    unit: StageableWorkspaceUnitOfWork
    approvals: ApprovalService
    resolver: SavedModelPolicyApprovalResolver

    def save(
        self,
        *,
        project_id: str,
        request_id: str,
        intent_id: str,
        expected_revision: int,
        parameters: Mapping[str, object],
        challenge_id: str | None,
    ) -> Mapping[str, object]:
        self.approvals.require_actor(project_id)
        normalized = policy_parameters(project_id, parameters)
        policy = TypeAdapter(ModelOutboundPolicy).validate_python(normalized["policy"])
        if type(expected_revision) is not int or expected_revision != policy.revision - 1:
            raise ApprovalRequired("policy revision differs from expected warehouse revision")
        original = self._original(project_id, intent_id, normalized)
        if original is not None:
            return original
        self.unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            original = self._original(project_id, intent_id, normalized)
            if original is not None:
                self.unit.rollback(request_id)
                return original
            if not challenge_id:
                raise ApprovalRequired("review this model policy's core challenge")
            challenge = self.approvals.read_challenge(
                project_id=project_id, challenge_id=challenge_id
            )
            if (challenge.basis.intent_id, challenge.basis.action, challenge.basis.target) != (
                intent_id,
                "save_model_outbound_policy",
                policy_record_id(project_id),
            ):
                raise ApprovalRequired(
                    "this challenge belongs to another policy or business intent"
                )
            confirmation = self.approvals.stage_confirmation(
                project_id=project_id,
                challenge_id=challenge_id,
                confirmation_intent_id=intent_id,
                parameters=normalized,
                trailing_records=2,
            )
            policy = replace(
                policy,
                confirmation=OutboundConfirmation(
                    confirmation.confirmation_id,
                    endpoint_digest(policy.endpoint),
                    material_kinds_digest(policy),
                    policy.source_snippets_enabled,
                    confirmation.confirmed_at_commit,
                ),
            )
            payload = policy_payload(policy) | {
                "workspace_id": self.approvals.workspace_id,
                "approval_confirmation_id": confirmation.confirmation_id,
                "approval_intent_id": intent_id,
                "approval_project_revision": normalized["project_revision"],
            }
            revision = self.unit.stage_record(
                aggregate_kind="model_outbound_policy",
                record_id=policy_record_id(project_id),
                expected_revision=expected_revision,
                payload=payload,
            )
            if type(revision) is not int or revision != policy.revision:
                raise ApprovalRequired("the staged policy warehouse revision cannot be verified")
            if self.unit.next_commit_seq() != confirmation.confirmed_at_commit:
                raise ApprovalRequired("the atomic model policy batch layout changed")
            self.unit.stage_record(
                aggregate_kind="approval_intent",
                record_id=self._identity(project_id, intent_id),
                expected_revision=0,
                payload={
                    "schema_version": "aitest.model-policy-intent/1.0",
                    "project_id": project_id,
                    "workspace_id": self.approvals.workspace_id,
                    "intent_id": intent_id,
                    "input_digest": _digest(normalized),
                    "policy_record_id": policy_record_id(project_id),
                    "policy_record_revision": revision,
                    "policy_record_digest": _digest(payload),
                    "confirmation_id": confirmation.confirmation_id,
                    "created_at_commit": confirmation.confirmed_at_commit,
                },
            )
            self.unit.commit(request_id)
            return self._result(project_id, revision)
        except BaseException as error:
            try:
                self.unit.rollback(request_id)
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise

    def _identity(self, project: str, intent: str) -> str:
        return "model-policy-save-" + _digest([self.approvals.workspace_id, project, intent])[7:]

    def _result(self, project: str, revision: int) -> Mapping[str, object]:
        return {
            "aggregate_kind": "model_outbound_policy",
            "record_id": policy_record_id(project),
            "revision": revision,
        }

    def _original(
        self, project: str, intent: str, normalized: Mapping[str, object]
    ) -> Mapping[str, object] | None:
        receipt = self.approvals._current(
            "approval_intent", self._identity(project, intent), project
        )
        if receipt is None:
            return None
        if (
            receipt.get("schema_version") != "aitest.model-policy-intent/1.0"
            or receipt.get("intent_id") != intent
            or receipt.get("policy_record_id") != policy_record_id(project)
        ):
            raise ApprovalRequired("the original policy receipt belongs to another saved intent")
        if receipt.get("input_digest") != _digest(normalized):
            raise ApprovalConflict("the model policy save intent has different inputs")
        revision = receipt.get("policy_record_revision")
        if type(revision) is not int or revision < 1:
            raise ApprovalRequired("the original policy result reference is unavailable")
        payload = self.resolver.materials._read(
            "model_outbound_policy", policy_record_id(project), revision, project
        )
        if (
            payload.get("approval_intent_id") != intent
            or receipt.get("policy_record_digest") != _digest(payload)
            or receipt.get("confirmation_id") != payload.get("approval_confirmation_id")
        ):
            raise ApprovalRequired("the original policy result differs from its exact intent")
        self._saved_origin(project, revision, payload)
        return self._result(project, revision)

    def validate_model_policy(
        self,
        *,
        project_id: str,
        record_revision: int,
        payload: Mapping[str, object],
    ) -> None:
        """Exact saved origin is required before sending; legacy DTO consent is insufficient."""
        origin = self._saved_origin(project_id, record_revision, payload)
        if origin.basis.credential_scope_ref != self.resolver.credential_scope_ref:
            raise ApprovalRequired(
                "the current model credential scope needs new controlled consent"
            )

    def _saved_origin(
        self,
        project_id: str,
        record_revision: int,
        payload: Mapping[str, object],
    ) -> ActionConfirmation:
        """Read frozen historical proof without turning it into current sending permission."""
        try:
            return self._validate_model_policy(project_id, record_revision, payload)
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise ApprovalRequired("the exact model policy origin is unavailable") from error

    def _validate_model_policy(
        self,
        project_id: str,
        record_revision: int,
        payload: Mapping[str, object],
    ) -> ActionConfirmation:
        if set(payload) != {f.name for f in fields(ModelOutboundPolicy)} | _POLICY_METADATA:
            raise ApprovalRequired("the saved model policy has no complete controlled origin")
        normalized = policy_parameters(
            project_id,
            {
                "project_revision": payload.get("approval_project_revision"),
                "policy": {k: v for k, v in payload.items() if k not in _POLICY_METADATA},
            },
        )
        policy = TypeAdapter(ModelOutboundPolicy).validate_python(payload)
        if (
            type(record_revision) is not int
            or record_revision != policy.revision
            or policy.confirmation is None
        ):
            raise ApprovalRequired("the saved policy revision/confirmation cannot be verified")
        confirmation_id, intent = (
            payload.get("approval_confirmation_id"),
            payload.get("approval_intent_id"),
        )
        if not isinstance(confirmation_id, str) or not isinstance(intent, str):
            raise ApprovalRequired("the saved policy has no exact confirmation intent")
        saved = self.resolver.materials._read(
            "model_outbound_policy", policy_record_id(project_id), record_revision, project_id
        )
        if dict(payload) != saved or payload.get("workspace_id") != self.approvals.workspace_id:
            raise ApprovalRequired("model policy differs from its exact saved material")
        origin = self.approvals.read_confirmation(
            project_id=project_id, confirmation_id=confirmation_id
        )
        receipt = self.approvals._current(
            "approval_intent", self._identity(project_id, intent), project_id
        )
        expected_refs = {("project", project_id, normalized["project_revision"])}
        if record_revision > 1:
            expected_refs.add(
                ("model_outbound_policy", policy_record_id(project_id), record_revision - 1)
            )
        if (
            receipt is None
            or receipt.get("schema_version") != "aitest.model-policy-intent/1.0"
            or receipt.get("intent_id") != intent
            or receipt.get("input_digest") != _digest(normalized)
            or receipt.get("policy_record_id") != policy_record_id(project_id)
            or receipt.get("policy_record_revision") != record_revision
            or type(receipt.get("policy_record_revision")) is not int
            or receipt.get("policy_record_digest") != _digest(dict(payload))
            or receipt.get("confirmation_id") != confirmation_id
            or receipt.get("created_at_commit") != origin.confirmed_at_commit
            or (
                origin.confirmation_intent_id,
                origin.basis.intent_id,
                origin.basis.action,
                origin.basis.target,
            )
            != (intent, intent, "save_model_outbound_policy", policy_record_id(project_id))
            or origin.basis.input_digest != _digest(normalized)
            or not origin.basis.credential_scope_ref.startswith("model:")
            or not origin.basis.credential_scope_ref.removeprefix("model:")
            or {(r.aggregate_kind, r.record_id, r.record_revision) for r in origin.basis.materials}
            != expected_refs
            or policy.confirmation.confirmation_id != confirmation_id
            or policy.confirmation.confirmed_at_commit != origin.confirmed_at_commit
            or not policy.confirmation.covers(policy)
        ):
            raise ApprovalRequired("the policy differs from its saved controlled consent")
        return origin
