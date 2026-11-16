"""Source mapping uses real saved preparation; it does not certify R or human AC."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from aitest.application.execution.facts import execution_payload_digest
from aitest.application.execution.reuse_sources import CaseReuseSourceReader
from aitest.application.execution.runtime_revision import SnapshotContentRef
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_initial_run_registration import register


def test_exact_whole_case_sources_survive_restart_and_do_not_mint_attempts(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    first = register(core, prepared)
    second = register(core, prepared, request="another-run", intent="another-run-intent")
    repo = FileUnitOfWork(core.workspace.root).repo
    reader = CaseReuseSourceReader(repo, PortsRecordReader(repo))
    for case_id in prepared["selected_case_ids"]:
        one = reader.read(
            project_id=inputs.project_id, run_id=first.run_id, case_id=case_id,
            reference=SnapshotContentRef.of(first),
        )
        two = reader.read(
            project_id=inputs.project_id, run_id=second.run_id, case_id=case_id,
            reference=SnapshotContentRef.of(second),
        )
        assert one.facts == first and two.facts == second
        assert one.original_preparation.prepared_run_id == prepared["prepared_run_id"]
        assert {item.step.step_id for item in one.steps}.isdisjoint(
            item.step.step_id for item in two.steps
        )
        assert [item.content.frozen_step.step_id for item in one.steps] == [
            item.content.frozen_step.step_id for item in two.steps
        ]
        assert [item.content.case_step_index for item in one.steps] == list(range(len(one.steps)))
        assert one.case_revision == two.case_revision
        assert all(item.attempt is None for item in (*one.steps, *two.steps))
    assert not first.attempts and not second.attempts
    assert not first.coverage.executed_attempt_ids and not second.coverage.executed_attempt_ids


def test_saved_source_rejects_incomplete_or_relabelled_material_without_writes(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    facts = register(core, prepared)
    repo = core.unit_of_work.repo
    reader = CaseReuseSourceReader(repo, PortsRecordReader(repo))
    case_id = prepared["selected_case_ids"][0]
    initial = reader.read(
        project_id=inputs.project_id, run_id=facts.run_id, case_id=case_id,
        reference=SnapshotContentRef.of(facts),
    )
    original = repo.read
    sequence = core.unit_of_work.current_commit_sequence()
    for change in [
        "missing", "physical", "optional", "ordinal", "local", "run", "case",
        "environment", "scope", "duplicate_scope",
    ]:
        snapshot = deepcopy(facts.model_dump(mode="json"))
        target = next(item for item in snapshot["steps"] if item["case_id"] == case_id)
        reference_id = target["step_revision_ref"]["step_revision_id"]
        body = deepcopy(initial.steps[0].content.model_dump(mode="json"))
        if change == "missing":
            snapshot["steps"].remove(target)
            del snapshot["current_attempt_by_step"][target["step_id"]]
        elif change == "physical":
            old = target["step_id"]
            target["step_id"] = "another-run-step"
            snapshot["current_attempt_by_step"][target["step_id"]] = (
                snapshot["current_attempt_by_step"].pop(old)
            )
        elif change == "optional":
            target["required_for_case"] = False
        elif change == "ordinal":
            target["ordinal"] += 100
        elif change == "local":
            body["frozen_step"]["step_id"] = "another-local-step"
            target["step_revision_ref"]["digest"] = payload_digest(body)
        elif change == "environment":
            snapshot["run"]["environment_ref"] = "another-environment@1"
        elif change == "scope":
            snapshot["coverage"]["mandatory_case_ids"] = []
        elif change == "duplicate_scope":
            snapshot["coverage"]["selected_case_ids"].append(case_id)

        def read(
            *, snapshot=snapshot, change=change, reference_id=reference_id, body=body, **kwargs
        ):
            saved = original(**kwargs)
            raw = deepcopy(saved.payload)
            if kwargs["aggregate_kind"] == "execution_facts":
                raw = snapshot
            elif kwargs["aggregate_kind"] == "step_revision" and change == "local":
                if kwargs["record_id"] == reference_id:
                    raw = body
            elif kwargs["aggregate_kind"] == "run" and change == "run":
                raw["frozen_input_refs"][0]["value_digest"] = "another-preparation"
            elif kwargs["aggregate_kind"] == "case" and change == "case":
                raw["steps"] = ["different saved case"]
            return SimpleNamespace(
                aggregate_kind=saved.aggregate_kind, record_id=saved.record_id,
                revision=saved.revision, payload=raw,
            )

        with monkeypatch.context() as patch:
            patch.setattr(repo, "read", read)
            with pytest.raises(ValueError):
                reader.read(
                    project_id=inputs.project_id, run_id=facts.run_id, case_id=case_id,
                    reference=SnapshotContentRef(
                        snapshot_commit_id=facts.snapshot_commit_id,
                        snapshot_cursor=facts.snapshot_cursor,
                        digest=execution_payload_digest(snapshot),
                    ),
                )
        assert core.unit_of_work.current_commit_sequence() == sequence
    with pytest.raises(ValueError, match="scope"):
        reader.read(
            project_id=inputs.project_id, run_id=facts.run_id, case_id="unknown-case",
            reference=SnapshotContentRef.of(facts),
        )
