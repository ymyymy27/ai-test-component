"""Port metadata cannot be coerced into a saved revision or a successful commit."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.planning.preparation import (
    preparation_record_id,
    preparation_record_payload,
)
from aitest.application.planning.substrate import CommittedRecord, SubstrateContractError
from aitest.application.planning.substrate_adapter import PortsRecordReader, PortsUnitOfWork
from tests.unit.test_substrate_contract import _preparation


@pytest.mark.parametrize("revision", [True, "1", 1.5, -1])
def test_preparation_lookup_does_not_coerce_current_revision(revision):
    repository = Mock()
    repository.current_revision.return_value = revision
    record = _preparation()
    identity = preparation_record_id(project_id="p1", client_id="c1", prepare_request_id="req-1")
    repository.read.return_value = CommittedRecord(
        "preparation_record", identity, 1, preparation_record_payload(record)
    )
    with pytest.raises((ValueError, SubstrateContractError)):
        PortsRecordReader(repository).find_preparation(
            project_id="p1", client_id="c1", prepare_request_id="req-1"
        )
    repository.read.assert_not_called()


@pytest.mark.parametrize("wrong", ["id", "kind", "revision"])
def test_ports_reader_proves_the_returned_record_envelope(wrong):
    repository = Mock()
    record = CommittedRecord("task", "task-1", 1, {"project_id": "p1"})
    record = replace(
        record,
        **{"record_id": "other"}
        if wrong == "id"
        else {"aggregate_kind": "delivery"}
        if wrong == "kind"
        else {"revision": 2},
    )
    repository.read.return_value = record
    with pytest.raises(ValueError):
        PortsRecordReader(repository).read(aggregate_kind="task", record_id="task-1", revision=1)


@pytest.mark.parametrize("wrong", ["project", "client", "request", "intent"])
@pytest.mark.parametrize("by_intent", [False, True])
def test_preparation_receipt_must_match_the_derived_original_namespace(wrong, by_intent):
    original = _preparation()
    record = (
        replace(original, intent_id="prepare-intent-foreign")
        if wrong == "intent"
        else replace(
            original,
            request=replace(
                original.request,
                **{
                    "project_id"
                    if wrong == "project"
                    else "client_id"
                    if wrong == "client"
                    else "prepare_request_id": "foreign"
                },
            ),
        )
    )
    identity = preparation_record_id(project_id="p1", client_id="c1", prepare_request_id="req-1")
    repository = Mock()
    repository.current_revision.return_value = 1
    repository.read.return_value = CommittedRecord(
        "preparation_record", identity, 1, preparation_record_payload(record)
    )
    reader = PortsRecordReader(repository)
    with pytest.raises(ValueError):
        if by_intent:
            reader.find_preparation_by_intent(intent_id=original.intent_id)
        else:
            reader.find_preparation(project_id="p1", client_id="c1", prepare_request_id="req-1")


def test_valid_preparation_namespace_reads_the_same_original_receipt():
    original = _preparation()
    identity = preparation_record_id(project_id="p1", client_id="c1", prepare_request_id="req-1")
    repository = Mock()
    repository.current_revision.return_value = 1
    repository.read.return_value = CommittedRecord(
        "preparation_record", identity, 1, preparation_record_payload(original)
    )
    reader = PortsRecordReader(repository)
    assert (
        reader.find_preparation(project_id="p1", client_id="c1", prepare_request_id="req-1")
        == original
    )
    assert reader.find_preparation_by_intent(intent_id=original.intent_id) == original


def unit(stage_revision=1, current_revision=0):
    ports = Mock()
    repository = Mock()
    sequence = Mock()
    repository.current_revision.return_value = current_revision
    ports.stage_record.return_value = stage_revision
    sequence.current_commit_sequence.return_value = 0
    adapter = PortsUnitOfWork(ports, repository=repository, sequence=sequence)
    adapter.open("p1")
    return adapter, ports, sequence


@pytest.mark.parametrize("revision", [True, "1", 1.5, 0, 2])
def test_wrong_staged_revision_rolls_back_before_it_becomes_a_basis(revision):
    adapter, ports, _ = unit(stage_revision=revision)
    with pytest.raises(SubstrateContractError):
        adapter.stage_record(
            aggregate_kind="task",
            record_id="task-1",
            expected_revision=0,
            payload={"project_id": "p1"},
        )
    ports.commit.assert_not_called()
    ports.rollback.assert_called_once()


@pytest.mark.parametrize("sequence_value", [True, "1", 1.5, -1])
def test_commit_sequence_source_requires_an_exact_nonnegative_integer(sequence_value):
    adapter, ports, sequence = unit()
    sequence.current_commit_sequence.return_value = sequence_value
    try:
        with pytest.raises(SubstrateContractError):
            adapter.next_commit_seq()
    finally:
        adapter.rollback()


@pytest.mark.parametrize(
    "bad_result",
    [
        {"created": [("task", "task-1", 1)], "commit_sequence": True},
        {"created": [("task", "task-1", 1)], "commit_sequence": "1"},
        {"created": [("task", "task-1", 1)], "commit_sequence": 0},
        {"created": [("task", "task-1", 0)], "commit_sequence": 1},
        {"created": [("task", "task-1", 1), ("task", "task-1", 1)], "commit_sequence": 1},
        {"created": [], "commit_sequence": 1},
        {"created": [("task", "other", 1)], "commit_sequence": 1},
        {"created": [("delivery", "task-1", 1)], "commit_sequence": 1},
        {"created": [("task", "task-1", 2)], "commit_sequence": 1},
    ],
)
def test_unverifiable_commit_receipt_is_rejected_and_adapter_releases_its_state(bad_result):
    adapter, ports, _ = unit()
    adapter.stage_record(
        aggregate_kind="task", record_id="task-1", expected_revision=0, payload={"project_id": "p1"}
    )
    ports.commit.return_value = bad_result
    with pytest.raises(SubstrateContractError):
        adapter.commit()
    ports.rollback.assert_called_once()
    # Cleanup is not a claim that the underlying commit was undone.
    ports.commit.assert_called_once()
    adapter.open("p1")
    adapter.rollback()


def test_foreign_preparation_staging_releases_the_open_adapter_transaction():
    adapter, ports, _ = unit()
    record = _preparation()
    foreign = replace(record, request=replace(record.request, project_id="foreign"))
    with pytest.raises(ValueError):
        adapter.stage_preparation(record=foreign)
    ports.rollback.assert_called_once()
    adapter.open("p1")
    adapter.rollback()


def test_empty_commit_releases_the_open_adapter_transaction():
    adapter, ports, _ = unit()
    with pytest.raises(ValueError):
        adapter.commit()
    ports.rollback.assert_called_once()
    ports.commit.assert_not_called()


def test_valid_commit_retains_extra_records_written_by_the_same_controlled_transaction():
    adapter, ports, _ = unit()
    adapter.stage_record(
        aggregate_kind="task", record_id="task-1", expected_revision=0, payload={"project_id": "p1"}
    )
    ports.commit.return_value = {
        "created": [("approval_confirmation", "consent", 1), ("task", "task-1", 1)],
        "commit_sequence": 2,
    }
    result = adapter.commit()
    assert result.commit_seq == "2"
    assert result.revision_of("task", "task-1").revision == 1
    ports.rollback.assert_not_called()
