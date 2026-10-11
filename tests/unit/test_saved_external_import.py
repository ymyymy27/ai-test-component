"""Immutable import identity and safe actual objects through the default API."""

from dataclasses import asdict

import pytest

from aitest.application.project.serialization import project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.project.context import LocalProject
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.security import known_secrets
from tests.unit.test_default_execution_authorization import RELAY


@pytest.fixture
def core(tmp_path):
    assembly = assemble_workspace_core(tmp_path / "workspace", instance_id="external-core")
    project = LocalProject(
        "project", assembly.workspace.workspace_id, "project", "external fixture", "1"
    )
    response = assembly.api.dispatch(
        Command(
            action="save_context",
            project_id=project.project_id,
            request_id="save-project",
            intent_id="save-project-intent",
            expected_revision=0,
            parameters={"project": project_to_payload(project)},
        ),
        RELAY,
    )
    assert response.error is None, response.error
    yield assembly
    assembly.lifetime_lock.release()


def document(**changes):
    value = {
        "schema_version": "aitest.external-result/1.0",
        "source_instance_id": "external-tool",
        "source_record_id": "result-1",
        "layer": "L3",
        "source_identity": None,
        "run_id": None,
        "step_id": None,
        "attempt_id": None,
        "actual_output": {"paid": False},
        "assertion_values": {"paid": True},
        "mock_declarations": [],
        "attachments": [],
    }
    value.update(changes)
    return value


def cmd(doc=None, **changes):
    values = dict(
        action="import_external_result",
        project_id="project",
        request_id="import-request",
        intent_id="import-intent",
        expected_revision=0,
        target="external-result",
        parameters={"document": document() if doc is None else doc},
    )
    values.update(changes)
    return Command(**values)


def test_default_import_keeps_self_report_separate_and_restarts_with_original_material(core):
    command = cmd()
    result = core.api.dispatch(command, RELAY)
    assert result.error is None, result.error
    assert result.result["origin"] == "external_import"
    assert result.result["verification_status"] == "not_verified"
    assert result.result["document"]["actual_output"] == {"paid": False}
    assert result.result["document"]["assertion_values"] == {"paid": True}
    assert result.result["document"]["attempt_id"] is None
    before = core.unit_of_work.commit_seq()
    replay = core.api.dispatch(command.model_copy(update={"request_id": "replay"}), RELAY)
    assert replay.result == result.result and core.unit_of_work.commit_seq() == before
    root = core.workspace.root
    core.lifetime_lock.release()
    reopened = assemble_workspace_core(root, instance_id="external-reopened")
    try:
        response = reopened.api.dispatch(
            command.model_copy(update={"request_id": "reopened"}), RELAY
        )
        assert response.error is None, response.error
        assert response.result == result.result
    finally:
        reopened.lifetime_lock.release()


def test_same_source_with_new_intent_returns_original_import_once(core):
    first = core.external_imports.apply(cmd())
    second = core.external_imports.apply(
        cmd(intent_id="second-intent", request_id="second-request")
    )
    assert second == first
    assert (
        core.unit_of_work.current_revision(
            aggregate_kind="external_import", record_id=first["import_id"]
        )
        == 1
    )


@pytest.mark.parametrize(
    "field,change",
    [
        ("actual_output", {"paid": True}),
        ("assertion_values", {"paid": False}),
        ("layer", "L2"),
        ("source_identity", {"commit": "other"}),
        ("run_id", "external-run"),
        ("attempt_id", "external-attempt"),
        ("mock_declarations", [{"scope": "other"}]),
    ],
)
def test_same_source_different_decisive_material_conflicts_without_a_second_import(
    core, field, change
):
    first = core.external_imports.apply(cmd())
    before = core.unit_of_work.commit_seq()
    with pytest.raises(ValueError, match="conflicts"):
        core.external_imports.apply(
            cmd(document(**{field: change}), intent_id="new-intent", request_id="new-request")
        )
    assert core.unit_of_work.commit_seq() == before
    assert (
        core.unit_of_work.current_revision(
            aggregate_kind="external_import", record_id=first["import_id"]
        )
        == 1
    )


def test_same_intent_cannot_switch_source_identity(core):
    core.external_imports.apply(cmd())
    with pytest.raises(ValueError, match="intent conflicts"):
        core.external_imports.apply(
            cmd(document(source_instance_id="other-tool"), request_id="other-request")
        )


def test_unknown_source_does_not_invent_identity_or_deduplicate_across_new_intents(core):
    value = document(source_instance_id=None, source_record_id=None)
    first = core.external_imports.apply(cmd(value))
    second = core.external_imports.apply(
        cmd(value, intent_id="unknown-new", request_id="unknown-request")
    )
    assert first["import_id"] != second["import_id"]
    assert first["document"]["source_instance_id"] is None
    assert "external_source_unknown" in first["gap_ids"]


