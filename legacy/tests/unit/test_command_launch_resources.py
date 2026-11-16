"""Actual launch failures must not orphan the exact suspended Windows process."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.infrastructure.adapters.execution import command as module
from tests.unit.test_command_adapter import _adapter, _request

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Actual retained Windows process handles")


@pytest.mark.parametrize(
    "phase",
    [
        "identity",
        "job",
        "runtime",
        "spool",
        "reader_first",
        "reader_second",
        "resume",
        "timer_construct",
        "timer_start",
    ],
)
def test_actual_launch_failure_reaps_original_process_and_closes_resources(
    monkeypatch, tmp_path, phase
):
    adapter = _adapter()
    processes, runtimes = [], []
    popen, runtime_type = module.subprocess.Popen, module._CommandRuntime
    marker = tmp_path / "executed"

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    def runtime(*args, **kwargs):
        if phase == "runtime":
            raise OSError("injected startup failure")
        value = runtime_type(*args, **kwargs)
        runtimes.append(value)
        return value

    failure = Mock(side_effect=OSError("injected startup failure"))
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module, "_CommandRuntime", runtime)
    monkeypatch.setattr(
        adapter, "_terminate_group", Mock(side_effect=AssertionError("PID stop forbidden"))
    )
    if phase == "identity":
        monkeypatch.setattr(module, "_process_start_identity", failure)
        monkeypatch.setattr(module, "_windows_handle_identity", failure)
    elif phase == "job":
        monkeypatch.setattr(module, "_assign_windows_job", failure)
    elif phase == "spool":
        monkeypatch.setattr(adapter, "_open_spool_writers", failure)
    elif phase.startswith("reader"):
        original_reader = adapter._start_reader
        count = [0]

        def reader(*args, **kwargs):
            count[0] += 1
            if count[0] == (1 if phase == "reader_first" else 2):
                failure()
            original_reader(*args, **kwargs)

        monkeypatch.setattr(adapter, "_start_reader", reader)
    elif phase == "resume":
        monkeypatch.setattr(module, "_resume_windows_process", failure)
    elif phase == "timer_construct":
        monkeypatch.setattr(module.threading, "Timer", failure)
    elif phase == "timer_start":
        monkeypatch.setattr(module.threading.Timer, "start", failure)
    try:
        with pytest.raises(OSError, match="injected startup failure"):
            adapter.start(
                _request(
                    "python",
                    (
                        "-c",
                        "from pathlib import Path; import time; "
                        f"Path({str(marker)!r}).write_text('actual'); "
                        "time.sleep(30)",
                    ),
                    timeout_ms=20000,
                )
            )
        assert len(processes) == 1
        process = processes[0]
        assert process.poll() is not None, "startup failure left a live child"
        assert process.stdout.closed and process.stderr.closed
        assert not adapter._runtimes
        assert all(value.job_handle is None for value in runtimes)
        assert all(not thread.is_alive() for value in runtimes for thread in value.threads)
        if phase != "timer_start":
            assert not marker.exists(), "failure before activation still executed the command"
    finally:
        # Clean this test's actual retained Popen/Job only, including the old-code counterexample.
        for value in runtimes:
            if value.job_handle is not None:
                module._terminate_and_wait_windows_job(value.job_handle, timeout_seconds=2)
                module._close_job(value.job_handle)
                value.job_handle = None
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def test_actual_success_never_reopens_a_pid_for_birth_job_or_resume(monkeypatch):
    adapter = _adapter()
    processes = []
    popen = module.subprocess.Popen

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    kernel = module._kernel32()
    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module, "_kernel32", lambda: kernel)
    kernel.OpenProcess = Mock(side_effect=AssertionError("launch must use owned Popen handle"))
    try:
        handle = adapter.start(_request("python", ("-c", "import time; time.sleep(30)")))
        assert ":created:" in handle.process_start_identity
        assert adapter.request_stop(handle).stop_confirmed
        assert adapter.collect(handle).complete
    finally:
        for value in tuple(adapter._runtimes.values()):
            adapter._abort_launch(value)
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


@pytest.mark.parametrize("phase", ["job_unavailable", "birth_unverified"])
def test_unverified_birth_or_job_never_activates_original_process(monkeypatch, phase):
    adapter = _adapter()
    processes = []
    original = module.subprocess.Popen

    def launch(*args, **kwargs):
        value = original(*args, **kwargs)
        processes.append(value)
        return value

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    resumed = Mock()
    monkeypatch.setattr(module, "_resume_windows_process", resumed)
    if phase == "job_unavailable":
        monkeypatch.setattr(module, "_assign_windows_job", Mock(return_value=None))
    else:
        monkeypatch.setattr(
            module, "_windows_handle_identity", Mock(return_value="win32-pid:1:unverified")
        )
    try:
        with pytest.raises(RuntimeError):
            adapter.start(_request("python", ("-c", "import time; time.sleep(30)")))
        resumed.assert_not_called()
        assert len(processes) == 1 and processes[0].poll() is not None
        assert processes[0].stdout.closed and processes[0].stderr.closed
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)


@pytest.mark.parametrize("failure", ["set_false", "set_raises", "assign_false", "assign_raises"])
def test_job_constructor_closes_untransferred_job_on_every_api_failure(monkeypatch, failure):
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 1234
    kernel.SetInformationJobObject.return_value = True
    kernel.AssignProcessToJobObject.return_value = True
    target = (
        kernel.SetInformationJobObject
        if failure.startswith("set")
        else kernel.AssignProcessToJobObject
    )
    if failure.endswith("false"):
        target.return_value = False
    else:
        target.side_effect = OSError("injected Job API failure")
    monkeypatch.setattr(module, "_kernel32", lambda: kernel)
    if failure.endswith("false"):
        assert module._assign_windows_job(5678, "fixture-token") is None
    else:
        with pytest.raises(OSError, match="injected Job API failure"):
            module._assign_windows_job(5678, "fixture-token")
    kernel.OpenProcess.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(1234)
    if failure.startswith("assign"):
        assert kernel.AssignProcessToJobObject.call_args.args[1].value == 5678


def test_failed_writer_cleanup_does_not_skip_other_resources_or_erase_start_cause(monkeypatch):
    adapter = _adapter()
    saved, runtimes = [], []
    adapter._handle_store = SimpleNamespace(
        load=Mock(side_effect=FileNotFoundError()), save=Mock(side_effect=saved.append)
    )
    first, second = Mock(), Mock()
    first.abort.side_effect = OSError("injected writer cleanup failure")

    def open_writers(runtime):
        runtimes.append(runtime)
        runtime.writers = {"first": first, "second": second}
        raise ValueError("original startup failure")

    monkeypatch.setattr(adapter, "_open_spool_writers", open_writers)
    with pytest.raises(ValueError, match="original startup failure") as caught:
        adapter.start(_request("python", ("-c", "import time; time.sleep(30)")))
    assert isinstance(caught.value.__cause__, OSError)
    assert len(saved) == 1, "already persisted startup facts must remain"
    second.abort.assert_called_once()
    runtime = runtimes[0]
    assert runtime.job_handle is None and runtime.process.poll() is not None
    assert runtime.process.stdout.closed and runtime.process.stderr.closed
    assert not adapter._runtimes
