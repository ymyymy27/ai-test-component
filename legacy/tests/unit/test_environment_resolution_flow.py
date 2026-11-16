"""Trusted environment observations survive B/C admission and safe history replay."""

from dataclasses import replace

import pytest

from aitest.application.ports import EnvironmentResolutionRequest
from aitest.infrastructure.python_environment import RegisteredPythonEnvironmentResolver
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare
from tests.unit.test_initial_run_registration import register


def test_client_fact_and_nested_resolution_are_replaced_by_trusted_core_observation(authoritative):
    core, inputs, _ = authoritative
    invented_detail = core.environment_resolution.resolver.resolve(
        EnvironmentResolutionRequest(
            inputs.project_id, inputs.environment.environment_id, "venv", "Python 3.13"
        )
    ).resolution.model_copy(update={"carrier_id": "client-forged-carrier"})
    fake = inputs.environment.model_copy(
        update={
            "interpreter_identity": "invented-client-python",
            "dependency_set_digest": "invented-client-deps",
            "resolution": invented_detail,
        }
    )
    response = prepare(core, replace(inputs, environment=fake))
    assert response.error is None
    assert response.result["status"] == "prepared"
    environment = response.result["environment"]
    assert environment["interpreter_identity"] != "invented-client-python"
    assert environment["dependency_set_digest"] != "invented-client-deps"
    resolution = environment["resolution"]
    assert resolution["carrier_id"] == "explicit-synthetic-test-carrier"
    assert resolution["project_id"] == inputs.project_id
    assert resolution["binding_id"] == inputs.binding_id
    assert resolution["environment_revision"] == inputs.environment.revision
    saved = core.unit_of_work.repo.read(
        aggregate_kind="prepared_run", record_id=response.result["prepared_run_id"], revision=1
    )
    assert dict(saved.payload)["environment"] == environment
    receipt = core.unit_of_work.repo.read(
        aggregate_kind="preparation_record",
        record_id="prep-" + response.result["intent_id"][len("intent-") :],
        revision=1,
    )
    assert str(receipt.payload["observed_environment_content_identity"]).startswith("sha256:")


def test_missing_trusted_carrier_blocks_without_creating_preparation(authoritative):
    core, inputs, _ = authoritative
    core.environment_resolution.resolver = RegisteredPythonEnvironmentResolver()
    before = core.unit_of_work.current_commit_sequence()
    response = prepare(core, inputs)
    assert response.error is None
    assert response.result["status"] == "blocked"
    assert any(
        reason["code"] == "environment_unverified" for reason in response.result["blocking_reasons"]
    )
    assert core.unit_of_work.current_commit_sequence() == before
    assert not core.unit_of_work.pending


def test_same_environment_revision_with_changed_content_requires_new_preparation(authoritative):
    core, inputs, _ = authoritative
    first = prepare(core, inputs).result
    before = core.unit_of_work.current_commit_sequence()
    core.environment_resolution.resolver.dependency_digest = "sha256:changed-dependencies"
    changed = prepare(core, inputs, request="changed-environment").result
    assert changed["status"] == "blocked"
    assert changed["intent_id"] == first["intent_id"]
    assert any(
        rule["source_kind"] == "environment_content_identity"
        for rule in changed["invalidation_rules"]
    )
    assert core.unit_of_work.current_commit_sequence() == before
    stored = core.unit_of_work.repo.read(
        aggregate_kind="prepared_run", record_id=first["prepared_run_id"], revision=1
    )
    assert dict(stored.payload) == first
    fresh_response = prepare(
        core,
        replace(inputs, prepare_request_id="explicit-new-prepare"),
        request="explicit-new-prepare-request",
        intent="explicit-new-prepare-command-intent",
    )
    assert fresh_response.error is None, fresh_response.error
    fresh = fresh_response.result
    assert fresh["status"] == "prepared"
    assert fresh["intent_id"] != first["intent_id"]
    assert fresh["environment"]["dependency_set_digest"] == "sha256:changed-dependencies"


def test_new_initial_registration_rechecks_environment_but_history_recall_does_not(authoritative):
    core, inputs, _ = authoritative
    prepared = prepare(core, inputs).result
    original = register(core, prepared)
    core.environment_resolution.resolver.dependency_digest = "sha256:changed-after-registration"
    before = core.unit_of_work.current_commit_sequence()
    calls = core.environment_resolution.resolver.calls
    assert register(core, prepared, request="history-retransmission") == original
    assert core.environment_resolution.resolver.calls == calls
    with pytest.raises(ValueError, match="actual environment changed"):
        register(core, prepared, request="new-registration", intent="new-registration-intent")
    assert core.unit_of_work.current_commit_sequence() == before
    assert not core.unit_of_work.pending


def test_environment_probe_never_runs_under_unit_of_work_lock(authoritative, monkeypatch):
    core, inputs, _ = authoritative
    resolver = core.environment_resolution.resolver
    original = resolver.resolve
    observations = []

    def check(request):
        observations.append(bool(core.unit_of_work.pending))
        assert not core.unit_of_work.pending
        return original(request)

    monkeypatch.setattr(resolver, "resolve", check)
    prepared = prepare(core, inputs).result
    register(core, prepared)
    assert observations == [False, False]


def test_preparation_atomic_failure_consumes_no_intent_and_retry_freezes_observation(
    authoritative,
    monkeypatch,
):
    core, inputs, _ = authoritative
    original = core.unit_of_work.stage_record
    before = core.unit_of_work.current_commit_sequence()

    def fail(**values):
        if values["aggregate_kind"] == "prepared_run":
            raise OSError("environment preparation publication failure")
        return original(**values)

    monkeypatch.setattr(core.unit_of_work, "stage_record", fail)
    failed = prepare(core, inputs)
    assert failed.error is not None
    assert core.unit_of_work.current_commit_sequence() == before
    assert not core.unit_of_work.pending
    monkeypatch.setattr(core.unit_of_work, "stage_record", original)
    retried = prepare(core, inputs, request="retry-after-publication-failure")
    assert retried.error is None
    assert retried.result["status"] == "prepared"
    assert retried.result["environment"]["resolution"] is not None
