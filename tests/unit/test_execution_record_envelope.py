"""A payload digest cannot replace the exact warehouse envelope identity."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from aitest.application.execution.commit import ExecutionCommitCoordinator, _payload_digest
from tests.unit.test_current_execution_snapshot import _batch


def reader(change=None, target="snapshot"):
    facts = _batch().facts
    payload = facts.model_dump(mode="json")
    pointer = {
        "schema_version": "aitest.execution-facts-reference/1.0",
        "project_id": facts.project_id,
        "run_id": facts.run_id,
        "snapshot_commit_id": facts.snapshot_commit_id,
        "snapshot_revision": 1,
        "digest": _payload_digest(payload),
        "previous_snapshot_commit_id": None,
    }
    if change == "extra":
        pointer["unverified"] = True
    elif change == "missing":
        pointer.pop("previous_snapshot_commit_id")
    elif change == "previous":
        pointer["previous_snapshot_commit_id"] = ""

    class Records:
        def current_revision(self, *, aggregate_kind, record_id):
            return 2 if change == "rewritten" and aggregate_kind == "execution_facts" else 1

        def read(self, *, aggregate_kind, record_id, revision):
            record = SimpleNamespace(
                aggregate_kind=aggregate_kind,
                record_id=record_id,
                revision=revision,
                payload=deepcopy(
                    pointer if aggregate_kind == "execution_facts_current" else payload
                ),
            )
            if (aggregate_kind == "execution_facts") == (target == "snapshot"):
                if change == "kind":
                    record.aggregate_kind = "foreign-kind"
                elif change == "identity":
                    record.record_id = "foreign-record"
                elif change in {"bool", "float", "string"}:
                    record.revision = {"bool": True, "float": 1.0, "string": "1"}[change]
            return record

    return ExecutionCommitCoordinator(Records()), facts


@pytest.mark.parametrize("target", ["pointer", "snapshot"])
@pytest.mark.parametrize("change", ["kind", "identity", "bool", "float", "string"])
def test_current_fact_requires_exact_record_envelope(target, change):
    coordinator, facts = reader(change, target)
    with pytest.raises(ValueError, match="envelope"):
        coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)


@pytest.mark.parametrize("change", ["extra", "missing", "previous", "rewritten"])
def test_current_fact_reference_is_closed_and_snapshot_is_immutable(change):
    coordinator, facts = reader(change)
    with pytest.raises(ValueError, match="reference|immutable"):
        coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id)


def test_matching_exact_snapshot_envelope_remains_readable():
    coordinator, facts = reader()
    assert coordinator.read_current_facts(project_id=facts.project_id, run_id=facts.run_id) == facts
