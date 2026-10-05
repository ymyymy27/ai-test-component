"""Persist a controlled single-use challenge and exact user-confirmation receipt.

The resolver reads saved action material only. External source/environment work
and actual user-interaction collection belong to their respective entry ports.
"""

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from hashlib import sha256
from typing import Any

from pydantic import TypeAdapter

from aitest.application.ports import (
    ApprovalActionResolver,
    ApprovalIdentitySource,
    ApprovalRecords,
    Clock,
    ControlledActorContext,
    StageableWorkspaceUnitOfWork,
)
from aitest.contracts.commands import HUMAN_ACTIONS
from aitest.domain.approvals import (
    ActionBasis,
    ActionConfirmation,
    ApprovalChallenge,
    ApprovalConflict,
    ApprovalMaterialRef,
    ApprovalRequired,
    ChallengeState,
    UserInteraction,
    require_challenge_confirmation,
    require_controlled_actor,
)


def _digest(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + sha256(raw).hexdigest()


def _payload(value: object) -> dict[str, Any]:
    return dict(TypeAdapter(type(value)).dump_python(value, mode="json"))


def _decode[RecordT](record_type: type[RecordT], raw: object) -> RecordT:
    try:
        if (
            not is_dataclass(record_type)
            or not isinstance(raw, dict)
            or set(raw) != {field.name for field in fields(record_type)}
        ):
            raise ValueError("approval record fields differ")
        if record_type in {ApprovalChallenge, ActionConfirmation}:
            basis = raw.get("basis")
            if not isinstance(basis, dict) or set(basis) != {
                field.name for field in fields(ActionBasis)
            }:
                raise ValueError("approval basis fields differ")
            materials = basis.get("materials")
            if (
                not isinstance(materials, list)
                or not materials
                or any(
                    not isinstance(value, dict)
                    or set(value) != {field.name for field in fields(ApprovalMaterialRef)}
                    for value in materials
                )
            ):
                raise ValueError("approval material fields differ")
        return TypeAdapter(record_type).validate_json(json.dumps(raw), strict=True)
    except (TypeError, ValueError) as error:
        raise ApprovalRequired("saved approval material is malformed or unknown") from error


class ApprovalService:
    def __init__(
        self,
        *,
        unit: StageableWorkspaceUnitOfWork,
        records: ApprovalRecords,
        actors: ControlledActorContext,
        resolver: ApprovalActionResolver,
        identities: ApprovalIdentitySource,
        clock: Clock,
        workspace_id: str,
    ) -> None:
        self.unit, self.records, self.actors = unit, records, actors
        self.resolver, self.identities, self.clock = resolver, identities, clock
        self.workspace_id = workspace_id

    def _read(self, kind: str, identity: str, revision: int, project_id: str) -> dict[str, Any]:
        if type(revision) is not int or revision < 1:
            raise ApprovalRequired("approval material has no exact saved revision")
        record = self.records.read(aggregate_kind=kind, record_id=identity, revision=revision)
        if (
            getattr(record, "aggregate_kind", None),
            getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (kind, identity, revision):
            raise ApprovalRequired("approval record envelope cannot be verified")
        if type(getattr(record, "revision", None)) is not int:
            raise ApprovalRequired("approval record revision cannot be verified")
        raw = getattr(record, "payload", None)
        if not isinstance(raw, Mapping) or raw.get("project_id") != project_id:
            raise ApprovalRequired("approval record has an unknown project")
        if raw.get("workspace_id") != self.workspace_id:
            raise ApprovalRequired("approval record belongs to another workspace")
        layouts = {
            "aitest.approval-challenge/1.0": {"challenge"},
            "aitest.approval-preparation/1.0": {
                "preparation_intent_id",
                "input_digest",
                "challenge_id",
            },
            "aitest.approval-interaction/1.0": {"interaction", "confirmation_id"},
            "aitest.action-confirmation/1.0": {"confirmation"},
            "aitest.approval-confirmation-intent/1.0": {"input_digest", "confirmation_id"},
        }
        schema = raw.get("schema_version")
        if (
            not isinstance(schema, str)
            or schema not in layouts
            or set(raw) != (layouts[schema] | {"schema_version", "project_id", "workspace_id"})
        ):
            raise ApprovalRequired("saved approval record has an unknown schema or fields")
        if (
            kind != "approval_challenge"
            and self.records.current_revision(aggregate_kind=kind, record_id=identity) != 1
        ):
            raise ApprovalRequired("an immutable approval record was revised")
        return dict(raw)

    def _current(self, kind: str, identity: str, project_id: str) -> dict[str, Any] | None:
        revision = self.records.current_revision(aggregate_kind=kind, record_id=identity)
        if type(revision) is not int or revision < 0:
            raise ApprovalRequired("approval record revision cannot be verified")
        return self._read(kind, identity, revision, project_id) if revision else None

    def read_challenge(self, *, project_id: str, challenge_id: str) -> ApprovalChallenge:
        raw = self._current("approval_challenge", challenge_id, project_id)
        if raw is None or raw.get("schema_version") != "aitest.approval-challenge/1.0":
            raise ApprovalRequired("the exact core challenge is unavailable")
        challenge = _decode(ApprovalChallenge, raw.get("challenge"))
        if (challenge.challenge_id, challenge.basis.workspace_id, challenge.basis.project_id) != (
            challenge_id,
            self.workspace_id,
            project_id,
        ):
            raise ApprovalRequired("approval challenge identity cannot be verified")
        revision = self.records.current_revision(
            aggregate_kind="approval_challenge", record_id=challenge_id
        )
        if (challenge.state is ChallengeState.PENDING and revision != 1) or (
            challenge.state is not ChallengeState.PENDING and revision != 2
        ):
            raise ApprovalRequired("approval challenge has an unknown state history")
        return challenge

    def _prepared(self, project: str, identity: str, fingerprint: str) -> ApprovalChallenge | None:
        raw = self._current("approval_intent", identity, project)
        if raw is None:
            return None
        if raw.get("schema_version") != "aitest.approval-preparation/1.0":
            raise ApprovalRequired("approval preparation receipt cannot be verified")
        if raw.get("input_digest") != fingerprint:
            raise ApprovalConflict("approval preparation intent has different frozen input")
        challenge_id = raw.get("challenge_id")
        if not isinstance(challenge_id, str) or not challenge_id.strip():
            raise ApprovalRequired("approval preparation has no exact challenge")
        challenge = self.read_challenge(project_id=project, challenge_id=challenge_id)
        if (
            _digest(
                [
                    _payload(challenge.basis),
                    challenge.origin_session_id,
                    challenge.origin_entry_kind.value,
                ]
            )
            != fingerprint
        ):
            raise ApprovalRequired("preparation receipt differs from its exact challenge")
        return challenge

    def prepare(
        self,
        *,
        project_id: str,
        action_intent_id: str,
        preparation_intent_id: str,
        request_id: str,
        action: str,
        target: str,
        parameters: Mapping[str, object],
    ) -> ApprovalChallenge:
        actor = self.actors.current()
        require_controlled_actor(actor, workspace_id=self.workspace_id, project_id=project_id)
        if action not in HUMAN_ACTIONS:
            raise ValueError("this action has no human-confirmation capability")
        basis = self.resolver.resolve(
            project_id=project_id,
            intent_id=action_intent_id,
            action=action,
            target=target,
            parameters=parameters,
        )
        if (basis.workspace_id, basis.project_id, basis.intent_id, basis.action, basis.target) != (
            self.workspace_id,
            project_id,
            action_intent_id,
            action,
            target,
        ):
            raise ApprovalRequired("saved action resolver returned another business identity")
        identity = (
            "approval-prepare-"
            + _digest([self.workspace_id, project_id, preparation_intent_id])[7:]
        )
        fingerprint = _digest([_payload(basis), actor.session_id, actor.entry_kind.value])
        original = self._prepared(project_id, identity, fingerprint)
        if original is not None:
            return original
        challenge = ApprovalChallenge(
            self.identities.create(),
            basis,
            actor.session_id,
            actor.entry_kind,
            ChallengeState.PENDING,
            self.clock.now(),
        )
        self.unit.begin(request_id, project_id, intent_id=preparation_intent_id)
        try:
            original = self._prepared(project_id, identity, fingerprint)
            if original is not None:
                self.unit.rollback(request_id)
                return original
            if (
                self.actors.current() != actor
                or self.resolver.resolve(
                    project_id=project_id,
                    intent_id=action_intent_id,
                    action=action,
                    target=target,
                    parameters=parameters,
                )
                != basis
            ):
                raise ApprovalRequired("action basis changed before challenge publication")
            self._stage_challenge(challenge, expected_revision=0)
            self.unit.stage_record(
                aggregate_kind="approval_intent",
                record_id=identity,
                expected_revision=0,
                payload={
                    "schema_version": "aitest.approval-preparation/1.0",
                    "project_id": project_id,
                    "workspace_id": self.workspace_id,
                    "preparation_intent_id": preparation_intent_id,
                    "input_digest": fingerprint,
                    "challenge_id": challenge.challenge_id,
                },
            )
            self.unit.commit(request_id)
        except BaseException as error:
            self._rollback(request_id, error)
            raise
        return challenge

    def confirm(
        self,
        *,
        project_id: str,
        challenge_id: str,
        confirmation_intent_id: str,
        request_id: str,
        parameters: Mapping[str, object],
    ) -> ActionConfirmation:
        self.require_actor(project_id)
        identity, fingerprint = self._confirmation_identity(
            project_id, confirmation_intent_id, challenge_id, parameters
        )
        original = self._confirmed(project_id, identity, fingerprint)
        if original is not None:
            return original
        self.unit.begin(request_id, project_id, intent_id=confirmation_intent_id)
        try:
            confirmation = self.stage_confirmation(
                project_id=project_id,
                challenge_id=challenge_id,
                confirmation_intent_id=confirmation_intent_id,
                parameters=parameters,
            )
            self.unit.commit(request_id)
        except BaseException as error:
            self._rollback(request_id, error)
            raise
        return confirmation

    def require_actor(self, project_id: str) -> None:
        require_controlled_actor(
            self.actors.current(), workspace_id=self.workspace_id, project_id=project_id
        )

    def _confirmation_identity(
        self, project: str, intent: str, challenge: str, parameters: Mapping[str, object]
    ) -> tuple[str, str]:
        identity = "approval-confirm-" + _digest([self.workspace_id, project, intent])[7:]
        return identity, _digest([challenge, _digest(dict(parameters))])

    def stage_confirmation(
        self,
        *,
        project_id: str,
        challenge_id: str,
        confirmation_intent_id: str,
        parameters: Mapping[str, object],
        trailing_records: int = 0,
    ) -> ActionConfirmation:
        """Stage inside the caller's owned transaction, before its atomic effect.

        ``trailing_records`` is the caller's fixed batch layout, never a command
        parameter. The basis-confirmation use case appends exactly one record.
        Authorization requires its own effect adapter and is not exposed by the
        current saved-basis resolver.
        """
        if type(trailing_records) is not int or trailing_records < 0:
            raise ValueError("the atomic confirmation layout must be exact")
        actor = self.actors.current()
        self.require_actor(project_id)
        identity, fingerprint = self._confirmation_identity(
            project_id, confirmation_intent_id, challenge_id, parameters
        )
        original = self._confirmed(project_id, identity, fingerprint)
        if original is not None:
            return original
        challenge = self.read_challenge(project_id=project_id, challenge_id=challenge_id)
        basis = self.resolver.resolve(
            project_id=project_id,
            intent_id=challenge.basis.intent_id,
            action=challenge.basis.action,
            target=challenge.basis.target,
            parameters=parameters,
        )
        interaction = require_challenge_confirmation(challenge, actor, basis)
        interaction_id = self._interaction_identity(
            project_id, actor.session_id, interaction.interaction_id
        )
        if self._current("approval_interaction", interaction_id, project_id) is not None:
            raise ApprovalConflict("this user interaction already confirmed another action")
        if self.actors.current() != actor:
            raise ApprovalRequired("the controlled actor changed before confirmation")
        confirmation = ActionConfirmation(
            identity,
            confirmation_intent_id,
            challenge_id,
            basis,
            actor.session_id,
            actor.entry_kind,
            interaction.interaction_id,
            str(int(self.unit.next_commit_seq()) + 3 + trailing_records),
            self.clock.now(),
        )
        self._stage_challenge(
            replace(challenge, state=ChallengeState.CONSUMED), expected_revision=1
        )
        self.unit.stage_record(
            aggregate_kind="approval_interaction",
            record_id=interaction_id,
            expected_revision=0,
            payload={
                "schema_version": "aitest.approval-interaction/1.0",
                "project_id": project_id,
                "workspace_id": self.workspace_id,
                "interaction": _payload(interaction),
                "confirmation_id": identity,
            },
        )
        self.unit.stage_record(
            aggregate_kind="approval_confirmation",
            record_id=identity,
            expected_revision=0,
            payload={
                "schema_version": "aitest.action-confirmation/1.0",
                "project_id": project_id,
                "workspace_id": self.workspace_id,
                "confirmation": _payload(confirmation),
            },
        )
        self.unit.stage_record(
            aggregate_kind="approval_intent",
            record_id=identity,
            expected_revision=0,
            payload={
                "schema_version": "aitest.approval-confirmation-intent/1.0",
                "project_id": project_id,
                "workspace_id": self.workspace_id,
                "input_digest": fingerprint,
                "confirmation_id": identity,
            },
        )
        return confirmation

    def _interaction_identity(self, project: str, session: str, interaction: str) -> str:
        return (
            "approval-interaction-"
            + _digest([self.workspace_id, project, session, interaction])[7:]
        )

    def revoke(
        self, *, project_id: str, challenge_id: str, request_id: str, intent_id: str
    ) -> ApprovalChallenge:
        """Retain consumed history; only the originating controlled session revokes."""
        self.require_actor(project_id)
        self.unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            challenge = self.read_challenge(project_id=project_id, challenge_id=challenge_id)
            actor = self.actors.current()
            if (actor.session_id, actor.entry_kind) != (
                challenge.origin_session_id,
                challenge.origin_entry_kind,
            ):
                raise ApprovalRequired("another session cannot revoke this challenge")
            if challenge.state is not ChallengeState.PENDING:
                self.unit.rollback(request_id)
                return challenge
            challenge = replace(challenge, state=ChallengeState.REVOKED)
            self._stage_challenge(challenge, expected_revision=1)
            self.unit.commit(request_id)
        except BaseException as error:
            self._rollback(request_id, error)
            raise
        return challenge

    def read_confirmation(self, *, project_id: str, confirmation_id: str) -> ActionConfirmation:
        """Read the complete immutable origin proof, without recalculating consent."""
        receipt = self._current("approval_intent", confirmation_id, project_id)
        if receipt is None or not isinstance(receipt.get("input_digest"), str):
            raise ApprovalRequired("the exact confirmation receipt is unavailable")
        result = self._confirmed(project_id, confirmation_id, receipt["input_digest"])
        if result is None:
            raise ApprovalRequired("the exact saved confirmation is unavailable")
        return result

    def validate_basis_confirmation(
        self, *, project_id: str, payload: Mapping[str, object]
    ) -> None:
        identity = payload.get("approval_confirmation_id")
        if not isinstance(identity, str) or not identity:
            raise ApprovalRequired("legacy basis confirmation needs controlled new consent")
        proof = self.read_confirmation(project_id=project_id, confirmation_id=identity)
        expected = {
            "case_id": payload.get("case_id"),
            "case_revision": payload.get("case_revision"),
            "basis_revision": payload.get("basis_revision"),
            "basis_text_digest": payload.get("basis_text_digest"),
        }
        if (
            proof.basis.action != "confirm_basis"
            or proof.basis.target != payload.get("case_id")
            or proof.confirmation_intent_id != payload.get("intent_id")
            or proof.basis.intent_id != payload.get("intent_id")
            or proof.basis.input_digest != _digest(expected)
            or proof.confirmed_at_commit != payload.get("confirmed_at_commit")
        ):
            raise ApprovalRequired("basis confirmation differs from its frozen user consent")

    def _confirmed(
        self, project: str, identity: str, fingerprint: str
    ) -> ActionConfirmation | None:
        receipt = self._current("approval_intent", identity, project)
        if receipt is None:
            return None
        if (
            receipt.get("schema_version") != "aitest.approval-confirmation-intent/1.0"
            or receipt.get("confirmation_id") != identity
        ):
            raise ApprovalRequired("confirmation receipt cannot be verified")
        if receipt.get("input_digest") != fingerprint:
            raise ApprovalConflict("confirmation intent has different frozen input")
        raw = self._read("approval_confirmation", identity, 1, project)
        if raw.get("schema_version") != "aitest.action-confirmation/1.0":
            raise ApprovalRequired("confirmation schema cannot be verified")
        result = _decode(ActionConfirmation, raw.get("confirmation"))
        if (result.confirmation_id, result.basis.workspace_id, result.basis.project_id) != (
            identity,
            self.workspace_id,
            project,
        ):
            raise ApprovalRequired("confirmation identity cannot be verified")
        expected_identity, _ = self._confirmation_identity(
            project, result.confirmation_intent_id, result.challenge_id, {}
        )
        challenge = self.read_challenge(project_id=project, challenge_id=result.challenge_id)
        interaction_raw = self._read(
            "approval_interaction",
            self._interaction_identity(project, result.origin_session_id, result.interaction_id),
            1,
            project,
        )
        interaction = _decode(UserInteraction, interaction_raw.get("interaction"))
        initial_raw = self._read("approval_challenge", result.challenge_id, 1, project)
        initial = _decode(ApprovalChallenge, initial_raw.get("challenge"))
        if (
            expected_identity != identity
            or fingerprint != _digest([result.challenge_id, result.basis.input_digest])
            or challenge.state is not ChallengeState.CONSUMED
            or replace(challenge, state=ChallengeState.PENDING) != initial
            or challenge.basis != result.basis
            or (challenge.origin_session_id, challenge.origin_entry_kind)
            != (result.origin_session_id, result.origin_entry_kind)
            or interaction_raw.get("schema_version") != "aitest.approval-interaction/1.0"
            or interaction_raw.get("confirmation_id") != identity
            or interaction
            != UserInteraction(
                result.interaction_id,
                result.origin_session_id,
                result.challenge_id,
                result.basis.input_digest,
            )
        ):
            raise ApprovalRequired("saved confirmation has no complete consumed origin proof")
        for reference in result.basis.materials:
            stored = self.records.read(
                aggregate_kind=reference.aggregate_kind,
                record_id=reference.record_id,
                revision=reference.record_revision,
            )
            material = getattr(stored, "payload", None)
            if (
                (
                    getattr(stored, "aggregate_kind", None),
                    getattr(stored, "record_id", None),
                    getattr(stored, "revision", None),
                )
                != (reference.aggregate_kind, reference.record_id, reference.record_revision)
                or type(getattr(stored, "revision", None)) is not int
                or not isinstance(material, Mapping)
                or material.get("project_id", material.get("local_project_id")) != project
                or _digest(dict(material)) != reference.digest
            ):
                raise ApprovalRequired("confirmation's exact frozen material cannot be verified")
        return result

    def _stage_challenge(self, challenge: ApprovalChallenge, *, expected_revision: int) -> None:
        self.unit.stage_record(
            aggregate_kind="approval_challenge",
            record_id=challenge.challenge_id,
            expected_revision=expected_revision,
            payload={
                "schema_version": "aitest.approval-challenge/1.0",
                "project_id": challenge.basis.project_id,
                "workspace_id": self.workspace_id,
                "challenge": _payload(challenge),
            },
        )

    def _rollback(self, request_id: str, error: BaseException) -> None:
        try:
            self.unit.rollback(request_id)
        except Exception as cleanup_error:
            raise error from cleanup_error
