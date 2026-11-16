"""Revocation is a persistent business intent, not merely an idempotent state setter."""

import pytest

from aitest.domain.approvals import ApprovalConflict, ApprovalRequired, ChallengeState
from tests.unit.test_approval_service import approvals as approvals
from tests.unit.test_approval_service import click, confirm, prepare


def test_same_revocation_intent_cannot_revoke_another_challenge(approvals):
    service, unit, _ = approvals
    first = prepare(service)
    second = service.prepare(
        project_id="project",
        action_intent_id="business2",
        preparation_intent_id="prepare2",
        request_id="request2",
        action="confirm_basis",
        target="case",
        parameters={"basis": "original"},
    )
    service.revoke(
        project_id="project",
        challenge_id=first.challenge_id,
        request_id="first-revocation-request",
        intent_id="same-revocation",
    )
    sequence = unit.sequence
    with pytest.raises(ApprovalConflict):
        service.revoke(
            project_id="project",
            challenge_id=second.challenge_id,
            request_id="second-revocation-request",
            intent_id="same-revocation",
        )
    assert unit.sequence == sequence
    assert service.read_challenge(project_id="project", challenge_id=second.challenge_id).state is (
        ChallengeState.PENDING
    )


@pytest.mark.parametrize(
    "missing", ["approval_confirmation", "approval_interaction", "approval_challenge"]
)
def test_missing_accurate_origin_returns_a_blocked_confirmation_not_a_raw_error(approvals, missing):
    service, unit, actors = approvals
    challenge = prepare(service)
    click(actors, challenge)
    saved = confirm(service, challenge)
    key = next(key for key in unit.saved if key[0] == missing and key[2] == 1)
    del unit.saved[key]
    with pytest.raises(ApprovalRequired):
        service.read_confirmation(project_id="project", confirmation_id=saved.confirmation_id)


def test_boolean_warehouse_revision_cannot_prove_an_immutable_confirmation(approvals, monkeypatch):
    service, unit, actors = approvals
    challenge = prepare(service)
    click(actors, challenge)
    saved = confirm(service, challenge)
    original = unit.current_revision

    def invalid_revision(**kwargs):
        if kwargs["aggregate_kind"] == "approval_confirmation":
            return True
        return original(**kwargs)

    monkeypatch.setattr(unit, "current_revision", invalid_revision)
    with pytest.raises(ApprovalRequired):
        service.read_confirmation(project_id="project", confirmation_id=saved.confirmation_id)


@pytest.mark.parametrize("fault", [1, 2, "before_commit"])
def test_revocation_fault_has_no_partial_receipt_or_state_change(approvals, fault):
    service, unit, _ = approvals
    challenge = prepare(service)
    sequence = unit.sequence
    unit.failure = fault
    with pytest.raises(OSError):
        service.revoke(
            project_id="project",
            challenge_id=challenge.challenge_id,
            request_id="revoke-request",
            intent_id="revoke-intent",
        )
    assert unit.sequence == sequence and not unit.pending and not unit.active
    assert (
        service.read_challenge(project_id="project", challenge_id=challenge.challenge_id)
        == challenge
    )
    unit.failure = None
    revoked = service.revoke(
        project_id="project",
        challenge_id=challenge.challenge_id,
        request_id="retry-revoke",
        intent_id="revoke-intent",
    )
    assert revoked.state is ChallengeState.REVOKED


def test_revocation_lost_response_replays_original_receipt_after_reconstruction(approvals):
    service, unit, _ = approvals
    challenge = prepare(service)
    unit.failure = "after_commit"
    with pytest.raises(OSError, match="lost response"):
        service.revoke(
            project_id="project",
            challenge_id=challenge.challenge_id,
            request_id="revoke-request",
            intent_id="revoke-intent",
        )
    unit.failure = None
    sequence = unit.sequence
    # New service instance over the same saved authority; no session-list memory.
    from aitest.application.approval_service import ApprovalService

    restarted = ApprovalService(
        unit=unit,
        records=unit,
        actors=service.actors,
        resolver=service.resolver,
        identities=service.identities,
        clock=service.clock,
        workspace_id="workspace",
    )
    result = restarted.revoke(
        project_id="project",
        challenge_id=challenge.challenge_id,
        request_id="retry-revoke",
        intent_id="revoke-intent",
    )
    assert result.state is ChallengeState.REVOKED and unit.sequence == sequence


def test_revoking_consumed_challenge_retains_exact_confirmation_history(approvals):
    service, unit, actors = approvals
    challenge = prepare(service)
    click(actors, challenge)
    saved = confirm(service, challenge)
    result = service.revoke(
        project_id="project",
        challenge_id=challenge.challenge_id,
        request_id="late-revoke",
        intent_id="late-revoke-intent",
    )
    assert result.state is ChallengeState.CONSUMED
    assert (
        service.read_confirmation(project_id="project", confirmation_id=saved.confirmation_id)
        == saved
    )
    assert service.pending_for_session("session", actors.actor.entry_kind) == ()


def test_pending_session_challenges_are_bounded_and_exact_replay_remains_available(approvals):
    service, unit, actors = approvals

    def make(index):
        return service.prepare(
            project_id="project",
            action_intent_id=f"business-{index}",
            preparation_intent_id=f"prepare-{index}",
            request_id=f"request-{index}",
            action="confirm_basis",
            target="case",
            parameters={"basis": "original"},
        )

    first = make(0)
    for index in range(1, 16):
        make(index)
    sequence = unit.sequence
    with pytest.raises(ApprovalRequired, match="finish or revoke"):
        make(16)
    assert unit.sequence == sequence
    assert make(0) == first
    assert len(service.pending_for_session("session", actors.actor.entry_kind)) == 16
    service.revoke(
        project_id="project",
        challenge_id=first.challenge_id,
        request_id="revoke-one",
        intent_id="revoke-one-intent",
    )
    assert make(16).state is ChallengeState.PENDING


def test_failed_unpublished_preparation_does_not_leave_a_close_candidate(approvals):
    service, unit, actors = approvals
    unit.failure = "before_commit"
    with pytest.raises(OSError):
        prepare(service)
    unit.failure = None
    assert service.pending_for_session("session", actors.actor.entry_kind) == ()
