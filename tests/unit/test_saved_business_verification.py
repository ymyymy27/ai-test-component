"""Actual file commits/query material; source and human proofs use explicit fixtures."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.application.evidence.evidence_review import EvidenceReviewService
from aitest.application.evidence.saved_verification import SavedBusinessVerification
from aitest.application.execution.commit import ExecutionCommitCoordinator
from aitest.application.execution.facts import project_attempt_fact
from aitest.application.ports import CapturedBusinessVerification, VerificationRequest
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.contracts.prepared_run import BindingFormFact
from aitest.domain.evidence.evidence import VerificationObservation
from aitest.domain.execution.runs import AttemptState, PlanRevisionRef
from aitest.infrastructure.adapters.execution.verification import BusinessVerificationAdapter
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.security import KnownSecretRegistry, guard_value
from tests.unit.test_current_execution_snapshot import _batch, _publish
from tests.unit.test_serial_runner import _request


def command(facts, **changes):
    payload = {
        "action": "verify_pending",
        "request_id": "query-request",
        "intent_id": "query-intent",
        "project_id": facts.project_id,
        "target": facts.steps[0].step_id,
        "expected_revision": 0,
        "parameters": {
            "run_id": facts.run_id,
            "step_id": facts.steps[0].step_id,
            "attempt_id": facts.attempts[0].attempt_id,
            "base_snapshot_commit_id": facts.snapshot_commit_id,
        },
    }
    payload.update(changes)
    return Command(**payload)


def service(tmp_path, *, actual=None, expected=None, registry=None):
    unit = FileUnitOfWork(tmp_path)
    batch = _batch()
    request = _request()
    attempt = replace(
        batch.checkpoint.attempt,
        expected_plan_revision_ref=PlanRevisionRef(**batch.facts.plan_revision.model_dump()),
        authorization_ref=request.authorization_ref,
    )
    batch = replace(
        batch,
        checkpoint=replace(batch.checkpoint, attempt=attempt),
        facts=batch.facts.model_copy(
            update={"attempts": (project_attempt_fact(attempt, is_current=True),)}
        ),
    )
    _publish(unit, batch)
    coordinator = ExecutionCommitCoordinator(unit)
    facts = coordinator.read_runtime_revision_facts(
        project_id=batch.facts.project_id, run_id=batch.facts.run_id
    )
    authorizations = Mock()
    authorizations._origin.return_value = (
        {},
        SimpleNamespace(
            attempt=replace(batch.checkpoint.attempt, state=AttemptState.INTENT_RECORDED),
            request=request,
        ),
    )
    # The original grant/start proof is synthetic in this component fixture.
    coordinator.find_start = Mock(return_value=batch.checkpoint.attempt)
    authorizations._context.return_value = (
        None,
        None,
        SimpleNamespace(
            binding_form=BindingFormFact.PLAIN,
            git_base_commit=None,
            plain_manifest_digest="sha256:fixture-source",
            snapshot=SimpleNamespace(source_snapshot_id="fixture-snapshot"),
        ),
        (),
    )
    resolver = Mock()
    resolver.resolve.return_value = VerificationRequest(
        facts.attempts[0].attempt_id,
        "order-1",
        "read-payment",
        "immediate",
        "deployment-1",
        expected_facts={"paid": True} if expected is None else expected,
    )
    query = Mock()
    query.read_business_object.return_value = {"paid": True} if actual is None else actual
    store = FileObjectStore(tmp_path, registry=registry)
    saved = SavedBusinessVerification(
        coordinator,
        authorizations,
        store,
        workspace_id=facts.run.origin_workspace_id,
        instance_id="query-core",
        resolver=resolver,
        verifier=BusinessVerificationAdapter(query),
        protector=lambda value: guard_value(value, registry)[0],
    )
    return saved, facts, query


def test_query_is_outside_transaction_and_replay_reads_original_bytes(tmp_path):
    saved, facts, query = service(tmp_path)

    def read(**kwargs):
        assert saved.unit.project is None
        assert kwargs == {"business_object_id": "order-1", "target_deployment_ref": "deployment-1"}
        return {"paid": True, "object_id": "order-1"}

    query.read_business_object.side_effect = read
    result = saved.apply(command(facts))
    assert result["status"] == "attached"
    assert result["verification"]["observation"] == "matched"
    assert result["verification"]["verification_of"] == facts.attempts[0].attempt_id
    current = saved.coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
    assert current.run == facts.run and current.attempts == facts.attempts
    assert current.coverage == facts.coverage and current.run.result_ref == facts.run.result_ref
    before = saved.unit.commit_seq()
    saved.resolver = saved.verifier = None
    replay = saved.apply(command(facts, request_id="new-transport-request"))
    assert replay == result and saved.unit.commit_seq() == before
    assert query.read_business_object.call_count == 1
    # A new core service has no in-memory adapter/resolver and still reads exact authority.
    reopened = SavedBusinessVerification(
        ExecutionCommitCoordinator(FileUnitOfWork(tmp_path)),
        Mock(),
        FileObjectStore(tmp_path),
        workspace_id=facts.run.origin_workspace_id,
        instance_id="other-core",
        resolver=None,
        verifier=None,
        protector=lambda value: guard_value(value)[0],
    )
    assert reopened.apply(command(facts, request_id="reopened-request")) == result


@pytest.mark.parametrize("field", ["run_id", "step_id", "attempt_id", "base_snapshot_commit_id"])
def test_same_intent_cannot_change_its_original_scope(tmp_path, field):
    saved, facts, query = service(tmp_path)
    original = command(facts)
    saved.apply(original)
    parameters = {**original.parameters, field: "other-identity"}
    changed = original.model_copy(
        update={"request_id": "replay", "parameters": parameters, "target": parameters["step_id"]}
    )
    with pytest.raises(ValueError, match="conflicts"):
        saved.apply(changed)
    assert query.read_business_object.call_count == 1


@pytest.mark.parametrize("claim", ["no_actual", "false_match", "mutate_basis"])
def test_adapter_claim_or_mutated_request_cannot_replace_actual_comparison(tmp_path, claim):
    saved, facts, query = service(tmp_path, actual={"paid": False})
    adapter = saved.verifier

    class LyingCapture:
        def capture(self, request):
            result = adapter.capture(request)
            if claim == "mutate_basis":
                request.expected_facts["paid"] = False
            return CapturedBusinessVerification(
                replace(
                    result.verification, observation=VerificationObservation.MATCHED, gap_ids=()
                ),
                None if claim == "no_actual" else result.actual_fields,
            )

    saved.verifier = LyingCapture()
    with pytest.raises(ValueError, match="actual query JSON"):
        saved.apply(command(facts))
    assert query.read_business_object.call_count == 1
    assert (
        saved.coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
        == facts
    )
    # No automatic second query after an admitted but unsaved original observation.
    with pytest.raises(ValueError, match="new intent"):
        saved.apply(command(facts, request_id="retry-request"))
    assert query.read_business_object.call_count == 1


def test_required_filtered_field_stays_unknown_and_never_reaches_disk(tmp_path):
    registry = KnownSecretRegistry()
    secret = "synthetic-business-secret"
    registry.register(secret)
    saved, facts, _ = service(
        tmp_path,
        actual={"status": secret, "paid": True},
        expected={"status": "[REDACTED]", "paid": True},
        registry=registry,
    )
    result = saved.apply(command(facts))
    assert result["verification"]["observation"] == "no_result"
    assert result["verification"]["gap_ids"] == ["business_fact_filtered:status"]
    assert result["evidence"]["redaction_state"] == "redacted"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_filtering_unrelated_field_keeps_valid_assertion_and_redaction_provenance(tmp_path):
    saved, facts, _ = service(tmp_path, actual={"paid": True, "password": "synthetic-only"})
    result = saved.apply(command(facts))
    assert result["verification"]["observation"] == "matched"
    assert result["evidence"]["redaction_state"] == "redacted"


def test_credential_in_json_field_name_is_filtered_before_admission_or_object(tmp_path):
    registry = KnownSecretRegistry()
    secret = "synthetic-sensitive-field-name"
    registry.register(secret)
    saved, facts, query = service(
        tmp_path, actual={"paid": True, secret: "ordinary"}, registry=registry
    )
    result = saved.apply(command(facts))
    assert result["verification"]["observation"] == "matched"
    assert result["evidence"]["redaction_state"] == "redacted"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
    saved, facts, query = service(
        tmp_path / "unsafe-basis", expected={secret: True}, registry=registry
    )
    with pytest.raises(ValueError, match="unsafe"):
        saved.apply(command(facts))
    assert not query.mock_calls


def test_filtered_field_collision_rejects_ambiguous_material():
    registry = KnownSecretRegistry()
    registry.register("synthetic-first-key")
    registry.register("synthetic-second-key")
    with pytest.raises(RuntimeError, match="ambiguous field"):
        guard_value({"synthetic-first-key": True, "synthetic-second-key": False}, registry)


def test_unsafe_expected_basis_stops_before_query_or_admission(tmp_path):
    saved, facts, query = service(tmp_path, expected={"password": "synthetic-only"})
    before = saved.unit.commit_seq()
    with pytest.raises(ValueError, match="unsafe"):
        saved.apply(command(facts))
    assert saved.unit.commit_seq() == before and not query.mock_calls


def test_new_snapshot_during_query_keeps_historical_material_without_overwrite(tmp_path):
    saved, facts, query = service(tmp_path)
    moved = None

    def read(**kwargs):
        nonlocal moved
        saved.unit.begin("other-progress", facts.project_id)
        _, moved = saved.coordinator._stage_snapshot(
            facts.model_copy(update={"facts_id": "new-boundary"})
        )
        saved.unit.commit()
        return {"paid": True}

    query.read_business_object.side_effect = read
    result = saved.apply(command(facts))
    assert result["status"] == "historical_only" and result["execution_facts"] is None
    assert (
        saved.coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
        == moved
    )
    assert not moved.verifications
    assert saved.apply(command(facts, request_id="replay-request")) == result


@pytest.mark.parametrize("fault", ["stage", "commit_after_publication"])
def test_result_publication_faults_never_requery_original_observation(tmp_path, fault):
    saved, facts, query = service(tmp_path)
    original_stage, original_commit = saved.unit.stage_record, saved.unit.commit

    def stage(**kwargs):
        if kwargs["aggregate_kind"] == "verification" and fault == "stage":
            raise OSError("synthetic stage fault")
        return original_stage(**kwargs)

    def commit(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        if query.read_business_object.call_count and fault == "commit_after_publication":
            raise OSError("synthetic lost response")
        return result

    saved.unit.stage_record, saved.unit.commit = stage, commit
    with pytest.raises(OSError, match="synthetic"):
        saved.apply(command(facts))
    saved.unit.stage_record, saved.unit.commit = original_stage, original_commit
    if fault == "stage":
        with pytest.raises(ValueError, match="new intent"):
            saved.apply(command(facts, request_id="retry-request"))
        assert (
            saved.coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)
            == facts
        )
    else:
        assert saved.apply(command(facts, request_id="retry-request"))["status"] == "attached"
    assert query.read_business_object.call_count == 1


@pytest.mark.parametrize("damage", ["deleted", "tampered"])
def test_replay_requires_actual_saved_query_object(tmp_path, damage):
    saved, facts, query = service(tmp_path)
    result = saved.apply(command(facts))
    identity = result["verification"]["verification_id"]
    raw = saved.coordinator._read_payload("verification", identity)
    path = tmp_path / raw["object_ref"]["relative_path"]
    if damage == "deleted":
        path.unlink()
    else:
        path.write_bytes(b'{"actual_fields":{"paid":false}}')
    with pytest.raises((OSError, ValueError)):
        saved.apply(command(facts, request_id="replay-request"))
    assert query.read_business_object.call_count == 1


def test_review_provider_cannot_mutate_the_original_request_basis():
    request = VerificationRequest(
        "op", "obj", "method", "deadline", "deployment", expected_facts={"paid": True}
    )
    verifier = Mock()

    def verify(copy):
        copy.expected_facts["paid"] = False
        return BusinessVerificationAdapter(
            SimpleNamespace(read_business_object=lambda **kw: {"paid": False})
        ).verify(copy)

    verifier.verify.side_effect = verify
    EvidenceReviewService(verifier).review(request)
    assert request.expected_facts == {"paid": True}


def test_default_handler_rejects_injected_query_material_and_reports_missing_capability(tmp_path):
    core = assemble_workspace_core(tmp_path, instance_id="query-default-core")
    try:
        saved = core.business_verification
        assert saved is not None
        values = {
            "run_id": "run",
            "step_id": "step",
            "attempt_id": "attempt",
            "base_snapshot_commit_id": "base",
        }
        cmd = Command(
            action="verify_pending",
            project_id="project",
            request_id="request",
            intent_id="intent",
            target="step",
            expected_revision=0,
            parameters=values,
        )
        with pytest.raises(ValueError) as error:
            saved.apply(cmd.model_copy(update={"parameters": {**values, "passed": True}}))
        assert error.value.code == "INVALID_REQUEST"
        with pytest.raises(Exception) as error:
            saved.apply(cmd)
        assert error.value.code == "CAPABILITY_UNAVAILABLE"
    finally:
        core.lifetime_lock.release()
