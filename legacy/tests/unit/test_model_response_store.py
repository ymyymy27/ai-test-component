"""Pending material is safe, bounded and immutable, without claiming business commit."""

import json
from dataclasses import replace

import pytest

from aitest.infrastructure.file_store.model_responses import FileModelResponseStore
from aitest.infrastructure.file_store.workspace import Workspace
from aitest.infrastructure.security import known_secrets


@pytest.fixture
def responses(tmp_path):
    workspace = Workspace(tmp_path)
    lock = workspace.admit_lifetime()
    store = FileModelResponseStore(tmp_path, writer_epoch=workspace.identity["writer_epoch"])
    yield store
    lock.release()


def save(store, **response):
    return store.save(
        project_id="project",
        request_id="model-request",
        identity={"selected_digest": "safe-input-identity"},
        response={"draft_text": "safe response", **response},
    )


def find(store, *, identity=None):
    return store.find(
        project_id="project",
        request_id="model-request",
        identity=identity or {"selected_digest": "safe-input-identity"},
    )


def test_receipt_original_bytes_and_reference_are_immutable_and_identity_bound(responses):
    first = save(responses)
    assert save(responses) == first
    ref, body = find(responses)
    assert ref == first and body["response"]["draft_text"] == "safe response"
    with pytest.raises(ValueError, match="different bytes"):
        save(responses, draft_text="replacement must not overwrite")
    with pytest.raises(ValueError, match="identity/bytes"):
        find(responses, identity={"selected_digest": "different-input"})
    assert find(responses)[0] == first


@pytest.mark.parametrize(
    "damage", ["object_bytes", "object_owner", "size_bool", "duplicate", "oversize"]
)
def test_corrupt_receipt_cannot_authorize_replay_or_masquerade_as_original(responses, damage):
    ref = save(responses)
    pointer = responses._pointer("project", "model-request")
    record = json.loads(pointer.read_bytes())
    if damage == "object_bytes":
        path = responses.root / ref.relative_path
        value = path.read_bytes()
        path.write_bytes(value.replace(b"safe response", b"fake response"))
    elif damage == "object_owner":
        record["object_ref"]["project_id"] = "foreign-project"
        pointer.write_text(json.dumps(record), encoding="utf-8")
    elif damage == "size_bool":
        record["object_ref"]["size"] = True
        pointer.write_text(json.dumps(record), encoding="utf-8")
    elif damage == "duplicate":
        pointer.write_bytes(
            b'{"schema_version":"old","schema_version":"aitest.model-response-pointer/1.0","object_ref":{}}'
        )
    else:
        pointer.write_bytes(b"x" * (16 * 1024 + 1))
    before = pointer.read_bytes()
    with pytest.raises(ValueError):
        find(responses)
    assert pointer.read_bytes() == before


def test_response_safety_rejection_happens_before_any_object_or_pointer_write(responses):
    secret = "synthetic-receipt-credential-value"
    known_secrets().register(secret)
    with pytest.raises(ValueError, match="completely filtered"):
        save(responses, draft_text=secret)
    assert not list(responses.root.glob("objects/**/*"))
    assert not list(responses.root.glob("model-responses/**/*"))


def test_receipt_byte_budget_rejects_before_persisting_any_material(responses, monkeypatch):
    monkeypatch.setattr("aitest.infrastructure.file_store.model_responses.MAX_RECEIPT_BYTES", 512)
    with pytest.raises(ValueError, match="size budget"):
        save(responses, draft_text="x" * 1024)
    assert not list(responses.root.glob("objects/**/*"))
    assert not list(responses.root.glob("model-responses/**/*"))


@pytest.mark.parametrize("namespace", ["../escape", "NUL", "project.", "project "])
def test_unsaveable_project_namespace_is_rejected_before_provider_admission(responses, namespace):
    with pytest.raises(ValueError, match="namespace"):
        responses.validate(project_id=namespace, request_id="request", identity={})


def test_old_core_epoch_cannot_publish_a_pending_response(responses):
    old = FileModelResponseStore(responses.root, writer_epoch=responses.epoch - 1)
    with pytest.raises(ValueError):
        save(old)
    assert find(responses) is None


def test_object_read_is_bounded_by_frozen_size_and_explicit_receipt_budget(responses):
    ref = save(responses)
    with pytest.raises(ValueError, match="read budget"):
        responses.objects.read_bytes(replace(ref, size=1025), max_bytes=1024)
    path = responses.root / ref.relative_path
    path.write_bytes(path.read_bytes() + b"x" * 1024)
    with pytest.raises(ValueError):
        responses.objects.read_bytes(ref, max_bytes=4 * 1024 * 1024)
