"""Actual saved query bytes must not be repaired or attached to another snapshot."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from aitest.application.execution.facts import execution_payload_digest
from tests.unit.test_saved_business_verification import command, service


@pytest.fixture
def original(tmp_path):
    saved, facts, query = service(tmp_path)
    result = saved.apply(command(facts))
    identity = result["verification"]["verification_id"]
    admission = saved.coordinator._read_payload("execution_intent", identity + "-admission")
    receipt = saved.coordinator._read_payload("verification", identity)
    return saved, facts, query, identity, admission, receipt


def substitute(saved, monkeypatch, replacements):
    read = saved.unit.read

    def replacement(**kwargs):
        record = read(**kwargs)
        return SimpleNamespace(
            aggregate_kind=record.aggregate_kind, record_id=record.record_id,
            revision=record.revision,
            payload=replacements.get((record.aggregate_kind, record.record_id), record.payload),
        )

    monkeypatch.setattr(saved.unit, "read", replacement)


@pytest.mark.parametrize("damage", [
    "cursor_bool", "revision_text", "current_number", "required_text", "inner_run",
    "step_run",
])
def test_base_snapshot_rejects_self_consistent_noncanonical_material(
    original, monkeypatch, damage,
):
    saved, facts, _, _, admission, _ = original
    payload = deepcopy(facts.model_dump(mode="json"))
    if damage == "cursor_bool":
        payload["snapshot_cursor"] = True
    elif damage == "revision_text":
        payload["snapshot_revision"] = "1"
    elif damage == "current_number":
        payload["attempts"][0]["is_current"] = 1
    elif damage == "required_text":
        payload["steps"][0]["required_for_case"] = "true"
    elif damage == "inner_run":
        payload["run"]["run_id"] = "foreign-run"
    else:
        payload["steps"][0]["run_id"] = "foreign-run"
    substitute(saved, monkeypatch, {("execution_facts", facts.snapshot_commit_id): payload})
    with pytest.raises(ValueError):
        saved._base({**admission, "snapshot_digest": execution_payload_digest(payload)})


@pytest.mark.parametrize("damage", [
    "own_identity", "revision_text", "current_number", "workspace", "inner_run",
    "step_run", "outcome", "extra_evidence", "extra_verification",
])
def test_attached_snapshot_rejects_substitution_and_business_rewrite(
    original, monkeypatch, damage,
):
    saved, facts, query, identity, _, receipt = original
    payload = deepcopy(saved.coordinator._read_payload(
        "execution_facts", receipt["result_snapshot"],
    ))
    if damage == "own_identity":
        payload["snapshot_commit_id"] = "other-material"
    elif damage == "revision_text":
        payload["snapshot_revision"] = "1"
    elif damage == "current_number":
        payload["attempts"][0]["is_current"] = 1
    elif damage == "workspace":
        payload["run"]["origin_workspace_id"] = "foreign-workspace"
    elif damage == "inner_run":
        payload["run"]["run_id"] = "foreign-run"
    elif damage == "step_run":
        payload["steps"][0]["run_id"] = "foreign-run"
    elif damage == "outcome":
        payload["run"]["result_ref"] = "invented-result"
    elif damage == "extra_evidence":
        payload["evidence_refs"].append({
            **payload["evidence_refs"][-1], "evidence_id": "unrelated-evidence",
        })
    else:
        payload["verifications"].append({
            **payload["verifications"][-1], "verification_id": "unrelated-verification",
        })
    substitute(saved, monkeypatch, {
        ("execution_facts", receipt["result_snapshot"]): payload,
        ("verification", identity): {
            **receipt, "result_snapshot_digest": execution_payload_digest(payload),
        },
    })
    before = saved.unit.commit_seq()
    with pytest.raises(ValueError):
        saved.apply(command(facts, request_id="read-damaged"))
    assert query.read_business_object.call_count == 1
    assert saved.unit.commit_seq() == before


@pytest.mark.parametrize("place", ["receipt", "evidence_ref"])
@pytest.mark.parametrize("field", ["evidence_revision", "object_size"])
def test_query_fact_equality_preserves_json_types(original, monkeypatch, place, field):
    saved, facts, query, identity, _, receipt = original
    if place == "receipt":
        kind, rid, payload = "verification", identity, deepcopy(receipt)
        evidence = payload["evidence"]
    else:
        kind, rid = "evidence_ref", receipt["evidence"]["evidence_id"]
        payload = deepcopy(saved.coordinator._read_payload(kind, rid))
        evidence = payload
    evidence[field] = True if field == "evidence_revision" else float(evidence[field])
    substitute(saved, monkeypatch, {(kind, rid): payload})
    with pytest.raises(ValueError):
        saved.apply(command(facts, request_id="read-wrong-types"))
    assert query.read_business_object.call_count == 1


def test_attached_query_reads_original_exact_snapshot_after_current_advances(original):
    saved, facts, query, _, _, _ = original
    result = saved.apply(command(facts, request_id="initial-recall"))
    saved.unit.begin("later-progress", facts.project_id)
    current = saved.coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
    saved.coordinator._stage_snapshot(current.model_copy(update={"facts_id": "later-facts"}))
    saved.unit.commit()
    before = saved.unit.commit_seq()
    saved.resolver = saved.verifier = None
    assert saved.apply(command(facts, request_id="original-recall")) == result
    assert saved.unit.commit_seq() == before
    assert query.read_business_object.call_count == 1


@pytest.mark.parametrize("kind", ["execution_intent", "verification", "evidence_ref"])
@pytest.mark.parametrize("revision", [True, 1.0, "1", 2])
def test_immutable_material_is_checked_before_reading_any_latest_body(
    original, monkeypatch, kind, revision,
):
    saved, _, _, identity, _, receipt = original
    rid = {
        "execution_intent": identity + "-admission", "verification": identity,
        "evidence_ref": receipt["evidence"]["evidence_id"],
    }[kind]
    monkeypatch.setattr(saved.records, "current_revision", lambda **kwargs: revision)
    reads = []
    monkeypatch.setattr(saved.records, "read", lambda **kwargs: reads.append(kwargs))
    with pytest.raises(ValueError, match="immutable"):
        saved._read(kind, rid)
    assert reads == []


@pytest.mark.parametrize("damage", ["wrong_bytes", "wrong_type"])
def test_object_port_output_must_match_exact_saved_reference(original, monkeypatch, damage):
    saved, facts, query, _, _, receipt = original
    read = saved.objects.read_bytes

    def wrong(ref):
        content = read(ref)
        if ref.digest == receipt["object_ref"]["digest"]:
            return (
                content.replace(b"true", b"false")
                if damage == "wrong_bytes" else bytearray(content)
            )
        return content

    monkeypatch.setattr(saved.objects, "read_bytes", wrong)
    with pytest.raises(ValueError, match="bytes differ"):
        saved.apply(command(facts, request_id="read-wrong-byte-source"))
    assert query.read_business_object.call_count == 1


@pytest.mark.parametrize("field", ["project_id", "digest", "relative_path", "media_type"])
def test_invalid_saved_object_reference_is_rejected_before_port(original, monkeypatch, field):
    saved, facts, query, identity, _, receipt = original
    payload = deepcopy(receipt)
    payload["object_ref"][field] = True
    substitute(saved, monkeypatch, {("verification", identity): payload})
    reads = []
    monkeypatch.setattr(saved.objects, "read_bytes", lambda ref: reads.append(ref))
    with pytest.raises(ValueError, match="reference"):
        saved.apply(command(facts, request_id="wrong-reference-type"))
    assert reads == [] and query.read_business_object.call_count == 1


def test_admission_source_instance_cannot_be_coerced_to_text(original, monkeypatch):
    saved, facts, query, identity, admission, _ = original
    substitute(saved, monkeypatch, {
        ("execution_intent", identity + "-admission"): {**admission, "source_instance_id": 1},
    })
    with pytest.raises(ValueError, match="admission"):
        saved.apply(command(facts, request_id="bad-source-instance"))
    assert query.read_business_object.call_count == 1
