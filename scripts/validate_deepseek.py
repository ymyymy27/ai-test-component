"""Live model component check; does not certify Trae or business acceptance.

Only synthetic selected material is sent. Credentials are resolved from an
explicit environment reference and never written into arguments or evidence.
Workspace records remain in the OS user-data directory for later verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from aitest.application.project.context import create_project
from aitest.application.project.serialization import project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.planning.model_outbound import (
    MaterialKind,
    ModelEndpoint,
    ModelOutboundPolicy,
    OutboundConfirmation,
    endpoint_digest,
    material_kinds_digest,
)
from aitest.infrastructure.adapters.model import HttpResponse, UrllibTransport
from aitest.infrastructure.credentials import EnvironmentSecretProvider, SecretManager
from aitest.interfaces.local.api import EntryKind, Session


class CountedTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.transport = UrllibTransport()

    def post(
        self, url: str, *, headers: dict[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse:
        entry: dict[str, object] = {
            "url": url,
            "request_sha256": hashlib.sha256(body).hexdigest(),
        }
        self.calls.append(entry)
        response = self.transport.post(
            url, headers=headers, body=body, timeout_seconds=timeout_seconds
        )
        entry["http_status"] = response.status
        entry["response_bytes"] = len(response.body)
        return response


def run(environment_name: str, existing_workspace: Path | None = None) -> dict[str, object]:
    reference = "deepseek-validation"
    manager = SecretManager(
        (EnvironmentSecretProvider({("model", reference): environment_name}),)
    )
    if not manager.has_secret(reference, purpose="model"):
        raise RuntimeError("Configured environment credential reference is unavailable")
    validation_root = Path(os.environ["LOCALAPPDATA"]) / "aitest" / "validation"
    token = uuid4().hex if existing_workspace is None else existing_workspace.name
    root = validation_root / token
    if existing_workspace is not None and (
        existing_workspace.resolve() != root.resolve() or not root.is_dir()
    ):
        raise ValueError("Resume only accepts an existing validation workspace")
    endpoint = ModelEndpoint("deepseek", "https://api.deepseek.com", "deepseek-flash")
    project_id = "synthetic-orders-billing-" + token
    policy = ModelOutboundPolicy(
        project_id, 1, endpoint, frozenset({MaterialKind.PROJECT_CONTEXT})
    )
    policy = replace(
        policy,
        confirmation=OutboundConfirmation(
            "authorized-deepseek-" + token,
            endpoint_digest(policy.endpoint),
            material_kinds_digest(policy),
            False,
            "pending-core-commit",
        ),
    )
    transport = CountedTransport()
    # The human selected DeepSeek and delegated the synthetic test project.
    # This calls LocalAPI in-process; it is not a real editor session claim.
    human = Session(session_id="authorized-model-validation", entry_kind=EntryKind.HUMAN_UI)
    relay = Session(session_id="validation-relay", entry_kind=EntryKind.AGENT_RELAY)

    def assemble(instance_id: str):
        core = assemble_workspace_core(
            root,
            instance_id=instance_id,
            model_endpoint=endpoint.address,
            model_secret_reference=("model", reference),
            secret_manager=manager,
        )
        assert core.model_provider is not None
        core.model_provider._transport = transport
        return core

    def dispatch(core, command, session):
        response = core.api.dispatch(command, session)
        if response.error is not None:
            raise RuntimeError(f"Component command rejected: {response.error.code}")
        return response.result

    core = assemble("live-model-" + token)
    try:
        project = create_project(
            project_id=project_id,
            workspace_id=core.workspace.workspace_id,
            name="Synthetic orders and billing acceptance project",
            goal="Verify two independent backends using synthetic data",
            created_at_commit="0",
        )
        if core.unit_of_work.repo.current_revision("project", project_id) == 0:
            dispatch(core, Command(
            request_id="context-request-" + token, intent_id="context-intent-" + token,
            action="save_context", project_id=project_id, expected_revision=0,
            parameters={"project": project_to_payload(project)},
            ), human)
        policy_id = "model-policy:" + project_id
        if core.unit_of_work.repo.current_revision("model_outbound_policy", policy_id) == 0:
            dispatch(core, Command(
            request_id="policy-request-" + token, intent_id="policy-intent-" + token,
            action="save_model_outbound_policy", project_id=project_id, expected_revision=0,
            parameters={
                "project_revision": 1,
                "policy": TypeAdapter(ModelOutboundPolicy).dump_python(policy, mode="json"),
            },
            ), human)
        command = Command(
            request_id="draft-request-" + token, intent_id="draft-intent-" + token,
            action="generate_draft", project_id=project_id, expected_revision=0,
            parameters={
                "generation_mode": "model", "project_revision": 1, "policy_revision": 1,
                "source_revision": 1, "base_manual_revision": 0,
                "task_type": "check_content_draft", "draft_kind": "check_content",
                "selected_material": {"project_context": (
                    "Synthetic test project: Orders backend creates orders; Billing backend "
                    "verifies the same order's payment independently. Use only fictitious "
                    "data. Write exactly three short acceptance checks in Chinese, under "
                    "120 characters in total. Output draft suggestions only."
                )},
            },
        )
        first = dispatch(core, command, relay)
        second = dispatch(core, command.model_copy(update={"request_id": "repeat-" + token}), relay)
        if first.get("status") != "draft_ready":
            return {
                "scope": "live_model_component_only", "status": "blocked",
                "workspace": str(root), "calls": transport.calls,
                "blocked_by": first.get("blocked_by"),
                "endpoint": endpoint.address, "model": endpoint.model_id,
            }
        assert first["content"] == second["content"]
        assert len(transport.calls) == (1 if existing_workspace is None else 0)
    finally:
        core.lifetime_lock.release()
    restarted = assemble("live-model-restarted-" + token)
    try:
        replay = dispatch(
            restarted, command.model_copy(update={"request_id": "restart-" + token}), relay
        )
        assert replay["content"] == first["content"]
        assert len(transport.calls) == (1 if existing_workspace is None else 0)
        outbound_id = f"outbound:{project_id}:draft-intent-{token}"
        outcome_revision = restarted.unit_of_work.repo.current_revision(
            "model_outbound_request", outbound_id
        )
        outcome = restarted.unit_of_work.repo.read(
            aggregate_kind="model_outbound_request", record_id=outbound_id,
            revision=outcome_revision,
        ).payload
        assert outcome["state"] == "outcome" and outcome["call_status"] == "ok"
        saved = restarted.unit_of_work.repo.read(
            aggregate_kind="generated_content",
            record_id=first["content"]["generated_content_id"],
            revision=first["content"]["revision"],
        ).payload
        draft_text = saved["draft_text"]
        assert isinstance(draft_text, str)
        saved_digest = "sha256:" + hashlib.sha256(draft_text.encode()).hexdigest()
        assert saved_digest == saved["content_digest"]
        # Lifetime writer locks intentionally deny other file readers on Windows.
        # Release after replay before inspecting permanent materials.
        restarted.lifetime_lock.release()
        secret = manager.resolve(reference, purpose="model")
        try:
            needle = secret.reveal().encode()
            files = [path for path in root.rglob("*") if path.is_file()]
            assert not any(needle in path.read_bytes() for path in files)
        finally:
            secret.clear()
        return {
            "scope": "live_model_component_only", "status": "passed",
            "captured_at": datetime.now().astimezone().isoformat(),
            "python": platform.python_version(), "platform": platform.platform(),
            "workspace": str(root), "project_id": project_id,
            "endpoint": endpoint.address, "model": endpoint.model_id,
            "credential_reference": reference, "credential_environment": environment_name,
            "calls_this_invocation": transport.calls,
            "original_result": "committed_successful_draft",
            "resumed_existing_workspace": existing_workspace is not None,
            "same_process_replay_equal": True,
            "new_core_replay_equal": True, "workspace_secret_scan": "passed",
            "files_scanned": len(files), "draft": first["content"],
            "saved_draft_text": draft_text, "saved_draft_digest_verified": True,
            "outbound_fact": {
                "record_id": outbound_id, "revision": outcome_revision,
                "provider_request_id": outcome["provider_request_id"],
                "call_status": outcome["call_status"],
                "projection_digest": outcome["projection_digest"],
            },
            "not_verified": [
                "real Trae entry", "business backend execution", "physical power loss",
            ],
        }
    finally:
        restarted.lifetime_lock.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-environment", default="DEEPSEEK_API_KEY")
    parser.add_argument("--resume-workspace", type=Path)
    args = parser.parse_args()
    result = run(args.credential_environment, args.resume_workspace)
    # ASCII JSON survives native Windows stdout redirection without losing text.
    print(json.dumps(result, ensure_ascii=True, indent=2))
