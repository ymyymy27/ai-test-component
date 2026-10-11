"""Durable start identity, one-use authorization and uncertain publication windows."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from pydantic import TypeAdapter

from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.recovery import CaseReuseBasis
from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    ConsumedCondition,
    ConsumedOutput,
    RecoveryRecord,
    SideEffectClass,
)
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import fixture_coordinator
from tests.unit.test_serial_runner import FakeExecutionPort, _attempt, _request

# 一次启动在**同一个事务**里落地的记录数，按提交顺序：
#   1. `execution_authorization…:state`（授权占用，由注入的授权适配器在同一事务内暂存）
#   2. `execution_authorization`（本次启动的原子占用声明）
#   3. `execution_intent`（原始意图，供重启后回读）
#   4. `execution_checkpoint`（attempt-1）
#   5. `execution_checkpoint_refs`（同提交检查点引用）
#   6. `execution_facts_current`
#   7. `execution_facts`
# `9230929` 写下 `before + 6` 时还没有第 5 条；`f5074d9` 加入"同提交检查点引用"后
# 事务变为 7 条记录，本文件的断言未同步。这里仍用精确计数，因为它要证明的正是
# "外呼之前这些记录已在同一事务里可见"，放宽成 `>=` 会丢掉这条性质。
_START_TRANSACTION_RECORDS = 7


def _runner(root, port=None, unit=None):
    return SerialRunner(
        port or FakeExecutionPort(),
        commit_coordinator=fixture_coordinator(
            unit or FileUnitOfWork(root), ((_attempt(), _request()),)
        ),
    )


@pytest.mark.parametrize("change", ["outputs", "conditions", "index", "adapter", "business_key"])
@pytest.mark.parametrize("restart", [False, True])
def test_same_request_cannot_replay_a_changed_attempt_basis(tmp_path, change, restart):
    port = FakeExecutionPort()
    runner = _runner(tmp_path, port)
    runner.start_attempt(_attempt(), _request())
    if restart:
        runner = _runner(tmp_path, port)
    changes = {
        "outputs": {"consumed_outputs": (ConsumedOutput("upstream", "sha256:a", "value:a"),)},
        "conditions": {
            "consumed_conditions": (ConsumedCondition("upstream", "fact:a", "sha256:a"),)
        },
        "index": {"attempt_index": 2},
        "adapter": {"adapter_version": "different/2"},
        "business_key": {"business_idempotency_key_ref": "another-key"},
    }
    before = FileUnitOfWork(tmp_path).current_commit_sequence()
    with pytest.raises(ValueError, match="different input"):
        runner.start_attempt(replace(_attempt(), **changes[change]), _request())
    assert len(port.started) == 1
    assert FileUnitOfWork(tmp_path).current_commit_sequence() == before


@pytest.mark.parametrize("legacy", [False, True])
def test_authorization_must_bind_the_exact_step_revision(tmp_path, legacy):
    port = FakeExecutionPort()
    request = _request()
    revision = None if legacy else replace(_attempt().step_revision_ref, digest="sha256:changed")
    request = replace(
        request, authorization_ref=replace(request.authorization_ref, step_revision_ref=revision)
    )
    runner = _runner(tmp_path, port)
    before = FileUnitOfWork(tmp_path).current_commit_sequence()
    with pytest.raises(ValueError, match="step revision"):
        runner.start_attempt(_attempt(), request)
    assert port.started == []
    assert FileUnitOfWork(tmp_path).current_commit_sequence() == before
    assert FileUnitOfWork(tmp_path).current_revision(
        aggregate_kind="execution_checkpoint", record_id=_attempt().attempt_id
    ) == 0


@pytest.mark.parametrize("sidecar", [False, True])
def test_new_attempt_cannot_reuse_an_occupied_authorization(tmp_path, sidecar):
    port = FakeExecutionPort()
    if not sidecar:
        runner = _runner(tmp_path, port)
    else:
        runner = SerialRunner(
            port,
            checkpoint_store=FileCheckpointStore(tmp_path),
            commit_coordinator=fixture_coordinator(
                FileUnitOfWork(tmp_path), ((_attempt(), _request()),)
            ),
        )
    runner.start_attempt(_attempt(), _request())
    if not sidecar:
        runner = _runner(tmp_path, port)
    else:
        runner = SerialRunner(
            port,
            checkpoint_store=FileCheckpointStore(tmp_path),
            commit_coordinator=fixture_coordinator(
                FileUnitOfWork(tmp_path), ((_attempt(), _request()),)
            ),
        )
    request = replace(
        _request(),
        attempt_id="attempt-2",
        intent_id="intent-2",
        authorization_ref=replace(_request().authorization_ref, intent_id="intent-2"),
    )
    attempt = replace(_attempt(), attempt_id="attempt-2", intent_id="intent-2", attempt_index=2)
    with pytest.raises(ValueError, match="authorization.*(consumed|different input)"):
        runner.start_attempt(attempt, request)
    assert len(port.started) == 1
    assert not FileUnitOfWork(tmp_path).current_revision(
        aggregate_kind="execution_checkpoint", record_id="attempt-2"
    )


def test_authorization_intent_and_checkpoint_are_visible_together_before_external_start(tmp_path):
    unit = FileUnitOfWork(tmp_path)
    port = FakeExecutionPort()
    original = port.start
    observed = []

    def start(request):
        record = unit.read(aggregate_kind="execution_checkpoint", record_id="attempt-1", revision=1)
        attempt = TypeAdapter(RecoveryRecord).validate_python(record.payload).attempt
        # This lookup verifies both the intent and the occupied authorization, not just the cursor.
        recovered = fixture_coordinator(
            FileUnitOfWork(tmp_path), ((_attempt(), _request()),)
        ).find_start(
            project_id=request.project_id,
            intent_id=request.intent_id,
            fingerprint=attempt.intent_digest,
        )
        assert recovered == attempt
        observed.append(unit.current_commit_sequence())
        return original(request)

    port.start = start
    runner = _runner(tmp_path, port, unit)
    before = unit.current_commit_sequence()
    runner.start_attempt(_attempt(), _request())
    assert observed == [before + _START_TRANSACTION_RECORDS]


def test_failed_claim_leaves_authorization_and_reuse_available_for_a_correct_retry(
    tmp_path, monkeypatch
):
    unit = FileUnitOfWork(tmp_path)
    original = unit.stage_record

    def fail_checkpoint(**kwargs):
        if kwargs["aggregate_kind"] == "execution_checkpoint":
            raise OSError("injected checkpoint staging failure")
        return original(**kwargs)

    monkeypatch.setattr(unit, "stage_record", fail_checkpoint)
    port = FakeExecutionPort()
    runner = SerialRunner(
        port,
        commit_coordinator=fixture_coordinator(unit, ((_attempt(), _request()),)),
        reuse_bases=(CaseReuseBasis("case-1", ("old",)),),
        previous_attempt_ids_by_step={"step-1": ("old",)},
    )
    before = unit.current_commit_sequence()
    with pytest.raises(OSError, match="injected"):
        runner.start_attempt(_attempt(), _request())
    assert runner.reuse_invalidations == ()
    assert port.started == []
    assert unit.current_commit_sequence() == before
    monkeypatch.setattr(unit, "stage_record", original)
    runner.start_attempt(_attempt(), _request())
    assert len(port.started) == 1
    assert len(runner.reuse_invalidations) == 1


def test_lost_reply_after_publishing_claim_never_replays_external_start(tmp_path, monkeypatch):
    unit = FileUnitOfWork(tmp_path)
    fixture_coordinator(unit, ((_attempt(), _request()),))
    before = unit.current_commit_sequence()
    original = unit.commit

    def lost_reply():
        original()
        raise OSError("injected reply loss after actual publication")

    monkeypatch.setattr(unit, "commit", lost_reply)
    port = FakeExecutionPort()
    with pytest.raises(OSError, match="reply loss"):
        _runner(tmp_path, port, unit).start_attempt(_attempt(), _request())
    recovered = _runner(tmp_path, port).start_attempt(_attempt(), _request())
    assert recovered.state is AttemptState.PENDING_VERIFICATION
    assert recovered.execution_handle_ref is None
    assert port.started == []
    assert FileUnitOfWork(tmp_path).current_commit_sequence() == (
        before + _START_TRANSACTION_RECORDS
    )


def test_two_cores_share_one_saved_start_even_before_the_first_handle_is_returned(tmp_path):
    port = FakeExecutionPort()
    entered, release = Event(), Event()
    original = port.start

    def held_start(request):
        entered.set()
        assert release.wait(10)
        return original(request)

    port.start = held_start
    first, second = _runner(tmp_path, port), _runner(tmp_path, port)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(first.start_attempt, _attempt(), _request())
        try:
            assert entered.wait(10)
            recovered = second.start_attempt(_attempt(), _request())
            assert recovered.state is AttemptState.PENDING_VERIFICATION
            assert recovered.execution_handle_ref is None
        finally:
            release.set()
        assert future.result(timeout=10).state is AttemptState.RUNNING
    assert len(port.started) == 1


@pytest.mark.parametrize("change", ["outputs", "conditions", "step", "authorization", "source"])
def test_checkpoint_progress_cannot_rewrite_the_frozen_start_basis(tmp_path, change):
    _runner(tmp_path).start_attempt(_attempt(), _request())
    unit = FileUnitOfWork(tmp_path)
    record = unit.read(aggregate_kind="execution_checkpoint", record_id="attempt-1", revision=2)
    checkpoint = TypeAdapter(RecoveryRecord).validate_python(record.payload)
    changes = {
        "outputs": {"consumed_outputs": (ConsumedOutput("upstream", "sha256:a", "value:a"),)},
        "conditions": {
            "consumed_conditions": (ConsumedCondition("upstream", "fact:a", "sha256:a"),)
        },
        "step": {"step_revision_ref": replace(checkpoint.attempt.step_revision_ref, revision_no=2)},
        "authorization": {
            "authorization_ref": replace(
                checkpoint.attempt.authorization_ref, target_ref="changed-target"
            )
        },
        "source": {"source_binding_digest": "sha256:another-source"},
    }
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="frozen execution basis"):
        ExecutionCommitCoordinator(unit).commit_checkpoint(
            project_id="project-1",
            checkpoint=replace(checkpoint, attempt=replace(checkpoint.attempt, **changes[change])),
        )
    assert unit.current_commit_sequence() == before
    assert unit.current_revision(aggregate_kind="execution_checkpoint", record_id="attempt-1") == 2


def test_original_request_with_saved_consumption_metadata_replays_the_same_attempt(tmp_path):
    port = FakeExecutionPort()
    first = _runner(tmp_path, port).start_attempt(_attempt(), _request())
    request = replace(
        _request(),
        authorization_ref=replace(_request().authorization_ref, consumed_by_attempt_id="attempt-1"),
    )
    assert _runner(tmp_path, port).start_attempt(_attempt(), request) == first
    assert len(port.started) == 1


def test_unreadable_authorization_claim_blocks_replay_without_starting_another_execution(
    tmp_path, monkeypatch
):
    port = FakeExecutionPort()
    _runner(tmp_path, port).start_attempt(_attempt(), _request())
    unit = FileUnitOfWork(tmp_path)
    original = unit.read

    def unreadable(**kwargs):
        if kwargs["aggregate_kind"] == "execution_authorization":
            raise OSError("occupied authorization unavailable")
        return original(**kwargs)

    monkeypatch.setattr(unit, "read", unreadable)
    with pytest.raises(OSError, match="authorization unavailable"):
        _runner(tmp_path, port, unit).start_attempt(_attempt(), _request())
    assert len(port.started) == 1


def test_frozen_checkpoint_keeps_the_original_revision_when_progress_is_saved(tmp_path):
    port = FakeExecutionPort()
    first = _runner(tmp_path, port).start_attempt(_attempt(), _request())
    unit = FileUnitOfWork(tmp_path)
    historical = unit.read(aggregate_kind="execution_checkpoint", record_id="attempt-1", revision=1)
    checkpoint = SerialRunner._checkpoint_record(
        replace(first, capture_completeness=CaptureCompleteness.PARTIAL), stage="collecting"
    )
    ExecutionCommitCoordinator(unit).commit_checkpoint(
        project_id="project-1", checkpoint=checkpoint
    )
    assert unit.current_revision(aggregate_kind="execution_checkpoint", record_id="attempt-1") == 3
    assert (
        unit.read(aggregate_kind="execution_checkpoint", record_id="attempt-1", revision=1).payload
        == historical.payload
    )


def test_auth_claim_is_project_bound_and_cannot_be_rebound_by_a_foreign_request(tmp_path):
    port = FakeExecutionPort()
    _runner(tmp_path, port).start_attempt(_attempt(), _request())
    request = replace(
        _request(),
        project_id="foreign",
        run_id="foreign-run",
        attempt_id="foreign-attempt",
    )
    attempt = replace(_attempt(), run_id="foreign-run", attempt_id="foreign-attempt")
    with pytest.raises(ValueError, match="authorization.*(consumed|different input)"):
        _runner(tmp_path, port).start_attempt(attempt, request)
    assert len(port.started) == 1
    key = "execution-authorization:" + hashlib.sha256(b"authorization-1").hexdigest()
    saved = FileUnitOfWork(tmp_path).read(
        aggregate_kind="execution_authorization", record_id=key, revision=1
    )
    assert saved.payload["project_id"] == "project-1"
    assert saved.payload["attempt_id"] == "attempt-1"


def test_attempt_cannot_misclassify_the_requested_side_effect_before_start(tmp_path):
    port = FakeExecutionPort()
    request = replace(_request(), side_effect_class=SideEffectClass.NON_IDEMPOTENT_WRITE)
    with pytest.raises(ValueError, match="side effect"):
        _runner(tmp_path, port).start_attempt(_attempt(), request)
    assert port.started == []
