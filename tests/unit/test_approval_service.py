"""Exact consent, persistence faults and lost-response replay of the core challenge."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from aitest.application.approval_service import ApprovalService, _digest
from aitest.domain.approvals import (
    ActionBasis,
    ApprovalConflict,
    ApprovalMaterialRef,
    ApprovalRequired,
    ChallengeState,
    EntryKind,
    TrustedActor,
    UserInteraction,
)


class MemoryApprovalUnit:
    def __init__(self):
        self.saved, self.pending = {}, []
        self.sequence, self.failure, self.active = 0, None, False
        self.on_begin = None

    def read(self, *, aggregate_kind, record_id, revision):
        return self.saved[(aggregate_kind, record_id, revision)]

    def current_revision(self, *, aggregate_kind, record_id):
        return max(
            (
                revision
                for kind, identity, revision in self.saved
                if (kind, identity) == (aggregate_kind, record_id)
            ),
            default=0,
        )

    def begin(self, request_id, project_id, workspace_id=None, intent_id=None):
        assert not self.active
        self.active = True
        if self.on_begin:
            self.on_begin()

    def open(self, project_id):
        self.begin("component", project_id)

    def next_commit_seq(self):
        return str(self.sequence + len(self.pending) + 1)

    def stage_record(self, *, aggregate_kind, record_id, expected_revision, payload):
        assert self.active
        if self.failure == len(self.pending) + 1:
            raise OSError("injected stage failure")
        current = self.current_revision(aggregate_kind=aggregate_kind, record_id=record_id)
        assert current == expected_revision
        self.pending.append(
            SimpleNamespace(
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                revision=current + 1,
                payload=deepcopy(dict(payload)),
            )
        )
        return current + 1

    def commit(self, request_id=None):
        assert self.active
        if self.failure == "before_commit":
            raise OSError("injected publication failure")
        for record in self.pending:
            self.saved[(record.aggregate_kind, record.record_id, record.revision)] = record
        self.sequence += len(self.pending)
        self.pending.clear()
        self.active = False
        if self.failure == "after_commit":
            raise OSError("injected lost response")

    def rollback(self, request_id=None):
        self.pending.clear()
        self.active = False


class Actors:
    actor = TrustedActor("workspace", "project", "session", EntryKind.HUMAN_UI, True)

    def current(self):
        return self.actor


class Clock:
    def now(self):
        return datetime(2026, 10, 5, tzinfo=UTC)

    def monotonic(self):
        return 0.0


class Identities:
    count = 0

    def create(self):
        self.count += 1
        return "challenge-" + str(self.count)


class Resolver:
    def __init__(self, unit):
        self.unit = unit

    def resolve(self, *, project_id, intent_id, action, target, parameters):
        project = self.unit.read(aggregate_kind="project", record_id="project", revision=1)
        return ActionBasis(
            "workspace",
            project_id,
            intent_id,
            action,
            target,
            _digest(dict(parameters)),
            "none",
            (ApprovalMaterialRef("project", "project", 1, _digest(project.payload)),),
        )


@pytest.fixture
def approvals():
    unit, actors = MemoryApprovalUnit(), Actors()
    unit.saved[("project", "project", 1)] = SimpleNamespace(
        aggregate_kind="project",
        record_id="project",
        revision=1,
        payload={"project_id": "project", "workspace_id": "workspace", "goal": "initial"},
    )
    service = ApprovalService(
        unit=unit,
        records=unit,
        actors=actors,
        resolver=Resolver(unit),
        identities=Identities(),
        clock=Clock(),
        workspace_id="workspace",
    )
    return service, unit, actors


def prepare(service, **kwargs):
    return service.prepare(
        project_id="project",
        action_intent_id="business",
        preparation_intent_id="prepare",
        request_id="prepare-request",
        action="confirm_basis",
        target="case",
        parameters={"basis": "original"},
        **kwargs,
    )


def click(actors, challenge):
    actors.actor = replace(
        actors.actor,
        interaction=UserInteraction(
            "interaction",
            actors.actor.session_id,
            challenge.challenge_id,
            challenge.basis.input_digest,
        ),
    )


def confirm(service, challenge, *, intent="confirm", parameters=None):
    return service.confirm(
        project_id="project",
        challenge_id=challenge.challenge_id,
        confirmation_intent_id=intent,
        request_id="confirmation-request",
        parameters={"basis": "original"} if parameters is None else parameters,
    )


def test_exact_confirm_single_use_and_original_replay_without_another_click(approvals):
    service, unit, actors = approvals
    challenge = prepare(service)
    assert prepare(service) == challenge
    click(actors, challenge)
    first = confirm(service, challenge)
    sequence = unit.sequence
    assert first.confirmed_at_commit == str(sequence)
    assert (
        service.read_confirmation(project_id="project", confirmation_id=first.confirmation_id)
        == first
    )
    actors.actor = replace(actors.actor, interaction=None)
    assert confirm(service, challenge) == first
    assert unit.sequence == sequence
    with pytest.raises(ApprovalRequired):
        confirm(service, challenge, intent="another")
    with pytest.raises(ApprovalConflict):
        confirm(service, challenge, parameters={"basis": "changed"})


@pytest.mark.parametrize("fault", [1, 2, "before_commit"])
def test_preparation_fault_has_no_partial_challenge_and_releases_transaction(approvals, fault):
    service, unit, _ = approvals
    before = deepcopy(unit.saved)
    unit.failure = fault
    with pytest.raises(OSError):
        prepare(service)
    assert unit.saved == before and not unit.active and not unit.pending
    unit.failure = None
    assert prepare(service).state is ChallengeState.PENDING


@pytest.mark.parametrize("fault", [1, 2, 3, 4, "before_commit"])
def test_confirmation_fault_never_leaves_consumption_without_the_saved_receipt(approvals, fault):
    service, unit, actors = approvals
    challenge = prepare(service)
    click(actors, challenge)
    before = deepcopy(unit.saved)
    unit.failure = fault
    with pytest.raises(OSError):
        confirm(service, challenge)
    assert unit.saved == before and not unit.active and not unit.pending
    assert (
        service.read_challenge(project_id="project", challenge_id=challenge.challenge_id)
        == challenge
    )
    unit.failure = None
    assert confirm(service, challenge).challenge_id == challenge.challenge_id


@pytest.mark.parametrize("operation", ["prepare", "confirm"])
def test_published_response_loss_recalls_the_original_result(approvals, operation):
    service, unit, actors = approvals
    if operation == "confirm":
        challenge = prepare(service)
        click(actors, challenge)
    unit.failure = "after_commit"
    with pytest.raises(OSError, match="lost response"):
        prepare(service) if operation == "prepare" else confirm(service, challenge)
    sequence = unit.sequence
    unit.failure = None
    if operation == "prepare":
        result = prepare(service)
        assert result.challenge_id == "challenge-1"
    else:
        actors.actor = replace(actors.actor, interaction=None)
        result = confirm(service, challenge)
        assert result.challenge_id == challenge.challenge_id
    assert unit.sequence == sequence


@pytest.mark.parametrize(
    "change",
    [
        "no_gesture",
        "relay",
        "noninteractive",
        "session",
        "project",
        "workspace",
        "gesture_session",
        "gesture_challenge",
        "gesture_digest",
        "input",
        "material",
    ],
)
def test_actor_gesture_and_material_mismatch_never_writes_consent(approvals, change):
    service, unit, actors = approvals
    challenge = prepare(service)
    click(actors, challenge)
    params = None
    if change == "no_gesture":
        actors.actor = replace(actors.actor, interaction=None)
    elif change == "relay":
        actors.actor = replace(actors.actor, entry_kind=EntryKind.AGENT_RELAY)
    elif change == "noninteractive":
        actors.actor = replace(actors.actor, interactive=False)
    elif change in {"session", "project", "workspace"}:
        actors.actor = replace(actors.actor, **{change + "_id": "other"})
    elif change.startswith("gesture_"):
        field = {
            "gesture_session": "session_id",
            "gesture_challenge": "challenge_id",
            "gesture_digest": "input_digest",
        }[change]
        actors.actor = replace(
            actors.actor, interaction=replace(actors.actor.interaction, **{field: "other"})
        )
    elif change == "input":
        params = {"basis": "changed"}
    else:
        unit.saved[("project", "project", 1)].payload["goal"] = "changed"
    before = deepcopy(unit.saved)
    with pytest.raises(ApprovalRequired):
        confirm(service, challenge, parameters=params)
    assert unit.saved == before and not unit.pending and not unit.active


def test_revocation_is_idempotent_and_only_origin_session_can_revoke(approvals):
    service, unit, actors = approvals
    challenge = prepare(service)
    original_actor = actors.actor
    actors.actor = replace(actors.actor, session_id="other")
    with pytest.raises(ApprovalRequired):
        service.revoke(
            project_id="project",
            challenge_id=challenge.challenge_id,
            request_id="revoke-request",
            intent_id="revoke-intent",
        )
    actors.actor = original_actor
    result = service.revoke(
        project_id="project",
        challenge_id=challenge.challenge_id,
        request_id="revoke-request",
        intent_id="revoke-intent",
    )
    assert result.state is ChallengeState.REVOKED
    sequence = unit.sequence
    assert (
        service.revoke(
            project_id="project",
            challenge_id=challenge.challenge_id,
            request_id="revoke-retry",
            intent_id="revoke-intent",
        )
        == result
    )
    click(actors, challenge)
    with pytest.raises(ApprovalRequired):
        confirm(service, challenge)
    assert unit.sequence == sequence


def test_one_actual_interaction_cannot_confirm_two_challenges(approvals):
    service, unit, actors = approvals
    first = prepare(service)
    click(actors, first)
    confirm(service, first)
    second = service.prepare(
        project_id="project",
        action_intent_id="business2",
        preparation_intent_id="prepare2",
        request_id="request2",
        action="confirm_basis",
        target="case",
        parameters={"basis": "original"},
    )
    click(actors, second)
    before = unit.sequence
    with pytest.raises(ApprovalConflict):
        confirm(service, second, intent="confirm2")
    assert unit.sequence == before


@pytest.mark.parametrize(
    "damage",
    [
        "challenge",
        "interaction",
        "receipt",
        "material",
        "extra",
        "revision",
        "state",
        "digest",
        "origin",
    ],
)
def test_saved_receipt_requires_complete_exact_origin_after_restart(approvals, damage):
    service, unit, actors = approvals
    challenge = prepare(service)
    click(actors, challenge)
    result = confirm(service, challenge)
    if damage == "material":
        unit.saved[("project", "project", 1)].payload["goal"] = "altered"
    elif damage == "challenge":
        unit.saved[("approval_challenge", challenge.challenge_id, 1)].payload["challenge"]["basis"][
            "target"
        ] = "other"
    elif damage == "interaction":
        record = next(
            record for (kind, _, _), record in unit.saved.items() if kind == "approval_interaction"
        )
        record.payload["interaction"]["input_digest"] = "other"
    elif damage == "receipt":
        unit.saved[("approval_intent", result.confirmation_id, 1)].payload["input_digest"] = "other"
    else:
        record = unit.saved[("approval_confirmation", result.confirmation_id, 1)]
        if damage == "extra":
            record.payload["confirmation"]["basis"]["user_confirmed"] = True
        elif damage == "revision":
            record.revision = True
        elif damage == "state":
            unit.saved[("approval_challenge", challenge.challenge_id, 2)].payload["challenge"][
                "state"
            ] = "future"
        else:
            record.payload["confirmation"][
                "basis" if damage == "digest" else "origin_session_id"
            ] = "other"
    with pytest.raises((ApprovalRequired, KeyError)):
        service.read_confirmation(project_id="project", confirmation_id=result.confirmation_id)


def test_preparation_rechecks_actor_and_material_after_acquiring_writer(approvals):
    service, unit, actors = approvals
    unit.on_begin = lambda: setattr(actors, "actor", replace(actors.actor, session_id="changed"))
    with pytest.raises(ApprovalRequired):
        prepare(service)
    assert not unit.active and unit.sequence == 0
