"""Controlled confirmation persists exact basis facts, not execution or verification."""

from dataclasses import replace

from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.prepared_run import AssertionBasisStateFact, ConfirmationRef
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_default_source_analysis import AGENT, dispatch


def confirm(core, inputs, **kwargs):
    entry = inputs.assertion_bases[0]
    return dispatch(
        core,
        "confirm_basis",
        project=inputs.project_id,
        parameters={
            "case_id": entry.case_id,
            "case_revision": 1,
            "basis_revision": entry.basis_revision,
            "basis_text_digest": entry.basis_text_digest,
        },
        **kwargs,
    )


def test_default_confirmation_freezes_exact_basis_and_preserves_plan_and_case(authoritative):
    core, inputs, _ = authoritative
    plan = core.unit_of_work.repo.read(
        aggregate_kind="plan", record_id=inputs.plan_revision.revision_id, revision=1
    )
    case_id = inputs.assertion_bases[0].case_id
    case = core.unit_of_work.repo.read(aggregate_kind="case", record_id=case_id, revision=1)
    result = confirm(core, inputs)
    assert result.error is None, result.error
    saved = result.result
    seq = core.unit_of_work.current_commit_sequence()
    assert saved["confirmed_at_commit"] == str(seq)
    reference = ConfirmationRef(
        **{
            key: saved[key]
            for key in ("confirmation_id", "case_id", "basis_revision", "confirmed_at_commit")
        }
    )
    basis = inputs.assertion_bases[0].model_copy(
        update={
            "assertion_basis_state": AssertionBasisStateFact.CONFIRMED,
            "confirmation_refs": (reference,),
        }
    )
    inputs = replace(inputs, assertion_bases=(basis, *inputs.assertion_bases[1:]))
    prepared = prepare(core, inputs)
    assert prepared.error is None, prepared.error
    assert prepared.result["status"] == "prepared", prepared.result
    assert core.unit_of_work.repo.read(aggregate_kind="case", record_id=case_id, revision=1) == case
    assert (
        core.unit_of_work.repo.read(
            aggregate_kind="plan", record_id=inputs.plan_revision.revision_id, revision=1
        )
        == plan
    )
    assert core.unit_of_work.repo.current_revision("attempt", "missing") == 0
    assert core.unit_of_work.repo.current_revision("execution_facts", "missing") == 0


def test_original_confirmation_recalled_after_restart_and_changed_input_conflicts(authoritative):
    core, inputs, _ = authoritative
    first = confirm(core, inputs, intent="confirm-original")
    assert first.error is None
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(core.workspace.root, instance_id="confirm-restarted")
    try:
        seq = restarted.unit_of_work.current_commit_sequence()
        recalled = confirm(
            restarted, inputs, intent="confirm-original", request="confirm-retransmit"
        )
        assert recalled.error is None
        assert recalled.result == first.result
        changed = replace(
            inputs,
            assertion_bases=(
                inputs.assertion_bases[0].model_copy(
                    update={"basis_text_digest": "sha256:different"}
                ),
                *inputs.assertion_bases[1:],
            ),
        )
        conflict = confirm(
            restarted, changed, intent="confirm-original", request="confirm-conflict"
        )
        assert conflict.error.code == "INTENT_CONFLICT"
        assert restarted.unit_of_work.current_commit_sequence() == seq
    finally:
        restarted.lifetime_lock.release()


def test_agent_cannot_self_confirm_and_mismatched_basis_does_not_consume_intent(authoritative):
    core, inputs, _ = authoritative
    seq = core.unit_of_work.current_commit_sequence()
    refused = confirm(core, inputs, session=AGENT)
    assert refused.error is not None
    changed = replace(
        inputs,
        assertion_bases=(
            inputs.assertion_bases[0].model_copy(update={"basis_text_digest": "sha256:different"}),
            *inputs.assertion_bases[1:],
        ),
    )
    refused = confirm(core, changed, request="wrong-basis")
    assert refused.error.code == "B_BASIS_UNVERIFIED"
    assert core.unit_of_work.current_commit_sequence() == seq
    correct = confirm(core, inputs, request="correct-basis")
    assert correct.error is None
