"""The host owns rejected connections and a finite acquisition window."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.interfaces.local import editor_host
from aitest.interfaces.local.editor_host import EditorHost, WorkspaceInUse


@pytest.mark.parametrize("field", ["wait_timeout_seconds", "poll_interval_seconds"])
@pytest.mark.parametrize(
    "value", [True, False, 0, -1, float("nan"), float("inf"), float("-inf"), "1", None, 61]
)
def test_invalid_wait_policy_fails_before_any_connection_or_launch(field, value):
    connector, launcher = Mock(), Mock()
    with pytest.raises(ValueError):
        EditorHost(connector=connector, launcher=launcher, **{field: value})
    connector.assert_not_called()
    launcher.start.assert_not_called()


@pytest.mark.parametrize("close_failure", [False, True])
def test_wrong_instance_releases_only_the_rejected_connection(close_failure):
    channel = Mock()
    if close_failure:
        channel.close.side_effect = OSError("close failure")
    connector = Mock(side_effect=[None, (channel, "another-core")])
    launcher = SimpleNamespace(start=Mock(return_value="expected-core"))
    with pytest.raises(WorkspaceInUse, match="身份") as error:
        EditorHost(connector=connector, launcher=launcher).acquire("workspace")
    channel.close.assert_called_once()
    assert (
        isinstance(error.value.__cause__, OSError)
        if close_failure
        else error.value.__cause__ is None
    )
    launcher.start.assert_called_once_with("workspace")


def test_success_transfers_connection_ownership_without_closing_it():
    channel = Mock()
    launcher = SimpleNamespace(start=Mock())
    endpoint = EditorHost(
        connector=Mock(return_value=(channel, "existing-core")), launcher=launcher
    ).acquire("workspace")
    assert endpoint.connection is channel
    channel.close.assert_not_called()
    launcher.start.assert_not_called()


def test_sleep_is_limited_to_remaining_acquisition_time(monkeypatch):
    clock = [0.0]
    delays = []

    def sleep(delay):
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(editor_host.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(editor_host.time, "sleep", sleep)
    host = EditorHost(
        connector=lambda _: None,
        launcher=SimpleNamespace(start=lambda _: "core"),
        wait_timeout_seconds=0.05,
        poll_interval_seconds=0.1,
    )
    with pytest.raises(WorkspaceInUse):
        host.acquire("workspace")
    assert delays == [0.05]
    assert clock[0] == 0.05


@pytest.mark.parametrize("initial", [True, False])
def test_connection_returned_after_deadline_is_closed_not_published(monkeypatch, initial):
    clock = [0.0]
    channel = Mock()
    calls = []

    def connect(_):
        calls.append(None)
        if not initial and len(calls) == 1:
            return None
        clock[0] += 2
        return channel, "core"

    monkeypatch.setattr(editor_host.time, "monotonic", lambda: clock[0])
    launcher = SimpleNamespace(start=Mock(return_value="core"))
    with pytest.raises(WorkspaceInUse):
        EditorHost(connector=connect, launcher=launcher, wait_timeout_seconds=1).acquire(
            "workspace"
        )
    channel.close.assert_called_once()
    assert launcher.start.call_count == (0 if initial else 1)


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="actual Windows core and pipe")
def test_rejected_live_connection_releases_sole_pipe_for_independent_cli(tmp_path):
    import json

    from aitest.bootstrap import acquire_endpoint, make_pipe_connector, shutdown_endpoint
    from aitest.infrastructure.file_store.workspace import Workspace
    from tests.unit.test_cli_core_forwarding import cli

    identity = Workspace(tmp_path)
    endpoint = acquire_endpoint(tmp_path, workspace_id=identity.workspace_id)
    core_id = endpoint.instance_id
    endpoint.connection.close()
    real = make_pipe_connector(tmp_path)
    acquired = []
    first = [True]

    def connector(workspace_id):
        if first[0]:
            first[0] = False
            return None
        result = real(workspace_id)
        if result is not None:
            acquired.append(result[0])
        return result

    launcher = SimpleNamespace(start=Mock(return_value="different-start-fact"))
    try:
        with pytest.raises(WorkspaceInUse, match="身份"):
            EditorHost(connector=connector, launcher=launcher).acquire(identity.workspace_id)
        assert len(acquired) == 1
        reply = cli(["doctor", "--workspace", str(tmp_path)])
        assert reply.returncode == 0, reply.stderr.decode("utf-8")
        value = json.loads(reply.stdout)
        assert value["instance_id"] == core_id
        assert value["workspace_id"] == identity.workspace_id
        assert value["error"] is None
    finally:
        for channel in acquired:
            channel.close()
        shutdown_endpoint(tmp_path, workspace_id=identity.workspace_id)
