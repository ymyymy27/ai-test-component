"""Runtime changes consume exact publication origin; history grants no new change."""

from dataclasses import replace

import pytest

from aitest.application.execution.runtime_revision_service import RuntimeRevisionService
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_persisted_runtime_revision import (
    apply,
    initial_basis,
    revision_request,
    saved_next_case,
)


def test_controlled_runtime_plan_requires_its_proof_port(authoritative):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    next_case = saved_next_case(core, inputs, replace(cases[0], revision=2))
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="proof port"):
        RuntimeRevisionService(unit=core.unit_of_work, records=core.unit_of_work.repo).apply(
            project_id=before.project_id,
            run_id=before.run_id,
            plan=plan,
            request=revision_request(before, next_case),
            request_id="unproved-runtime-request",
            intent_id="unproved-runtime-intent",
        )
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert not core.unit_of_work.pending


@pytest.mark.parametrize("damage", ["receipt", "core_confirmation", "boolean_revision"])
def test_lost_publication_origin_never_publishes_a_runtime_change(
    authoritative, monkeypatch, damage
):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    next_case = saved_next_case(core, inputs, replace(cases[0], revision=2))
    sequence = core.unit_of_work.current_commit_sequence()
    read = core.unit_of_work.repo.read

    def changed(**kwargs):
        saved = read(**kwargs)
        if saved.payload.get("action") == "publish_plan" and damage == "receipt":
            raise KeyError("original publication effect receipt lost")
        if saved.aggregate_kind == "approval_confirmation" and damage == "core_confirmation":
            raise KeyError("original core confirmation lost")
        if saved.aggregate_kind == "plan" and damage == "boolean_revision":
            return replace(saved, revision=True)
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", changed)
    with pytest.raises(ValueError):
        apply(core, plan, before, revision_request(before, next_case))
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert not core.unit_of_work.pending


def test_original_runtime_result_survives_lost_origin_but_new_change_is_blocked(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    next_case = saved_next_case(core, inputs, replace(cases[0], revision=2))
    request = revision_request(before, next_case)
    after = apply(core, plan, before, request)
    later_case = saved_next_case(core, inputs, replace(next_case, revision=3))
    sequence = core.unit_of_work.current_commit_sequence()
    read = core.unit_of_work.repo.read

    def lost(**kwargs):
        saved = read(**kwargs)
        if saved.payload.get("action") == "publish_plan":
            raise KeyError("original publication receipt lost after runtime commit")
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", lost)
    assert apply(core, plan, before, request, request_id="replayed-runtime-request") == after
    with pytest.raises(ValueError):
        apply(
            core,
            plan,
            after,
            revision_request(after, later_case),
            intent="new-runtime-intent",
            request_id="new-runtime-request",
        )
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert not core.unit_of_work.pending
