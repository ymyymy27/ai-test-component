"""Component gestures only; these tests do not prove a real user or Trae acceptance."""

import json
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aitest.application.project.serialization import binding_to_payload, project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.project.context import BindingForm, LocalProject, LocalProjectBinding
from aitest.interfaces.local.api import EntryKind, Session
from aitest.interfaces.local.core_client import CoreClient, CoreResultUnverified
from aitest.interfaces.local.core_worker import CONTROL_KEY, dispatch_frame
from aitest.interfaces.local.editor_host import CoreEndpoint
from aitest.interfaces.local.native_confirmation import (
    MAX_PROMPT_CHARS,
    NATIVE_CONFIRMATION_VERSION,
    dispatch_native,
)


def test_tcl_paths_use_base_interpreter_and_restore_environment(monkeypatch, tmp_path):
    import tkinter

    from aitest.interfaces.local.native_confirmation import _create_windows_root

    for directory, filename in (("tcl8.6", "init.tcl"), ("tk8.6", "tk.tcl")):
        target = tmp_path / "tcl" / directory / filename
        target.parent.mkdir(parents=True)
        target.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path))
    monkeypatch.setattr(tkinter, "TclVersion", 8.6)
    monkeypatch.setattr(tkinter, "TkVersion", 8.6)
    monkeypatch.setenv("TCL_LIBRARY", "untrusted-caller-value")
    monkeypatch.delenv("TK_LIBRARY", raising=False)

    def fail():
        assert os.environ["TCL_LIBRARY"] == str(tmp_path / "tcl/tcl8.6")
        assert os.environ["TK_LIBRARY"] == str(tmp_path / "tcl/tk8.6")
        raise RuntimeError("desktop unavailable")

    monkeypatch.setattr(tkinter, "Tk", fail)
    with pytest.raises(RuntimeError, match="desktop unavailable"):
        _create_windows_root()
    assert os.environ["TCL_LIBRARY"] == "untrusted-caller-value"
    assert "TK_LIBRARY" not in os.environ


def test_missing_tcl_refuses_without_using_caller_path(monkeypatch, tmp_path):
    from aitest.interfaces.local.native_confirmation import _create_windows_root

    monkeypatch.setattr(sys, "base_prefix", str(tmp_path))
    monkeypatch.setenv("TCL_LIBRARY", "untrusted-caller-value")
    with pytest.raises(RuntimeError, match="Tcl/Tk"):
        _create_windows_root()
    assert os.environ["TCL_LIBRARY"] == "untrusted-caller-value"


@pytest.fixture
def core(tmp_path):
    result = assemble_workspace_core(tmp_path / "workspace", instance_id="native-component")
    try:
        yield result
    finally:
        result.lifetime_lock.release()


def project_command(core, **changes):
    project = LocalProject(
        "project", core.workspace.workspace_id, "项目", "测试目标", "@core-assigned"
    )
    return Command(
        request_id=uuid4().hex,
        intent_id="create-project",
        project_id="project",
        action="save_context",
        expected_revision=0,
        parameters={"project": project_to_payload(project)},
    ).model_copy(update=changes)


def seed(core):
    response = core.api.dispatch(project_command(core), Session("fixture", EntryKind.AGENT_RELAY))
    assert response.error is None, response.error


def binding_command(core, tmp_path):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    binding = LocalProjectBinding(
        "binding",
        1,
        "project",
        source.as_posix(),
        BindingForm.PLAIN,
        manifest_digest="declared-component-fixture",
        confirmed=True,
    )
    return Command(
        request_id=uuid4().hex,
        intent_id="bind-source",
        project_id="project",
        action="save_binding",
        expected_revision=0,
        target="binding",
        parameters={
            "binding": binding_to_payload(binding),
            "project_revision": 1,
            "expected_revision": 0,
        },
    )


def test_no_gesture_does_not_create_project(core):
    command = project_command(core)
    result = dispatch_native(core.api, command, prompt=lambda text: False)
    assert result.error.code == "USER_DECLINED"
    assert core.unit_of_work.repo.current_revision("project", "project") == 0


