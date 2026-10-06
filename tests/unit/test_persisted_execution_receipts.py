"""Persisted adapter receipts must retain exact types and reliable process identity."""

import json
from dataclasses import replace

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    AttemptState,
    ExecutionInspectionState,
    StopRequestResult,
)
from aitest.infrastructure.file_store.execution_handles import (
    FileExecutionHandleStore,
    PersistedExecutionHandle,
)
from tests.unit.test_execution_observation_identity import observation


def material(tmp_path):
    attempt, inspection, collection = observation()
    handle = attempt.execution_handle_ref
    store = FileExecutionHandleStore(tmp_path)
    store.save(
        PersistedExecutionHandle(attempt.attempt_id, collection.exit_fact_ref.startup_token, handle)
    )
    return store, handle, attempt, inspection, collection


@pytest.mark.parametrize(
    "confirmed,state",
    [
        (1, ExecutionInspectionState.STOPPED),
        ("true", ExecutionInspectionState.STOPPED),
        (True, ExecutionInspectionState.RUNNING),
        (True, ExecutionInspectionState.UNKNOWN),
        (True, ExecutionInspectionState.LOST),
        (True, ExecutionInspectionState.EXITED),
    ],
)
def test_saving_stop_requires_exact_boolean_and_stopped_state(tmp_path, confirmed, state):
    store, handle, _, _, _ = material(tmp_path)
    with pytest.raises(ValueError):
        store.save_stop(handle, StopRequestResult(handle.handle_id, confirmed, state))
    assert not store._stop_path(handle.handle_id).exists()


@pytest.mark.parametrize(
    "fault", ["number", "string", "running", "extra", "nested_extra", "duplicate"]
)
def test_reading_stop_does_not_coerce_or_ignore_material(tmp_path, fault):
    store, handle, _, _, _ = material(tmp_path)
    store.save_stop(
        handle, StopRequestResult(handle.handle_id, True, ExecutionInspectionState.STOPPED)
    )
    path = store._stop_path(handle.handle_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if fault == "number":
        raw["result"]["stop_confirmed"] = 1
    elif fault == "string":
        raw["result"]["stop_confirmed"] = "true"
    elif fault == "running":
        raw["result"]["observed_state"] = "running"
    elif fault == "extra":
        raw["unexpected"] = True
    elif fault == "nested_extra":
        raw["result"]["unexpected"] = True
    else:
        path.write_text(
            json.dumps(raw).replace(
                '"stop_confirmed": true', '"stop_confirmed": false, "stop_confirmed": true'
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            store.load_stop(handle)
        return
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load_stop(handle)


@pytest.mark.parametrize(
    "fault",
    ["complete_number", "complete_string", "exit_boolean", "extra", "nested_extra", "duplicate"],
)
def test_reading_collection_keeps_exact_completion_and_exit_material(tmp_path, fault):
    store, handle, _, _, collection = material(tmp_path)
    store.save_collection(handle, collection)
    path = store._result_path(handle.handle_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if fault == "complete_number":
        raw["result"]["complete"] = 1
    elif fault == "complete_string":
        raw["result"]["complete"] = "true"
    elif fault == "exit_boolean":
        raw["result"]["exit_fact_ref"]["real_exit_code"] = True
    elif fault == "extra":
        raw["unexpected"] = True
    elif fault == "nested_extra":
        raw["result"]["exit_fact_ref"]["unexpected"] = True
    else:
        path.write_text(
            json.dumps(raw).replace('"complete": true', '"complete": false, "complete": true'),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            store.load_collection(handle)
        return
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load_collection(handle)


@pytest.mark.parametrize("fault", ["complete_number", "exit_boolean"])
def test_invalid_collection_is_rejected_before_persistence(tmp_path, fault):
    store, handle, _, _, collection = material(tmp_path)
    if fault == "complete_number":
        collection = replace(collection, complete=1)
    else:
        collection = replace(
            collection, exit_fact_ref=replace(collection.exit_fact_ref, real_exit_code=True)
        )
    with pytest.raises(ValueError):
        store.save_collection(handle, collection)
    assert not store._result_path(handle.handle_id).exists()


def test_owned_strict_receipts_round_trip(tmp_path):
    store, handle, _, _, collection = material(tmp_path)
    stopped = StopRequestResult(handle.handle_id, True, ExecutionInspectionState.STOPPED)
    store.save_stop(handle, stopped)
    store.save_collection(handle, collection)
    assert store.load_stop(handle) == stopped and store.load_collection(handle) == collection


def test_confirmed_inspection_without_exit_fact_cannot_make_attempt_cancelled():
    attempt, inspection, collection = observation()
    stopped = replace(inspection, state=ExecutionInspectionState.STOPPED, stop_confirmed=True)
    missing = replace(collection, exit_fact_ref=None, complete=False)
    assert (
        SerialRunner._attempt_state_for(attempt, stopped, missing)
        is AttemptState.PENDING_VERIFICATION
    )


@pytest.mark.parametrize("code", [True, "0", 1.0])
def test_confirmed_stop_cannot_hide_an_invalid_exit_code(code):
    from aitest.domain.execution.runs import ProcessTerminationReason, has_verified_exit

    attempt, _, collection = observation()
    fact = replace(
        collection.exit_fact_ref,
        termination_reason=ProcessTerminationReason.CONFIRMED_STOP,
        real_exit_code=code,
    )
    assert not has_verified_exit(replace(attempt, exit_fact_ref=fact))


def test_confirmed_stop_may_preserve_an_unknown_exit_code():
    from aitest.domain.execution.runs import ProcessTerminationReason, has_verified_exit

    attempt, _, collection = observation()
    fact = replace(
        collection.exit_fact_ref,
        termination_reason=ProcessTerminationReason.CONFIRMED_STOP,
        real_exit_code=None,
    )
    assert has_verified_exit(replace(attempt, exit_fact_ref=fact))


@pytest.mark.parametrize("saved_collection", [False, True])
def test_restored_inspection_retains_confirmed_stop_and_missing_material(
    tmp_path, saved_collection
):
    from aitest.domain.execution.runs import ProcessTerminationReason
    from aitest.infrastructure.adapters.execution.command import CommandAdapter

    store, handle, attempt, _, collection = material(tmp_path)
    store.save_stop(
        handle, StopRequestResult(handle.handle_id, True, ExecutionInspectionState.STOPPED)
    )
    if saved_collection:
        store.save_collection(
            handle,
            replace(
                collection,
                exit_fact_ref=replace(
                    collection.exit_fact_ref,
                    termination_reason=ProcessTerminationReason.CONFIRMED_STOP,
                ),
            ),
        )
    adapter = CommandAdapter(handle_store=store)
    inspection = adapter.inspect(handle)
    assert inspection.state is ExecutionInspectionState.STOPPED
    assert inspection.stop_confirmed is True and inspection.identity_matches is True
    collected = adapter.collect(handle)
    if not saved_collection:
        assert collected.exit_fact_ref is None and collected.complete is False
        assert (
            SerialRunner._attempt_state_for(attempt, inspection, collected)
            is AttemptState.PENDING_VERIFICATION
        )
