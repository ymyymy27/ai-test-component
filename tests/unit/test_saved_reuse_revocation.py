"""Reuse denial comes from committed starts; candidates are not eligibility proof."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.dependencies import CaseReuseBasis, invalidate_reuse_bases
from aitest.domain.execution.runs import AttemptState, Step, StepLevel
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import fixture_coordinator
from tests.unit.test_serial_runner import FakeExecutionPort, _attempt, _request

BASES = (
    CaseReuseBasis("case-1", ("historic-source",)),
    CaseReuseBasis("independent-case", ("independent-source",)),
)


def _runner(root, *, optional=False, port=None, unit=None):
    unit = unit or FileUnitOfWork(root)
    attempt, request = _attempt(), _request()
    step = Step(
        attempt.step_id,
        attempt.run_id,
        1,
        "case-1",
        StepLevel.L2,
        attempt.step_revision_ref,
        required_for_case=not optional,
    )
    coordinator = fixture_coordinator(unit, ((attempt, request),), steps=(step,))
    runner = SerialRunner(
        port or FakeExecutionPort(), commit_coordinator=coordinator, reuse_bases=BASES
    )
    return runner, coordinator, unit


@pytest.mark.parametrize("optional", [False, True])
def test_first_new_attempt_revokes_whole_case_without_a_local_previous_attempt(tmp_path, optional):
    runner, coordinator, unit = _runner(tmp_path, optional=optional)
    initial = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    runner.start_attempt(_attempt(), _request())
    assert [item.case_id for item in runner.reuse_invalidations] == ["case-1"]
    assert runner.reuse_invalidations[0].source_attempt_ids == ("historic-source",)
    assert runner.reuse_invalidations[0].reason == "case_new_attempt"
    assert unit.read(
        aggregate_kind="execution_facts", record_id=initial.snapshot_commit_id, revision=1
    ).payload == initial.model_dump(mode="json")


@pytest.mark.parametrize("optional", [False, True])
def test_restart_and_original_intent_replay_rederive_denial_without_memory(tmp_path, optional):
    runner, _, unit = _runner(tmp_path, optional=optional)
    runner.start_attempt(_attempt(), _request())
    sequence = unit.current_commit_sequence()
    recovered, _, _ = _runner(tmp_path, optional=optional)
    assert recovered.reuse_invalidations == ()
    recovered.start_attempt(_attempt(), _request())
    assert [item.case_id for item in recovered.reuse_invalidations] == ["case-1"]
    assert unit.current_commit_sequence() == sequence


@pytest.mark.parametrize(
    "state",
    [
        AttemptState.CANCELLED,
        AttemptState.EXECUTION_ERROR,
        AttemptState.PENDING_VERIFICATION,
        AttemptState.UNKNOWN,
    ],
)
def test_failed_or_unknown_start_never_restores_a_saved_reuse_candidate(tmp_path, state):
    runner, coordinator, _ = _runner(tmp_path)
    started = runner.start_attempt(_attempt(), _request())
    checkpoint = coordinator.read_checkpoint(project_id="project-1", attempt_id=started.attempt_id)
    coordinator.commit_checkpoint(
        project_id="project-1",
        checkpoint=replace(checkpoint, attempt=replace(checkpoint.attempt, state=state)),
    )
    restarted = ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    denied = restarted.read_reuse_invalidations(project_id="project-1", run_id="run-1", bases=BASES)
    assert [item.case_id for item in denied] == ["case-1"]


def test_actual_start_failure_keeps_the_committed_intent_denial(tmp_path):
    port = FakeExecutionPort()

    def fail(request):
        raise OSError("injected start failure")

    port.start = fail
    runner, _, _ = _runner(tmp_path, port=port)
    with pytest.raises(OSError, match="start failure"):
        runner.start_attempt(_attempt(), _request())
    restarted = ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    denied = restarted.read_reuse_invalidations(project_id="project-1", run_id="run-1", bases=BASES)
    assert [item.case_id for item in denied] == ["case-1"]


def test_unpublished_claim_does_not_create_reuse_denial(tmp_path, monkeypatch):
    runner, coordinator, unit = _runner(tmp_path)
    sequence = unit.current_commit_sequence()
    original = unit.stage_record

    def fail(**kwargs):
        if kwargs["aggregate_kind"] == "execution_checkpoint":
            raise OSError("injected stage failure")
        return original(**kwargs)

    monkeypatch.setattr(unit, "stage_record", fail)
    with pytest.raises(OSError, match="stage failure"):
        runner.start_attempt(_attempt(), _request())
    assert (
        coordinator.read_reuse_invalidations(project_id="project-1", run_id="run-1", bases=BASES)
        == ()
    )
    assert runner.reuse_invalidations == ()
    assert unit.current_commit_sequence() == sequence


def test_reply_loss_is_recovered_from_the_published_claim_before_external_start(
    tmp_path, monkeypatch
):
    runner, _, unit = _runner(tmp_path)
    original = unit.commit

    def lose_reply():
        original()
        raise OSError("injected lost reply")

    monkeypatch.setattr(unit, "commit", lose_reply)
    with pytest.raises(OSError, match="lost reply"):
        runner.start_attempt(_attempt(), _request())
    recovered, _, _ = _runner(tmp_path)
    result = recovered.start_attempt(_attempt(), _request())
    assert result.state is AttemptState.PENDING_VERIFICATION
    assert result.execution_handle_ref is None
    assert len(recovered.reuse_invalidations) == 1
    assert recovered._execution_port.started == []


@pytest.mark.parametrize("corrupt", ["digest", "project_id", "snapshot_commit_id"])
def test_bad_saved_reference_cannot_fall_back_to_unrevoked_history(tmp_path, monkeypatch, corrupt):
    runner, _, _ = _runner(tmp_path)
    runner.start_attempt(_attempt(), _request())
    unit = FileUnitOfWork(tmp_path)
    original = unit.read

    def wrong(**kwargs):
        record = original(**kwargs)
        if kwargs["aggregate_kind"] == "execution_facts_current":
            payload = dict(record.payload)
            payload[corrupt] = "unverified"
            return SimpleNamespace(
                aggregate_kind=record.aggregate_kind,
                record_id=record.record_id,
                revision=record.revision,
                payload=payload,
            )
        return record

    monkeypatch.setattr(unit, "read", wrong)
    with pytest.raises(ValueError):
        ExecutionCommitCoordinator(unit).read_reuse_invalidations(
            project_id="project-1", run_id="run-1", bases=BASES
        )


def test_missing_registered_run_is_unknown_not_no_denial(tmp_path):
    with pytest.raises(ValueError, match="registered"):
        ExecutionCommitCoordinator(FileUnitOfWork(tmp_path)).read_reuse_invalidations(
            project_id="project-1", run_id="missing", bases=BASES
        )


def test_whole_case_new_attempt_and_actual_source_closure_have_distinct_reasons():
    bases = (*BASES, CaseReuseBasis("consumer-case", ("invalidated-middle", "unrelated")))
    result = invalidate_reuse_bases(
        bases, affected_upstream_attempt_ids=("invalidated-middle",), started_case_ids=("case-1",)
    )
    assert [(item.case_id, item.source_attempt_ids, item.reason) for item in result] == [
        ("case-1", ("historic-source",), "case_new_attempt"),
        ("consumer-case", ("invalidated-middle",), "reuse_basis_invalidated"),
    ]


@pytest.mark.parametrize("historical_middle", [False, True])
def test_saved_actual_transitive_consumers_are_denied_and_independent_source_survives(
    tmp_path, historical_middle
):
    from tests.unit.test_current_attempt_transition_integrity import (
        _new_start,
        _saved_graph,
    )

    unit, coordinator, _, domain = _saved_graph(tmp_path, historical_middle=historical_middle)
    bases = (
        CaseReuseBasis("consumer-case", ("attempt-3",)),
        CaseReuseBasis("independent-case", ("attempt-4",)),
    )
    assert (
        coordinator.read_reuse_invalidations(project_id="project-1", run_id="run-1", bases=bases)
        == ()
    )
    runner, attempt, request = _new_start(domain[0], FakeExecutionPort(), coordinator)
    runner.start_attempt(attempt, request)
    restarted = ExecutionCommitCoordinator(FileUnitOfWork(tmp_path))
    sequence = unit.current_commit_sequence()
    denied = restarted.read_reuse_invalidations(project_id="project-1", run_id="run-1", bases=bases)
    assert [(item.case_id, item.source_attempt_ids, item.reason) for item in denied] == [
        ("consumer-case", ("attempt-3",), "reuse_basis_invalidated")
    ]
    assert unit.current_commit_sequence() == sequence


@pytest.mark.parametrize("changed", ["project_id", "run_id"])
def test_runner_candidates_cannot_leak_denial_to_a_second_context(tmp_path, changed):
    runner, _, unit = _runner(tmp_path)
    runner.start_attempt(_attempt(), _request())
    sequence = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="one project/run context"):
        runner.start_attempt(_attempt(), replace(_request(), **{changed: "another-context"}))
    assert unit.current_commit_sequence() == sequence
    assert len(runner._execution_port.started) == 1


def test_caller_previous_attempt_hint_cannot_invalidate_an_independent_source(tmp_path):
    _, coordinator, _ = _runner(tmp_path)
    runner = SerialRunner(
        FakeExecutionPort(),
        commit_coordinator=coordinator,
        reuse_bases=BASES,
        previous_attempt_ids_by_step={"step-1": ("independent-source",)},
    )
    runner.start_attempt(_attempt(), _request())
    assert [item.case_id for item in runner.reuse_invalidations] == ["case-1"]


def test_terminal_authority_repairs_stale_sidecar_without_a_business_commit(tmp_path):
    from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
    from aitest.infrastructure.file_store.spool import FileSpoolStore
    from tests.unit.test_current_attempt_transition_integrity import (
        _new_start,
        _record,
        _saved_graph,
    )

    unit, coordinator, _, domain = _saved_graph(tmp_path)
    sidecars = FileCheckpointStore(tmp_path)
    sidecars.persist(_record(domain[1]))
    starter, attempt, request = _new_start(domain[0], FakeExecutionPort(), coordinator)
    starter.start_attempt(attempt, request)
    authoritative = coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-2")
    before = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    sequence = unit.current_commit_sequence()
    port = FakeExecutionPort()
    SerialRunner(
        port, FileSpoolStore(tmp_path), checkpoint_store=sidecars, commit_coordinator=coordinator
    ).recover_pending()
    assert sidecars.load("attempt-2") == authoritative
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == before
    assert unit.current_commit_sequence() == sequence
    assert port.started == []


def test_stale_sidecar_write_failure_is_explicit_and_retry_uses_original_authority(
    tmp_path, monkeypatch
):
    from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
    from aitest.infrastructure.file_store.spool import FileSpoolStore
    from tests.unit.test_current_attempt_transition_integrity import (
        _new_start,
        _record,
        _saved_graph,
    )

    unit, coordinator, _, domain = _saved_graph(tmp_path)
    sidecars = FileCheckpointStore(tmp_path)
    sidecars.persist(_record(domain[1]))
    starter, attempt, request = _new_start(domain[0], FakeExecutionPort(), coordinator)
    starter.start_attempt(attempt, request)
    authoritative = coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-2")
    original = sidecars.persist

    def fail(record):
        raise OSError("injected sidecar repair failure")

    monkeypatch.setattr(sidecars, "persist", fail)
    sequence = unit.current_commit_sequence()
    runner = SerialRunner(
        FakeExecutionPort(),
        FileSpoolStore(tmp_path),
        checkpoint_store=sidecars,
        commit_coordinator=coordinator,
    )
    with pytest.raises(OSError, match="sidecar repair failure"):
        runner.recover_pending()
    assert unit.current_commit_sequence() == sequence
    monkeypatch.setattr(sidecars, "persist", original)
    runner.recover_pending()
    assert sidecars.load("attempt-2") == authoritative
    assert unit.current_commit_sequence() == sequence
    assert runner._execution_port.started == []
