"""Real default policy persistence with synthetic controlled events and provider transport."""

import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from aitest.application.project.context import create_project
from aitest.application.project.serialization import project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.planning.model_outbound import ModelOutboundPolicy
from aitest.infrastructure.adapters.model import HttpResponse
from aitest.infrastructure.credentials import EnvironmentSecretProvider, SecretManager
from aitest.interfaces.local.api import EntryKind, Session
from tests.support.controlled_model_policy import (
    controlled_policy_confirm,
    prepare_policy_challenge,
)
from tests.unit.test_model_orchestration import PROJECT_ID, _policy

HUMAN = Session("model-policy-controlled-fixture", EntryKind.HUMAN_UI, True)
RELAY = Session("model-policy-relay-fixture", EntryKind.AGENT_RELAY)


@pytest.fixture
def policy_core(tmp_path, monkeypatch):
    monkeypatch.setenv("AITEST_POLICY_PROOF_FIXTURE", "synthetic-policy-proof-secret")
    policy = _policy()
    core = assemble_workspace_core(
        tmp_path / "workspace",
        instance_id="policy-proof-fixture",
        model_endpoint=policy.endpoint.address,
        model_secret_reference=("model", "policy-proof"),
        secret_manager=SecretManager(
            (
                EnvironmentSecretProvider(
                    {
                        ("model", "policy-proof"): "AITEST_POLICY_PROOF_FIXTURE",
                    }
                ),
            )
        ),
    )
    project = create_project(
        project_id=PROJECT_ID,
        workspace_id=core.workspace.workspace_id,
        name="policy proof",
        goal="draft only",
        created_at_commit="0",
    )
    response = core.api.dispatch(
        Command(
            action="save_context",
            request_id="policy-project-request",
            intent_id="policy-project-intent",
            project_id=PROJECT_ID,
            expected_revision=0,
            parameters={"project": project_to_payload(project)},
        ),
        HUMAN,
    )
    assert response.error is None
    calls = []

    class Transport:
        def post(self, url, *, headers, body, timeout_seconds):
            calls.append(url)
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "id": "fixture-model-response",
                        "choices": [
                            {
                                "message": {"content": "safe policy draft"},
                            }
                        ],
                    }
                ).encode(),
            )

    core.model_provider._transport = Transport()
    yield core, calls
    core.lifetime_lock.release()


def policy_command(
    *,
    policy=None,
    intent="model-policy-save-intent",
    request="model-policy-save-request",
    expected=0,
):
    return Command(
        action="save_model_outbound_policy",
        project_id=PROJECT_ID,
        intent_id=intent,
        request_id=request,
        expected_revision=expected,
        parameters={
            "project_revision": 1,
            "policy": TypeAdapter(ModelOutboundPolicy).dump_python(
                policy or _policy(),
                mode="json",
            ),
        },
    )


def generation_command(
    *, intent="model-policy-generation", request="model-policy-generation-request"
):
    return Command(
        action="generate_draft",
        project_id=PROJECT_ID,
        request_id=request,
        intent_id=intent,
        expected_revision=0,
        parameters={
            "generation_mode": "model",
            "project_revision": 1,
            "policy_revision": 1,
            "source_revision": 0,
            "base_manual_revision": 0,
            "task_type": "check_content_draft",
            "draft_kind": "check_content",
            "selected_material": {"project_context": "safe selected context"},
        },
    )


def test_role_and_client_confirmation_cannot_save_a_confirmed_model_policy(policy_core):
    core, calls = policy_core
    sequence = core.unit_of_work.current_commit_sequence()
    response = core.api.dispatch(policy_command(), HUMAN)
    assert response.error is not None
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert calls == []


def test_default_generation_cannot_use_a_legacy_policy_without_saved_origin(policy_core):
    core, calls = policy_core
    unit = core.unit_of_work
    unit.begin("legacy-policy-fixture", PROJECT_ID, intent_id="legacy-policy-fixture-intent")
    unit.stage_record(
        aggregate_kind="model_outbound_policy",
        record_id="model-policy:" + PROJECT_ID,
        expected_revision=0,
        payload=TypeAdapter(ModelOutboundPolicy).dump_python(
            _policy(),
            mode="json",
        ),
    )
    unit.commit("legacy-policy-fixture")
    sequence = unit.current_commit_sequence()
    response = core.api.dispatch(generation_command(), RELAY)
    assert response.error is not None
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert calls == []
    assert unit.current_commit_sequence() == sequence


