"""Every B consumer must use the exact saved envelope, body identity and owner."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aitest.application.planning.draft import text_digest
from aitest.application.planning.drift import _readable
from aitest.application.planning.model_orchestration import _recall_existing_draft
from aitest.application.planning.substrate import CommittedRecord, require_scoped_record
from aitest.application.usecase_registry import BUseCaseError, _rule_versions_for


@pytest.mark.parametrize(
    "kind,field",
    [
        ("plan", "plan_id"),
        ("rule_version", "rule_id"),
        ("case_link", "confirmation_id"),
        ("generated_content", "generated_content_id"),
        ("prepared_run", "prepared_run_id"),
        ("model_outbound_request", "request_id"),
    ],
)
@pytest.mark.parametrize("wrong", ["missing", "other"])
def test_scoped_record_rejects_missing_or_substituted_body_identity(kind, field, wrong):
    payload = {"project_id": "p"}
    if wrong == "other":
        payload[field] = "other"
    record = CommittedRecord(kind, "expected", 1, payload)
    with pytest.raises(ValueError):
        require_scoped_record(
            record, project_id="p", aggregate_kind=kind, record_id="expected", revision=1
        )


def _wrong(record, change):
    if change == "kind":
        return replace(record, aggregate_kind="task")
    if change == "id":
        return replace(record, record_id="foreign")
    return replace(record, revision=True if change == "bool_revision" else record.revision + 1)


def _rules():
    return CommittedRecord(
        "rule_version",
        "r",
        2,
        {
            "project_id": "p",
            "rule_id": "r",
            "revision": 7,
            "scope": "scope",
            "text": "observe real data",
            "steps": [],
            "evidence_requirements": [],
            "source": "manual",
            "status": "published",
        },
    )


@pytest.mark.parametrize("wrong", ["kind", "id", "revision", "bool_revision", "body", "draft"])
def test_plan_rule_reader_rejects_another_saved_record_or_unpublished_version(wrong):
    record = _rules()
    if wrong == "body":
        record = replace(record, payload={**record.payload, "rule_id": "foreign"})
    elif wrong == "draft":
        record = replace(record, payload={**record.payload, "status": "draft"})
    else:
        record = _wrong(record, wrong)
    reader = Mock()
    reader.read.return_value = record
    with pytest.raises(BUseCaseError):
        _rule_versions_for(
            SimpleNamespace(reader=reader),
            project_id="p",
            parameters={"rule_revisions": [{"rule_id": "r", "revision": 2}]},
        )


def test_plan_rule_keeps_body_version_distinct_from_warehouse_revision():
    reader = Mock()
    reader.read.return_value = _rules()
    (version,) = _rule_versions_for(
        SimpleNamespace(reader=reader),
        project_id="p",
        parameters={"rule_revisions": [{"rule_id": "r", "revision": 2}]},
    )
    assert version.rule_id == "r" and version.revision == 7 and version.record_revision == 2


@pytest.mark.parametrize("wrong", ["kind", "id", "revision", "bool_revision"])
def test_drift_readability_cannot_accept_a_different_saved_envelope(wrong):
    record = CommittedRecord("binding", "b", 1, {"project_id": "p", "binding_id": "b"})
    reader = Mock()
    reader.read.return_value = _wrong(record, wrong)
    assert not _readable(
        reader, project_id="p", aggregate_kind="binding", record_id="b", revision=1
    )


def _draft():
    text = "the original safe draft"
    return CommittedRecord(
        "generated_content",
        "g",
        1,
        {
            "project_id": "p",
            "generated_content_id": "g",
            "revision": 1,
            "draft_text": text,
            "content_digest": text_digest(text),
            "draft_kind": "case",
            "template_id": "ticket-workflow",
            "template_version": "1.0.0",
            "revision_context": {"project_revision": 1, "template_revision": "1.0.0"},
            "status": "draft",
        },
    )


@pytest.mark.parametrize("wrong", ["kind", "id", "revision", "bool_revision", "bool_reference"])
def test_model_recall_cannot_reuse_a_different_saved_draft(wrong):
    reader = Mock()
    reader.read.return_value = _draft() if wrong == "bool_reference" else _wrong(_draft(), wrong)
    result = _recall_existing_draft(
        reader,
        project_id="p",
        payload={
            "generated_content_id": "g",
            "generated_content_revision": True if wrong == "bool_reference" else 1,
        },
    )
    assert result is None
    if wrong == "bool_reference":
        reader.read.assert_not_called()


def test_exact_model_recall_preserves_the_original_safe_body():
    reader = Mock()
    reader.read.return_value = _draft()
    result = _recall_existing_draft(
        reader,
        project_id="p",
        payload={
            "generated_content_id": "g",
            "generated_content_revision": 1,
        },
    )
    assert result.generated_content_id == "g" and result.revision == 1
    assert result.content_digest == text_digest("the original safe draft")


@pytest.mark.parametrize("wrong", ["kind", "id", "revision", "bool_revision"])
def test_wrong_source_envelope_blocks_before_reading_pinned_material(wrong):
    from aitest.application.project.source_analysis import SourceAnalysisService

    reader, snapshots = Mock(), Mock()
    reader.read.return_value = _wrong(
        CommittedRecord("source_snapshot", "s", 1, {"project_id": "p", "snapshot_id": "s"}), wrong
    )
    service = SourceAnalysisService(
        reader=reader, unit_of_work=Mock(), snapshots=snapshots, source_control=None
    )
    with pytest.raises(ValueError):
        service.check(project_id="p", snapshot_id="s", revision=1)
    snapshots.assert_not_called()
    assert not snapshots.method_calls


@pytest.mark.parametrize(
    "kind,field",
    [("task", "task_id"), ("delivery", "delivery_id"), ("model_outbound_policy", None)],
)
def test_stable_namespace_cannot_change_owner_and_survives_restart(tmp_path, kind, field):
    from aitest.application.planning.substrate import read_scoped_record
    from aitest.application.planning.substrate_adapter import PortsRecordReader
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

    repo = FileUnitOfWork(tmp_path).repo
    original = {"project_id": "owner-a", **({field: "shared"} if field else {})}
    repo.append(kind, "shared", 0, original)
    with pytest.raises(ValueError, match="cross-project ownership"):
        repo.append(kind, "shared", 1, {**original, "project_id": "owner-b"})
    fresh = FileUnitOfWork(tmp_path).repo
    reader = PortsRecordReader(fresh)
    saved = read_scoped_record(
        reader, project_id="owner-a", aggregate_kind=kind, record_id="shared", revision=1
    )
    assert dict(saved.payload) == original and fresh.current_revision(kind, "shared") == 1
    with pytest.raises(ValueError):
        read_scoped_record(
            reader, project_id="owner-b", aggregate_kind=kind, record_id="shared", revision=1
        )


@pytest.mark.parametrize("kind", ["task", "delivery", "model_outbound_policy"])
def test_unknown_legacy_owner_is_blocked_without_migration_or_relabelling(tmp_path, kind):
    from aitest.application.planning.substrate import read_scoped_record
    from aitest.application.planning.substrate_adapter import PortsRecordReader
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork

    repo = FileUnitOfWork(tmp_path).repo
    original = {"legacy": "owner unavailable"}
    repo.append(kind, "legacy", 0, original)
    reader = PortsRecordReader(repo)
    with pytest.raises(ValueError, match="unknown"):
        read_scoped_record(
            reader, project_id="claimant", aggregate_kind=kind, record_id="legacy", revision=1
        )
    with pytest.raises(ValueError, match="unverified historical ownership"):
        repo.append(kind, "legacy", 1, {"project_id": "claimant"})
    fresh = FileUnitOfWork(tmp_path).repo
    assert dict(fresh.read(aggregate_kind=kind, record_id="legacy", revision=1).payload) == original
    assert fresh.current_revision(kind, "legacy") == 1
