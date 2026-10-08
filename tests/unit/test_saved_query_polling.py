"""Actual saved safe polling material; synthetic source/authorization and clock."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from aitest.application.evidence.saved_verification import SavedBusinessVerification
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.security import KnownSecretRegistry
from tests.unit.test_business_query_polling import Time
from tests.unit.test_saved_business_verification import command, service


def original(tmp_path, values, *, query_ms=0, registry=None, expected=None):
    saved, facts, _ = service(tmp_path, registry=registry, expected=expected)
    saved.resolver.resolve.return_value = replace(
        saved.resolver.resolve.return_value,
        deadline_condition="poll_deadline_ms:50",
        query_interval="poll_interval_ms:10",
    )
    time = Time()
    replies = iter(values)
    calls = []

    def read(**kw):
        assert saved.unit.project is None
        calls.append(kw)
        time.ns += query_ms * 1_000_000
        return next(replies)

    saved.verifier = BusinessVerificationAdapter(
        SimpleNamespace(read_business_object_before=read), clock_ns=time.clock, wait=time.wait
    )
    return saved, facts, calls


@pytest.mark.parametrize(
    "values,query_ms,observation",
    [
        ([None, {}, {"paid": True}], 1, "matched"),
        ([{"paid": False}, {"paid": True}], 0, "mismatched"),
        ([None] * 8, 0, "deadline_reached"),
        ([{"paid": True}], 51, "deadline_reached"),
    ],
)
def test_safe_actual_timeline_is_saved_and_replayed_without_adapter(
    tmp_path, values, query_ms, observation
):
    saved, facts, calls = original(tmp_path, values, query_ms=query_ms)
    result = saved.apply(command(facts))
    assert result["verification"]["observation"] == observation
    ref = saved.records.read(
        aggregate_kind="verification",
        record_id=result["verification"]["verification_id"],
        revision=1,
    ).payload["object_ref"]
    material = json.loads((tmp_path / ref["relative_path"]).read_bytes())
    assert material["schema_version"] == "aitest.business-query-material/1.1"
    assert len(material["polling"]["observations"]) == len(calls)
    assert material["polling"]["observations"][-1]["actual_fields"] == material["actual_fields"]
    before, count = saved.unit.commit_seq(), len(calls)
    reopened = SavedBusinessVerification(
        ExecutionCommitCoordinator(FileUnitOfWork(tmp_path)),
        saved.authorizations,
        FileObjectStore(tmp_path),
        workspace_id=facts.run.origin_workspace_id,
        instance_id="reopened-polling",
        resolver=None,
        verifier=None,
        protector=lambda x: x,
    )
    assert reopened.apply(command(facts, request_id="poll-replay")) == result
    assert reopened.unit.commit_seq() == before and len(calls) == count


def test_all_historical_samples_are_filtered_before_object_and_backup_material(tmp_path):
    registry = KnownSecretRegistry()
    registry.register("polling-private-secret")
    saved, facts, _ = original(
        tmp_path, [{"api_key": "polling-private-secret"}, {"paid": True}], registry=registry
    )
    result = saved.apply(command(facts))
    assert result["verification"]["observation"] == "matched"
    assert result["evidence"]["redaction_state"] == "redacted"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"polling-private-secret" not in path.read_bytes(), path
    assert saved.apply(command(facts, request_id="safe-replay")) == result


def test_filtered_necessary_terminal_field_does_not_turn_into_a_match(tmp_path):
    registry = KnownSecretRegistry()
    registry.register("sensitive-required-value")
    saved, facts, _ = original(
        tmp_path,
        [{"paid": True, "object": "sensitive-required-value"}],
        expected={"paid": True, "object": "public-expected"},
        registry=registry,
    )
    result = saved.apply(command(facts))
    assert result["verification"]["observation"] == "no_result"
    assert result["verification"]["gap_ids"] == ["business_fact_filtered:object"]
    assert saved.apply(command(facts, request_id="safe-filtered-replay")) == result


@pytest.mark.parametrize(
    "damage", ["last_fields", "bool_time", "early_retry", "early_elapsed", "scope", "redaction"]
)
def test_actual_material_reader_rejects_rewritten_polling_proof(tmp_path, damage):
    saved, facts, _ = original(tmp_path, [None, {"paid": True}])
    result = saved.apply(command(facts))
    identity = result["verification"]["verification_id"]
    admission = saved._read("execution_intent", identity + "-admission")
    record = saved._read("verification", identity)
    material_path = tmp_path / record["object_ref"]["relative_path"]
    material = json.loads(material_path.read_bytes())
    timeline = material["polling"]
    if damage == "last_fields":
        timeline["observations"][-1]["actual_fields"] = {"paid": False}
    elif damage == "bool_time":
        timeline["observations"][0]["started_after_ms"] = False
    elif damage == "early_retry":
        timeline["observations"][-1]["started_after_ms"] = 9
    elif damage == "early_elapsed":
        timeline["elapsed_ms"] = 9
    elif damage == "scope":
        material["polling"]["another_scope"] = "deployment-2"
    else:
        timeline["observations"][-1]["redacted"] = True
    ref = saved.objects.publish_bytes(
        facts.project_id, json.dumps(material).encode(), media_type="application/json"
    )
    with pytest.raises(ValueError):
        saved._derive(identity, admission, material, ref)