def test_policy_origin_business_record_and_receipt_share_one_commit_before_send(policy_core):
    core, calls = policy_core
    command = policy_command()
    prepared = prepare_policy_challenge(core, command, HUMAN)
    assert prepared.error is None, prepared.error
    sequence = core.unit_of_work.current_commit_sequence()
    saved = controlled_policy_confirm(core, command, session=HUMAN)
    assert saved.error is None, saved.error
    assert saved.result == {
        "aggregate_kind": "model_outbound_policy",
        "record_id": "model-policy:" + PROJECT_ID,
        "revision": 1,
    }
    assert core.unit_of_work.current_commit_sequence() == sequence + 6
    payload = core.unit_of_work.repo.read(
        aggregate_kind="model_outbound_policy", record_id="model-policy:" + PROJECT_ID, revision=1
    ).payload
    confirmation_id = payload["approval_confirmation_id"]
    origin = core.unit_of_work.repo.read(
        aggregate_kind="approval_confirmation", record_id=confirmation_id, revision=1
    ).payload["confirmation"]
    assert payload["confirmation"]["confirmation_id"] == confirmation_id != "confirmation-1"
    assert (
        payload["confirmation"]["confirmed_at_commit"]
        == origin["confirmed_at_commit"]
        == str(sequence + 6)
    )
    assert origin["basis"]["credential_scope_ref"] == "model:policy-proof"
    assert origin["basis"]["action"] == command.action
    assert calls == []
    first = core.api.dispatch(generation_command(), RELAY)
    assert first.error is None, first.error
    assert first.result["status"] == "draft_ready", first.result
    replay = core.api.dispatch(generation_command(request="generation-retry"), RELAY)
    assert replay.error is None
    assert replay.result["content"] == first.result["content"]
    assert len(calls) == 1


def test_policy_save_replay_returns_its_exact_historical_revision_and_new_input_conflicts(
    policy_core,
):
    core, _ = policy_core
    original = policy_command()
    first = controlled_policy_confirm(core, original, session=HUMAN)
    assert first.error is None, first.error
    second = controlled_policy_confirm(
        core,
        policy_command(
            policy=replace(_policy(), revision=2, ai_enabled=False),
            expected=1,
            intent="second-policy-intent",
            request="second-policy-request",
        ),
        session=HUMAN,
    )
    assert second.error is None, second.error
    assert second.result["revision"] == 2
    sequence = core.unit_of_work.current_commit_sequence()
    replay = core.api.dispatch(
        original.model_copy(update={"request_id": "historical-policy-retry"}), HUMAN
    )
    assert replay.error is None, replay.error
    assert replay.result == first.result
    conflict = core.api.dispatch(
        policy_command(policy=replace(_policy(), ai_enabled=False), request="changed-policy-input"),
        HUMAN,
    )
    assert conflict.error.code == "INTENT_CONFLICT"
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert (
        core.unit_of_work.repo.current_revision(
            "model_outbound_policy", "model-policy:" + PROJECT_ID
        )
        == 2
    )


@pytest.mark.parametrize("point", [1, 2, 3, 4, 5, 6, "before_commit"])
def test_policy_consent_and_result_roll_back_at_every_batch_fault(policy_core, monkeypatch, point):
    core, calls = policy_core
    command = policy_command()
    prepared = prepare_policy_challenge(core, command, HUMAN)
    assert prepared.error is None
    unit = core.unit_of_work
    sequence = unit.current_commit_sequence()
    stage, commit = unit.stage_record, unit.commit
    counter = [0]

    def staged(**kwargs):
        counter[0] += 1
        if counter[0] == point:
            raise OSError("injected policy batch stage failure")
        return stage(**kwargs)

    def committed(*args, **kwargs):
        if point == "before_commit":
            raise OSError("injected policy batch publication failure")
        return commit(*args, **kwargs)

    monkeypatch.setattr(unit, "stage_record", staged)
    monkeypatch.setattr(unit, "commit", committed)
    failed = controlled_policy_confirm(core, command, session=HUMAN)
    assert failed.error is not None
    assert unit.current_commit_sequence() == sequence
    assert unit.repo.current_revision("model_outbound_policy", "model-policy:" + PROJECT_ID) == 0
    assert unit.repo.current_revision("approval_challenge", prepared.result["challenge_id"]) == 1
    assert calls == []
    monkeypatch.setattr(unit, "stage_record", stage)
    monkeypatch.setattr(unit, "commit", commit)
    retry = controlled_policy_confirm(
        core, command.model_copy(update={"request_id": "retry-after-fault"}), session=HUMAN
    )
    assert retry.error is None, retry.error
    assert unit.current_commit_sequence() == sequence + 6


