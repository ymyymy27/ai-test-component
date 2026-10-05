"""A frozen step reference must resolve to independently saved exact content."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from aitest.application.execution.registration import _initial_domain
from aitest.application.execution.step_content import (
    StepContentReader,
    freeze_initial_step_contents,
)
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import case_content_digest, case_to_payload
from aitest.application.planning.substrate import CommittedRecord
from aitest.domain.execution.runs import Run, Step
from tests.support.prepared_run_factory import build_scenario
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_initial_run_registration import register


@pytest.fixture
def content_basis():
    scenario = build_scenario("plain")
    prepared = scenario.prepared_run.model_copy(update={
        "case_revisions": tuple(ref.model_copy(update={
            "digest": case_content_digest(case, project_id=scenario.project.project_id)
        }) for ref, case in zip(scenario.prepared_run.case_revisions, scenario.cases, strict=True))
    })
    run, steps = _initial_domain(prepared, "test-run", payload_digest(prepared.model_dump()))
    cases = {case.case_id: case for case in scenario.cases}
    reader = SimpleNamespace(read=lambda *, aggregate_kind, record_id, revision: CommittedRecord(
        aggregate_kind, record_id, revision,
        case_to_payload(cases[record_id], project_id=run.project_id),
    ))
    material = freeze_initial_step_contents(prepared=prepared, run=run, steps=steps, reader=reader)
    item = material[0]
    record = SimpleNamespace(
        aggregate_kind="step_revision", record_id=item.step.step_revision_ref.step_revision_id,
        revision=1, payload=item.content.model_dump(mode="json"),
    )
    records = SimpleNamespace(read=lambda **kwargs: record)
    return run, item.step, record, records


def saved_domain(core, facts):
    run = TypeAdapter(Run).validate_python(core.unit_of_work.repo.read(
        aggregate_kind="run", record_id=facts.run_id, revision=1,
    ).payload)
    steps = tuple(TypeAdapter(Step).validate_python(core.unit_of_work.repo.read(
        aggregate_kind="step", record_id=step.step_id, revision=1,
    ).payload) for step in facts.steps)
    return run, steps


def test_initial_step_reference_has_actual_content_in_the_same_authority(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    facts = register(core, prepared)
    for step in facts.steps:
        reference = step.step_revision_ref
        assert core.unit_of_work.repo.current_revision(
            "step_revision", reference.step_revision_id
        ) == reference.revision_no
        record = core.unit_of_work.repo.read(
            aggregate_kind="step_revision", record_id=reference.step_revision_id,
            revision=reference.revision_no,
        )
        assert record.payload["project_id"] == inputs.project_id
        assert record.payload["run_id"] == facts.run_id
        assert record.payload["step_id"] == step.step_id
        assert record.payload["case_content"]["case_id"] == step.case_id
        assert payload_digest(record.payload) == reference.digest
    run, steps = saved_domain(core, facts)
    bodies = tuple(StepContentReader(core.unit_of_work.repo).read(run=run, step=s) for s in steps)
    assert all(body.prepared_run_ref.prepared_run_id == prepared["prepared_run_id"]
               for body in bodies)
    assert all(body.checked_case().steps[body.case_step_index] == body.frozen_step.objective
               for body in bodies)


def test_content_can_be_read_after_restart_and_each_new_run_has_its_own_body(authoritative):
    from aitest.bootstrap import assemble_workspace_core

    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    first = register(core, prepared)
    second = register(core, prepared, request="second-request", intent="second-run-intent")
    assert not {s.step_revision_ref.step_revision_id for s in first.steps}.intersection(
        s.step_revision_ref.step_revision_id for s in second.steps
    )
    original_run, original_steps = saved_domain(core, first)
    before = tuple(StepContentReader(core.unit_of_work.repo).read(run=original_run, step=s)
                   for s in original_steps)
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(core.workspace.root, instance_id="step-content-restarted")
    try:
        run, steps = saved_domain(restarted, first)
        after = tuple(StepContentReader(restarted.unit_of_work.repo).read(run=run, step=s)
                      for s in steps)
        assert after == before
        assert all(body.run_id == first.run_id for body in after)
        assert register(restarted, prepared, request="restarted-replay") == first
    finally:
        restarted.lifetime_lock.release()


def test_step_content_staging_and_publication_failures_are_atomic(authoritative, monkeypatch):
    from aitest.infrastructure.file_store.commit_manifest import FileCommitStore

    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    original = core.unit_of_work.stage_record
    for kind in (
        "step_revision", "run", "execution_intent", "execution_facts_current", "execution_facts"
    ):
        def fail(failing_kind=kind, **kwargs):
            if kwargs["aggregate_kind"] == failing_kind:
                raise OSError("controlled step content publication failure")
            return original(**kwargs)

        monkeypatch.setattr(core.unit_of_work, "stage_record", fail)
        with pytest.raises(OSError, match="controlled"):
            register(core, prepared, request="failure-" + kind)
        assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
        assert core.unit_of_work.repo._load()["intents"].get("register-intent") is None
    monkeypatch.setattr(core.unit_of_work, "stage_record", original)
    facts = register(core, prepared, request="atomic-retry")
    run, steps = saved_domain(core, facts)
    assert all(StepContentReader(core.unit_of_work.repo).read(run=run, step=s) for s in steps)


def test_lost_commit_response_recalls_exact_content_without_duplicate_revision(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    original = core.unit_of_work.commit

    def lose_response(request):
        original(request)
        raise OSError("controlled response lost after commit")

    monkeypatch.setattr(core.unit_of_work, "commit", lose_response)
    with pytest.raises(OSError, match="response lost"):
        register(core, prepared)
    sequence = core.unit_of_work.current_commit_sequence()
    monkeypatch.setattr(core.unit_of_work, "commit", original)
    facts = register(core, prepared, request="lost-response-retry")
    assert core.unit_of_work.current_commit_sequence() == sequence
    run, steps = saved_domain(core, facts)
    assert all(StepContentReader(core.unit_of_work.repo).read(run=run, step=s) for s in steps)
    assert all(core.unit_of_work.repo.current_revision(
        "step_revision", s.step_revision_ref.step_revision_id
    ) == 1 for s in steps)


@pytest.mark.parametrize("field,value", [
    ("aggregate_kind", "case"), ("record_id", "another-content"),
    ("revision", 2), ("revision", True), ("payload", None),
])
def test_exact_content_envelope_and_shape_are_required(content_basis, field, value):
    run, step, record, records = content_basis
    setattr(record, field, value)
    with pytest.raises(ValueError, match="envelope|digest"):
        StepContentReader(records).read(run=run, step=step)


@pytest.mark.parametrize("field,value", [
    ("project_id", "other-project"), ("origin_workspace_id", "other-workspace"),
    ("run_id", "other-run"), ("step_id", "other-step"),
    ("case_step_index", 900), ("case_step_index", True),
    ("schema_version", "aitest.step-content/0.1"),
])
def test_self_consistent_digest_does_not_authorize_foreign_or_invalid_content(
    content_basis, field, value
):
    run, step, record, records = content_basis
    record.payload[field] = value
    step = replace(step, step_revision_ref=replace(
        step.step_revision_ref, digest=payload_digest(record.payload)
    ))
    with pytest.raises(ValueError):
        StepContentReader(records).read(run=run, step=step)


@pytest.mark.parametrize("section,field,value", [
    ("case_content", "expected", "different expected"),
    ("case_content", "project_id", "foreign-case"),
    ("case_revision_ref", "case_id", "foreign-case"),
    ("case_revision_ref", "revision", True),
    ("case_revision_ref", "digest", "sha256:unverified"),
    ("frozen_step", "objective", "different step"),
    ("frozen_step", "expected", "different expected"),
    ("frozen_step", "layer", "L3"),
    ("plan_revision_ref", "revision_no", True),
    ("plan_revision_ref", "digest", "another-plan"),
    ("prepared_run_ref", "prepared_run_id", "another-preparation"),
    ("prepared_run_ref", "digest", "another-preparation-digest"),
    ("prepared_run_ref", "record_revision", True),
])
def test_self_consistent_body_digest_still_checks_full_case_step_and_frozen_basis(
    content_basis, section, field, value
):
    run, step, record, records = content_basis
    record.payload[section][field] = value
    step = replace(step, step_revision_ref=replace(
        step.step_revision_ref, digest=payload_digest(record.payload)
    ))
    with pytest.raises(ValueError):
        StepContentReader(records).read(run=run, step=step)


def test_body_change_outside_step_text_is_detected(content_basis):
    run, step, record, records = content_basis
    record.payload["case_content"]["verification_method"] = "changed verification"
    with pytest.raises(ValueError, match="digest"):
        StepContentReader(records).read(run=run, step=step)


def test_reader_uses_exact_warehouse_revision_even_when_a_newer_one_exists(content_basis):
    run, step, record, _ = content_basis
    newer = deepcopy(record)
    newer.revision = 2
    newer.payload["case_content"]["expected"] = "changed current content"
    records = {1: record, 2: newer}
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return records[kwargs["revision"]]

    body = StepContentReader(SimpleNamespace(read=read)).read(run=run, step=step)
    assert body.case_content == record.payload["case_content"]
    assert calls == [{"aggregate_kind": "step_revision",
                     "record_id": step.step_revision_ref.step_revision_id, "revision": 1}]
    records.pop(1)
    with pytest.raises(KeyError):
        StepContentReader(SimpleNamespace(read=read)).read(run=run, step=step)
    assert all(call["revision"] == 1 for call in calls)
