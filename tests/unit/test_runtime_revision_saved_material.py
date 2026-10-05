"""Missing material and substituted lineage fail without publishing a partial revision."""

import json
from dataclasses import replace

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.runtime_revision import SavedRuntimeRevisionReader
from aitest.domain.planning.runtime_revision import RuntimeRevisionRefused
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_persisted_runtime_revision import (
    apply,
    initial_basis,
    revision_request,
    saved_next_case,
)


def test_missing_unaffected_initial_content_blocks_first_revision_atomically(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    case2 = saved_next_case(core, inputs, replace(cases[0], revision=2))
    request = revision_request(before, case2)
    records = core.unit_of_work.repo
    original = records.read
    unrelated = next(step for step in before.steps if step.case_id != case2.case_id)
    sequence = core.unit_of_work.current_commit_sequence()

    def read(**kwargs):
        if (kwargs["aggregate_kind"], kwargs["record_id"]) == (
            "step_revision",
            unrelated.step_revision_ref.step_revision_id,
        ):
            raise KeyError("controlled missing unaffected initial content")
        return original(**kwargs)

    monkeypatch.setattr(records, "read", read)
    with pytest.raises(KeyError, match="controlled missing"):
        apply(core, plan, before, request)
    monkeypatch.setattr(records, "read", original)
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert (
        ExecutionCommitCoordinator(core.unit_of_work, records=records).read_current_facts(
            project_id=inputs.project_id, run_id=before.run_id
        )
        == before
    )
    assert len(apply(core, plan, before, request).runtime_revision_refs) == 1


def test_exact_saved_lineage_and_current_basis_cannot_be_substituted(authoritative, monkeypatch):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    case2 = saved_next_case(core, inputs, replace(cases[0], revision=2))
    after = apply(core, plan, before, revision_request(before, case2))
    reader = SavedRuntimeRevisionReader(core.unit_of_work.repo)
    reference = after.runtime_revision_refs[-1]
    record = reader.read_record(project_id=inputs.project_id, reference=reference)
    original = core.unit_of_work.repo.read
    assert reader.read_effective_cases(facts=after, plan=plan, initial_cases=cases)[0] == case2
    alterations = {
        "sequence": lambda raw: raw.update(revision_no=2),
        "previous": lambda raw: raw.update(previous_revision_ref=reference),
        "workspace": lambda raw: raw.update(origin_workspace_id="foreign-workspace"),
        "effective_case": lambda raw: raw["effective_case_refs"][0].update(revision=99),
        "previous_step": lambda raw: raw["step_changes"][0]["previous_ref"].update(digest="other"),
        "consumers": lambda raw: raw.update(invalidated_consumer_attempt_ids=["made-up-attempt"]),
        "input": lambda raw: raw["request_payload"].update(reason="substituted reason"),
        "result_digest": lambda raw: raw["result_snapshot"].update(digest="sha256:other"),
    }
    for alteration in alterations.values():

        def read(alteration=alteration, **kwargs):
            saved = original(**kwargs)
            if (
                kwargs["aggregate_kind"] == "run_plan_revision"
                and kwargs["record_id"] == record.record_id
            ):
                raw = json.loads(json.dumps(saved.payload))
                alteration(raw)
                return replace(saved, payload=raw)
            return saved

        monkeypatch.setattr(core.unit_of_work.repo, "read", read)
        with pytest.raises(ValueError):
            reader.read_effective_cases(facts=after, plan=plan, initial_cases=cases)
    monkeypatch.setattr(core.unit_of_work.repo, "read", original)
    replacements = (
        after.model_copy(update={"steps": after.steps[:-1]}),
        after.model_copy(update={"run": after.run.model_copy(update={"environment_ref": "other"})}),
        after.model_copy(update={"run": after.run.model_copy(update={"rules_revision": "other"})}),
        after.model_copy(
            update={"run": after.run.model_copy(update={"source_binding_digest": "other"})}
        ),
        after.model_copy(
            update={
                "steps": (
                    after.steps[0].model_copy(update={"registered_entry_ref": "other"}),
                    *after.steps[1:],
                )
            }
        ),
    )
    for replacement in replacements:
        with pytest.raises(ValueError):
            reader.read_effective_cases(facts=replacement, plan=plan, initial_cases=cases)


def test_stale_snapshot_and_changed_layout_never_append_a_revision(authoritative):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    next_case = saved_next_case(
        core, inputs, replace(cases[0], revision=2, steps=(*cases[0].steps, "extra step"))
    )
    request = revision_request(before, next_case)
    sequence = core.unit_of_work.current_commit_sequence()
    with pytest.raises(RuntimeRevisionRefused):
        apply(
            core,
            plan,
            before,
            replace(request, observed_snapshot_cursor=before.snapshot_cursor - 1),
            intent="stale-revision",
        )
    with pytest.raises(ValueError, match="explicit registration"):
        apply(core, plan, before, request, intent="layout-change")
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert (
        ExecutionCommitCoordinator(
            core.unit_of_work, records=core.unit_of_work.repo
        ).read_current_facts(project_id=inputs.project_id, run_id=before.run_id)
        == before
    )