def test_published_policy_lost_reply_replays_without_another_user_event(policy_core, monkeypatch):
    core, calls = policy_core
    command = policy_command()
    assert prepare_policy_challenge(core, command, HUMAN).error is None
    commit = core.unit_of_work.commit

    def lost(*args, **kwargs):
        commit(*args, **kwargs)
        raise OSError("injected policy response lost after publication")

    monkeypatch.setattr(core.unit_of_work, "commit", lost)
    failed = controlled_policy_confirm(core, command, session=HUMAN)
    assert failed.error is not None
    assert "response lost" in failed.error.message
    assert (
        core.unit_of_work.repo.current_revision(
            "model_outbound_policy", "model-policy:" + PROJECT_ID
        )
        == 1
    )
    sequence = core.unit_of_work.current_commit_sequence()
    monkeypatch.setattr(core.unit_of_work, "commit", commit)
    replay = core.api.dispatch(
        command.model_copy(update={"request_id": "lost-policy-retry"}), HUMAN
    )
    assert replay.error is None, replay.error
    assert replay.result["revision"] == 1
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert calls == []


@pytest.mark.parametrize(
    "damage",
    [
        "endpoint",
        "kinds",
        "origin_id",
        "confirmation_missing",
        "receipt_missing",
        "receipt_intent",
        "receipt_digest",
        "boolean_revision",
        "interaction_missing",
    ],
)
def test_corrupt_saved_policy_origin_cannot_reach_model_transport(policy_core, monkeypatch, damage):
    core, calls = policy_core
    assert controlled_policy_confirm(core, policy_command(), session=HUMAN).error is None
    repo = core.unit_of_work.repo
    read = repo.read

    def damaged(**kwargs):
        saved = read(**kwargs)
        kind = kwargs["aggregate_kind"]
        if kind == "approval_confirmation" and damage == "confirmation_missing":
            raise KeyError("exact core confirmation missing")
        if kind == "approval_interaction" and damage == "interaction_missing":
            raise OSError("exact interaction unavailable")
        raw = json.loads(json.dumps(saved.payload))
        if (
            kind == "approval_intent"
            and raw.get("schema_version") == "aitest.model-policy-intent/1.0"
        ):
            if damage == "receipt_missing":
                raise KeyError("exact policy receipt missing")
            if damage == "receipt_intent":
                raw["intent_id"] = "other-policy-intent"
            if damage == "receipt_digest":
                raw["policy_record_digest"] = "sha256:other"
        if kind == "model_outbound_policy":
            if damage == "endpoint":
                raw["endpoint"]["address"] = "https://different.invalid/v1"
            if damage == "kinds":
                raw["allowed_material_kinds"] = ["project_context"]
            if damage == "origin_id":
                raw["approval_confirmation_id"] = "another-origin"
            if damage == "boolean_revision":
                return replace(saved, revision=True)
        return replace(saved, payload=raw)

    monkeypatch.setattr(repo, "read", damaged)
    sequence = core.unit_of_work.current_commit_sequence()
    refused = core.api.dispatch(generation_command(), RELAY)
    assert refused.error is not None, refused.result
    assert refused.error.code == "AWAITING_USER_CONFIRMATION"
    assert calls == []
    assert core.unit_of_work.current_commit_sequence() == sequence


def test_read_after_restart_requires_the_same_frozen_credential_reference(policy_core):
    core, _ = policy_core
    command = policy_command()
    saved = controlled_policy_confirm(core, command, session=HUMAN)
    assert saved.error is None, saved.error
    sequence = core.unit_of_work.current_commit_sequence()
    core.lifetime_lock.release()
    scope = {
        ("model", "policy-proof"): "AITEST_POLICY_PROOF_FIXTURE",
        ("model", "different-reference"): "AITEST_POLICY_PROOF_FIXTURE",
    }
    restarted = assemble_workspace_core(
        core.workspace.root,
        instance_id="policy-same-scope-restart",
        model_endpoint=_policy().endpoint.address,
        model_secret_reference=("model", "policy-proof"),
        secret_manager=SecretManager((EnvironmentSecretProvider(scope),)),
    )
    try:
        replay = restarted.api.dispatch(
            command.model_copy(update={"request_id": "new-core-policy-replay"}), HUMAN
        )
        assert replay.error is None, replay.error
        assert replay.result == saved.result
        assert restarted.unit_of_work.current_commit_sequence() == sequence
    finally:
        restarted.lifetime_lock.release()
    changed = assemble_workspace_core(
        core.workspace.root,
        instance_id="policy-changed-scope-restart",
        model_endpoint=_policy().endpoint.address,
        model_secret_reference=("model", "different-reference"),
        secret_manager=SecretManager((EnvironmentSecretProvider(scope),)),
    )
    try:
        refused = changed.api.dispatch(generation_command(), RELAY)
        assert refused.error.code == "AWAITING_USER_CONFIRMATION"
        assert changed.unit_of_work.current_commit_sequence() == sequence
    finally:
        changed.lifetime_lock.release()


