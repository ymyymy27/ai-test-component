"""A new Attempt and its actual dependency closure share one authority boundary."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import project_attempt_fact
from aitest.application.execution.recovery import RecoveryAction, recover_attempt
from aitest.application.execution.runner import SerialRunner
from aitest.contracts.execution_facts import RunControlStateFact, StepRevisionRefFact, StepStateFact
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    ConsumedCondition,
    ConsumedOutput,
    RecoveryCheckpoint,
    RecoveryRecord,
    StepRevisionRef,
)
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import SavedFixtureExecutionAuthority, fixture_coordinator
from tests.support.persistent_evidence_fixture import OUTPUT_DIGEST, save_fixture_bytes
from tests.unit.test_current_execution_snapshot import _batch, _publish
from tests.unit.test_serial_runner import FakeExecutionPort, _request


def _record(attempt, stage="completed"):
    return RecoveryRecord(
        RecoveryCheckpoint(
            attempt.run_id,
            attempt.step_id,
            attempt.attempt_id,
            stage,
            resolved_input_digest=attempt.resolved_input_digest,
            side_effect_class=attempt.side_effect_class,
            execution_handle_ref=attempt.execution_handle_ref,
        ),
        attempt,
        project_id="project-1",
    )


def _saved_graph(root, middle_state=AttemptState.COMPLETED, *, historical_middle=False):
    unit = FileUnitOfWork(root)
    save_fixture_bytes(root)
    coordinator = ExecutionCommitCoordinator(unit)
    batch = _batch()
    first = replace(batch.checkpoint.attempt, capture_completeness=CaptureCompleteness.COMPLETE)
    domain = [first]
    for index in range(2, 5):
        domain.append(
            replace(
                first,
                attempt_id=f"attempt-{index}",
                step_id=f"step-{index}",
                intent_id=f"intent-{index}",
                step_revision_ref=StepRevisionRef(f"step-rev-{index}", 1, f"sha256:step-{index}"),
                consumed_outputs=(ConsumedOutput("attempt-1", OUTPUT_DIGEST, "value:a"),)
                if index == 2
                else (),
                consumed_conditions=(ConsumedCondition("attempt-2", "condition:a", "sha256:c"),)
                if index == 3
                else (),
                state=middle_state if index == 2 else AttemptState.COMPLETED,
            )
        )
    if historical_middle:
        domain.append(
            replace(
                domain[1],
                attempt_id="independent-current-2",
                intent_id="independent-intent-2",
                attempt_index=2,
                consumed_outputs=(),
                consumed_conditions=(),
            )
        )
    current = {attempt.step_id: attempt.attempt_id for attempt in domain}
    for attempt in domain[1:]:
        coordinator.commit_checkpoint(project_id="project-1", checkpoint=_record(attempt))
    steps = tuple(
        batch.facts.steps[0].model_copy(
            update={
                "step_id": attempt.step_id,
                "step_revision_ref": StepRevisionRefFact(
                    step_revision_id=attempt.step_revision_ref.step_revision_id,
                    revision_no=1,
                    digest=attempt.step_revision_ref.digest,
                ),
                "ordinal": index,
                "current_attempt_id": current[attempt.step_id],
                "state": StepStateFact(
                    "pending"
                    if attempt.state is AttemptState.INTENT_RECORDED
                    else "running"
                    if attempt.state in {AttemptState.STOP_REQUESTED, AttemptState.COLLECTING}
                    else "pending_verification"
                    if attempt.state is AttemptState.UNKNOWN
                    else attempt.state.value
                ),
            }
        )
        for index, attempt in enumerate(domain[:4], 1)
    )
    facts = batch.facts.model_copy(
        update={
            "run": batch.facts.run.model_copy(
                update={"control_state": RunControlStateFact.RUNNING}
            ),
            "steps": steps,
            "attempts": tuple(
                project_attempt_fact(
                    attempt, is_current=current[attempt.step_id] == attempt.attempt_id
                )
                for attempt in domain
            ),
            "current_attempt_by_step": current,
            "coverage": batch.facts.coverage.model_copy(
                update={
                    "executed_attempt_ids": tuple(
                        attempt.attempt_id
                        for attempt in domain
                        if current[attempt.step_id] == attempt.attempt_id
                    )
                }
            ),
        }
    )
    published = _publish(unit, replace(batch, checkpoint=_record(first), facts=facts))
    coordinator = fixture_coordinator(
        unit, (_replacement_pair(domain[0]), _replacement_pair(domain[3]))
    )
    return unit, coordinator, published, domain


def test_historical_consumption_bridge_invalidates_current_descendant_before_start(tmp_path):
    unit, coordinator, published, domain = _saved_graph(tmp_path, historical_middle=True)
    port = FakeExecutionPort()
    original = port.start
    observed = []

    def start(request):
        current = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
        attempts = {item.attempt_id: item for item in current.attempts}
        assert attempts["attempt-2"].state == attempts["attempt-3"].state == "invalidated"
        assert attempts["independent-current-2"].state == "completed"
        assert current.steps[1] == published.facts.steps[1]
        assert set(current.coverage.invalidated_step_ids) == {"step-3"}
        assert set(current.coverage.executed_attempt_ids) == {"independent-current-2", "attempt-4"}
        observed.append(current)
        return original(request)

    port.start = start
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    runner.start_attempt(attempt, request)
    assert len(observed) == len(port.started) == 1
    assert (
        unit.read(aggregate_kind="execution_checkpoint", record_id="attempt-2", revision=1).payload[
            "attempt"
        ]["state"]
        == "completed"
    )


def _replacement_pair(first):
    replacement_id = "replacement" if first.step_id == "step-1" else f"replacement-{first.step_id}"
    attempt = replace(
        first,
        attempt_id=replacement_id,
        intent_id=f"{replacement_id}-intent",
        attempt_index=2,
        state=AttemptState.INTENT_RECORDED,
        capture_completeness=CaptureCompleteness.UNKNOWN,
    )
    request = replace(
        _request(),
        step_id=first.step_id,
        attempt_id=attempt.attempt_id,
        intent_id=attempt.intent_id,
        authorization_ref=replace(
            _request().authorization_ref,
            authorization_id=f"replacement-authorization-{first.step_id}",
            intent_id=attempt.intent_id,
            step_id=first.step_id,
            step_revision_ref=first.step_revision_ref,
        ),
    )
    return attempt, request


def _new_start(first, port, coordinator):
    attempt, request = _replacement_pair(first)
    return SerialRunner(port, commit_coordinator=coordinator), attempt, request


@pytest.mark.parametrize(
    "middle_state",
    [
        AttemptState.COMPLETED,
        AttemptState.CANCELLED,
        AttemptState.EXECUTION_ERROR,
        AttemptState.INVALIDATED,
    ],
)
def test_start_updates_current_and_transitive_consumers_before_external_effect(
    tmp_path, middle_state
):
    unit, coordinator, published, domain = _saved_graph(tmp_path, middle_state)
    port = FakeExecutionPort()
    original = port.start
    observed = []

    def start(request):
        current = ExecutionCommitCoordinator(FileUnitOfWork(tmp_path)).read_current_facts(
            project_id="project-1", run_id="run-1"
        )
        assert current.current_attempt_by_step["step-1"] == "replacement"
        by_id = {attempt.attempt_id: attempt for attempt in current.attempts}
        assert by_id["replacement"].state == "intent_recorded"
        assert not by_id["attempt-1"].is_current
        assert by_id["attempt-1"].state == "completed"
        assert by_id["attempt-2"].state == by_id["attempt-3"].state == "invalidated"
        assert by_id["attempt-4"].state == "completed"
        assert set(current.coverage.invalidated_step_ids) == {"step-2", "step-3"}
        assert set(current.coverage.executed_attempt_ids) == {"attempt-4"}
        assert current.run.result_ref is None and current.run.evidence_level is None
        assert current.run.coverage_summary is None
        assert current.snapshot_cursor == unit.current_commit_sequence()
        observed.append(current)
        return original(request)

    port.start = start
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    runner.start_attempt(attempt, request)
    assert len(observed) == len(port.started) == 1
    rebuilt = ExecutionCommitCoordinator(FileUnitOfWork(tmp_path)).read_current_facts(
        project_id="project-1", run_id="run-1"
    )
    assert (
        next(item for item in rebuilt.attempts if item.attempt_id == "replacement").state
        == "running"
    )
    assert rebuilt.snapshot_cursor == unit.current_commit_sequence()
    assert unit.read(
        aggregate_kind="execution_facts", record_id=published.facts.snapshot_commit_id, revision=1
    ).payload == published.facts.model_dump(mode="json")
    # The overwritten current basis is a new revision; the old checkpoint remains readable.
    assert (
        unit.read(aggregate_kind="execution_checkpoint", record_id="attempt-2", revision=1).payload[
            "attempt"
        ]["state"]
        == middle_state.value
    )


@pytest.mark.parametrize(
    "state",
    [
        AttemptState.INTENT_RECORDED,
        AttemptState.RUNNING,
        AttemptState.STOP_REQUESTED,
        AttemptState.COLLECTING,
        AttemptState.PENDING_VERIFICATION,
        AttemptState.UNKNOWN,
    ],
)
def test_active_or_uncertain_consumers_block_upstream_replacement_without_any_write(
    tmp_path, state
):
    unit, coordinator, published, domain = _saved_graph(tmp_path, state)
    before = unit.current_commit_sequence()
    port = FakeExecutionPort()
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    with pytest.raises(ValueError, match="stop and verify"):
        runner.start_attempt(attempt, request)
    assert port.started == []
    assert unit.current_commit_sequence() == before
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == published.facts
    assert (
        unit.current_revision(aggregate_kind="execution_checkpoint", record_id="replacement") == 0
    )


@pytest.mark.parametrize(
    "failure_kind", ["execution_checkpoint", "execution_facts_current", "execution_facts"]
)
def test_failed_replacement_cannot_publish_partial_invalidation_or_occupancy(
    tmp_path, monkeypatch, failure_kind
):
    unit, coordinator, published, domain = _saved_graph(tmp_path)
    before = unit.current_commit_sequence()
    original = unit.stage_record

    def fail(**kwargs):
        if kwargs["aggregate_kind"] == failure_kind:
            raise OSError("injected replacement staging failure")
        return original(**kwargs)

    monkeypatch.setattr(unit, "stage_record", fail)
    port = FakeExecutionPort()
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    with pytest.raises(OSError, match="injected replacement"):
        runner.start_attempt(attempt, request)
    assert port.started == []
    assert unit.current_commit_sequence() == before
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == published.facts
    monkeypatch.setattr(unit, "stage_record", original)
    runner.start_attempt(attempt, request)
    assert len(port.started) == 1


def test_lost_replacement_reply_preserves_invalidations_and_never_restarts_effect(
    tmp_path, monkeypatch
):
    unit, coordinator, _published, domain = _saved_graph(tmp_path)
    original = unit.commit

    def lost_reply():
        original()
        raise OSError("injected loss after replacement publication")

    monkeypatch.setattr(unit, "commit", lost_reply)
    port = FakeExecutionPort()
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    with pytest.raises(OSError, match="after replacement"):
        runner.start_attempt(attempt, request)
    rebuilt_unit = FileUnitOfWork(tmp_path)
    rebuilt = ExecutionCommitCoordinator(
        rebuilt_unit, execution_authorizations=SavedFixtureExecutionAuthority(rebuilt_unit)
    )
    current = rebuilt.read_current_facts(project_id="project-1", run_id="run-1")
    assert current.current_attempt_by_step["step-1"] == "replacement"
    assert set(current.coverage.invalidated_step_ids) == {"step-2", "step-3"}
    runner, attempt, request = _new_start(domain[0], port, rebuilt)
    assert runner.start_attempt(attempt, request).state is AttemptState.PENDING_VERIFICATION
    assert port.started == []


def test_publisher_cannot_restore_a_superseded_current_attempt(tmp_path):
    unit, coordinator, published, domain = _saved_graph(tmp_path)
    runner, attempt, request = _new_start(domain[0], FakeExecutionPort(), coordinator)
    runner.start_attempt(attempt, request)
    before = unit.current_commit_sequence()
    batch = replace(_batch(), checkpoint=_record(domain[0]), facts=published.facts)
    with pytest.raises(ValueError, match="current attempt"):
        _publish(unit, batch, "restore-old-current")
    assert unit.current_commit_sequence() == before


def test_checkpoint_progress_and_current_snapshot_share_the_same_commit(tmp_path):
    unit, coordinator, _published, domain = _saved_graph(tmp_path)
    port = FakeExecutionPort()
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    started = runner.start_attempt(attempt, request)
    finished = replace(
        started,
        state=AttemptState.CANCELLED,
        unknown_reason_ref="confirmed_stop_without_complete_capture",
        capture_completeness=CaptureCompleteness.GAP,
    )
    result = coordinator.commit_checkpoint(project_id="project-1", checkpoint=_record(finished))
    current = ExecutionCommitCoordinator(FileUnitOfWork(tmp_path)).read_current_facts(
        project_id="project-1", run_id="run-1"
    )
    assert current == result.facts
    assert current.snapshot_cursor == result.committed["commit_sequence"]
    fact = next(item for item in current.attempts if item.attempt_id == "replacement")
    assert fact.state == "cancelled" and fact.capture_completeness == "gap"
    assert "replacement" not in current.coverage.executed_attempt_ids
    assert current.current_attempt_by_step["step-1"] == "replacement"
    assert {item.affected_attempt_id for item in current.dependency_invalidations} == {
        "attempt-2",
        "attempt-3",
    }


def test_outdated_checkpoint_cannot_restore_validity_without_a_new_attempt(tmp_path):
    unit, coordinator, _published, domain = _saved_graph(tmp_path)
    runner, attempt, request = _new_start(domain[0], FakeExecutionPort(), coordinator)
    runner.start_attempt(attempt, request)
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="outdated attempt"):
        coordinator.commit_checkpoint(project_id="project-1", checkpoint=_record(domain[1]))
    assert unit.current_commit_sequence() == before


@pytest.mark.parametrize(
    "change", ["wrong_index", "step_same_digest", "stale_source", "descendant"]
)
def test_new_start_rejects_unverified_or_invalidated_input_before_occupancy(tmp_path, change):
    unit, coordinator, published, domain = _saved_graph(tmp_path)
    port = FakeExecutionPort()
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    if change == "wrong_index":
        attempt = replace(attempt, attempt_index=9)
    elif change == "step_same_digest":
        revision = replace(
            attempt.step_revision_ref, step_revision_id="other-step-with-same-digest"
        )
        attempt = replace(attempt, step_revision_ref=revision)
        request = replace(
            request,
            authorization_ref=replace(request.authorization_ref, step_revision_ref=revision),
        )
    elif change == "stale_source":
        attempt = replace(attempt, source_binding_digest="sha256:unregistered-source")
        request = replace(request, source_binding_digest=attempt.source_binding_digest)
    else:
        attempt = replace(
            attempt, consumed_outputs=(ConsumedOutput("attempt-2", "sha256:old", "value:old"),)
        )
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError):
        runner.start_attempt(attempt, request)
    assert port.started == []
    assert unit.current_commit_sequence() == before
    assert coordinator.read_current_facts(project_id="project-1", run_id="run-1") == published.facts


def test_saved_active_handle_without_a_terminal_fact_blocks_even_a_terminal_label(tmp_path):
    unit, coordinator, published, domain = _saved_graph(tmp_path)
    # A stored "completed" label cannot by itself prove that the process group stopped.
    port = FakeExecutionPort()
    unknown_terminal = replace(domain[1], execution_handle_ref=port.handle)
    coordinator.commit_checkpoint(project_id="project-1", checkpoint=_record(unknown_terminal))
    before = unit.current_commit_sequence()
    runner, attempt, request = _new_start(domain[0], port, coordinator)
    with pytest.raises(ValueError, match="stop and verify"):
        runner.start_attempt(attempt, request)
    assert port.started == []
    assert unit.current_commit_sequence() == before
    assert (
        coordinator.read_current_facts(
            project_id="project-1", run_id="run-1"
        ).current_attempt_by_step
        == published.facts.current_attempt_by_step
    )


def test_new_attempt_cannot_consume_a_superseded_or_outdated_upstream(tmp_path):
    unit, coordinator, _published, domain = _saved_graph(tmp_path)
    runner, attempt, request = _new_start(domain[0], FakeExecutionPort(), coordinator)
    runner.start_attempt(attempt, request)
    for upstream_id in ("attempt-1", "attempt-2", "foreign-missing"):
        port = FakeExecutionPort()
        runner, attempt, request = _new_start(domain[3], port, coordinator)
        attempt = replace(
            attempt,
            consumed_conditions=(ConsumedCondition(upstream_id, "condition:old", "sha256:old"),),
            attempt_id="invalid-consumer-" + upstream_id,
            intent_id="invalid-consumer-intent-" + upstream_id,
        )
        request = replace(
            request,
            attempt_id=attempt.attempt_id,
            intent_id=attempt.intent_id,
            authorization_ref=replace(
                request.authorization_ref,
                authorization_id="invalid-consumer-grant-" + upstream_id,
                intent_id=attempt.intent_id,
            ),
        )
        # This test intentionally authorizes the exact input. The consumer must
        # still reject its superseded/missing dependency before any external effect.
        coordinator = fixture_coordinator(unit, ((attempt, request),))
        runner = SerialRunner(port, commit_coordinator=coordinator)
        before = unit.current_commit_sequence()
        with pytest.raises(ValueError, match="valid current upstream"):
            runner.start_attempt(attempt, request)
        assert port.started == [] and unit.current_commit_sequence() == before


def test_publisher_cannot_drop_a_registered_current_step(tmp_path):
    unit, coordinator, published, domain = _saved_graph(tmp_path)
    facts = published.facts.model_copy(
        update={
            "steps": published.facts.steps[:1],
            "attempts": published.facts.attempts[:1],
            "current_attempt_by_step": {"step-1": "attempt-1"},
        }
    )
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="current attempt"):
        _publish(unit, replace(_batch(), checkpoint=_record(domain[0]), facts=facts), "drop-step")
    assert unit.current_commit_sequence() == before


def test_a_dependency_without_a_registered_run_is_blocked_before_any_effect(tmp_path):
    unit = FileUnitOfWork(tmp_path)
    port = FakeExecutionPort()
    runner, attempt, request = _new_start(
        _batch().checkpoint.attempt,
        port,
        ExecutionCommitCoordinator(unit, execution_authorizations=Mock()),
    )
    attempt = replace(attempt, consumed_outputs=(ConsumedOutput("unknown", "sha256:x", "value:x"),))
    with pytest.raises(ValueError, match="registered current run"):
        runner.start_attempt(attempt, request)
    assert port.started == [] and unit.current_commit_sequence() == 0


def test_recovery_uses_invalidated_authority_even_when_the_sidecar_still_says_completed(tmp_path):
    unit, coordinator, _published, domain = _saved_graph(tmp_path)
    sidecars = FileCheckpointStore(tmp_path)
    sidecars.persist(_record(domain[1]))
    runner, attempt, request = _new_start(domain[0], FakeExecutionPort(), coordinator)
    runner.start_attempt(attempt, request)
    assert sidecars.load("attempt-2").attempt.state is AttemptState.COMPLETED
    port = FakeExecutionPort()
    recovered = SerialRunner(
        port,
        FileSpoolStore(tmp_path),
        checkpoint_store=sidecars,
        commit_coordinator=ExecutionCommitCoordinator(FileUnitOfWork(tmp_path)),
    ).recover_pending()
    assert len(recovered) == 1
    assert recovered[0].attempt.state is AttemptState.INVALIDATED
    assert sidecars.load("attempt-2").attempt.state is AttemptState.INVALIDATED
    assert port.started == []
    current = coordinator.read_current_facts(project_id="project-1", run_id="run-1")
    assert (
        next(item for item in current.attempts if item.attempt_id == "attempt-2").state
        == "invalidated"
    )
    assert "attempt-2" not in current.coverage.executed_attempt_ids


def test_outdated_live_execution_cannot_become_a_valid_basis_during_recovery(tmp_path):
    attempt = replace(
        _batch().checkpoint.attempt,
        state=AttemptState.INVALIDATED,
        execution_handle_ref=FakeExecutionPort().handle,
        capture_completeness=CaptureCompleteness.GAP,
    )
    record = _record(attempt)
    recovered = recover_attempt(record.checkpoint, attempt, FileSpoolStore(tmp_path))
    assert recovered.attempt.state is AttemptState.INVALIDATED
    assert recovered.action is RecoveryAction.PENDING_VERIFICATION
    assert recovered.capture_completeness is CaptureCompleteness.GAP
    assert recovered.gaps == ("outdated_execution_termination_unverified",)


def test_projection_checkpoint_without_authority_cannot_be_promoted_during_recovery(tmp_path):
    sidecars = FileCheckpointStore(tmp_path)
    sidecars.persist(_record(_batch().checkpoint.attempt))
    port = FakeExecutionPort()
    unit = FileUnitOfWork(tmp_path)
    runner = SerialRunner(
        port,
        FileSpoolStore(tmp_path),
        checkpoint_store=sidecars,
        commit_coordinator=ExecutionCommitCoordinator(unit),
    )
    with pytest.raises(ValueError, match="unavailable or foreign"):
        runner.recover_pending()
    assert port.started == [] and unit.current_commit_sequence() == 0
