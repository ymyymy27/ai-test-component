"""Live Windows stop paths retain the actual launch Job and serialize its lifetime."""

import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.domain.execution.runs import ExecutionInspectionState
from aitest.infrastructure.adapters.execution import command as command_module
from aitest.infrastructure.adapters.execution.command import CommandAdapter
from tests.unit.test_command_adapter import _adapter, _request, _wait_for_terminal


def runtime_fixture():
    request = _request("python", ("-c", "pass"))
    process = Mock(pid=123)
    process.poll.return_value = None
    process.wait.return_value = 1
    handle = SimpleNamespace(handle_id="owned-live-handle")
    runtime = SimpleNamespace(
        process=process,
        handle=handle,
        job_handle=456,
        timeout_timer=None,
        collection_lock=threading.Lock(),
        timed_out=False,
        stop_requested=False,
        group_stopped=False,
        writers={},
        threads=[],
        request=request,
        collection=None,
    )
    adapter = CommandAdapter()
    adapter._runtimes[handle.handle_id] = runtime
    adapter._saved_stop = Mock(return_value=None)
    adapter._persist_stop = Mock()
    adapter._terminate_group = Mock()
    return adapter, runtime


@pytest.mark.skipif(os.name != "nt", reason="Windows owned Job stop paths")
@pytest.mark.parametrize("operation", ["cancel", "timeout", "abort"])
def test_live_windows_stop_never_targets_a_pid(operation):
    adapter, runtime = runtime_fixture()
    adapter._cleanup_group = Mock(return_value=True)
    if operation == "cancel":
        adapter.request_stop(runtime.handle)
    elif operation == "timeout":
        adapter._timeout_runtime(runtime)
    else:
        adapter._abort_launch(runtime)
    adapter._terminate_group.assert_not_called()
    adapter._cleanup_group.assert_called_once_with(runtime)


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended launch cleanup")
def test_unassigned_suspended_launch_uses_the_original_popen_handle():
    adapter, runtime = runtime_fixture()
    runtime.job_handle = None
    adapter._abort_launch(runtime)
    adapter._terminate_group.assert_not_called()
    runtime.process.terminate.assert_called_once_with()


@pytest.mark.skipif(os.name != "nt", reason="Windows owned Job stop paths")
@pytest.mark.parametrize("operation", ["cancel", "timeout"])
def test_stop_and_timeout_take_the_collection_lifetime_lock(operation):
    adapter, runtime = runtime_fixture()
    adapter._cleanup_group = Mock(return_value=True)
    entered = threading.Event()
    runtime.process.poll.side_effect = lambda: entered.set() or None

    def target():
        if operation == "cancel":
            adapter.request_stop(runtime.handle)
        else:
            adapter._timeout_runtime(runtime)

    with runtime.collection_lock:
        thread = threading.Thread(target=target)
        thread.start()
        reached_while_collection_held = entered.wait(0.1)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not reached_while_collection_held
    assert entered.is_set()


@pytest.mark.parametrize("state", ["cancelled", "exited"])
def test_late_timeout_cannot_replace_manual_stop_or_natural_exit(state):
    adapter, runtime = runtime_fixture()
    if state == "cancelled":
        runtime.stop_requested = True
    else:
        runtime.process.poll.return_value = 0
    adapter._cleanup_group = Mock()
    adapter._timeout_runtime(runtime)
    assert not runtime.timed_out
    adapter._cleanup_group.assert_not_called()
    adapter._terminate_group.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows owned Job stop paths")
def test_group_stop_failure_stays_unknown_without_pid_fallback():
    adapter, runtime = runtime_fixture()
    adapter._cleanup_group = Mock(return_value=False)
    result = adapter.request_stop(runtime.handle)
    assert not result.stop_confirmed
    assert result.observed_state is ExecutionInspectionState.UNKNOWN
    adapter._persist_stop.assert_called_once_with(runtime.handle, result)
    adapter._terminate_group.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows process and Job integration")
@pytest.mark.parametrize("operation", ["cancel", "timeout"])
def test_real_job_stops_original_command_without_taskkill(monkeypatch, operation):
    adapter = _adapter()
    handle = adapter.start(
        _request("python", ("-c", "import time; time.sleep(30)"))
    )
    runtime = adapter._runtimes[handle.handle_id]
    original_job = runtime.job_handle
    assert original_job is not None
    monkeypatch.setattr(
        adapter, "_terminate_group", Mock(side_effect=AssertionError("PID stop is forbidden"))
    )
    try:
        if operation == "cancel":
            result = adapter.request_stop(handle)
            assert result.stop_confirmed
        else:
            adapter._timeout_runtime(runtime)
        inspection = _wait_for_terminal(adapter, handle)
        assert inspection.identity_matches
        assert not inspection.process_reachable
        collected = adapter.collect(handle)
        assert collected.complete and collected.exit_fact_ref is not None
        assert collected.exit_fact_ref.timed_out is (operation == "timeout")
        assert runtime.job_handle is None and runtime.group_stopped
    finally:
        # Test cleanup uses this launch's actual Job/Popen resources only.
        if runtime.process.poll() is None:
            command_module._terminate_and_wait_windows_job(original_job, timeout_seconds=2)
        runtime.process.wait(timeout=2)
        if runtime.job_handle is not None:
            command_module._close_job(runtime.job_handle)
            runtime.job_handle = None


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows suspended startup failure")
def test_actual_handle_save_failure_releases_only_its_launched_job(monkeypatch):
    store = SimpleNamespace(
        load=Mock(side_effect=FileNotFoundError("fixture has no previous launch")),
        save=Mock(side_effect=OSError("fixture handle publication failure")),
    )
    adapter = _adapter()
    adapter._handle_store = store
    original_abort = adapter._abort_launch
    aborted = []

    def capture_abort(runtime):
        aborted.append(runtime)
        original_abort(runtime)

    monkeypatch.setattr(adapter, "_abort_launch", capture_abort)
    monkeypatch.setattr(
        adapter, "_terminate_group", Mock(side_effect=AssertionError("PID stop is forbidden"))
    )
    with pytest.raises(OSError, match="publication failure"):
        adapter.start(_request("python", ("-c", "import time; time.sleep(30)")))
    assert len(aborted) == 1
    runtime = aborted[0]
    assert runtime.process.poll() is not None
    assert runtime.job_handle is None
    assert not adapter._runtimes
