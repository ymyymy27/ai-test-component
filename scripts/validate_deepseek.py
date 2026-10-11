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
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter

from aitest.bootstrap import CoreAssembly, assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.planning.model_outbound import ModelOutboundPolicy
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


class ValidationBlocked(RuntimeError):
    pass


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationBlocked("saved_identity_unverified")
    return value


def _revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValidationBlocked("saved_record_revision_unverified")
    return value


def _content(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise ValidationBlocked("saved_draft_reference_unverified")
    return cast(dict[str, JsonValue], dict(value))


def dispatch(core: CoreAssembly, command: Command, session: Session) -> dict[str, JsonValue]:
    response = core.api.dispatch(command, session)
    if response.error is not None:
        raise ValidationBlocked(response.error.code)
    if response.result is None:
        raise ValidationBlocked("component_result_missing")
    return dict(response.result)


def _saved_policy(core: CoreAssembly, project_id: str, revision: int) -> ModelOutboundPolicy:
    saved = core.unit_of_work.repo.read(
        aggregate_kind="model_outbound_policy",
        record_id="model-policy:" + project_id,
        revision=_revision(revision),
    )
    if core.model_policy_proof is None:
        raise ValidationBlocked("controlled_policy_source_unavailable")
    core.model_policy_proof.validate_model_policy(
        project_id=project_id,
        record_revision=saved.revision,
        payload=saved.payload,
    )
    policy = TypeAdapter(ModelOutboundPolicy).validate_python(saved.payload)
    if policy.endpoint.provider.lower() != "deepseek" or policy.endpoint.purpose != "model":
        raise ValidationBlocked("configured_policy_is_not_deepseek_model")
    return policy


def run(environment_name: str, existing_workspace: Path | None = None) -> dict[str, object]:
    reference = "deepseek-validation"
    manager = SecretManager((EnvironmentSecretProvider({("model", reference): environment_name}),))
    if existing_workspace is None:
        return {
            "scope": "live_model_component_only",
            "status": "blocked",
            "calls": [],
            "blocked_by": ["existing_controlled_validation_workspace_required"],
        }
    validation_root = Path(os.environ["LOCALAPPDATA"]) / "aitest" / "validation"
    token = existing_workspace.name
    root = validation_root / token
    if existing_workspace.resolve() != root.resolve() or not root.is_dir():
        raise ValueError("Resume only accepts an existing validation workspace")
    project_id = "synthetic-orders-billing-" + token
    # Read controlled permission before resolving credentials or creating transport.
    offline = assemble_workspace_core(
        root,
        instance_id="model-validation-preflight-" + token,
        model_secret_reference=("model", reference),
        secret_manager=manager,
    )
    try:
        project_revision = _revision(
            offline.unit_of_work.repo.current_revision("project", project_id)
        )
        policy_revision = _revision(
            offline.unit_of_work.repo.current_revision(
                "model_outbound_policy", "model-policy:" + project_id
            )
        )
        policy = _saved_policy(offline, project_id, policy_revision)
        original_claim = offline.unit_of_work.repo.current_revision(
            "model_outbound_request", f"outbound:{project_id}:draft-intent-{token}"
        )
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        return {
            "scope": "live_model_component_only",
            "status": "blocked",
            "calls": [],
            "workspace": str(root),
            "blocked_by": [getattr(error, "code", "controlled_model_policy_unverified")],
        }
    finally:
        offline.lifetime_lock.release()
    if not manager.has_secret(reference, purpose="model"):
        return {
            "scope": "live_model_component_only",
            "status": "blocked",
            "calls": [],
            "blocked_by": ["model_credential_reference_unavailable"],
            "workspace": str(root),
        }
    endpoint = policy.endpoint
    transport = CountedTransport()
    relay = Session(session_id="validation-relay", entry_kind=EntryKind.AGENT_RELAY)
    expected_calls = 0 if original_claim else 1

    def assemble(instance_id: str) -> CoreAssembly:
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

    core = assemble("live-model-" + token)
    try:
        command = Command(
            request_id="draft-request-" + token,
            intent_id="draft-intent-" + token,
            action="generate_draft",
            project_id=project_id,
            expected_revision=0,
            parameters={
                "generation_mode": "model",
                "project_revision": project_revision,
                "policy_revision": policy_revision,
                "source_revision": 0,
                "base_manual_revision": 0,
                "task_type": "check_content_draft",
                "draft_kind": "check_content",
                "selected_material": {
                    "project_context": (
                        "Synthetic test project: Orders backend creates orders; Billing backend "
                        "verifies the same order's payment independently. Use only fictitious "
                        "data. Write exactly three short acceptance checks in Chinese, under "
                        "120 characters in total. Output draft suggestions only."
                    )
                },
            },
        )
        first = dispatch(core, command, relay)
        second = dispatch(core, command.model_copy(update={"request_id": "repeat-" + token}), relay)
        if first.get("status") != "draft_ready":
            return {
                "scope": "live_model_component_only",
                "status": "blocked",
                "workspace": str(root),
                "calls": transport.calls,
                "blocked_by": first.get("blocked_by"),
                "endpoint": endpoint.address,
                "model": endpoint.model_id,
            }
        first_content = _content(first["content"])
        assert first_content == _content(second["content"])
        assert len(transport.calls) == expected_calls
    finally:
        core.lifetime_lock.release()
    restarted = assemble("live-model-restarted-" + token)
    try:
        replay = dispatch(
            restarted, command.model_copy(update={"request_id": "restart-" + token}), relay
        )
        assert _content(replay["content"]) == first_content
        assert len(transport.calls) == expected_calls
        outbound_id = f"outbound:{project_id}:draft-intent-{token}"
        outcome_revision = restarted.unit_of_work.repo.current_revision(
            "model_outbound_request", outbound_id
        )
        outcome = restarted.unit_of_work.repo.read(
            aggregate_kind="model_outbound_request",
            record_id=outbound_id,
            revision=_revision(outcome_revision),
        ).payload
        assert outcome["state"] == "outcome" and outcome["call_status"] == "ok"
        saved = restarted.unit_of_work.repo.read(
            aggregate_kind="generated_content",
            record_id=_text(first_content["generated_content_id"]),
            revision=_revision(first_content["revision"]),
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
            "scope": "live_model_component_only",
            "status": "passed",
            "captured_at": datetime.now().astimezone().isoformat(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "workspace": str(root),
            "project_id": project_id,
            "endpoint": endpoint.address,
            "model": endpoint.model_id,
            "credential_reference": reference,
            "credential_environment": environment_name,
            "calls_this_invocation": transport.calls,
            "original_result": "committed_successful_draft",
            "resumed_existing_workspace": existing_workspace is not None,
            "same_process_replay_equal": True,
            "new_core_replay_equal": True,
            "workspace_secret_scan": "passed",
            "files_scanned": len(files),
            "draft": first_content,
            "saved_draft_text": draft_text,
            "saved_draft_digest_verified": True,
            "outbound_fact": {
                "record_id": outbound_id,
                "revision": outcome_revision,
                "provider_request_id": outcome["provider_request_id"],
                "call_status": outcome["call_status"],
                "projection_digest": outcome["projection_digest"],
            },
            "not_verified": [
                "real Trae entry",
                "business backend execution",
                "physical power loss",
            ],
        }
    finally:
        restarted.lifetime_lock.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-environment", default="DEEPSEEK_API_KEY")
    parser.add_argument("--resume-workspace", type=Path)
    args = parser.parse_args()
    try:
        result = run(args.credential_environment, args.resume_workspace)
    except ValidationBlocked as error:
        result = {
            "scope": "live_model_component_only",
            "status": "blocked",
            "blocked_by": [str(error)],
        }
    # ASCII JSON survives native Windows stdout redirection without losing text.
    print(json.dumps(result, ensure_ascii=True, indent=2))
