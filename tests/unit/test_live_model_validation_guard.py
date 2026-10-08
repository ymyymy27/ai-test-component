"""The live harness consumes saved consent; it never generates a human event."""

import json
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from aitest.application.project.context import create_project
from aitest.application.project.serialization import project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.approvals import ApprovalRequired
from aitest.domain.planning.model_outbound import ModelOutboundPolicy
from aitest.infrastructure.adapters.model import HttpResponse
from aitest.infrastructure.credentials import EnvironmentSecretProvider, SecretManager
from scripts.validate_deepseek import ValidationBlocked, _revision, _saved_policy, run
from tests.support.controlled_model_policy import controlled_policy_confirm
from tests.unit.test_http_transport_limits import server
from tests.unit.test_model_orchestration import PROJECT_ID, _policy
from tests.unit.test_model_policy_approval import HUMAN, policy_command
from tests.unit.test_model_policy_approval import policy_core as policy_core


@pytest.fixture(params=["stub", "actual_http"])
def model_http_target(request):
    if request.param == "stub":
        yield None, []
        return

    def reply(connection, stopped):
        body = json.dumps({
            "id": "actual-local-model-response",
            "choices": [{"message": {"content": "合成草稿 synthetic-live-model-guard-secret"}}],
        }).encode()
        connection.sendall(
            f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
        )

    with server(reply) as value:
        yield value


def test_fresh_harness_cannot_create_confirmation_workspace_or_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must not assemble, resolve a secret, or send")

    monkeypatch.setattr("scripts.validate_deepseek.assemble_workspace_core", forbidden)
    monkeypatch.setattr("scripts.validate_deepseek.CountedTransport", forbidden)
    monkeypatch.setattr("scripts.validate_deepseek.SecretManager.has_secret", forbidden)
    result = run("SYNTHETIC_REFERENCE_ONLY")
    assert result["status"] == "blocked" and result["calls"] == []
    assert result["blocked_by"] == ["existing_controlled_validation_workspace_required"]


@pytest.mark.parametrize("value", [True, 1.0, "1", -1, 0])
def test_harness_cannot_coerce_a_saved_revision(value):
    with pytest.raises(ValidationBlocked, match="revision_unverified"):
        _revision(value)


def test_harness_reads_actual_saved_controlled_policy_without_model_call(policy_core):
    core, calls = policy_core
    response = controlled_policy_confirm(core, policy_command(), session=HUMAN)
    assert response.error is None
    before = core.unit_of_work.current_commit_sequence()
    policy = _saved_policy(core, PROJECT_ID, 1)
    assert policy.endpoint == _policy().endpoint and policy.confirmation is not None
    assert policy.confirmation.confirmation_id != _policy().confirmation.confirmation_id
    assert calls == [] and core.unit_of_work.current_commit_sequence() == before


def test_legacy_self_signed_policy_cannot_pass_harness_preflight(policy_core):
    core, calls = policy_core
    unit = core.unit_of_work
    unit.begin("legacy-harness-policy", PROJECT_ID)
    unit.stage_record(
        aggregate_kind="model_outbound_policy",
        record_id="model-policy:" + PROJECT_ID,
        expected_revision=0,
        payload=TypeAdapter(ModelOutboundPolicy).dump_python(_policy(), mode="json"),
    )
    unit.commit("legacy-harness-policy")
    before = unit.current_commit_sequence()
    with pytest.raises(ApprovalRequired, match="origin"):
        _saved_policy(core, PROJECT_ID, 1)
    assert calls == [] and unit.current_commit_sequence() == before


def test_existing_controlled_workspace_sends_once_then_replays_without_transport(
    tmp_path, monkeypatch, model_http_target
):
    url, actual_calls = model_http_target
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("AITEST_LIVE_GUARD_FIXTURE", "synthetic-live-model-guard-secret")
    root = tmp_path / "aitest" / "validation" / "saved-guard"
    project_id = "synthetic-orders-billing-saved-guard"
    manager = SecretManager(
        (
            EnvironmentSecretProvider(
                {("model", "deepseek-validation"): "AITEST_LIVE_GUARD_FIXTURE"}
            ),
        )
    )
    core = assemble_workspace_core(
        root,
        instance_id="live-guard-producer",
        secret_manager=manager,
        model_secret_reference=("model", "deepseek-validation"),
    )
    try:
        project = create_project(
            project_id=project_id,
            workspace_id=core.workspace.workspace_id,
            name="synthetic validation",
            goal="draft only",
            created_at_commit="0",
        )
        saved = core.api.dispatch(
            Command(
                request_id="live-guard-context",
                intent_id="live-guard-context-intent",
                action="save_context",
                project_id=project_id,
                expected_revision=0,
                parameters={"project": project_to_payload(project)},
            ),
            HUMAN,
        )
        assert saved.error is None
        policy = replace(_policy(), project_id=project_id)
        if url is not None:
            policy = replace(policy, endpoint=replace(policy.endpoint, address=url.rstrip("/")))
        command = Command(
            request_id="live-guard-policy",
            intent_id="live-guard-policy-intent",
            action="save_model_outbound_policy",
            project_id=project_id,
            expected_revision=0,
            parameters={
                "project_revision": 1,
                "policy": TypeAdapter(ModelOutboundPolicy).dump_python(policy, mode="json"),
            },
        )
        saved = controlled_policy_confirm(core, command, session=HUMAN)
        assert saved.error is None
    finally:
        core.lifetime_lock.release()

    bodies = []

    def post(self, url, *, headers, body, timeout_seconds):
        bodies.append(json.loads(body))
        return HttpResponse(
            200,
            json.dumps(
                {"id": "live-guard-response", "choices": [{"message": {"content": "合成草稿"}}]}
            ).encode(),
        )

    if url is None:
        monkeypatch.setattr("scripts.validate_deepseek.UrllibTransport.post", post)
    first = run("AITEST_LIVE_GUARD_FIXTURE", root)
    assert first["status"] == "passed", first
    assert len(first["calls_this_invocation"]) == 1
    if url is not None:
        assert len(actual_calls) == 1
        assert actual_calls[0].startswith(b"POST /chat/completions ")
        bodies.append(json.loads(actual_calls[0].partition(b"\r\n\r\n")[2]))
    assert bodies[0]["model"] == policy.endpoint.model_id
    second = run("AITEST_LIVE_GUARD_FIXTURE", root)
    assert second["status"] == "passed" and second["calls_this_invocation"] == []
    assert first["draft"] == second["draft"] and len(bodies) == 1
    if url is not None:
        assert len(actual_calls) == 1
        assert "synthetic-live-model-guard-secret" not in json.dumps(first)
        assert all(
            b"synthetic-live-model-guard-secret" not in path.read_bytes()
            for path in root.rglob("*") if path.is_file()
        )
