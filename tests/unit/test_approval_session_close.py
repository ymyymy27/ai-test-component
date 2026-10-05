"""Channel close rolls back only its transaction and persists unused-challenge revocation."""

import pytest

from aitest.contracts.commands import Command
from aitest.interfaces.local.api import EntryKind, LocalAPI, Session
from tests.support.controlled_confirmation import prepare_basis_challenge
from tests.unit.test_approval_origin_boundary import exact_command
from tests.unit.test_authoritative_preparation import authoritative as authoritative


def test_default_channel_close_revokes_pending_challenge_and_is_repeatable(authoritative):
    core, inputs, _ = authoritative
    session = Session("closing-controlled-fixture", EntryKind.HUMAN_UI, True)
    command = exact_command(core, inputs)
    prepared = prepare_basis_challenge(core, command, session=session)
    assert prepared.error is None
    identity = prepared.result["challenge_id"]
    core.api.close_session(session)
    saved = core.unit_of_work.repo.read(
        aggregate_kind="approval_challenge", record_id=identity, revision=2
    )
    assert saved.payload["challenge"]["state"] == "revoked"
    sequence = core.unit_of_work.current_commit_sequence()
    core.api.close_session(session)
    assert core.unit_of_work.current_commit_sequence() == sequence
    approved = command.model_copy(
        update={"parameters": {**command.parameters, "approval_challenge_id": identity}}
    )
    refused = core.api.dispatch_user_confirmation(
        approved,
        session,
        challenge_id=identity,
        input_digest=prepared.result["basis"]["input_digest"],
    )
    assert refused.error.code == "AWAITING_USER_CONFIRMATION"


def test_failed_close_does_not_claim_revocation_or_discard_its_retry(authoritative, monkeypatch):
    core, inputs, _ = authoritative
    session = Session("failing-close-fixture", EntryKind.HUMAN_UI, True)
    prepared = prepare_basis_challenge(core, exact_command(core, inputs), session=session)
    assert prepared.error is None
    original = core.unit_of_work.stage_record

    def failed(**kwargs):
        if kwargs["aggregate_kind"] == "approval_challenge":
            raise OSError("injected channel revocation failure")
        return original(**kwargs)

    monkeypatch.setattr(core.unit_of_work, "stage_record", failed)
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(OSError, match="revocation failure"):
        core.api.close_session(session)
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert (
        core.unit_of_work.repo.current_revision(
            "approval_challenge", prepared.result["challenge_id"]
        )
        == 1
    )
    monkeypatch.setattr(core.unit_of_work, "stage_record", original)
    core.api.close_session(session)
    assert (
        core.unit_of_work.repo.current_revision(
            "approval_challenge", prepared.result["challenge_id"]
        )
        == 2
    )


def test_close_cannot_roll_back_another_sessions_transaction():
    calls = []

    class Transactions:
        def begin(self, **kwargs):
            calls.append(("begin", kwargs["request_id"]))
            return {"state": "active"}

        def rollback(self, **kwargs):
            calls.append(("rollback", kwargs["request_id"]))
            return {"state": "rolled_back"}

    api = LocalAPI("instance", "workspace", transaction_port=Transactions())
    owner = Session("owner", EntryKind.HUMAN_UI, True)
    result = api.dispatch(
        Command(action="begin", request_id="owned-begin", project_id="project"), owner
    )
    assert result.error is None
    api.close_session(Session("other", EntryKind.HUMAN_UI, True))
    assert calls == [("begin", "owned-begin")]
    api.close_session(owner)
    assert calls == [("begin", "owned-begin"), ("rollback", "owned-begin")]


def test_same_named_noninteractive_close_cannot_touch_humans_pending_challenge(authoritative):
    core, inputs, _ = authoritative
    owner = Session("same-name-close", EntryKind.HUMAN_UI, True)
    prepared = prepare_basis_challenge(core, exact_command(core, inputs), session=owner)
    assert prepared.error is None
    sequence = core.unit_of_work.current_commit_sequence()
    core.api.close_session(Session(owner.session_id, owner.entry_kind, False))
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert (
        core.unit_of_work.repo.current_revision(
            "approval_challenge", prepared.result["challenge_id"]
        )
        == 1
    )
    # The real owner's cached original preparation is also preserved.
    replay = prepare_basis_challenge(core, exact_command(core, inputs), session=owner)
    assert replay.error is None
    assert replay.result["challenge_id"] == prepared.result["challenge_id"]
    core.api.close_session(owner)
    assert (
        core.unit_of_work.repo.current_revision(
            "approval_challenge", prepared.result["challenge_id"]
        )
        == 2
    )
