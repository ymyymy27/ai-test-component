"""Saved response reconciliation through the actual API and permanent backup."""

import copy
from dataclasses import replace

import pytest
from pydantic import TypeAdapter

from aitest.application.planning.draft import text_digest
from aitest.application.planning.model_orchestration import outbound_request_id
from aitest.application.planning.model_ports import ModelCallResult, ModelCallStatus
from aitest.bootstrap import assemble_workspace_core
from aitest.domain.planning.model_outbound import ModelOutboundPolicy, ModelTaskType
from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.model_responses import FileModelResponseStore
from tests.support.controlled_model_policy import controlled_policy_save
from tests.unit.test_default_source_analysis import dispatch
from tests.unit.test_model_basis_authority import (
    generate,
    save_manual,
    saved_outcome,
    source_basis,
    source_blob,
)
from tests.unit.test_model_basis_authority import model_stack as model_stack
from tests.unit.test_model_orchestration import PROJECT_ID, _policy


def pending_response(core, monkeypatch, **kwargs):
    original = core.unit_of_work.stage_record

    def fail_outcome(**staged):
        if (
            staged["aggregate_kind"] == "model_outbound_request"
            and staged["payload"].get("state") == "outcome"
        ):
            raise OSError("controlled outcome publication failure")
        return original(**staged)

    with monkeypatch.context() as patch:
        patch.setattr(core.unit_of_work, "stage_record", fail_outcome)
        response = generate(core, **kwargs)
    assert response.error is None, response.error
    assert response.result["status"] == "unresolved"
    assert response.result["saved_response_ref"] is not None
    return response.result["saved_response_ref"]


def resolve(core, ref, *, expected=1, request="resolve-response", **kwargs):
    return dispatch(
        core,
        "resolve_model_response",
        project=PROJECT_ID,
        request=request,
        intent="resolve-response-intent",
        expected=expected,
        parameters={
            "outbound_request_id": outbound_request_id(
                PROJECT_ID, 1, ModelTaskType.CHECK_CONTENT_DRAFT, "model-basis-intent"
            ),
            "saved_response_ref": ref,
            **kwargs,
        },
    )


def test_pending_response_can_commit_without_provider_reissue(model_stack, monkeypatch):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_ref=source_basis(core))
    response = resolve(core, ref)
    assert response.error is None, response.error
    assert response.result["status"] == "draft_ready"
    assert len(calls) == 1
    assert saved_outcome(core)["saved_response_ref"] == ref
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    repeated = resolve(core, ref, request="repeat-resolve")
    assert repeated.error is None, repeated.error
    assert repeated.result["content"] == response.result["content"]
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert len(calls) == 1


def test_pending_response_backup_preserves_pointer_and_resolves_without_model_configuration(
    model_stack, monkeypatch, tmp_path
):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_revision=0)
    store = FileBackupStore(core.workspace.root)
    backup = store.create(tmp_path / "backup")
    restored = tmp_path / "restored"
    assert store.restore(backup=backup, target=restored).verified
    pointers = list((restored / "model-responses").glob("*/receipt.json"))
    assert len(pointers) == 1, "backup omitted permanent response pointer"
    restarted = assemble_workspace_core(restored, instance_id="restored-model-response")
    try:
        assert restarted.model_provider is None
        response = resolve(restarted, ref)
        assert response.error is None, response.error
        assert response.result["status"] == "draft_ready"
        assert len(calls) == 1
    finally:
        restarted.lifetime_lock.release()


@pytest.mark.parametrize("damage", ["digest", "size_bool", "owner", "path", "extra"])
def test_forged_response_reference_cannot_publish(model_stack, monkeypatch, damage):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_revision=0)
    if damage == "digest":
        ref["digest"] = "sha256:" + "0" * 64
    elif damage == "size_bool":
        ref["size"] = True
    elif damage == "owner":
        ref["project_id"] = "another-project"
    elif damage == "path":
        ref["relative_path"] = "../outside"
    else:
        ref["invented"] = "unknown"
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    response = resolve(core, ref)
    assert response.error is None and response.result["status"] == "unresolved"
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert len(calls) == 1


