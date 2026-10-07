"""Saved ordinary write intents on actual file storage, including a restarted entry."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.planning.substrate import CommittedRecord
from aitest.application.project.serialization import delivery_to_payload, task_to_payload
from aitest.application.record_write import KIND, RecordWriteBlocked, RecordWriteIntent
from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure import security
from aitest.infrastructure.file_store.commit_manifest import FileCommitStore
from aitest.infrastructure.file_store.publication_backend import FilePublicationBackend
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.interfaces.local.api import EntryKind, Session
from tests.contracts.test_b_use_case_registration import (
    PROJECT_ID,
    _api,
    _case_payload,
    _environment_payload,
    _graph_payload,
    _project_payload,
    _scope_payload,
    _start,
    _write_command,
)
from tests.contracts.test_b_use_case_registration import (
    workspace_root as workspace_root,
)
from tests.unit.test_complete_commit_closure import workspace as workspace
from tests.unit.test_task_and_delivery_persistence import _delivery, _task


def parameters(action):
    return {
        "save_context": {"project": _project_payload()},
        "save_environment": {"environment": _environment_payload()},
        "save_dependency_graph": {"dependency_graph": _graph_payload()},
        "save_case": {"case": _case_payload()},
        "save_acceptance": {"acceptance_scope": _scope_payload()},
        "save_task": {"task": task_to_payload(replace(_task(), project_id=PROJECT_ID))},
        "save_delivery": {"delivery": delivery_to_payload(_delivery(), project_id=PROJECT_ID)},
    }[action]


ACTIONS = (
    "save_context",
    "save_environment",
    "save_dependency_graph",
    "save_case",
    "save_acceptance",
    "save_task",
    "save_delivery",
)


@pytest.mark.parametrize("action", ACTIONS)
def test_same_intent_new_request_and_restarted_entry_returns_original(workspace_root, action):
    api = _api(_start(workspace_root))
    first_session = Session("first-cli", EntryKind.AGENT_RELAY)
    if action == "save_delivery":
        seeded = api.dispatch(
            _write_command(
                action="save_task", request_id="seed-task", parameters=parameters("save_task")
            ),
            first_session,
        )
        assert seeded.error is None
    original = _write_command(action=action, request_id="first", parameters=parameters(action))
    first = api.dispatch(original, first_session)
    assert first.error is None, first.error
    raw = FileUnitOfWork(workspace_root)
    boundary = raw.current_commit_sequence()
    restarted = _api(_start(workspace_root))
    replay = restarted.dispatch(
        original.model_copy(update={"request_id": "reconnected"}),
        Session("new-mcp", EntryKind.AGENT_RELAY),
    )
    assert replay.error is None, replay.error
    assert replay.result == first.result
    assert replay.intent_id == original.intent_id
    assert raw.current_commit_sequence() == boundary


@pytest.mark.parametrize(
    "field",
    [
        "action",
        "target",
        "binding_revision",
        "expected_revision",
        "parameters",
        "record_id",
        "unused_parameter",
    ],
)
def test_changed_business_basis_conflicts_without_writing(workspace_root, field):
    command = _write_command(
        action="save_acceptance", request_id="original", parameters=parameters("save_acceptance")
    )
    api = _api(_start(workspace_root))
    session = Session("agent", EntryKind.AGENT_RELAY)
    assert api.dispatch(command, session).error is None
    raw = FileUnitOfWork(workspace_root)
    before = raw.current_commit_sequence()
    update = {"request_id": "changed"}
    if field == "action":
        update |= {"action": "save_case", "parameters": parameters("save_case")}
    elif field in {"target", "binding_revision", "expected_revision"}:
        update[field] = "another-target" if field == "target" else 2
    else:
        value = deepcopy(command.parameters)
        if field == "unused_parameter":
            value["unused"] = "different"
        else:
            value["acceptance_scope"]["name" if field == "parameters" else "scope_id"] = "changed"
        update["parameters"] = value
    result = api.dispatch(command.model_copy(update=update), session)
    assert result.error is not None and result.error.code == "INTENT_CONFLICT"
    assert raw.current_commit_sequence() == before


def test_later_revision_does_not_replace_original_result(workspace_root):
    api = _api(_start(workspace_root))
    session = Session("agent", EntryKind.AGENT_RELAY)
    command = _write_command(
        action="save_acceptance", request_id="original", parameters=parameters("save_acceptance")
    )
    original = api.dispatch(command, session)
    assert original.error is None
    value = deepcopy(command.parameters)
    value["acceptance_scope"].update(revision=2, name="newer")
    newer = _write_command(
        action=command.action,
        request_id="new-business-intent",
        parameters=value,
        expected_revision=1,
    )
    response = api.dispatch(newer, session)
    assert response.error is None and response.result["revision"] == 2
    raw = FileUnitOfWork(workspace_root)
    before = raw.current_commit_sequence()
    reopened = _api(_start(workspace_root))
    result = reopened.dispatch(command.model_copy(update={"request_id": "read-old"}), session)
    assert result.error is None and result.result == original.result
    assert raw.repo.current_revision("acceptance_scope", "scope-1") == 2
    assert raw.current_commit_sequence() == before


def test_same_global_intent_cannot_write_another_project(workspace_root):
    api = _api(_start(workspace_root))
    session = Session("agent", EntryKind.AGENT_RELAY)
    command = _write_command(
        action="save_context", request_id="original", parameters=parameters("save_context")
    )
    assert api.dispatch(command, session).error is None
    value = deepcopy(command.parameters)
    value["project"]["local_project_id"] = "other-project"
    for module in value["project"]["modules"]:
        module["project_id"] = "other-project"
    changed = command.model_copy(
        update={"project_id": "other-project", "request_id": "changed-project", "parameters": value}
    )
    raw = FileUnitOfWork(workspace_root)
    before = raw.current_commit_sequence()
    result = api.dispatch(changed, session)
    assert result.error is not None and result.error.code == "INTENT_CONFLICT"
    assert raw.current_commit_sequence() == before
    assert raw.repo.current_revision("project", "other-project") == 0


@pytest.mark.parametrize("published", [False, True])
def test_actual_publication_fault_or_lost_reply_keeps_one_original_write(
    workspace, monkeypatch, published
):
    api = _api(_start(workspace))
    session = Session("agent", EntryKind.AGENT_RELAY)
    command = _write_command(
        action="save_acceptance", request_id="original", parameters=parameters("save_acceptance")
    )
    before = FileUnitOfWork(workspace).current_commit_sequence()
    replace_current = FilePublicationBackend.replace_current

    def fault(backend, data, *, previous):
        if published:
            replace_current(backend, data, previous=previous)
        raise OSError("injected actual publication fault")

    with monkeypatch.context() as patch:
        patch.setattr(FilePublicationBackend, "replace_current", fault)
        assert api.dispatch(command, session).error is not None
    raw = FileUnitOfWork(workspace)
    intermediate = raw.current_commit_sequence()
    assert intermediate == before + (2 if published else 0)
    assert FileCommitStore(workspace).read_current(verify_material=True) is not None
    reopened = _api(_start(workspace))
    result = reopened.dispatch(
        command.model_copy(update={"request_id": "verify-original"}), session
    )
    assert result.error is None and result.result["revision"] == 1
    assert raw.current_commit_sequence() == before + 2
    intent = RecordWriteIntent.from_command(
        command, reader=_start(workspace).reader, workspace_id=None
    )
    receipt = raw.repo.read(aggregate_kind=KIND, record_id=intent.record_id, revision=1)
    assert receipt.payload["created_at_commit"] == str(before + 2)
    assert FileCommitStore(workspace).read_current(verify_material=True) is not None


@pytest.fixture
def saved(workspace_root):
    command = _write_command(
        action="save_acceptance", request_id="original", parameters=parameters("save_acceptance")
    )
    stack = _start(workspace_root)
    result = _api(stack).dispatch(command, Session("agent", EntryKind.AGENT_RELAY))
    assert result.error is None
    intent = RecordWriteIntent.from_command(command, reader=stack.reader, workspace_id=None)
    receipt = stack.reader.read(aggregate_kind=KIND, record_id=intent.record_id, revision=1)
    material = stack.reader.read(aggregate_kind="acceptance_scope", record_id="scope-1", revision=1)
    return intent, receipt, material


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "unknown"),
        ("workspace_id", "other"),
        ("project_id", "other"),
        ("intent_id", "other"),
        ("aggregate_kind", "case"),
        ("record_id", "other"),
        ("record_revision", True),
        ("record_revision", 1.0),
        ("record_revision", "1"),
        ("record_digest", "sha256:bad"),
        ("created_at_commit", True),
        ("created_at_commit", "02"),
        ("created_at_commit", "0"),
        ("extra", "unknown"),
    ],
)
def test_unverified_receipt_never_returns_a_saved_success(saved, field, value):
    intent, receipt, material = saved
    damaged = dict(receipt.payload) | {field: value}
    reader = Mock()
    reader.current_revision.return_value = 1
    reader.read.side_effect = [CommittedRecord(KIND, intent.record_id, 1, damaged), material]
    with pytest.raises(RecordWriteBlocked):
        replace(intent, reader=reader).original()


@pytest.mark.parametrize("problem", ["missing", "wrong_envelope", "wrong_body"])
def test_original_material_must_be_exact_and_present(saved, problem):
    intent, receipt, material = saved
    reader = Mock()
    reader.current_revision.return_value = 1
    if problem == "missing":
        second = ValueError("unknown revision")
    elif problem == "wrong_envelope":
        second = replace(material, revision=True)
    else:
        second = replace(material, payload=dict(material.payload) | {"name": "changed"})
    reader.read.side_effect = [receipt, second]
    with pytest.raises(RecordWriteBlocked):
        replace(intent, reader=reader).original()


def test_known_credential_in_parameters_is_rejected_before_any_record_bytes(tmp_path, monkeypatch):
    registry = security.KnownSecretRegistry()
    secret = "ordinary-write-private-value-" + "z" * 25
    registry.register(secret)
    monkeypatch.setattr(security, "_GLOBAL_REGISTRY", registry)
    root = tmp_path / "core"
    core = assemble_workspace_core(root, instance_id="core")
    try:
        command = _write_command(
            action="save_acceptance", request_id="unsafe", parameters=parameters("save_acceptance")
        )
        value = deepcopy(command.parameters)
        value["acceptance_scope"]["name"] = secret
        before = core.unit_of_work.current_commit_sequence()
        result = core.api.dispatch(
            command.model_copy(update={"parameters": value}),
            Session("agent", EntryKind.AGENT_RELAY),
        )
        assert result.error is not None and result.error.code == "B_RECORD_WRITE_UNVERIFIED"
        assert core.unit_of_work.current_commit_sequence() == before
        assert core.unit_of_work.repo.current_revision("acceptance_scope", "scope-1") == 0
    finally:
        core.lifetime_lock.release()
    assert not any(
        secret.encode() in path.read_bytes() for path in root.rglob("*") if path.is_file()
    )


def test_default_core_saves_and_reopens_all_seven_original_intents(tmp_path):
    root = tmp_path / "default-core"
    core = assemble_workspace_core(root, instance_id="first-core")
    session = Session("first-agent", EntryKind.AGENT_RELAY)
    originals = []
    try:
        # Project precedes its children; delivery refers to the exact saved task.
        for number, action in enumerate(ACTIONS):
            value = parameters(action)
            if action == "save_context":
                value["project"]["workspace_id"] = core.workspace.workspace_id
            command = _write_command(
                action=action, request_id=f"original-{number}", parameters=value
            )
            response = core.api.dispatch(command, session)
            assert response.error is None, response.error
            originals.append((command, response.result))
        before = core.unit_of_work.current_commit_sequence()
        assert before == len(ACTIONS) * 2
        assert FileCommitStore(root).read_current(verify_material=True) is not None
    finally:
        core.lifetime_lock.release()
    reopened = assemble_workspace_core(root, instance_id="new-core")
    try:
        for number, (command, original) in enumerate(originals):
            response = reopened.api.dispatch(
                command.model_copy(update={"request_id": f"reopen-{number}"}),
                Session("another-agent-entry", EntryKind.AGENT_RELAY),
            )
            assert response.error is None and response.result == original
        assert reopened.unit_of_work.current_commit_sequence() == before
        assert FileCommitStore(root).read_current(verify_material=True) is not None
    finally:
        reopened.lifetime_lock.release()


def test_legacy_record_without_an_intent_is_not_claimed_as_original(workspace_root):
    from aitest.application.planning.persistence import save_acceptance_scope
    from aitest.application.planning.serialization import acceptance_scope_from_payload

    stack = _start(workspace_root)
    save_acceptance_scope(
        acceptance_scope_from_payload(_scope_payload()),
        project_id=PROJECT_ID,
        unit_of_work=stack.unit_of_work,
    )
    raw = FileUnitOfWork(workspace_root)
    before = raw.current_commit_sequence()
    command = _write_command(
        action="save_acceptance", request_id="new-intent", parameters=parameters("save_acceptance")
    )
    result = _api(stack).dispatch(command, Session("agent", EntryKind.AGENT_RELAY))
    assert result.error is not None and result.error.code == "B_REVISION_CONFLICT"
    assert raw.current_commit_sequence() == before
    intent = RecordWriteIntent.from_command(command, reader=stack.reader, workspace_id=None)
    assert raw.repo.current_revision(KIND, intent.record_id) == 0


def test_json_object_order_is_equivalent_but_list_order_remains_frozen(workspace_root):
    api = _api(_start(workspace_root))
    session = Session("agent", EntryKind.AGENT_RELAY)
    command = _write_command(
        action="save_acceptance", request_id="original", parameters=parameters("save_acceptance")
    )
    value = deepcopy(command.parameters)
    value["acceptance_scope"]["required_case_ids"] = ["case-1", "case-2"]
    command = command.model_copy(update={"parameters": value})
    original = api.dispatch(command, session)
    assert original.error is None
    value = deepcopy(command.parameters)
    value["acceptance_scope"] = dict(reversed(list(value["acceptance_scope"].items())))
    equivalent = api.dispatch(
        command.model_copy(update={"request_id": "equivalent", "parameters": value}), session
    )
    assert equivalent.error is None and equivalent.result == original.result
    before = FileUnitOfWork(workspace_root).current_commit_sequence()
    value["acceptance_scope"]["required_case_ids"].reverse()
    changed = api.dispatch(
        command.model_copy(update={"request_id": "changed-order", "parameters": value}), session
    )
    assert changed.error is not None and changed.error.code == "INTENT_CONFLICT"
    assert FileUnitOfWork(workspace_root).current_commit_sequence() == before


def test_guard_change_after_preparation_does_not_publish_filtered_body_and_stale_digest(
    workspace_root, monkeypatch
):
    registry = security.KnownSecretRegistry()
    monkeypatch.setattr(security, "_GLOBAL_REGISTRY", registry)
    secret = "secret-known-after-preparation-" + "q" * 25
    command = _write_command(
        action="save_acceptance", request_id="original", parameters=parameters("save_acceptance")
    )
    value = deepcopy(command.parameters)
    value["acceptance_scope"]["name"] = secret
    command = command.model_copy(update={"parameters": value})
    prepare = RecordWriteIntent.prepare_payload

    def changed_guard(intent, payload):
        safe = prepare(intent, payload)
        registry.register(secret)
        return safe

    monkeypatch.setattr(RecordWriteIntent, "prepare_payload", changed_guard)
    stack = _start(workspace_root)
    before = FileUnitOfWork(workspace_root).current_commit_sequence()
    response = _api(stack).dispatch(command, Session("agent", EntryKind.AGENT_RELAY))
    assert response.error is not None
    raw = FileUnitOfWork(workspace_root)
    assert raw.current_commit_sequence() == before
    assert raw.repo.current_revision("acceptance_scope", "scope-1") == 0
    assert not any(
        secret.encode() in path.read_bytes() for path in workspace_root.rglob("*") if path.is_file()
    )
