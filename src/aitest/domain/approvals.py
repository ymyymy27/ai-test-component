"""Single-use user confirmation rules; actors come from a controlled core context."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class EntryKind(StrEnum):
    HUMAN_UI = "human_ui"
    INTERACTIVE_CLI = "interactive_cli"
    AGENT_RELAY = "agent_relay"


class ChallengeState(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    REVOKED = "revoked"


class ApprovalRequired(ValueError):
    code = "AWAITING_USER_CONFIRMATION"


class ApprovalConflict(ValueError):
    code = "INTENT_CONFLICT"


@dataclass(frozen=True, slots=True)
class ApprovalMaterialRef:
    aggregate_kind: str
    record_id: str
    record_revision: int
    digest: str

    def __post_init__(self) -> None:
        _text(self.aggregate_kind, self.record_id, self.digest)
        if type(self.record_revision) is not int or self.record_revision < 1:
            raise ValueError("approval material requires an exact warehouse revision")


@dataclass(frozen=True, slots=True)
class ActionBasis:
    workspace_id: str
    project_id: str
    intent_id: str
    action: str
    target: str
    input_digest: str
    credential_scope_ref: str
    materials: tuple[ApprovalMaterialRef, ...]

    def __post_init__(self) -> None:
        _text(
            self.workspace_id,
            self.project_id,
            self.intent_id,
            self.action,
            self.target,
            self.input_digest,
            self.credential_scope_ref,
        )
        identities = [(ref.aggregate_kind, ref.record_id) for ref in self.materials]
        if len(identities) != len(set(identities)):
            raise ValueError("approval materials must have unique exact identities")


@dataclass(frozen=True, slots=True)
class UserInteraction:
    interaction_id: str
    session_id: str
    challenge_id: str
    input_digest: str

    def __post_init__(self) -> None:
        _text(self.interaction_id, self.session_id, self.challenge_id, self.input_digest)


@dataclass(frozen=True, slots=True)
class TrustedActor:
    workspace_id: str
    project_id: str
    session_id: str
    entry_kind: EntryKind
    interactive: bool
    interaction: UserInteraction | None = None

    def __post_init__(self) -> None:
        _text(self.workspace_id, self.project_id, self.session_id)
        if not isinstance(self.entry_kind, EntryKind) or type(self.interactive) is not bool:
            raise ValueError("actor context must contain a controlled entry fact")


@dataclass(frozen=True, slots=True)
class ApprovalChallenge:
    challenge_id: str
    basis: ActionBasis
    origin_session_id: str
    origin_entry_kind: EntryKind
    state: ChallengeState
    created_at: datetime

    def __post_init__(self) -> None:
        _text(self.challenge_id, self.origin_session_id)
        if (
            not isinstance(self.state, ChallengeState)
            or not isinstance(self.origin_entry_kind, EntryKind)
            or self.created_at.tzinfo is None
        ):
            raise ValueError("approval challenge requires an exact state, origin and time")


@dataclass(frozen=True, slots=True)
class ActionConfirmation:
    confirmation_id: str
    confirmation_intent_id: str
    challenge_id: str
    basis: ActionBasis
    origin_session_id: str
    origin_entry_kind: EntryKind
    interaction_id: str
    confirmed_at_commit: str
    confirmed_at: datetime

    def __post_init__(self) -> None:
        _text(
            self.confirmation_id,
            self.confirmation_intent_id,
            self.challenge_id,
            self.origin_session_id,
            self.interaction_id,
            self.confirmed_at_commit,
        )
        if not isinstance(self.origin_entry_kind, EntryKind) or self.confirmed_at.tzinfo is None:
            raise ValueError("confirmation requires a controlled origin and time")


def require_human_actor(actor: TrustedActor, basis: ActionBasis) -> None:
    require_controlled_actor(actor, workspace_id=basis.workspace_id, project_id=basis.project_id)


def require_controlled_actor(actor: TrustedActor, *, workspace_id: str, project_id: str) -> None:
    if (
        actor.entry_kind is EntryKind.AGENT_RELAY
        or not actor.interactive
        or (actor.workspace_id, actor.project_id) != (workspace_id, project_id)
    ):
        raise ApprovalRequired("a controlled interactive user entry must review this action")


def require_challenge_confirmation(
    challenge: ApprovalChallenge, actor: TrustedActor, current_basis: ActionBasis
) -> UserInteraction:
    require_human_actor(actor, current_basis)
    interaction = actor.interaction
    if (
        challenge.state is not ChallengeState.PENDING
        or challenge.basis != current_basis
        or (challenge.origin_session_id, challenge.origin_entry_kind)
        != (actor.session_id, actor.entry_kind)
        or interaction is None
        or (interaction.session_id, interaction.challenge_id, interaction.input_digest)
        != (actor.session_id, challenge.challenge_id, challenge.basis.input_digest)
    ):
        raise ApprovalRequired("the exact live challenge needs its own actual user interaction")
    return interaction


def _text(*values: str) -> None:
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("approval identities and frozen facts must be nonempty text")