@pytest.mark.parametrize("expected", [0, 2])
def test_reconciliation_requires_original_revision(model_stack, monkeypatch, expected):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_revision=0)
    response = resolve(core, ref, expected=expected)
    assert response.error is not None and response.error.code == "B_INVALID_PARAMETER"
    assert len(calls) == 1


def test_current_source_change_keeps_only_original_history(model_stack, monkeypatch):
    core, source, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_ref=source_basis(core))
    (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    response = resolve(core, ref)
    assert response.error is None, response.error
    assert response.result["response_currency"] == "source_changed"
    assert response.result["content"] is None
    assert saved_outcome(core)["historical_draft_text"] == "safe model draft"
    assert saved_outcome(core)["generated_content_id"] is None
    assert len(calls) == 1


def test_ai_disabled_after_response_allows_local_historical_reconciliation(
    model_stack, monkeypatch
):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_revision=0)
    policy = replace(_policy(), revision=2, ai_enabled=False)
    saved = controlled_policy_save(
        core,
        project=PROJECT_ID,
        intent="disable-policy",
        request="disable-policy-request",
        expected=1,
        parameters={
            "project_revision": 1,
            "policy": TypeAdapter(ModelOutboundPolicy).dump_python(policy, mode="json"),
        },
    )
    assert saved.error is None, saved.error
    response = resolve(core, ref)
    assert response.error is None, response.error
    assert response.result["response_currency"] == "source_changed"
    assert saved_outcome(core)["historical_draft_text"] == "safe model draft"
    assert len(calls) == 1


def test_failed_response_keeps_exact_safe_error_digest_without_reissue(model_stack, monkeypatch):
    core, _, calls, _ = model_stack

    def fail(call):
        calls.append("controlled-provider-call")
        return ModelCallResult(
            status=ModelCallStatus.FAILED, error_kind="timeout", error_detail="safe fixture detail"
        )

    monkeypatch.setattr(core.model_provider, "call", fail)
    ref = pending_response(core, monkeypatch, source_revision=0)
    response = resolve(core, ref)
    assert response.error is None, response.error
    assert response.result["status"] == "blocked"
    outcome = saved_outcome(core)
    assert outcome["call_status"] == "failed" and outcome["error_kind"] == "timeout"
    assert outcome["error_detail_digest"] == text_digest("safe fixture detail")
    assert outcome["error_detail_chars"] == len("safe fixture detail")
    assert outcome["generated_content_id"] is None
    assert len(calls) == 1