def test_create_project_allocates_actual_commit_and_retransmission_is_idempotent(core):
    command = project_command(core)
    before = core.unit_of_work.current_commit_sequence()
    first = dispatch_native(core.api, command, prompt=lambda text: True)
    assert first.error is None, first.error
    saved = core.unit_of_work.repo.read(aggregate_kind="project", record_id="project", revision=1)
    assert saved.payload["created_at_commit"] == str(before + 1)
    sequence = core.unit_of_work.current_commit_sequence()
    again = dispatch_native(
        core.api, command.model_copy(update={"request_id": uuid4().hex}), prompt=lambda text: True
    )
    assert again.result == first.result
    assert core.unit_of_work.current_commit_sequence() == sequence


def test_binding_relay_cannot_authorize_but_core_gesture_has_saved_materials(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)
    relay = core.api.dispatch(command, Session("relay", EntryKind.AGENT_RELAY))
    assert relay.error.code == "AWAITING_USER_CONFIRMATION"
    previews = []

    def gesture(text):
        previews.append(text)
        assert core.unit_of_work.repo.current_revision("binding", "binding") == 0
        assert '"saved_materials"' in text and '"测试目标"' in text
        assert core.workspace.workspace_id in text and "bind-source" in text
        return True

    first = dispatch_native(core.api, command, prompt=gesture, records=core.unit_of_work.repo)
    assert first.error is None, first.error
    assert first.request_id == command.request_id and first.intent_id == command.intent_id
    assert len(previews) == 1
    sequence = core.unit_of_work.current_commit_sequence()
    again = dispatch_native(
        core.api,
        command.model_copy(update={"request_id": uuid4().hex}),
        prompt=lambda text: pytest.fail("committed intent must be recalled before a new challenge"),
        records=core.unit_of_work.repo,
    )
    assert again.result == first.result and again.error is None
    assert core.unit_of_work.current_commit_sequence() == sequence


def test_declined_challenge_is_revoked_without_saving_binding(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)
    sessions = []
    original = core.api._session_finalizer
    core.api._session_finalizer = lambda session: (sessions.append(session), original(session))
    result = dispatch_native(
        core.api, command, prompt=lambda text: False, records=core.unit_of_work.repo
    )
    assert result.error.code == "USER_DECLINED"
    assert sessions and sessions[-1].entry_kind is EntryKind.HUMAN_UI
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0
    # The normal finalizer persists revocation; it may not leave a live UI session.
    assert not any(key[0].startswith("native-ui-") for key in core.api._requests)


def test_changed_saved_owner_during_review_cannot_be_confirmed(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)

    def gesture(text):
        payload = dict(
            core.unit_of_work.repo.read(
                aggregate_kind="project",
                record_id="project",
                revision=1,
            ).payload
        )
        payload.update(name="changed", revision=2)
        response = core.api.dispatch(
            Command(
                request_id=uuid4().hex,
                intent_id="change-project",
                project_id="project",
                action="save_context",
                expected_revision=1,
                parameters={"project": payload},
            ),
            Session("other-entry", EntryKind.AGENT_RELAY),
        )
        assert response.error is None
        return True

    result = dispatch_native(core.api, command, prompt=gesture, records=core.unit_of_work.repo)
    assert result.error is not None
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0


@pytest.mark.parametrize(
    "key", ["user_confirmed", "approval_challenge_id", "interaction", "input_digest"]
)
def test_client_cannot_supply_user_event(core, key):
    command = project_command(core)
    command = command.model_copy(update={"parameters": command.parameters | {key: True}})
    response = dispatch_native(
        core.api, command, prompt=lambda text: pytest.fail("must not prompt")
    )
    assert response.error.code == "INVALID_COMMAND"


def test_missing_exact_materials_or_failed_preview_never_writes(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)
    result = dispatch_native(core.api, command, prompt=lambda text: pytest.fail("must not prompt"))
    assert result.error.code == "NATIVE_CONFIRMATION_UNAVAILABLE"
    corrupted = SimpleNamespace(read=lambda **kwargs: SimpleNamespace(payload={}))
    result = dispatch_native(
        core.api, command, prompt=lambda text: pytest.fail("must not prompt"), records=corrupted
    )
    assert result.error.code == "NATIVE_CONFIRMATION_UNAVAILABLE"
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0


