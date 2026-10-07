"""Bounded authority projection and saved main-thread continuation."""

import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.application.execution.continuation import stage_continuation
from aitest.application.execution.facts import execution_payload_digest
from aitest.application.execution.run_schedule import SavedRunSchedule
from aitest.application.execution.saved_control import SavedRunControl
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.execution_facts import RunControlStateFact
from aitest.contracts.prepared_run import RunDriverFact
from aitest.domain.execution.continuation import RunContinuation
from aitest.domain.execution.runs import AttemptState
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.continuations import (
    _KEY,
    ContinuationCapacityExceeded,
    active_catalog,
    verify_catalog_record,
)
from aitest.infrastructure.file_store.sharded_records import AuthorityTree
from aitest.interfaces.local import core_worker
from aitest.interfaces.local.api import LocalAPI
from tests.unit.test_current_execution_snapshot import _batch


@pytest.fixture
def core(tmp_path):
    value = assemble_workspace_core(tmp_path, instance_id="background-test")
    yield value
    value.lifetime_lock.release()


def work(core, *, run="run", project="project", active=True):
    return RunContinuation(
        core.workspace.workspace_id,
        project,
        run,
        "original-admission-" + run,
        "original-intent-" + run,
        "original-snapshot-" + run,
        active,
    )


def publish(core, values):
    core.unit_of_work.begin("catalog-publication", values[0].project_id)
    try:
        for value in values:
            if (
                core.unit_of_work.repo.current_revision(
                    aggregate_kind="execution_intent", record_id=value.schedule_intent_id
                )
                == 0
            ):
                snapshot = {
                    "project_id": value.project_id,
                    "run_id": value.run_id,
                    "snapshot_commit_id": value.base_snapshot_commit_id,
                    "snapshot_revision": 1,
                }
                core.unit_of_work.stage_record(
                    aggregate_kind="execution_facts",
                    record_id=value.base_snapshot_commit_id,
                    expected_revision=0,
                    payload=snapshot,
                )
                core.unit_of_work.stage_record(
                    aggregate_kind="execution_intent",
                    record_id=value.schedule_intent_id,
                    expected_revision=0,
                    payload={
                        "schema_version": "aitest.run-schedule-intent/1.0",
                        "workspace_id": value.workspace_id,
                        "project_id": value.project_id,
                        "run_id": value.run_id,
                        "intent_id": value.intent_id,
                        "base_snapshot_commit_id": value.base_snapshot_commit_id,
                        "snapshot_digest": execution_payload_digest(snapshot),
                    },
                )
            stage_continuation(core.execution_coordinator, value)
        core.unit_of_work.commit()
    except BaseException:
        core.unit_of_work.rollback()
        raise


def test_catalog_and_history_share_the_actual_publication(core, monkeypatch):
    value = work(core)
    publish(core, [value])
    entries = core.unit_of_work.repo.active_execution_schedules(
        workspace_id=core.workspace.workspace_id
    )
    assert entries == (
        {
            "record_id": value.record_id,
            "workspace_id": value.workspace_id,
            "project_id": value.project_id,
            "run_id": value.run_id,
            "revision": 1,
        },
    )
    entries[0]["project_id"] = "mutated-read"
    # Reading inventory must never traverse all business identities/ledger entries.
    monkeypatch.setattr(AuthorityTree, "items", lambda _: pytest.fail("history scan"))
    again = core.unit_of_work.repo.active_execution_schedules(workspace_id=value.workspace_id)
    assert again[0]["project_id"] == value.project_id
    publish(core, [replace(value, active=False)])
    assert not core.unit_of_work.repo.active_execution_schedules(workspace_id=value.workspace_id)
    assert (
        core.unit_of_work.repo.read(
            aggregate_kind="execution_schedule", record_id=value.record_id, revision=1
        ).payload
        == value.payload()
    )


def test_capacity_rejection_preserves_the_original_root_and_all_sixty_four_entries(core):
    values = [work(core, run="run-" + str(i)) for i in range(64)]
    publish(core, values)
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    with pytest.raises(ContinuationCapacityExceeded):
        publish(core, [work(core, run="sixty-fifth")])
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    entries = core.unit_of_work.repo.active_execution_schedules(
        workspace_id=core.workspace.workspace_id
    )
    assert len(entries) == 64 and {entry["run_id"] for entry in entries} == {
        item.run_id for item in values
    }


def test_prepublication_failure_does_not_publish_a_ready_item(core, monkeypatch):
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    monkeypatch.setattr(FileCommitStore, "publish", Mock(side_effect=OSError("injected publish")))
    with pytest.raises(OSError):
        publish(core, [work(core)])
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert not core.unit_of_work.repo.active_execution_schedules(
        workspace_id=core.workspace.workspace_id
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("active", "true"),
        ("run_id", 1),
        ("workspace_id", ""),
        ("intent_id", False),
        ("schema_version", "unknown"),
    ],
)
def test_continuation_strict_identity_and_boolean(field, value):
    original = RunContinuation("workspace", "project", "run", "admit", "intent", "base", True)
    with pytest.raises(ValueError):
        RunContinuation.read(original.payload() | {field: value})


