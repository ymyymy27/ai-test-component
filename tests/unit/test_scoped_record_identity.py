"""B readers must prove the exact warehouse identity and project ownership."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.planning.persistence import load_case
from aitest.application.planning.serialization import case_to_payload
from aitest.application.planning.substrate import CommittedRecord, RecordPage, current_record
from aitest.application.project.persistence import load_task
from aitest.application.project.serialization import task_to_payload
from tests.unit.test_planning_persistence_real_store import _case
from tests.unit.test_task_and_delivery_persistence import PROJECT_ID, _task


@pytest.mark.parametrize("loader", ["task", "case"])
@pytest.mark.parametrize("wrong", ["kind", "id", "revision", "bool_revision", "body_id"])
def test_exact_load_rejects_another_record_even_with_the_same_project(loader, wrong):
    payload = (
        task_to_payload(_task())
        if loader == "task"
        else case_to_payload(_case(), project_id=PROJECT_ID)
    )
    identity = "task-1" if loader == "task" else "case-1"
    record = CommittedRecord(loader, identity, 1, payload)
    if wrong == "kind":
        record = replace(record, aggregate_kind="delivery")
    elif wrong == "id":
        record = replace(record, record_id="other")
    elif wrong == "revision":
        record = replace(record, revision=2)
    elif wrong == "bool_revision":
        record = replace(record, revision=True)
    else:
        record = replace(record, payload={**payload, loader + "_id": "other"})
    reader = Mock()
    reader.read.return_value = record
    with pytest.raises(ValueError):
        if loader == "task":
            load_task(reader, project_id=PROJECT_ID, task_id=identity, revision=1)
        else:
            load_case(reader, project_id=PROJECT_ID, case_id=identity, revision=1)


def test_planning_record_with_unknown_project_is_blocked():
    payload = case_to_payload(_case(), project_id=PROJECT_ID)
    payload.pop("project_id")
    reader = Mock()
    reader.read.return_value = CommittedRecord("case", "case-1", 1, payload)
    with pytest.raises(ValueError):
        load_case(reader, project_id=PROJECT_ID, case_id="case-1", revision=1)


@pytest.mark.parametrize("revision", [True, "1", 1.5, -1])
def test_current_revision_is_not_coerced_to_a_valid_warehouse_revision(revision):
    reader = Mock()
    reader.current_revision.return_value = revision
    reader.read.return_value = CommittedRecord("task", "task-1", 1, task_to_payload(_task()))
    with pytest.raises(ValueError):
        current_record(reader, project_id=PROJECT_ID, aggregate_kind="task", record_id="task-1")
    reader.read.assert_not_called()


def test_current_read_checks_the_returned_exact_record():
    reader = Mock()
    reader.current_revision.return_value = 1
    reader.read.return_value = CommittedRecord("task", "another-task", 1, task_to_payload(_task()))
    with pytest.raises(ValueError):
        current_record(reader, project_id=PROJECT_ID, aggregate_kind="task", record_id="task-1")


@pytest.mark.parametrize("wrong", ["kind", "id", "owner", "bool_revision"])
def test_legacy_current_query_cannot_substitute_a_foreign_or_unknown_row(wrong):
    payload = task_to_payload(_task())
    record = CommittedRecord("task", "task-1", 1, payload)
    if wrong == "kind":
        record = replace(record, aggregate_kind="delivery")
    elif wrong == "id":
        record = replace(record, record_id="another-task")
    elif wrong == "owner":
        record = replace(record, payload={**payload, "project_id": "another-project"})
    else:
        record = replace(record, revision=True)

    class LegacyReader:
        def query(self, query):
            return RecordPage((record,))

    with pytest.raises(ValueError):
        current_record(
            LegacyReader(), project_id=PROJECT_ID, aggregate_kind="task", record_id="task-1"
        )


def test_exact_task_read_retains_declared_business_revision_separate_from_warehouse():
    payload = task_to_payload(_task(revision=7))
    reader = Mock()
    reader.read.return_value = CommittedRecord("task", "task-1", 2, payload)
    result = load_task(reader, project_id=PROJECT_ID, task_id="task-1", revision=2)
    assert result.task_id == "task-1" and result.revision == 7


@pytest.mark.parametrize(
    "parameters",
    [
        {"project_id": "", "task_id": "task-1", "revision": 1},
        {"project_id": PROJECT_ID, "task_id": " ", "revision": 1},
        {"project_id": PROJECT_ID, "task_id": "task-1", "revision": True},
        {"project_id": PROJECT_ID, "task_id": "task-1", "revision": "1"},
    ],
)
def test_invalid_exact_lookup_stops_before_the_reader(parameters):
    reader = Mock()
    with pytest.raises(ValueError):
        load_task(reader, **parameters)
    reader.read.assert_not_called()


@pytest.mark.parametrize("record", [None, {}, []])
def test_unreadable_saved_record_shape_is_a_blocking_error(record):
    reader = Mock()
    reader.read.return_value = record
    with pytest.raises(ValueError):
        load_task(reader, project_id=PROJECT_ID, task_id="task-1", revision=1)