def test_outcome_committed_but_resolution_reply_lost_reads_original_once(model_stack, monkeypatch):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_revision=0)
    original = core.unit_of_work.commit

    def lost_reply(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("controlled lost commit acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(core.unit_of_work, "commit", lost_reply)
        response = resolve(core, ref)
    assert response.error is None and response.result["status"] == "unresolved"
    assert saved_outcome(core)["state"] == "outcome"
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    repeated = resolve(core, ref, request="after-lost-reply")
    assert repeated.error is None, repeated.error
    assert repeated.result["status"] == "draft_ready"
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert len(calls) == 1


def test_observed_expiry_cannot_be_promoted_when_source_reverts(model_stack, monkeypatch):
    core, source, calls, on_call = model_stack
    basis = source_basis(core)
    on_call[0] = lambda: (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    ref = pending_response(core, monkeypatch, source_ref=basis)
    (source / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    response = resolve(core, ref)
    assert response.error is None, response.error
    assert response.result["response_currency"] == "source_changed"
    assert saved_outcome(core)["generated_content_id"] is None
    assert len(calls) == 1


def test_manual_advance_wins_over_source_change(model_stack, monkeypatch):
    core, source, calls, _ = model_stack
    manual = save_manual(core, 1)
    ref = pending_response(
        core, monkeypatch, source_ref=source_basis(core), base_manual_revision=1, manual_ref=manual
    )
    save_manual(core, 2)
    (source / "main.py").write_text("VALUE = 3\n", encoding="utf-8")
    response = resolve(core, ref)
    assert response.error is None, response.error
    assert response.result["response_currency"] == "superseded_by_manual"
    assert saved_outcome(core)["generated_content_id"] is None
    assert len(calls) == 1


def test_occupied_target_keeps_human_content(model_stack, monkeypatch):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_revision=0)
    request_id = outbound_request_id(
        PROJECT_ID, 1, ModelTaskType.CHECK_CONTENT_DRAFT, "model-basis-intent"
    )
    target = f"draft:{PROJECT_ID}:check_content:{request_id}:1"
    save_manual(core, 1, record_id=target)
    response = resolve(core, ref)
    assert response.error is None, response.error
    assert response.result["response_currency"] == "superseded_by_manual"
    assert saved_outcome(core)["generated_content_id"] is None
    assert (
        core.unit_of_work.repo.read(
            aggregate_kind="generated_content", record_id=target, revision=1
        ).payload["draft_text"]
        == "human fixture revision 1"
    )
    assert len(calls) == 1


def test_lost_source_blob_does_not_repin_or_weaken_current_guard(model_stack, monkeypatch):
    core, _, calls, _ = model_stack
    basis = source_basis(core)
    ref = pending_response(core, monkeypatch, source_ref=basis)
    blob = source_blob(core, basis)
    blob.unlink()
    response = resolve(core, ref)
    assert response.error is None, response.error
    assert response.result["status"] == "unresolved"
    assert response.result["saved_response_ref"] == ref and not blob.exists()
    with pytest.raises(ValueError):
        FileCommitStore(core.workspace.root).read_current(verify_material=True)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_call_started", "unknown"),
        ("observed_currency", "unknown"),
        ("call_status", "passed"),
        ("draft_text", 7),
        ("error_detail_chars", True),
        ("error_detail_digest", "not-a-digest"),
        ("extra", "invented"),
        (
            "credential_filter",
            {"policy": "known-values@1", "filtered": False, "replacements": True},
        ),
    ],
)
def test_malformed_saved_response_cannot_publish(model_stack, monkeypatch, field, value):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_revision=0)
    original = FileModelResponseStore.find

    def malformed(store, **kwargs):
        found = original(store, **kwargs)
        receipt = copy.deepcopy(found[1])
        receipt["response"][field] = value
        return found[0], receipt

    monkeypatch.setattr(FileModelResponseStore, "find", malformed)
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]
    response = resolve(core, ref)
    assert response.error is None and response.result["status"] == "unresolved"
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    assert len(calls) == 1


def test_legacy_receipt_missing_error_length_preserves_unknown(model_stack, monkeypatch):
    core, _, calls, _ = model_stack
    original = FileModelResponseStore.save

    def legacy(store, **kwargs):
        kwargs["response"] = dict(kwargs["response"])
        kwargs["response"].pop("error_detail_chars")
        return original(store, **kwargs)

    monkeypatch.setattr(FileModelResponseStore, "save", legacy)
    ref = pending_response(core, monkeypatch, source_revision=0)
    response = resolve(core, ref)
    assert response.error is None and response.result["status"] == "draft_ready"
    assert saved_outcome(core)["error_detail_chars"] is None
    assert len(calls) == 1


def test_recovery_publication_failure_preserves_receipt_for_next_resolution(
    model_stack, monkeypatch
):
    core, _, calls, _ = model_stack
    ref = pending_response(core, monkeypatch, source_revision=0)
    before = FileCommitStore(core.workspace.root).read_current()["pointer"]

    def fail_commit(*args, **kwargs):
        raise OSError("controlled publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(core.unit_of_work, "commit", fail_commit)
        response = resolve(core, ref)
    assert response.error is None and response.result["status"] == "unresolved"
    assert response.result["saved_response_ref"] == ref
    assert FileCommitStore(core.workspace.root).read_current()["pointer"] == before
    recovered = resolve(core, ref, request="resolution-after-failure")
    assert recovered.error is None and recovered.result["status"] == "draft_ready"
    assert len(calls) == 1