@pytest.mark.parametrize("problem", ["bool_revision", "foreign", "wrong_id", "missing", "null"])
def test_corrupt_self_hashed_inventory_is_not_an_empty_healthy_queue(tmp_path, problem):
    tree = AuthorityTree(tmp_path)
    value = RunContinuation("workspace", "project", "run", "admit", "intent", "base", True)
    entry = {
        "record_id": value.record_id,
        "workspace_id": "workspace",
        "project_id": "project",
        "run_id": "run",
        "revision": 1,
    }
    if problem == "bool_revision":
        entry["revision"] = True
    if problem == "foreign":
        entry["workspace_id"] = "foreign"
    if problem == "wrong_id":
        entry["run_id"] = "other-run"
    raw = {
        "schema": "aitest.active-execution-schedules/1",
        "workspace_id": "workspace",
        "entries": {value.record_id: entry},
    }
    if problem == "missing":
        del raw["entries"]
    tree.put(_KEY, None if problem == "null" else raw)
    with pytest.raises(ValueError):
        active_catalog(tree, "workspace")


def test_manifest_directory_verification_rejects_an_inactive_record_with_an_active_entry(core):
    value = work(core)
    publish(core, [value])
    tree = core.unit_of_work.repo._load()["_tree"]
    with pytest.raises(ValueError):
        verify_catalog_record(
            tree,
            workspace=value.workspace_id,
            identity=value.record_id,
            revision=2,
            payload=replace(value, active=False).payload(),
        )


def tick_service():
    values = [
        RunContinuation(
            "workspace", "project", name, "admit-" + name, "intent-" + name, "base-" + name, True
        )
        for name in ("a", "b")
    ]
    values.sort(key=lambda item: item.record_id)
    entries = tuple(
        {
            "record_id": item.record_id,
            "workspace_id": item.workspace_id,
            "project_id": item.project_id,
            "run_id": item.run_id,
            "revision": 1,
        }
        for item in values
    )
    coordinator = Mock()
    records = {item.record_id: item.payload() for item in values}
    coordinator._read_payload.side_effect = lambda kind, identity: records.get(identity)
    coordinator._revision.return_value = 1
    service = SavedRunSchedule(
        coordinator,
        Mock(),
        "workspace",
        Mock(active_execution_schedules=Mock(return_value=entries)),
    )
    service._recall = Mock(return_value=True)
    service._current = Mock(return_value=_batch().facts)
    service._advance = Mock(return_value={"status": "active"})
    service._retire_quiescent = Mock()
    return service, values


def test_one_slice_per_tick_and_fair_rotation_even_after_a_failed_item():
    service, values = tick_service()
    service._advance.side_effect = [
        ValueError("bad first item"),
        {"status": "active"},
        {"status": "active"},
    ]
    with pytest.raises(ValueError):
        service.tick(Mock())
    service.tick(Mock())
    service.tick(Mock())
    assert [call.args[1] for call in service._advance.call_args_list] == [
        values[0].run_id,
        values[1].run_id,
        values[0].run_id,
    ]
    assert all(call.kwargs == {"budget": 1} for call in service._advance.call_args_list)
    assert not service.execution.mock_calls


def test_stepwise_tick_can_never_dispatch_new_steps():
    service, _ = tick_service()
    facts = _batch().facts
    service._current.return_value = facts.model_copy(
        update={"run": facts.run.model_copy(update={"driver": RunDriverFact.STEPWISE})}
    )
    service._attempts = Mock(return_value=())
    service.tick(Mock())
    assert not service._advance.mock_calls and not service.execution.mock_calls


def test_unverified_completion_or_unknown_activity_cannot_retire_saved_work():
    service, values = tick_service()
    facts = _batch().facts
    service._current.return_value = facts.model_copy(
        update={
            "run": facts.run.model_copy(update={"control_state": RunControlStateFact.COMPLETED})
        }
    )
    service._attempts = Mock(
        return_value=(
            replace(_batch().checkpoint.attempt, state=AttemptState.UNKNOWN, exit_fact_ref=None),
        )
    )
    service._retire_quiescent = SavedRunSchedule._retire_quiescent.__get__(service)
    service._retire_quiescent(values[0])
    assert not service.unit.stage_record.mock_calls and not service.unit.commit.mock_calls


def test_default_assembly_has_an_empty_bounded_continuation_hook(core):
    before = core.unit_of_work.current_commit_sequence()
    assert callable(core.continue_work) and core.continue_work() is None
    assert core.unit_of_work.current_commit_sequence() == before


def test_dangling_original_admission_is_rejected_before_publication(core):
    value = work(core)
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    core.unit_of_work.begin("dangling-work", value.project_id)
    try:
        stage_continuation(core.execution_coordinator, value)
        with pytest.raises(ValueError, match="dependency"):
            core.unit_of_work.commit()
    finally:
        core.unit_of_work.rollback()
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before