@pytest.mark.parametrize("fault", ["stage_receipt", "commit_after_publication"])
def test_failed_import_does_not_occupy_source_and_lost_response_replays_original(
    core, fault, monkeypatch
):
    original_stage, original_commit = core.unit_of_work.stage_record, core.unit_of_work.commit

    def stage(**kwargs):
        if fault == "stage_receipt" and kwargs["aggregate_kind"] == "execution_intent":
            raise OSError("synthetic stage fault")
        return original_stage(**kwargs)

    def commit(*args, **kwargs):
        value = original_commit(*args, **kwargs)
        if fault == "commit_after_publication":
            raise OSError("synthetic lost response")
        return value

    monkeypatch.setattr(core.unit_of_work, "stage_record", stage)
    monkeypatch.setattr(core.unit_of_work, "commit", commit)
    with pytest.raises(OSError, match="synthetic"):
        core.external_imports.apply(cmd())
    monkeypatch.setattr(core.unit_of_work, "stage_record", original_stage)
    monkeypatch.setattr(core.unit_of_work, "commit", original_commit)
    value = document(actual_output={"paid": True}) if fault == "stage_receipt" else document()
    result = core.external_imports.apply(cmd(value, request_id="retry-request"))
    assert result["document"]["actual_output"] == value["actual_output"]


def test_attachment_is_project_owned_readable_and_in_the_authority_closure(core):
    ref = FileObjectStore(core.workspace.root).publish_bytes(
        "project", b'{"payment":"received"}', media_type="application/json"
    )
    result = core.external_imports.apply(cmd(document(attachments=[asdict(ref)])))
    raw = core.execution_coordinator._read_payload("external_import", result["import_id"])
    assert raw["attachment_refs"] == [asdict(ref)]
    (core.workspace.root / ref.relative_path).unlink()
    with pytest.raises((OSError, ValueError)):
        core.external_imports.apply(
            cmd(document(attachments=[asdict(ref)]), request_id="missing-attachment")
        )


@pytest.mark.parametrize("problem", ["foreign", "binary", "path_only", "bad_json"])
def test_unverifiable_attachment_is_rejected_before_any_import(core, problem):
    store = FileObjectStore(core.workspace.root)
    if problem == "path_only":
        refs = [{"path": "arbitrary-local-file"}]
    else:
        ref = store.publish_bytes(
            "foreign" if problem == "foreign" else "project",
            b"not-json" if problem == "bad_json" else b"fixture",
            media_type="application/octet-stream" if problem == "binary" else "application/json",
        )
        refs = [asdict(ref)]
    before = core.unit_of_work.commit_seq()
    with pytest.raises((OSError, ValueError)):
        core.external_imports.apply(cmd(document(attachments=refs)))
    assert core.unit_of_work.commit_seq() == before


def test_credentials_in_values_and_keys_are_filtered_before_objects_and_records(core):
    secret = "synthetic-external-key-credential"
    registry = known_secrets()
    registry.register(secret)
    try:
        result = core.external_imports.apply(
            cmd(document(actual_output={secret: "ordinary", "password": secret}))
        )
        assert "external_material_filtered" in result["gap_ids"]
        assert result["verification_status"] == "not_verified"
        core.lifetime_lock.release()
        for path in core.workspace.root.rglob("*"):
            if path.is_file():
                assert secret.encode() not in path.read_bytes()
    finally:
        registry.clear()


@pytest.mark.parametrize(
    "change",
    [{"schema_version": "unknown/2.0"}, {"layer": {}}, {"extra": True}, {"actual_output": []}],
)
def test_unknown_format_and_invalid_shape_do_not_get_receipts(core, change):
    before = core.unit_of_work.commit_seq()
    with pytest.raises(ValueError):
        core.external_imports.apply(cmd(document(**change)))
    assert core.unit_of_work.commit_seq() == before


@pytest.mark.parametrize("content", [b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}', b'{"a":NaN}'])
def test_ambiguous_attachment_json_is_rejected(core, content):
    ref = FileObjectStore(core.workspace.root).publish_bytes(
        "project", content, media_type="application/json"
    )
    before = core.unit_of_work.commit_seq()
    with pytest.raises(ValueError, match="duplicate|non-finite"):
        core.external_imports.apply(cmd(document(attachments=[asdict(ref)])))
    assert core.unit_of_work.commit_seq() == before


@pytest.mark.parametrize("field,value", [("digest", []), ("size", True), ("relative_path", None)])
def test_malformed_object_ref_returns_a_blocked_error_through_default_api(core, field, value):
    ref = asdict(
        FileObjectStore(core.workspace.root).publish_bytes(
            "project", b"safe", media_type="text/plain"
        )
    )
    ref[field] = value
    response = core.api.dispatch(cmd(document(attachments=[ref])), RELAY)
    assert response.error.code == "EXTERNAL_IMPORT_BLOCKED"


def test_attachment_disappearing_after_preflight_blocks_the_entire_commit(core, monkeypatch):
    ref = FileObjectStore(core.workspace.root).publish_bytes(
        "project", b"safe attachment", media_type="text/plain"
    )
    begin = core.unit_of_work.begin
    before = core.unit_of_work.commit_seq()

    def remove_after_begin(*args, **kwargs):
        begin(*args, **kwargs)
        (core.workspace.root / ref.relative_path).unlink()

    monkeypatch.setattr(core.unit_of_work, "begin", remove_after_begin)
    with pytest.raises((OSError, ValueError)):
        core.external_imports.apply(cmd(document(attachments=[asdict(ref)])))
    assert core.unit_of_work.commit_seq() == before
