"""Panel confirmation component tests; not a real Trae/host acceptance."""

import json
from uuid import uuid4

import pytest

from aitest.application.project.serialization import binding_to_payload, project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.project.context import BindingForm, LocalProject, LocalProjectBinding
from aitest.interfaces.local.api import EntryKind, Session
from aitest.interfaces.local.core_worker import CONTROL_KEY, dispatch_frame
from aitest.interfaces.local.panel_confirmation import (
    PANEL_CONFIRMATION_VERSION,
    confirm_panel_confirmation,
    dispatch_panel_human,
    prepare_panel_confirmation,
)


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


def test_panel_confirmation_prepares_then_commits_only_after_its_gesture(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)
    panel = Session("panel-host", EntryKind.HUMAN_UI, True)
    prepared = prepare_panel_confirmation(
        core.api, panel, command, records=core.unit_of_work.repo
    )
    assert prepared.error is None, prepared.error
    assert prepared.request_id == command.request_id
    result = prepared.result or {}
    assert result["prompt"]["title"]
    assert "facts" in result["prompt"] and "details" in result["prompt"]
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0

    confirmed = confirm_panel_confirmation(
        core.api,
        panel,
        command,
        challenge_id=str(result["challenge_id"]),
        input_digest=str(result["input_digest"]),
    )
    assert confirmed.error is None, confirmed.error
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 1

    # Re-sending the same intent recalls the committed receipt without a prompt.
    recalled = prepare_panel_confirmation(
        core.api, panel, command, records=core.unit_of_work.repo
    )
    assert recalled.error is None, recalled.error
    assert "prompt" not in (recalled.result or {})
    assert recalled.result == confirmed.result


def test_panel_confirmation_rejects_wrong_challenge_without_writing(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)
    panel = Session("panel-host", EntryKind.HUMAN_UI, True)
    prepared = prepare_panel_confirmation(
        core.api, panel, command, records=core.unit_of_work.repo
    )
    assert prepared.error is None, prepared.error
    result = prepared.result or {}
    rejected = confirm_panel_confirmation(
        core.api,
        panel,
        command,
        challenge_id="approval-challenge-wrong",
        input_digest=str(result["input_digest"]),
    )
    assert rejected.error is not None
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0


def test_panel_control_frames_prepare_and_confirm_through_the_core_worker(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)
    prepare_frame = json.dumps({
        CONTROL_KEY: PANEL_CONFIRMATION_VERSION,
        "phase": "prepare",
        "command": command.model_dump(mode="json"),
    }).encode()
    prepared = dispatch_frame(
        core.api,
        Session("panel-host", EntryKind.HUMAN_UI, True),
        prepare_frame,
        panel_prepare_dispatch=lambda session, cmd: prepare_panel_confirmation(
            core.api, session, cmd, records=core.unit_of_work.repo
        ),
        panel_confirm_dispatch=lambda session, cmd, challenge, digest:
            confirm_panel_confirmation(
                core.api, session, cmd, challenge_id=challenge, input_digest=digest
            ),
    )
    assert prepared.error is None, prepared.error
    result = prepared.result or {}
    assert result["prompt"]["title"]
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0

    confirm_frame = json.dumps({
        CONTROL_KEY: PANEL_CONFIRMATION_VERSION,
        "phase": "confirm",
        "command": command.model_dump(mode="json"),
        "challenge_id": result["challenge_id"],
        "input_digest": result["input_digest"],
    }).encode()
    confirmed = dispatch_frame(
        core.api,
        Session("panel-host", EntryKind.HUMAN_UI, True),
        confirm_frame,
        panel_prepare_dispatch=lambda session, cmd: prepare_panel_confirmation(
            core.api, session, cmd, records=core.unit_of_work.repo
        ),
        panel_confirm_dispatch=lambda session, cmd, challenge, digest:
            confirm_panel_confirmation(
                core.api, session, cmd, challenge_id=challenge, input_digest=digest
            ),
    )
    assert confirmed.error is None, confirmed.error
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 1


def test_panel_confirmation_rejects_agent_connections(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)
    relay = Session("relay", EntryKind.AGENT_RELAY, True)
    prepared = prepare_panel_confirmation(
        core.api, relay, command, records=core.unit_of_work.repo
    )
    assert prepared.error is not None
    assert prepared.error.code == "ENTRY_ACTION_NOT_ALLOWED"
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0


def test_panel_confirm_must_come_from_the_preparing_connection(core, tmp_path):
    seed(core)
    command = binding_command(core, tmp_path)
    first = Session("panel-first", EntryKind.HUMAN_UI, True)
    second = Session("panel-second", EntryKind.HUMAN_UI, True)
    prepared = prepare_panel_confirmation(
        core.api, first, command, records=core.unit_of_work.repo
    )
    assert prepared.error is None, prepared.error
    result = prepared.result or {}
    rejected = confirm_panel_confirmation(
        core.api,
        second,
        command,
        challenge_id=str(result["challenge_id"]),
        input_digest=str(result["input_digest"]),
    )
    assert rejected.error is not None
    assert rejected.error.code == "PANEL_CONFIRMATION_EXPIRED"
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0


def test_panel_human_dispatch_routes_non_challenge_human_actions(core):
    seed(core)
    command = Command(
        request_id="panel-human-1",
        intent_id="panel-human-intent",
        project_id="project",
        action="update_issue",
        expected_revision=0,
        parameters={},
    )
    response = dispatch_panel_human(
        core.api, Session("panel-host", EntryKind.HUMAN_UI, True), command
    )
    assert response.error is None or response.error.code != "ENTRY_ACTION_NOT_ALLOWED"


def test_panel_human_dispatch_rejects_challenge_and_agent_entries(core):
    seed(core)
    command = Command(
        request_id="panel-human-2",
        intent_id="panel-human-intent-2",
        project_id="project",
        action="publish_plan",
        expected_revision=0,
        parameters={},
    )
    human = dispatch_panel_human(
        core.api, Session("panel-host", EntryKind.HUMAN_UI, True), command
    )
    assert human.error is not None and human.error.code == "ENTRY_ACTION_NOT_ALLOWED"
    relay = dispatch_panel_human(
        core.api, Session("relay", EntryKind.AGENT_RELAY, True), command
    )
    assert relay.error is not None and relay.error.code == "ENTRY_ACTION_NOT_ALLOWED"