def test_missing_known_inventory_is_blocked_while_an_old_unregistered_workspace_is_empty(tmp_path):
    tree = AuthorityTree(tmp_path)
    assert active_catalog(tree, "workspace") == {}
    with pytest.raises(ValueError, match="missing"):
        active_catalog(tree, "workspace", required=True)


@pytest.mark.parametrize("budget", [True, 0, 101, "1"])
def test_background_control_budget_cannot_be_coerced_or_unbounded(budget):
    coordinator, execution = Mock(), Mock()
    service = SavedRunControl(coordinator, execution, "workspace")
    with pytest.raises(ValueError, match="budget"):
        service.advance_current("project", "run", max_inspections=budget)
    assert not coordinator.mock_calls and not execution.mock_calls


def test_control_settlement_rechecks_scope_after_external_observation():
    facts = _batch().facts
    pending = facts.model_copy(
        update={
            "run": facts.run.model_copy(
                update={"control_state": RunControlStateFact.PAUSE_REQUESTED}
            )
        }
    )
    changed = facts.model_copy(
        update={"run": facts.run.model_copy(update={"control_state": RunControlStateFact.RUNNING})}
    )
    coordinator = Mock()
    coordinator.read_runtime_revision_facts.side_effect = [pending, changed]
    coordinator._stage_snapshot.return_value = ("snapshot", changed)
    coordinator._read_payload.return_value = None
    service = SavedRunControl(coordinator, Mock(execution_port=None), "workspace")
    service._read = Mock(return_value=None)
    service._owns = Mock(return_value=True)
    service._facts = Mock(return_value=pending)
    service._attempts = Mock(return_value=())
    with pytest.raises(ValueError, match="changed during"):
        service._advance({"complete": False, "action": "pause_run"}, "control", "project", "run")
    assert not coordinator._stage_snapshot.mock_calls
    assert not service.unit.commit.mock_calls and not service.unit.stage_record.mock_calls


@pytest.mark.parametrize("field", ["schedule_intent_id", "intent_id", "base_snapshot_commit_id"])
def test_later_work_cannot_replace_the_original_admission(core, field):
    value = work(core)
    publish(core, [value])
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    changed = replace(value, **{field: "other-original"})
    core.unit_of_work.begin("replace-original", value.project_id)
    try:
        stage_continuation(core.execution_coordinator, changed)
        with pytest.raises(ValueError, match="cannot replace"):
            core.unit_of_work.commit()
    finally:
        core.unit_of_work.rollback()
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before


def test_later_schedule_admission_keeps_the_runs_original_work():
    service, values = tick_service()
    service._stage_work("project", values[0].run_id, "later", "later-base", "later-admission")
    assert not service.unit.stage_record.mock_calls


@pytest.mark.parametrize("fails", [False, True])
def test_worker_waits_and_command_boundaries_continue_on_owner_thread(
    tmp_path, monkeypatch, capsys, fails
):
    import aitest.bootstrap as bootstrap
    from aitest.interfaces.local import pipe

    owner = threading.get_ident()
    calls = []
    clock = [1.0]

    def pulse():
        calls.append(threading.get_ident())
        if fails:
            raise ValueError("secret-test-credential-must-not-be-logged")

    def wait(on_wait):
        for _ in range(10):
            clock[0] += 0.1
            on_wait()

    incoming = [b'{"action":"doctor","request_id":"while-active"}']
    output = []

    def read(*, on_wait):
        wait(on_wait)
        if not incoming:
            raise OSError("peer closed")
        return incoming.pop(0)

    server = SimpleNamespace(
        start=lambda: None,
        wait_for_client=wait,
        validate_peer=lambda: None,
        peer_process_basename=lambda: "relay.exe",
        read_message=read,
        write_message=output.append,
        close=lambda: None,
    )
    assembly = SimpleNamespace(
        api=LocalAPI(instance_id="core", workspace_id="workspace"),
        continue_work=pulse,
        lifetime_lock=Mock(),
        shutdown_blocker=lambda: None,
        seal_inflight=lambda: (),
    )
    monkeypatch.setattr(bootstrap, "assemble_workspace_core", lambda *a, **kw: assembly)
    monkeypatch.setattr(pipe, "NamedPipeServer", lambda *a, **kw: server)
    monkeypatch.setattr(core_worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(core_worker.sys, "platform", "win32")
    assert (
        core_worker.main(
            [
                "--workspace-root",
                str(tmp_path),
                "--workspace-id",
                "workspace",
                "--instance-id",
                "core",
                "--max-clients",
                "1",
            ]
        )
        == 0
    )
    assert len(calls) >= 3 and set(calls) == {owner}
    assert len(output) == 1 and b'"READY"' in output[0]
    error_output = capsys.readouterr().err
    assert "secret-test-credential" not in error_output
    assert error_output.count("已登记运行续行受阻") == int(fails)
    assembly.lifetime_lock.release.assert_called_once()
