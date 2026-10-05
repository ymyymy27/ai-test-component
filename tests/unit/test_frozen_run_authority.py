"""The real file UOW keeps the authoritative pointer on rejected progress edits."""

from dataclasses import replace

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_current_execution_snapshot import _batch, _publish
from tests.unit.test_saved_runtime_revision import runtime as runtime


@pytest.mark.parametrize("change", ["driver", "source", "runtime", "coverage"])
def test_invalid_progress_cannot_change_saved_authority(runtime, change):
    core, assessment, _, _, facts, _ = runtime
    raw = facts.model_dump(mode="json")
    if change == "driver":
        raw["run"]["driver"] = "stepwise"
    elif change == "source":
        assert raw["run"]["source_binding_digest"] is None
        raw["run"]["source_binding_digest"] = "sha256:invented-binding"
    elif change == "runtime":
        raw["runtime_revision_refs"] = raw["run"]["runtime_revision_refs"] = ["unsaved-revision"]
    else:
        raw["coverage"]["mandatory_case_ids"] = []
    previous_sequence = core.unit_of_work.current_commit_sequence()
    core.unit_of_work.begin("reject-frozen-edit", facts.project_id)
    try:
        with pytest.raises(ValueError, match="frozen run|runtime revision|frozen.*scope"):
            assessment.execution._stage_snapshot(ExecutionFacts.model_validate(raw))
            core.unit_of_work.commit("reject-frozen-edit")
        assert not core.unit_of_work.pending
    finally:
        core.unit_of_work.rollback()
    fresh = FileUnitOfWork(core.unit_of_work.workspace.root)
    assert fresh.current_commit_sequence() == previous_sequence
    assert (
        ExecutionCommitCoordinator(fresh).read_current_facts(
            project_id=facts.project_id, run_id=facts.run_id
        )
        == facts
    )


@pytest.mark.parametrize("change", ["run", "step", "coverage"])
def test_batch_refuses_a_frozen_edit_before_staging_checkpoint(tmp_path, change):
    unit = FileUnitOfWork(tmp_path)
    batch = _batch()
    previous = _publish(unit, batch, "original-publication").facts
    coordinator = ExecutionCommitCoordinator(unit)
    raw = previous.model_dump(mode="json")
    if change == "run":
        raw["run"]["driver"] = "stepwise"
    elif change == "step":
        raw["steps"][0]["required_for_case"] = False
    else:
        raw["coverage"]["mandatory_case_ids"] = []
    sequence = unit.current_commit_sequence()
    unit.begin("invalid-batch", previous.project_id)
    try:
        with pytest.raises(ValueError, match="frozen"):
            coordinator.stage(replace(batch, facts=ExecutionFacts.model_validate(raw)))
        assert not unit.pending
    finally:
        unit.rollback()
    assert unit.current_commit_sequence() == sequence
    assert (
        coordinator.read_current_facts(project_id=previous.project_id, run_id=previous.run_id)
        == previous
    )
