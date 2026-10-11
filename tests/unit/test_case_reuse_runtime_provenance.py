"""Saved files/transactions with synthetic source authority, never real reuse AC."""

from dataclasses import replace

import pytest

from aitest.application.execution.reuse_sources import CaseReuseSourceReader
from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import case_content_digest, case_to_payload
from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.contracts.prepared_run import CaseRevisionRef, RunDriverFact
from aitest.domain.planning.plans import RunDriver
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.legacy_execution_snapshot import stage_legacy_snapshot
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_default_source_analysis import dispatch
from tests.unit.test_persisted_runtime_revision import (
    apply,
    initial_basis,
    revision_request,
)


def reader(core):
    repo = FileUnitOfWork(core.workspace.root).repo
    return CaseReuseSourceReader(repo, PortsRecordReader(repo))


def read(core, facts, case_id):
    return reader(core).read(
        project_id=facts.project_id, run_id=facts.run_id, case_id=case_id,
        reference=SnapshotContentRef.of(facts),
    )


def snapshot(core, facts, label):
    unit = core.unit_of_work
    unit.begin(label, facts.project_id)
    try:
        # An old/untrusted saved record bypasses today's guarded publisher. The
        # history reader must independently reject it, even with a valid digest.
        result = stage_legacy_snapshot(unit, facts, facts)
        unit.commit()
        return result
    except BaseException:
        unit.rollback()
        raise


def test_source_rejects_self_consistent_content_without_a_saved_revision_chain(authoritative):
    core, inputs, _ = authoritative
    _, cases, before = initial_basis(core, inputs)
    case = cases[0]
    original = read(core, before, case.case_id)
    changed = replace(case, revision=2, steps=tuple(x + " substituted" for x in case.steps))
    unit = core.unit_of_work
    unit.begin("unlinked-content", before.project_id)
    updates = {}
    try:
        unit.stage_record(
            aggregate_kind="case", record_id=changed.case_id, expected_revision=1,
            payload=case_to_payload(changed, project_id=before.project_id),
        )
        for item in original.steps:
            body = item.content.model_copy(update={
                "case_revision_ref": CaseRevisionRef(
                    case_id=changed.case_id, revision=2,
                    digest=case_content_digest(changed, project_id=before.project_id),
                ),
                "frozen_step": item.content.frozen_step.model_copy(update={
                    "objective": changed.steps[item.content.case_step_index],
                }),
                "case_content": case_to_payload(changed, project_id=before.project_id),
            })
            body.checked_case()
            identity = "unlinked-" + item.step.step_id
            unit.stage_record(
                aggregate_kind="step_revision", record_id=identity, expected_revision=0,
                payload=body.model_dump(mode="json"),
            )
            updates[item.step.step_id] = item.step.step_revision_ref.model_copy(update={
                "step_revision_id": identity,
                "digest": payload_digest(body.model_dump(mode="json")),
            })
        unit.commit()
    except BaseException:
        unit.rollback()
        raise
    forged = snapshot(core, before.model_copy(update={
        "steps": tuple(step.model_copy(update={"step_revision_ref": updates[step.step_id]})
                       if step.step_id in updates else step for step in before.steps),
    }), "unlinked-snapshot")
    sequence = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="runtime|initial"):
        read(core, forged, case.case_id)
    assert unit.current_commit_sequence() == sequence
    assert read(core, before, case.case_id) == original


def test_source_rejects_unproved_driver_and_revision_labels(authoritative):
    core, inputs, _ = authoritative
    _, cases, before = initial_basis(core, inputs)
    bogus = "run_plan_revision:run-revision-" + "a" * 64 + "@1"
    variants = [
        {"run": before.run.model_copy(update={"driver": RunDriverFact.STEPWISE})},
        {"runtime_revision_refs": (bogus,),
         "run": before.run.model_copy(update={"runtime_revision_refs": (bogus,)})},
    ]
    for index, changes in enumerate(variants):
        forged = snapshot(core, before.model_copy(update=changes), f"unproved-label-{index}")
        sequence = core.unit_of_work.current_commit_sequence()
        with pytest.raises((ValueError, FileNotFoundError)):
            read(core, forged, cases[0].case_id)
        assert core.unit_of_work.current_commit_sequence() == sequence


def test_exact_saved_revision_chain_is_readable_after_restart(authoritative):
    core, inputs, _ = authoritative
    plan, cases, before = initial_basis(core, inputs)
    changed = replace(
        cases[0], revision=2, steps=tuple(x + " authorized revision" for x in cases[0].steps),
    )
    response = dispatch(
        core, "save_case", project=inputs.project_id, request="save-next-case-request",
        intent="save-next-case-intent", expected=1,
        parameters={"case": case_to_payload(changed, project_id=inputs.project_id)},
    )
    assert response.error is None, response.error
    after = apply(core, plan, before, revision_request(
        before, changed, requested_driver=RunDriver.STEPWISE,
    ))
    sequence = core.unit_of_work.current_commit_sequence()
    result = read(core, after, changed.case_id)
    assert result.case_revision.revision == 2
    assert all(item.content.checked_case() == changed for item in result.steps)
    assert result.facts == after and all(item.attempt is None for item in result.steps)
    assert read(core, before, changed.case_id).case_revision.revision == 1
    assert core.unit_of_work.current_commit_sequence() == sequence
