"""The consumer must reject an unreadable proof before external execution."""

from copy import deepcopy

import pytest

from aitest.application.execution.commit import _authorization_record_id
from aitest.application.execution.runner import SerialRunner
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from tests.support.execution_authority import fixture_coordinator
from tests.unit.test_serial_runner import FakeExecutionPort, _attempt, _request


@pytest.mark.parametrize(
    "change",
    ["missing", "extra", "bool_grant", "bool_state", "foreign", "digest", "state", "list"],
)
def test_bad_occupation_proof_rolls_back_before_external_start(tmp_path, monkeypatch, change):
    unit = FileUnitOfWork(tmp_path)
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    proof = coordinator._execution_authorizations
    original = proof.stage_occupation

    def bad_proof(**kwargs):
        result = dict(original(**kwargs))
        if change == "missing":
            result.pop("grant_digest")
        elif change == "extra":
            result["unverified"] = True
        elif change == "bool_grant":
            result["grant_revision"] = True
        elif change == "bool_state":
            result["state_revision"] = True
        elif change == "foreign":
            result["grant_id"] = "foreign-authorization"
        elif change == "digest":
            result["grant_digest"] = "sha256:unverified"
        elif change == "state":
            result["state_revision"] = 3
        else:
            return list(result)
        return result

    monkeypatch.setattr(proof, "stage_occupation", bad_proof)
    port = FakeExecutionPort()
    before = unit.current_commit_sequence()
    with pytest.raises(ValueError, match="original authorization proof"):
        SerialRunner(port, commit_coordinator=coordinator).start_attempt(_attempt(), _request())
    assert port.started == []
    assert unit.current_commit_sequence() == before
    assert (
        unit.current_revision(
            aggregate_kind="execution_authorization",
            record_id=proof.key(_request().authorization_ref.authorization_id) + ":state",
        )
        == 1
    )
    assert (
        unit.current_revision(
            aggregate_kind="execution_checkpoint", record_id=_attempt().attempt_id
        )
        == 0
    )


def test_saved_proof_shape_is_checked_even_when_provider_validation_is_incomplete(
    tmp_path, monkeypatch
):
    unit = FileUnitOfWork(tmp_path)
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    port = FakeExecutionPort()
    first = SerialRunner(port, commit_coordinator=coordinator).start_attempt(_attempt(), _request())
    identity = _authorization_record_id(_request().authorization_ref.authorization_id)
    claim = unit.read(aggregate_kind="execution_authorization", record_id=identity, revision=1)
    malformed = deepcopy(claim.payload)
    malformed["original_grant_proof"]["grant_revision"] = True
    original = coordinator._read_payload

    def read(kind, identity):
        return (
            malformed
            if identity == _authorization_record_id(_request().authorization_ref.authorization_id)
            else original(kind, identity)
        )

    monkeypatch.setattr(coordinator, "_read_payload", read)
    monkeypatch.setattr(
        coordinator._execution_authorizations, "validate_occupation", lambda **kw: None
    )
    with pytest.raises(ValueError, match="original authorization proof"):
        SerialRunner(port, commit_coordinator=coordinator).start_attempt(_attempt(), _request())
    assert first.execution_handle_ref is not None and len(port.started) == 1
