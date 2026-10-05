"""Environment declarations do not constitute actual resolution or execution grants."""

from dataclasses import replace
from pathlib import Path

import pytest

import aitest.application.controlled_write as controlled_module
from aitest.application.project.serialization import environment_to_payload, project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.domain.project.context import EnvironmentRef, IsolationMode, LocalProject
from aitest.interfaces.local.api import EntryKind, Session
from tests.support.controlled_environment import (
    confirm_environment,
    environment_command,
    prepare_environment,
)
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_default_source_analysis import AGENT, dispatch


@pytest.fixture
def environment_core(tmp_path):
    assert Path(controlled_module.__file__).resolve().is_relative_to(Path.cwd())
    core = assemble_workspace_core(tmp_path / "workspace", instance_id="environment-core")
    project = LocalProject(
        "project-env", core.workspace.workspace_id, "Environment", "Controlled component proof", "1"
    )
    assert (
        dispatch(
            core,
            "save_context",
            project=project.project_id,
            parameters={"project": project_to_payload(project)},
        ).error
        is None
    )
    try:
        yield core, project
    finally:
        core.lifetime_lock.release()


def command(core, project, mode=IsolationMode.NONE, **kwargs):
    environment = EnvironmentRef(
        "env",
        1,
        "Python 3.13",
        "declared dependencies",
        isolation_mode=mode,
        isolation_confirmed=mode is not IsolationMode.VENV,
    )
    return environment_command(
        project.project_id,
        environment_to_payload(environment, project_id=project.project_id),
        **kwargs,
    )


@pytest.mark.parametrize("session", [AGENT, Session("label-only", EntryKind.HUMAN_UI, True)])
def test_non_default_environment_needs_actual_core_interaction(environment_core, session):
    core, project = environment_core
    before = core.unit_of_work.current_commit_sequence()
    response = core.api.dispatch(command(core, project), session)
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.current_commit_sequence() == before
    assert core.unit_of_work.repo.current_revision("environment", "env") == 0


@pytest.mark.parametrize("mode", [IsolationMode.NONE, IsolationMode.UNMANAGED])
def test_confirmed_environment_has_exact_saved_origin_and_six_record_batch(environment_core, mode):
    core, project = environment_core
    cmd = command(core, project, mode)
    session = Session("environment-batch", EntryKind.HUMAN_UI, True)
    prepared = prepare_environment(core, cmd, session)
    assert prepared.error is None, prepared.error
    before = core.unit_of_work.current_commit_sequence()
    challenge = prepared.result
    approved = cmd.model_copy(
        update={
            "request_id": "approved-environment-batch",
            "parameters": dict(cmd.parameters)
            | {"approval_challenge_id": challenge["challenge_id"]},
        }
    )
    response = core.api.dispatch_user_confirmation(
        approved,
        session,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )
    assert response.error is None, response.error
    assert core.unit_of_work.current_commit_sequence() == before + 6
    raw = core.unit_of_work.repo.read(
        aggregate_kind="environment", record_id="env", revision=1
    ).payload
    assert raw["isolation_mode"] == mode.value
    proof = core.initial_run_registration.controlled_writes
    proof.validate_saved_write(
        project_id=project.project_id,
        action="save_environment",
        aggregate_kind="environment",
        record_id="env",
        record_revision=1,
        payload=raw,
    )
    original_seq = core.unit_of_work.current_commit_sequence()
    assert (
        core.api.dispatch(
            cmd.model_copy(update={"request_id": "replay"}),
            Session("history", EntryKind.HUMAN_UI, True),
        ).error
        is None
    )
    assert core.unit_of_work.current_commit_sequence() == original_seq
    assert core.unit_of_work.repo.current_revision("attempt", "none") == 0
    # Canonical transaction manifest, rather than guessed per-record commit ids, proves the batch.
    receipt = core.unit_of_work.repo.read(
        aggregate_kind="approval_intent",
        record_id=proof.identity(project.project_id, cmd.intent_id),
        revision=1,
    )
    assert receipt.payload["record_revision"] == 1
    assert receipt.payload["created_at_commit"] == raw["approval_commit_seq"]


def test_ordinary_venv_declaration_remains_available_to_relay(environment_core):
    core, project = environment_core
    cmd = command(core, project, IsolationMode.VENV)
    response = dispatch(
        core,
        "save_environment",
        project=project.project_id,
        parameters={"environment": cmd.parameters["environment"]},
        session=AGENT,
    )
    assert response.error is None, response.error
    raw = core.unit_of_work.repo.read(
        aggregate_kind="environment", record_id="env", revision=1
    ).payload
    assert "approval_confirmation_id" not in raw
    assert raw["isolation_mode"] == "venv"