def test_oversized_preview_and_dialog_error_do_not_write(core):
    command = project_command(core)
    payload = dict(command.parameters["project"], goal="x" * MAX_PROMPT_CHARS)
    result = dispatch_native(
        core.api,
        command.model_copy(update={"parameters": {"project": payload}}),
        prompt=lambda text: pytest.fail("must not truncate a preview"),
    )
    assert result.error.code == "CONFIRMATION_PREVIEW_LIMIT"

    def broken(text):
        raise RuntimeError("desktop unavailable")

    assert (
        dispatch_native(core.api, command, prompt=broken).error.code
        == "NATIVE_CONFIRMATION_UNAVAILABLE"
    )
    assert core.unit_of_work.repo.current_revision("project", "project") == 0


def test_control_requires_negotiated_adapter_and_exact_envelope(core):
    command = project_command(core)
    frame = {
        CONTROL_KEY: NATIVE_CONFIRMATION_VERSION,
        "request_id": command.request_id,
        "command": command.model_dump(mode="json"),
    }
    session = Session("relay", EntryKind.AGENT_RELAY)
    assert (
        dispatch_frame(core.api, session, json.dumps(frame).encode()).error.code
        == "UNKNOWN_CONTROL"
    )
    for changes in ({"user_confirmed": True}, {"request_id": "wrong"}):
        response = dispatch_frame(
            core.api,
            session,
            json.dumps(frame | changes).encode(),
            native_dispatch=lambda c: pytest.fail("must not dispatch"),
        )
        assert response.error.code == "INVALID_COMMAND"


def test_client_negotiates_then_uses_verified_core_control_without_approval_claim(core):
    commands = []

    class Channel:
        def write_message(self, payload):
            commands.append(json.loads(payload))
            self.response = dispatch_frame(
                core.api,
                Session("relay", EntryKind.AGENT_RELAY),
                payload,
                native_dispatch=lambda command: dispatch_native(
                    core.api, command, prompt=lambda t: False
                ),
            )

        def read_message(self):
            return self.response.model_dump_json().encode()

        def close(self):
            pass

    client = CoreClient(CoreEndpoint(core.workspace.workspace_id, core.api.instance_id, Channel()))
    response = client.send_native(project_command(core))
    assert response.error.code == "USER_DECLINED"
    assert commands[0]["action"] == "doctor"
    assert commands[1][CONTROL_KEY] == NATIVE_CONFIRMATION_VERSION
    assert set(commands[1]) == {CONTROL_KEY, "request_id", "command"}


def test_old_core_does_not_receive_new_control(core):
    commands = []

    class Channel:
        def write_message(self, payload):
            commands.append(payload)
            self.response = dispatch_frame(
                core.api, Session("relay", EntryKind.AGENT_RELAY), payload
            )

        def read_message(self):
            return self.response.model_dump_json().encode()

        def close(self):
            pass

    client = CoreClient(CoreEndpoint(core.workspace.workspace_id, core.api.instance_id, Channel()))
    with pytest.raises(CoreResultUnverified, match="不支持"):
        client.send_native(project_command(core))
    assert len(commands) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"project_id": None},
        {"intent_id": None},
        {"expected_revision": None},
        {"request_id": "bind-source"},
    ],
)
def test_native_binding_requires_full_write_identity(core, tmp_path, changes):
    command = binding_command(core, tmp_path).model_copy(update=changes)
    result = dispatch_native(core.api, command, prompt=lambda text: pytest.fail("must not prompt"))
    assert result.error.code == "INVALID_COMMAND"
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0


def test_finalizer_uncertainty_is_not_acknowledged_as_success_or_core_shutdown(core):
    command = project_command(core)

    def uncertain(command):
        result = dispatch_native(core.api, command, prompt=lambda text: True)
        assert result.error is None
        raise OSError("component injected finalizer failure after commit")

    frame = json.dumps(
        {
            CONTROL_KEY: NATIVE_CONFIRMATION_VERSION,
            "request_id": command.request_id,
            "command": command.model_dump(mode="json"),
        }
    ).encode()
    result = dispatch_frame(
        core.api, Session("relay", EntryKind.AGENT_RELAY), frame, native_dispatch=uncertain
    )
    assert result.error.code == "NATIVE_SESSION_FINALIZATION_UNVERIFIED"
    assert core.unit_of_work.repo.current_revision("project", "project") == 1
    doctor = core.api.dispatch(
        Command(request_id=uuid4().hex, action="doctor"), Session("relay", EntryKind.AGENT_RELAY)
    )
    assert doctor.result["status"] == "READY"
