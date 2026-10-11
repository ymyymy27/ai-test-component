"""Public discovery must validate original paths before canonicalization."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

import aitest.bootstrap as bootstrap
from aitest.application.errors import WorkspaceInUse
from aitest.infrastructure import path_compat as compat


@pytest.mark.parametrize("entry", ["acquire", "shutdown"])
@pytest.mark.skipif(sys.platform != "win32", reason="real Windows directory junction")
def test_public_entry_rejects_junction_before_any_host_or_target_access(
    tmp_path, monkeypatch, entry
):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    assert result.returncode == 0 and compat.is_junction(link)
    calls = []
    monkeypatch.setattr(
        bootstrap,
        "make_editor_host",
        lambda *a, **kw: SimpleNamespace(acquire=lambda workspace: calls.append("acquire")),
    )
    monkeypatch.setattr(
        bootstrap,
        "make_pipe_connector",
        lambda *a, **kw: lambda workspace: calls.append("connect"),
    )
    try:
        with pytest.raises(WorkspaceInUse, match="filesystem link"):
            if entry == "acquire":
                bootstrap.acquire_endpoint(link, workspace_id="workspace")
            else:
                bootstrap.shutdown_endpoint(link, workspace_id="workspace", wait_timeout_seconds=0)
        assert calls == [] and list(target.rglob("*")) == []
    finally:
        # Remove the junction itself, never recursively delete its target.
        assert compat.is_junction(link) and target.is_dir()
        link.rmdir()


@pytest.mark.parametrize(
    "name",
    [
        "NUL",
        "nul.txt",
        "AUX",
        "COM1",
        "LPT2",
        "stream:id",
        "id.",
        "id ",
        "bad?name",
        "id\x00",
    ],
)
def test_reserved_or_ambiguous_pointer_filename_is_rejected_before_workspace_access(tmp_path, name):
    with pytest.raises(WorkspaceInUse):
        bootstrap.SystemProcessLauncher(tmp_path, instance_id_file=name)
    assert list(tmp_path.rglob("*")) == []