def test_environment_proof_cannot_survive_lost_original_receipt(environment_core, monkeypatch):
    core, project = environment_core
    assert confirm_environment(core, command(core, project)).error is None
    raw = core.unit_of_work.repo.read(
        aggregate_kind="environment", record_id="env", revision=1
    ).payload
    before = core.unit_of_work.current_commit_sequence()
    read = core.unit_of_work.repo.read

    def lost(**kwargs):
        saved = read(**kwargs)
        if saved.payload.get("action") == "save_environment":
            raise KeyError("lost original environment receipt")
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", lost)
    with pytest.raises(ValueError):
        core.initial_run_registration.controlled_writes.validate_saved_write(
            project_id=project.project_id,
            action="save_environment",
            aggregate_kind="environment",
            record_id="env",
            record_revision=1,
            payload=raw,
        )
    assert core.unit_of_work.current_commit_sequence() == before


@pytest.mark.parametrize(
    ("kind", "occurrence"),
    [
        ("approval_challenge", 1),
        ("approval_interaction", 1),
        ("approval_confirmation", 1),
        ("approval_intent", 1),
        ("environment", 1),
        ("approval_intent", 2),
    ],
)
def test_environment_publication_failure_does_not_consume_confirmation(
    environment_core, monkeypatch, kind, occurrence
):
    core, project = environment_core
    cmd = command(core, project)
    session = Session("atomic-environment", EntryKind.HUMAN_UI, True)
    prepared = prepare_environment(core, cmd, session)
    assert prepared.error is None, prepared.error
    before = core.unit_of_work.current_commit_sequence()
    stage = core.unit_of_work.stage_record

    matched = 0

    def fail(**kwargs):
        nonlocal matched
        if kwargs["aggregate_kind"] == kind:
            matched += 1
        if kwargs["aggregate_kind"] == kind and matched == occurrence:
            raise OSError("controlled environment stage failure")
        return stage(**kwargs)

    monkeypatch.setattr(core.unit_of_work, "stage_record", fail)
    challenge = prepared.result
    approved = cmd.model_copy(
        update={
            "request_id": "actual-environment-confirmation",
            "parameters": dict(cmd.parameters)
            | {"approval_challenge_id": challenge["challenge_id"]},
        }
    )
    response = core.api.dispatch_user_confirmation(
        approved,
        session,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )
    assert response.error is not None
    assert core.unit_of_work.current_commit_sequence() == before
    assert core.unit_of_work.repo.current_revision("environment", "env") == 0
    assert not core.unit_of_work.pending
    assert matched == occurrence
    monkeypatch.setattr(core.unit_of_work, "stage_record", stage)
    retry = core.api.dispatch_user_confirmation(
        approved.model_copy(update={"request_id": "retry-environment-confirmation"}),
        session,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )
    assert retry.error is None, retry.error
    assert core.unit_of_work.repo.current_revision("environment", "env") == 1
    assert core.unit_of_work.current_commit_sequence() == before + 6


@pytest.mark.parametrize("consumer", ["initial", "runtime"])
def test_non_default_environment_origin_is_consumed_by_c(authoritative, monkeypatch, consumer):
    from aitest.contracts.prepared_run import EnvironmentIsolationModeFact
    from tests.unit.test_authoritative_preparation import prepare
    from tests.unit.test_initial_run_registration import register
    from tests.unit.test_persisted_runtime_revision import (
        apply,
        initial_basis,
        revision_request,
        saved_next_case,
    )

    core, inputs, _ = authoritative
    declared = EnvironmentRef(
        "env-local",
        2,
        "Python 3.13",
        "component declaration",
        isolation_mode=IsolationMode.NONE,
        isolation_confirmed=True,
    )
    cmd = environment_command(
        inputs.project_id,
        environment_to_payload(declared, project_id=inputs.project_id),
        expected=1,
    )
    response = confirm_environment(core, cmd)
    assert response.error is None, response.error
    inputs = replace(
        inputs,
        environment=inputs.environment.model_copy(
            update={"revision": 2, "isolation_mode": EnvironmentIsolationModeFact.NONE}
        ),
        input_revisions=replace(inputs.input_revisions, environment_revision=2),
    )
    # Existing fake resolved facts remain unverified; this proves confirmation consumption only.
    if consumer == "initial":
        prepared = prepare(core, inputs).result
        assert prepared["status"] == "prepared"
    else:
        plan, cases, before = initial_basis(core, inputs)
        next_case = saved_next_case(core, inputs, replace(cases[0], revision=2))
        request = revision_request(before, next_case)
    sequence = core.unit_of_work.current_commit_sequence()
    read = core.unit_of_work.repo.read

    def lost(**kwargs):
        saved = read(**kwargs)
        if saved.payload.get("action") == "save_environment":
            raise KeyError("lost exact environment confirmation receipt")
        return saved

    monkeypatch.setattr(core.unit_of_work.repo, "read", lost)
    with pytest.raises(ValueError):
        if consumer == "initial":
            register(core, prepared)
        else:
            apply(core, plan, before, request)
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert not core.unit_of_work.pending
