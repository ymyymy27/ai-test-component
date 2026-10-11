"""A role label alone cannot supply an actual user confirmation fact."""

from dataclasses import replace

import pytest

from aitest.application.planning.serialization import case_from_payload
from aitest.domain.approvals import EntryKind
from aitest.interfaces.local.api import Session
from tests.support.controlled_confirmation import basis_command, prepare_basis_challenge
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_default_source_analysis import dispatch


def exact_command(core, inputs, **kwargs):
    ref = inputs.case_revisions[0]
    case = case_from_payload(
        core.unit_of_work.repo.read(
            aggregate_kind="case", record_id=ref.case_id, revision=ref.revision
        ).payload
    )
    return basis_command(
        inputs.project_id,
        {
            "case_id": case.case_id,
            "case_revision": ref.revision,
            "basis_revision": case.assertion_basis.revision,
            "basis_text_digest": case.assertion_basis.text_digest,
        },
        **kwargs,
    )


def test_default_basis_confirmation_requires_a_core_challenge_and_user_interaction(authoritative):
    core, inputs, _ = authoritative
    reference = inputs.case_revisions[0]
    case = case_from_payload(
        core.unit_of_work.repo.read(
            aggregate_kind="case",
            record_id=reference.case_id,
            revision=reference.revision,
        ).payload
    )
    sequence = core.unit_of_work.current_commit_sequence()
    response = dispatch(
        core,
        "confirm_basis",
        project=inputs.project_id,
        request="no-user-gesture-request",
        intent="no-user-gesture-intent",
        parameters={
            "case_id": case.case_id,
            "case_revision": case.revision,
            "basis_revision": case.assertion_basis.revision,
            "basis_text_digest": case.assertion_basis.text_digest,
        },
    )
    assert response.error is not None
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.current_commit_sequence() == sequence


@pytest.mark.parametrize("damage", ["no_event", "input_digest", "session", "input", "relay"])
def test_default_challenge_does_not_replace_its_actual_bound_user_event(authoritative, damage):
    core, inputs, _ = authoritative
    command = exact_command(core, inputs)
    session = Session("actual-controlled-fixture", EntryKind.HUMAN_UI, True)
    prepared = prepare_basis_challenge(core, command, session=session)
    assert prepared.error is None, prepared.error
    challenge = prepared.result
    approved = command.model_copy(
        update={
            "parameters": {**command.parameters, "approval_challenge_id": challenge["challenge_id"]}
        }
    )
    sequence = core.unit_of_work.current_commit_sequence()
    digest = challenge["basis"]["input_digest"]
    if damage == "no_event":
        result = core.api.dispatch(approved, session)
    else:
        if damage == "session":
            session = replace(session, session_id="other")
        elif damage == "relay":
            session = replace(session, entry_kind=EntryKind.AGENT_RELAY)
        elif damage == "input_digest":
            digest = "sha256:other"
        else:
            approved = approved.model_copy(
                update={"parameters": {**approved.parameters, "basis_text_digest": "sha256:other"}}
            )
        result = core.api.dispatch_user_confirmation(
            approved, session, challenge_id=challenge["challenge_id"], input_digest=digest
        )
    assert result.error is not None
    assert result.error.code in {"AWAITING_USER_CONFIRMATION", "B_BASIS_UNVERIFIED"}
    assert core.unit_of_work.current_commit_sequence() == sequence
    saved = core.unit_of_work.repo.read(
        aggregate_kind="approval_challenge", record_id=challenge["challenge_id"], revision=1
    )
    assert saved.payload["challenge"]["state"] == "pending"
    assert (
        core.unit_of_work.repo.current_revision("approval_challenge", challenge["challenge_id"])
        == 1
    )


def test_default_confirmation_effect_failure_keeps_challenge_and_original_intent_retryable(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    command = exact_command(core, inputs)
    session = Session("atomic-effect-fixture", EntryKind.HUMAN_UI, True)
    prepared = prepare_basis_challenge(core, command, session=session)
    assert prepared.error is None
    challenge = prepared.result
    approved = command.model_copy(
        update={
            "parameters": {**command.parameters, "approval_challenge_id": challenge["challenge_id"]}
        }
    )
    original = core.unit_of_work.stage_record

    def fail_effect(**kwargs):
        if kwargs["aggregate_kind"] == "case_link":
            raise OSError("basis effect failed after all consent records staged")
        return original(**kwargs)

    monkeypatch.setattr(core.unit_of_work, "stage_record", fail_effect)
    sequence = core.unit_of_work.current_commit_sequence()
    failed = core.api.dispatch_user_confirmation(
        approved,
        session,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )
    assert failed.error is not None
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert (
        core.unit_of_work.repo.current_revision("approval_challenge", challenge["challenge_id"])
        == 1
    )
    monkeypatch.setattr(core.unit_of_work, "stage_record", original)
    repeated = core.api.dispatch_user_confirmation(
        approved.model_copy(update={"request_id": "retry-effect-request"}),
        session,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )
    assert repeated.error is None, repeated.error
    assert repeated.result["confirmed_at_commit"] == str(sequence + 5)


def test_relay_cannot_read_preparation_cached_by_same_named_human_session(authoritative):
    core, inputs, _ = authoritative
    command = exact_command(core, inputs)
    session = Session("same-name", EntryKind.HUMAN_UI, True)
    result = prepare_basis_challenge(core, command, session=session)
    assert result.error is None
    refused = prepare_basis_challenge(
        core, command, session=replace(session, entry_kind=EntryKind.AGENT_RELAY)
    )
    assert refused.error is not None
    assert refused.error.code == "AWAITING_USER_CONFIRMATION"
    assert refused.result is None


def test_default_prepare_does_not_accept_a_legacy_role_only_confirmation(authoritative):
    core, inputs, _ = authoritative
    command = exact_command(core, inputs, intent="legacy-role-only")
    # Reproduce an old persisted result through the isolated legacy component;
    # default bootstrap always installs the controlled proof service.
    from aitest.application.planning.basis_confirmation import BasisConfirmationService
    from aitest.application.planning.substrate_adapter import PortsRecordReader
    from aitest.contracts.prepared_run import AssertionBasisStateFact, ConfirmationRef
    from tests.unit.test_authoritative_preparation import prepare

    saved = BasisConfirmationService(
        PortsRecordReader(core.unit_of_work.repo), core.unit_of_work
    ).confirm(
        request_id="legacy-fixture-request",
        intent_id=command.intent_id,
        project_id=inputs.project_id,
        **command.parameters,
    )
    reference = ConfirmationRef(
        **{
            key: saved[key]
            for key in ("confirmation_id", "case_id", "basis_revision", "confirmed_at_commit")
        }
    )
    index = next(
        index
        for index, entry in enumerate(inputs.assertion_bases)
        if entry.case_id == saved["case_id"]
    )
    entry = inputs.assertion_bases[index].model_copy(
        update={
            "assertion_basis_state": AssertionBasisStateFact.CONFIRMED,
            "confirmation_refs": (reference,),
        }
    )
    bases = list(inputs.assertion_bases)
    bases[index] = entry
    prepared = prepare(core, replace(inputs, assertion_bases=tuple(bases)))
    assert prepared.error is None
    assert prepared.result["status"] == "blocked"
    assert any(
        "controlled new consent" in value["message"]
        for value in prepared.result["blocking_reasons"]
    )