@pytest.mark.parametrize(
    "damage", ["ai_enabled", "endpoint", "project_revision", "expected_revision", "purpose"]
)
def test_a_policy_challenge_cannot_confirm_changed_inputs(policy_core, damage):
    core, calls = policy_core
    command = policy_command()
    prepared = prepare_policy_challenge(core, command, HUMAN)
    assert prepared.error is None
    parameters = json.loads(json.dumps(command.parameters))
    if damage == "ai_enabled":
        parameters["policy"]["ai_enabled"] = False
    elif damage == "endpoint":
        parameters["policy"]["endpoint"]["address"] = "https://different.invalid/v1"
    elif damage == "project_revision":
        parameters["project_revision"] = 2
    elif damage == "purpose":
        parameters["policy"]["endpoint"]["purpose"] = "business-api"
    parameters["approval_challenge_id"] = prepared.result["challenge_id"]
    approved = command.model_copy(
        update={
            "request_id": "changed-frozen-policy",
            "parameters": parameters,
            "expected_revision": 1 if damage == "expected_revision" else 0,
        }
    )
    sequence = core.unit_of_work.current_commit_sequence()
    response = core.api.dispatch_user_confirmation(
        approved,
        HUMAN,
        challenge_id=prepared.result["challenge_id"],
        input_digest=prepared.result["basis"]["input_digest"],
    )
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.current_commit_sequence() == sequence
    assert (
        core.unit_of_work.repo.current_revision(
            "approval_challenge", prepared.result["challenge_id"]
        )
        == 1
    )
    assert calls == []


def test_a_save_receipt_cannot_be_redirected_to_another_valid_policy_intent(
    policy_core, monkeypatch
):
    from aitest.application.approval_service import _digest

    core, _ = policy_core
    first = controlled_policy_confirm(core, policy_command(), session=HUMAN)
    assert first.error is None
    second = controlled_policy_confirm(
        core,
        policy_command(
            policy=replace(_policy(), revision=2, ai_enabled=False),
            expected=1,
            intent="other-valid-policy-intent",
            request="other-valid-policy-request",
        ),
        session=HUMAN,
    )
    assert second.error is None
    repo, sequence = core.unit_of_work.repo, core.unit_of_work.current_commit_sequence()
    read = repo.read
    other = read(
        aggregate_kind="model_outbound_policy", record_id="model-policy:" + PROJECT_ID, revision=2
    ).payload

    def redirected(**kwargs):
        saved = read(**kwargs)
        raw = dict(saved.payload)
        if (
            kwargs["aggregate_kind"] == "approval_intent"
            and raw.get("schema_version") == "aitest.model-policy-intent/1.0"
            and raw.get("intent_id") == "model-policy-save-intent"
        ):
            raw.update(
                policy_record_revision=2,
                policy_record_digest=_digest(dict(other)),
                confirmation_id=other["approval_confirmation_id"],
            )
            return replace(saved, payload=raw)
        return saved

    monkeypatch.setattr(repo, "read", redirected)
    response = core.api.dispatch(policy_command(request="redirected-policy-receipt"), HUMAN)
    assert response.error is not None
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.current_commit_sequence() == sequence


def test_historical_save_replay_is_readable_after_credential_reference_changes(policy_core):
    core, _ = policy_core
    first = controlled_policy_confirm(core, policy_command(), session=HUMAN)
    assert first.error is None
    sequence = core.unit_of_work.current_commit_sequence()
    core.lifetime_lock.release()
    changed = assemble_workspace_core(
        core.workspace.root,
        instance_id="historical-policy-recall",
        model_endpoint=_policy().endpoint.address,
        model_secret_reference=("model", "different-reference"),
        secret_manager=SecretManager(
            (
                EnvironmentSecretProvider(
                    {
                        ("model", "different-reference"): "AITEST_POLICY_PROOF_FIXTURE",
                    }
                ),
            )
        ),
    )
    try:
        historical = changed.api.dispatch(policy_command(request="historical-policy-only"), HUMAN)
        assert historical.error is None, historical.error
        assert historical.result == first.result
        assert changed.unit_of_work.current_commit_sequence() == sequence
        refused = changed.api.dispatch(generation_command(), RELAY)
        assert refused.error.code == "AWAITING_USER_CONFIRMATION"
        assert changed.unit_of_work.current_commit_sequence() == sequence
    finally:
        changed.lifetime_lock.release()
