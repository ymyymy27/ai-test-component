"""Initial run publication uses saved preparation and no invented execution."""

from dataclasses import replace

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.application.project.source_analysis import SourceAnalysisService
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare


def service(core):
    from aitest.application.execution.registration import InitialRunRegistration

    reader = PortsRecordReader(core.unit_of_work.repo)
    return InitialRunRegistration(
        unit=core.unit_of_work,
        reader=reader,
        records=core.unit_of_work.repo,
        workspace_id=core.workspace.workspace_id,
        source_analysis=SourceAnalysisService(
            reader=reader,
            unit_of_work=core.unit_of_work,
            snapshots=core.snapshot_store,
            source_control=None,
        ),
    )


def register(core, prepared, *, request="register-request", intent="register-intent", project=None):
    return service(core).register(
        project_id=project or prepared["project_id"],
        prepared_run_id=prepared["prepared_run_id"],
        request_id=request,
        intent_id=intent,
    )


def test_initial_run_step_and_current_snapshot_publish_once_without_attempt(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    facts = register(core, prepared)
    current = ExecutionCommitCoordinator(
        core.unit_of_work, records=core.unit_of_work.repo
    ).read_current_facts(project_id=inputs.project_id, run_id=facts.run_id)
    assert current == facts
    assert facts.run.control_state.value == "not_started"
    assert facts.run.source_binding_digest is None
    assert facts.run.evidence_level is None
    assert facts.run.intent_id == prepared["intent_id"]
    assert set(facts.run.required_scope) == set(prepared["frozen_required_case_ids"])
    assert set(facts.run.selected_scope) == set(prepared["selected_case_ids"])
    assert not facts.attempts and not facts.verifications and not facts.evidence_refs
    assert all(
        step.current_attempt_id is None and step.state.value == "pending" for step in facts.steps
    )
    assert not facts.coverage.executed_attempt_ids
    assert facts.snapshot_cursor == int(core.unit_of_work.current_commit_sequence())
    assert core.unit_of_work.repo.current_revision("run", facts.run_id) == 1
    assert all(core.unit_of_work.repo.current_revision("step", s.step_id) == 1 for s in facts.steps)
    seq = core.unit_of_work.current_commit_sequence()
    assert register(core, prepared, request="retransmission") == facts
    assert core.unit_of_work.current_commit_sequence() == seq


def test_initial_run_recall_survives_restart_and_active_source_change(authoritative):
    from aitest.bootstrap import assemble_workspace_core

    core, inputs, source = authoritative
    prepared = prepare(core, inputs).result
    first = register(core, prepared)
    core.lifetime_lock.release()
    (source / "main.py").write_bytes(b"VALUE = 2\n")
    restarted = assemble_workspace_core(core.workspace.root, instance_id="registration-restarted")
    try:
        assert register(restarted, prepared, request="restarted-request") == first
        with pytest.raises(ValueError, match="source"):
            register(restarted, prepared, request="new-request", intent="new-run-intent")
    finally:
        restarted.lifetime_lock.release()


def test_foreign_preparation_is_rejected_without_consuming_registration_intent(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    seq = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="project"):
        register(core, prepared, project="other-project")
    assert core.unit_of_work.current_commit_sequence() == seq
    assert core.unit_of_work.repo._load()["intents"].get("register-intent") is None


def test_initial_publication_failure_leaves_no_run_step_or_current_authority(
    authoritative, monkeypatch
):
    from aitest.infrastructure.file_store.commit_manifest import FileCommitStore

    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    initial = FileCommitStore(core.workspace.root).read_current()["pointer"]
    original = core.unit_of_work.stage_record

    def fail_snapshot(**kwargs):
        if kwargs["aggregate_kind"] == "execution_facts":
            raise OSError("controlled initial snapshot failure")
        return original(**kwargs)

    monkeypatch.setattr(core.unit_of_work, "stage_record", fail_snapshot)
    with pytest.raises(OSError, match="controlled"):
        register(core, prepared)
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == initial
    assert core.unit_of_work.repo._load()["intents"].get("register-intent") is None
    monkeypatch.setattr(core.unit_of_work, "stage_record", original)
    facts = register(core, prepared, request="retry-registration")
    assert facts.run.control_state.value == "not_started"


def test_same_run_intent_cannot_be_rebound_to_different_preparation(authoritative):
    core, inputs, _ = authoritative
    one = prepare(core, inputs).result
    first = register(core, one)
    two = prepare(
        core,
        replace(inputs, prepare_request_id="second-preparation-business-request"),
        request="second-prepare-request",
        intent="second-prepare-intent",
    ).result
    assert two["prepared_run_id"] != one["prepared_run_id"]
    seq = core.unit_of_work.current_commit_sequence()
    with pytest.raises(ValueError, match="conflicts"):
        register(core, two, request="conflicting-request")
    assert core.unit_of_work.current_commit_sequence() == seq
    assert register(core, one) == first


def test_explicit_new_run_intent_has_distinct_run_and_step_namespace(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    one = register(core, prepared)
    two = register(core, prepared, request="new-run-request", intent="new-run-intent")
    assert one.run_id != two.run_id
    assert not {s.step_id for s in one.steps}.intersection(s.step_id for s in two.steps)
    assert one.run.required_scope == two.run.required_scope
    assert not one.attempts and not two.attempts


def test_registration_recall_reads_original_snapshot_even_if_current_has_advanced(authoritative):
    from aitest.contracts.execution_facts import RunControlStateFact

    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    one = register(core, prepared)
    coordinator = ExecutionCommitCoordinator(core.unit_of_work, records=core.unit_of_work.repo)
    # Controlled later projection exercises the immutable registration receipt.
    later = one.model_copy(
        update={
            "run_revision": 2,
            "run": one.run.model_copy(
                update={"run_revision": 2, "control_state": RunControlStateFact.PAUSED}
            ),
        }
    )
    core.unit_of_work.begin(
        "later-projection-request", inputs.project_id, intent_id="later-projection-intent"
    )
    coordinator._stage_snapshot(later)
    core.unit_of_work.commit("later-projection-request")
    assert coordinator.read_current_facts(project_id=inputs.project_id, run_id=one.run_id) != one
    assert register(core, prepared, request="recall-initial-request") == one
