"""Controlled model response races; actual default core, no real provider acceptance."""

import json

import pytest
from pydantic import TypeAdapter

from aitest.application.planning.draft import text_digest
from aitest.application.planning.model_orchestration import outbound_request_id
from aitest.bootstrap import assemble_workspace_core
from aitest.domain.planning.model_outbound import ModelOutboundPolicy, ModelTaskType
from aitest.infrastructure.adapters.model import HttpResponse
from aitest.infrastructure.credentials import EnvironmentSecretProvider, SecretManager
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from tests.support.controlled_model_policy import controlled_policy_save
from tests.unit.test_default_source_analysis import analyze, dispatch, seed
from tests.unit.test_model_orchestration import PROJECT_ID, _policy


@pytest.fixture
def model_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("AITEST_BASIS_FIXTURE_SECRET", "synthetic-basis-credential")
    policy = _policy()
    core = assemble_workspace_core(
        tmp_path / "workspace",
        instance_id="model-basis-fixture",
        model_endpoint=policy.endpoint.address,
        model_secret_reference=("model", "basis-fixture"),
        secret_manager=SecretManager(
            (
                EnvironmentSecretProvider(
                    {
                        ("model", "basis-fixture"): "AITEST_BASIS_FIXTURE_SECRET",
                    }
                ),
            )
        ),
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    seed(core, source, project=PROJECT_ID)
    response = controlled_policy_save(
        core,
        project=PROJECT_ID,
        parameters={
            "project_revision": 1,
            "policy": TypeAdapter(ModelOutboundPolicy).dump_python(policy, mode="json"),
        },
    )
    assert response.error is None, response.error
    calls = []
    on_call = [lambda: None]

    class Transport:
        def post(self, url, *, headers, body, timeout_seconds):
            calls.append(url)
            on_call[0]()
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "id": "controlled-response",
                        "choices": [{"message": {"content": "safe model draft"}}],
                    }
                ).encode(),
            )

    core.model_provider._transport = Transport()
    yield core, source, calls, on_call
    core.lifetime_lock.release()


def generate(core, *, request=None, intent="model-basis-intent", **extra):
    return dispatch(
        core,
        "generate_draft",
        project=PROJECT_ID,
        intent=intent,
        request=request,
        parameters={
            "generation_mode": "model",
            "project_revision": 1,
            "policy_revision": 1,
            "source_revision": 1,
            "base_manual_revision": 0,
            "task_type": "check_content_draft",
            "draft_kind": "check_content",
            "selected_material": {"project_context": "safe selected context"},
            **extra,
        },
    )


def saved_outcome(core):
    record_id = outbound_request_id(
        PROJECT_ID, 1, ModelTaskType.CHECK_CONTENT_DRAFT, "model-basis-intent"
    )
    return core.unit_of_work.repo.read(
        aggregate_kind="model_outbound_request", record_id=record_id, revision=2
    ).payload


def save_manual(core, revision, record_id="manual-draft"):
    text = f"human fixture revision {revision}"
    payload = {
        "project_id": PROJECT_ID,
        "generated_content_id": record_id,
        "revision": revision,
        "draft_kind": "check_content",
        "status": "draft",
        "draft_text": text,
        "content_digest": text_digest(text),
        "template_id": "ticket-workflow",
        "template_version": "1.0.0",
        "revision_context": {
            "project_revision": 1,
            "binding_revision": 1,
            "template_revision": "1.0.0",
        },
    }
    unit = core.unit_of_work
    unit.begin(f"manual-{revision}", PROJECT_ID, intent_id=f"manual-{revision}")
    unit.stage_record(
        aggregate_kind="generated_content",
        record_id=record_id,
        expected_revision=revision - 1,
        payload=payload,
    )
    unit.commit(f"manual-{revision}")
    return {
        "record_id": record_id,
        "record_revision": revision,
        "content_digest": payload["content_digest"],
    }


def test_unknown_anonymous_source_revision_does_not_authorize_model_call(model_stack):
    core, _, calls, _ = model_stack
    result = generate(core)
    assert result.error is None, result.error
    assert result.result["status"] == "blocked"
    assert calls == []


def test_actual_source_change_during_model_call_saves_only_original_history(model_stack):
    core, source, calls, on_call = model_stack
    pinned = analyze(core, project=PROJECT_ID, purpose="analysis")
    assert pinned.error is None, pinned.error
    source_ref = {
        "source_snapshot_id": pinned.result["snapshot_id"],
        "record_revision": 1,
        "content_identity": pinned.result["content_identity"],
        "purpose": "analysis",
    }
    on_call[0] = lambda: (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = generate(core, source_ref=source_ref)
    assert result.error is None, result.error
    assert result.result["status"] == "blocked"
    assert result.result["response_currency"] == "source_changed"
    assert len(calls) == 1
    outcome = saved_outcome(core)
    assert outcome["historical_draft_text"] == "safe model draft"
    assert outcome["generated_content_id"] is None


def test_actual_manual_revision_during_model_call_wins_and_response_is_history(model_stack):
    core, _, calls, on_call = model_stack
    pinned = analyze(core, project=PROJECT_ID, purpose="analysis")
    assert pinned.error is None, pinned.error
    source_ref = {
        "source_snapshot_id": pinned.result["snapshot_id"],
        "record_revision": 1,
        "content_identity": pinned.result["content_identity"],
        "purpose": "analysis",
    }
    manual_ref = save_manual(core, 1)
    on_call[0] = lambda: save_manual(core, 2)
    result = generate(core, source_ref=source_ref, base_manual_revision=1, manual_ref=manual_ref)
    assert result.error is None, result.error
    assert result.result["status"] == "blocked"
    assert result.result["response_currency"] == "superseded_by_manual"
    assert len(calls) == 1
    outcome = saved_outcome(core)
    assert outcome["historical_draft_text"] == "safe model draft"
    assert outcome["generated_content_id"] is None


def test_generation_without_source_uses_non_applicable_context_and_saves_response(model_stack):
    core, _, calls, _ = model_stack
    result = generate(core, source_revision=0)
    assert result.error is None, result.error
    assert result.result["status"] == "draft_ready"
    assert len(calls) == 1
    stored = core.unit_of_work.repo.read(
        aggregate_kind="generated_content",
        record_id=result.result["content"]["generated_content_id"],
        revision=1,
    ).payload
    assert stored["revision_context"]["source_revision"] is None
    assert stored["revision_context"]["binding_revision"] is None


def source_basis(core):
    pinned = analyze(core, project=PROJECT_ID, purpose="analysis")
    assert pinned.error is None, pinned.error
    return {
        "source_snapshot_id": pinned.result["snapshot_id"],
        "record_revision": 1,
        "content_identity": pinned.result["content_identity"],
        "purpose": "analysis",
    }


def source_blob(core, ref):
    saved = core.unit_of_work.repo.read(
        aggregate_kind="source_snapshot", record_id=ref["source_snapshot_id"], revision=1
    ).payload
    digest = saved["files"][0]["content_digest"].removeprefix("sha256:")
    return core.workspace.root / "snapshots" / "blobs" / digest


@pytest.mark.parametrize("damage", ["revision_bool", "content_identity", "purpose", "missing"])
def test_invalid_source_basis_has_no_outbound_intent_or_provider_call(model_stack, damage):
    core, _, calls, _ = model_stack
    ref = source_basis(core)
    if damage == "revision_bool":
        ref["record_revision"] = True
    elif damage == "content_identity":
        ref["content_identity"] = "sha256:" + "0" * 64
    elif damage == "purpose":
        ref["purpose"] = "prepare"
    else:
        ref["source_snapshot_id"] = "unknown-snapshot"
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    result = generate(core, source_ref=ref)
    assert result.error is None and result.result["status"] == "blocked"
    assert calls == []
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before


def test_model_checks_actual_source_only_outside_short_write_transaction(model_stack, monkeypatch):
    core, _, calls, _ = model_stack
    ref = source_basis(core)
    original = core.snapshot_store.detect_changes
    observations = []

    def observed(snapshot_id):
        assert core.unit_of_work.project is None, "actual filesystem walk held a write transaction"
        observations.append(snapshot_id)
        return original(snapshot_id)

    monkeypatch.setattr(core.snapshot_store, "detect_changes", observed)
    result = generate(core, source_ref=ref)
    assert result.error is None and result.result["status"] == "draft_ready"
    assert len(observations) == 3 and len(calls) == 1
    manifest = FileCommitStore(core.workspace.root).read_current(verify_material=True)["manifest"]
    assert (
        source_blob(core, ref).relative_to(core.workspace.root).as_posix()
        in manifest["source_material_files"]
    )
    repeat = generate(core, source_ref=ref, request="repeat-transport")
    assert repeat.error is None and repeat.result["content"] == result.result["content"]
    assert len(observations) == 3 and len(calls) == 1


@pytest.mark.parametrize("damage", ["missing_ref", "digest", "stale"])
def test_unverified_or_stale_manual_basis_never_sends(model_stack, damage):
    core, _, calls, _ = model_stack
    ref = save_manual(core, 1)
    if damage == "missing_ref":
        ref = None
    elif damage == "digest":
        ref["content_digest"] = "sha256:" + "0" * 64
    else:
        save_manual(core, 2)
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    result = generate(core, source_revision=0, base_manual_revision=1, manual_ref=ref)
    assert result.error is None and result.result["status"] == "blocked"
    assert calls == []
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before


def test_generation_target_occupied_during_provider_call_preserves_manual_and_safe_response(
    model_stack,
):
    core, _, calls, on_call = model_stack
    request_id = outbound_request_id(
        PROJECT_ID, 1, ModelTaskType.CHECK_CONTENT_DRAFT, "model-basis-intent"
    )
    target = f"draft:{PROJECT_ID}:check_content:{request_id}:1"
    on_call[0] = lambda: save_manual(core, 1, record_id=target)
    result = generate(core, source_revision=0)
    assert result.error is None, result.error
    assert result.result["response_currency"] == "superseded_by_manual"
    assert saved_outcome(core)["historical_draft_text"] == "safe model draft"
    assert core.unit_of_work.repo.current_revision("generated_content", target) == 1
    assert (
        core.unit_of_work.repo.read(
            aggregate_kind="generated_content", record_id=target, revision=1
        ).payload["draft_text"]
        == "human fixture revision 1"
    )
    repeat = generate(core, source_revision=0, request="occupied-repeat")
    assert repeat.error is None and repeat.result["response_currency"] == "superseded_by_manual"
    assert len(calls) == 1


def test_blob_lost_in_provider_call_saves_safe_receipt_without_recertifying_source(model_stack):
    core, _, calls, on_call = model_stack
    ref = source_basis(core)
    path = source_blob(core, ref)
    on_call[0] = path.unlink
    result = generate(core, source_ref=ref)
    assert result.error is None, result.error
    assert result.result["status"] == "unresolved"
    assert result.result["content"] is None
    object_ref = result.result["saved_response_ref"]
    import json

    receipt = json.loads((core.workspace.root / object_ref["relative_path"]).read_bytes())
    assert receipt["response"]["draft_text"] == "safe model draft"
    assert receipt["response"]["observed_currency"] == "source_changed"
    assert not path.exists()
    with pytest.raises(ValueError):
        FileCommitStore(core.workspace.root).read_current(verify_material=True)
    repeat = generate(core, source_ref=ref, request="lost-source-repeat")
    assert repeat.error is None and repeat.result["status"] == "unresolved"
    assert repeat.result["saved_response_ref"] == result.result["saved_response_ref"]
    assert len(calls) == 1 and not path.exists()


def test_lost_blob_before_intent_publication_does_not_consume_intent(model_stack, monkeypatch):
    core, _, calls, _ = model_stack
    ref = source_basis(core)
    path = source_blob(core, ref)
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    original = core.unit_of_work.stage_record

    def lose(**kwargs):
        if kwargs["aggregate_kind"] == "model_outbound_request":
            path.unlink()
        return original(**kwargs)

    monkeypatch.setattr(core.unit_of_work, "stage_record", lose)
    result = generate(core, source_ref=ref)
    assert result.error is not None and result.error.code == "COMMIT_MATERIAL_UNVERIFIED"
    assert calls == []
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    request_id = outbound_request_id(
        PROJECT_ID, 1, ModelTaskType.CHECK_CONTENT_DRAFT, "model-basis-intent"
    )
    assert core.unit_of_work.repo.current_revision("model_outbound_request", request_id) == 0


def test_source_revoked_after_intent_but_before_send_has_terminal_unsent_receipt(
    model_stack, monkeypatch
):
    core, source, calls, _ = model_stack
    ref = source_basis(core)
    original = core.unit_of_work.commit

    def revoke(request_id=None):
        is_intent = any(
            kind == "model_outbound_request" and body.get("state") == "intent"
            for kind, _, _, body in core.unit_of_work.pending
        )
        result = original(request_id)
        if is_intent:
            (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
        return result

    monkeypatch.setattr(core.unit_of_work, "commit", revoke)
    result = generate(core, source_ref=ref)
    assert result.error is None, result.error
    assert result.result["status"] == "blocked" and calls == []
    outcome = saved_outcome(core)
    assert outcome["provider_call_started"] is False
    assert outcome["error_kind"] == "basis_revoked_before_send"
    repeat = generate(core, source_ref=ref, request="unsent-repeat")
    assert repeat.error is None and repeat.result["status"] == "blocked" and calls == []


def test_declared_source_revision_without_frozen_reference_cannot_publish_through_raw_uow(
    model_stack,
):
    import copy

    core, _, _, _ = model_stack
    response = generate(core, source_revision=0)
    assert response.error is None and response.result["status"] == "draft_ready"
    body = copy.deepcopy(saved_outcome(core))
    body["source_revision"] = 1
    unit = core.unit_of_work
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    unit.begin("forged-source-request", PROJECT_ID, intent_id="forged-source-intent")
    unit.stage_record(
        aggregate_kind="model_outbound_request",
        record_id="forged-model-source",
        expected_revision=0,
        payload=body,
    )
    with pytest.raises(ValueError, match="source/manual revision"):
        unit.commit("forged-source-request")
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert unit.repo.current_revision("model_outbound_request", "forged-model-source") == 0


def test_non_applicable_binding_does_not_expire_a_source_free_model_draft(model_stack):
    from dataclasses import replace

    from aitest.application.planning.draft import GeneratedContent, RevisionContext, draft_expiry
    from aitest.domain.planning.templates import TemplateRef

    context = RevisionContext(project_revision=1, binding_revision=None, template_revision="1")
    content = GeneratedContent(
        "source-free",
        PROJECT_ID,
        "check_content",
        TemplateRef(template_id="model-only", version="1"),
        1,
        context,
    )
    assert draft_expiry(content, replace(context, binding_revision=2)) == ()


def test_saved_response_survives_failed_outcome_then_core_restart_without_provider_reissue(
    model_stack, monkeypatch
):
    core, _, calls, _ = model_stack
    original = core.unit_of_work.stage_record

    def fail_outcome(**kwargs):
        if (
            kwargs["aggregate_kind"] == "model_outbound_request"
            and kwargs["payload"].get("state") == "outcome"
        ):
            raise ValueError("controlled outcome publication failure")
        return original(**kwargs)

    monkeypatch.setattr(core.unit_of_work, "stage_record", fail_outcome)
    first = generate(core, source_revision=0)
    assert first.error is None and first.result["status"] == "unresolved"
    assert first.result["saved_response_ref"] is not None and len(calls) == 1
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(
        core.workspace.root,
        instance_id="pending-model-restarted",
        model_endpoint=_policy().endpoint.address,
        model_secret_reference=("model", "basis-fixture"),
        secret_manager=core.secret_manager,
    )
    try:

        def no_new_call(*args, **kwargs):
            pytest.fail("saved response must not invoke the provider on retry")

        monkeypatch.setattr(restarted.model_provider, "call", no_new_call)
        repeat = generate(restarted, source_revision=0, request="restart-receipt-read")
        assert repeat.error is None, repeat.error
        assert repeat.result["status"] == "unresolved"
        assert repeat.result["saved_response_ref"] == first.result["saved_response_ref"]
        assert len(calls) == 1
    finally:
        restarted.lifetime_lock.release()
