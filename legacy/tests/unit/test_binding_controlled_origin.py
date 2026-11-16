"""Binding confirmation must be saved and consumed as exact controlled origin."""

from dataclasses import replace

import pytest

from aitest.application.approval_service import _digest
from aitest.application.project.serialization import binding_to_payload, project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.project.context import BindingForm, LocalProject, LocalProjectBinding
from aitest.interfaces.local.api import EntryKind, Session
from tests.support.controlled_binding import controlled_binding_confirm, prepare_binding_challenge
from tests.unit.test_authoritative_preparation import authoritative as authoritative
from tests.unit.test_authoritative_preparation import prepare

HUMAN = Session("binding-origin-fixture", EntryKind.HUMAN_UI, True)


def make_core(tmp_path):
    source = tmp_path / "tested"
    source.mkdir()
    (source / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    core = assemble_workspace_core(tmp_path / "workspace", instance_id="binding-origin-core")
    project = LocalProject("project", core.workspace.workspace_id, "binding", "goal", "0")
    response = core.api.dispatch(
        Command(
            action="save_context",
            project_id="project",
            request_id="owner-save",
            intent_id="owner-intent",
            expected_revision=0,
            parameters={"project": project_to_payload(project)},
        ),
        HUMAN,
    )
    assert response.error is None
    return core, LocalProjectBinding(
        "binding",
        1,
        "project",
        source.as_posix(),
        BindingForm.PLAIN,
        manifest_digest="declared-baseline",
        confirmed=True,
    )


def command(binding, *, intent="binding-intent", request="binding-request"):
    return Command(
        action="save_binding",
        project_id="project",
        intent_id=intent,
        request_id=request,
        expected_revision=0,
        parameters={
            "project_revision": 1,
            "expected_revision": 0,
            "binding": binding_to_payload(binding),
        },
    )


def test_a_bare_human_label_cannot_create_confirmed_binding(tmp_path):
    core, binding = make_core(tmp_path)
    try:
        before = core.unit_of_work.current_commit_sequence()
        response = core.api.dispatch(command(binding), HUMAN)
        assert response.error is not None
        assert response.error.code == "AWAITING_USER_CONFIRMATION"
        assert core.unit_of_work.repo.current_revision("binding", "binding") == 0
        assert core.unit_of_work.current_commit_sequence() == before
    finally:
        core.lifetime_lock.release()


def test_legacy_confirmed_binding_is_not_permission_to_pin_source(tmp_path, monkeypatch):
    core, binding = make_core(tmp_path)
    try:
        unit = core.unit_of_work
        unit.begin("legacy-binding", "project", intent_id="legacy-binding-intent")
        unit.stage_record(
            aggregate_kind="binding",
            record_id="binding",
            expected_revision=0,
            payload=binding_to_payload(binding),
        )
        unit.commit("legacy-binding")

        def forbidden(**kwargs):
            raise AssertionError("source pinning must not precede controlled origin validation")

        monkeypatch.setattr(core.snapshot_store, "pin", forbidden)
        before = unit.current_commit_sequence()
        response = core.api.dispatch(
            Command(
                action="analyze_project",
                project_id="project",
                request_id="legacy-pin",
                intent_id="legacy-pin-intent",
                expected_revision=0,
                binding_revision=1,
                parameters={"binding_id": "binding", "purpose": "prepare", "source_scope": "."},
            ),
            Session("binding-relay", EntryKind.AGENT_RELAY),
        )
        assert response.error is not None
        assert response.error.code == "B_SOURCE_UNVERIFIED"
        assert unit.current_commit_sequence() == before
    finally:
        core.lifetime_lock.release()


@pytest.fixture
def binding_core(tmp_path):
    core, binding = make_core(tmp_path)
    yield core, binding
    core.lifetime_lock.release()


def analyzed(core, *, request="proof-pin", intent="proof-pin-intent", purpose="prepare"):
    return core.api.dispatch(
        Command(
            action="analyze_project",
            project_id="project",
            request_id=request,
            intent_id=intent,
            expected_revision=0,
            binding_revision=1,
            parameters={"binding_id": "binding", "purpose": purpose, "source_scope": "."},
        ),
        Session("binding-proof-relay", EntryKind.AGENT_RELAY),
    )


def test_binding_save_and_confirmation_share_exact_six_record_commit(binding_core):
    core, binding = binding_core
    challenge = prepare_binding_challenge(core, command(binding), HUMAN)
    assert challenge.error is None
    before = core.unit_of_work.current_commit_sequence()
    approved = command(binding).model_copy(
        update={
            "parameters": dict(command(binding).parameters)
            | {"approval_challenge_id": challenge.result["challenge_id"]}
        }
    )
    result = core.api.dispatch_user_confirmation(
        approved,
        HUMAN,
        challenge_id=challenge.result["challenge_id"],
        input_digest=challenge.result["basis"]["input_digest"],
    )
    assert result.error is None
    unit = core.unit_of_work
    payload = unit.repo.read(aggregate_kind="binding", record_id="binding", revision=1).payload
    origin = unit.repo.read(
        aggregate_kind="approval_confirmation",
        record_id=payload["approval_confirmation_id"],
        revision=1,
    ).payload["confirmation"]
    assert unit.current_commit_sequence() == before + 6
    assert payload["approval_commit_seq"] == origin["confirmed_at_commit"] == str(before + 6)
    assert analyzed(core).error is None


@pytest.mark.parametrize("fault", [1, 2, 3, 4, 5, 6, "before_commit"])
def test_binding_batch_failure_keeps_challenge_pending_and_retry_exact(
    binding_core, monkeypatch, fault
):
    core, binding = binding_core
    challenge = prepare_binding_challenge(core, command(binding), HUMAN)
    assert challenge.error is None
    approved = command(binding).model_copy(
        update={
            "parameters": dict(command(binding).parameters)
            | {"approval_challenge_id": challenge.result["challenge_id"]}
        }
    )
    unit = core.unit_of_work
    stage, commit = unit.stage_record, unit.commit
    before = unit.current_commit_sequence()
    count = 0

    def stage_fault(**kwargs):
        nonlocal count
        count += 1
        if count == fault:
            raise OSError("synthetic binding stage fault")
        return stage(**kwargs)

    def commit_fault(*args, **kwargs):
        if fault == "before_commit":
            raise OSError("synthetic binding publish fault")
        return commit(*args, **kwargs)

    monkeypatch.setattr(unit, "stage_record", stage_fault)
    monkeypatch.setattr(unit, "commit", commit_fault)
    result = core.api.dispatch_user_confirmation(
        approved,
        HUMAN,
        challenge_id=challenge.result["challenge_id"],
        input_digest=challenge.result["basis"]["input_digest"],
    )
    assert result.error is not None
    assert unit.current_commit_sequence() == before
    assert unit.repo.current_revision("binding", "binding") == 0
    assert unit.repo.current_revision("approval_challenge", challenge.result["challenge_id"]) == 1
    monkeypatch.setattr(unit, "stage_record", stage)
    monkeypatch.setattr(unit, "commit", commit)
    replay = core.api.dispatch_user_confirmation(
        approved.model_copy(update={"request_id": "retry-binding"}),
        HUMAN,
        challenge_id=challenge.result["challenge_id"],
        input_digest=challenge.result["basis"]["input_digest"],
    )
    assert replay.error is None
    assert unit.current_commit_sequence() == before + 6


def test_lost_binding_save_reply_is_exact_original_without_second_event(binding_core, monkeypatch):
    core, binding = binding_core
    unit = core.unit_of_work
    commit = unit.commit

    def lost(*args, **kwargs):
        commit(*args, **kwargs)
        raise OSError("synthetic lost response after publication")

    challenge = prepare_binding_challenge(core, command(binding), HUMAN)
    assert challenge.error is None
    monkeypatch.setattr(unit, "commit", lost)
    approved = command(binding).model_copy(
        update={
            "parameters": dict(command(binding).parameters)
            | {"approval_challenge_id": challenge.result["challenge_id"]}
        }
    )
    result = core.api.dispatch_user_confirmation(
        approved,
        HUMAN,
        challenge_id=challenge.result["challenge_id"],
        input_digest=challenge.result["basis"]["input_digest"],
    )
    assert result.error is not None
    monkeypatch.setattr(unit, "commit", commit)
    before = unit.current_commit_sequence()
    replay = core.api.dispatch(command(binding, request="read-original"), HUMAN)
    assert replay.error is None and replay.result["revision"] == 1
    assert unit.current_commit_sequence() == before


@pytest.mark.parametrize(
    "damage",
    [
        "path",
        "confirmed",
        "origin",
        "missing_interaction",
        "receipt_digest",
        "receipt_revision_bool",
        "receipt_other_intent",
        "schema",
    ],
)
def test_binding_origin_damage_blocks_pin_before_files_and_no_commit(
    binding_core, monkeypatch, damage
):
    core, binding = binding_core
    assert controlled_binding_confirm(core, command(binding), session=HUMAN).error is None
    unit = core.unit_of_work
    read = unit.repo.read

    def damaged(**kwargs):
        record = read(**kwargs)
        raw = dict(record.payload)
        if record.aggregate_kind == "binding":
            if damage == "path":
                raw["canonical_path"] = raw["canonical_path"] + "/elsewhere"
            elif damage == "confirmed":
                raw["confirmed"] = False
            elif damage == "origin":
                raw["approval_confirmation_id"] = "another-confirmation"
            elif damage == "schema":
                raw["schema_version"] = "aitest.binding/999"
        if record.aggregate_kind == "approval_interaction" and damage == "missing_interaction":
            raise KeyError("missing exact interaction")
        if raw.get("schema_version") == "aitest.controlled-write-intent/1.0":
            if damage == "receipt_digest":
                raw["record_digest"] = "sha256:" + "0" * 64
            elif damage == "receipt_revision_bool":
                raw["record_revision"] = True
            elif damage == "receipt_other_intent":
                raw["intent_id"] = "other"
        return replace(record, payload=raw)

    monkeypatch.setattr(unit.repo, "read", damaged)

    def forbidden(**kwargs):
        pytest.fail("unverified binding reached actual source pin")

    monkeypatch.setattr(core.snapshot_store, "pin", forbidden)
    before = unit.current_commit_sequence()
    result = analyzed(core)
    assert result.error is not None and result.error.code == "B_SOURCE_UNVERIFIED"
    assert unit.current_commit_sequence() == before


@pytest.mark.parametrize("change", ["path", "confirmed", "owner_revision", "warehouse_revision"])
def test_binding_challenge_cannot_be_used_after_input_changes(binding_core, change):
    core, binding = binding_core
    base = command(binding)
    challenge = prepare_binding_challenge(core, base, HUMAN)
    assert challenge.error is None
    parameters = dict(base.parameters)
    parameters["binding"] = dict(parameters["binding"])
    outer_expected = 0
    if change == "path":
        parameters["binding"]["canonical_path"] += "/elsewhere"
    elif change == "confirmed":
        parameters["binding"]["confirmed"] = False
    elif change == "owner_revision":
        parameters["project_revision"] = 2
    else:
        parameters["expected_revision"] = outer_expected = 1
    parameters["approval_challenge_id"] = challenge.result["challenge_id"]
    before = core.unit_of_work.current_commit_sequence()
    result = core.api.dispatch_user_confirmation(
        base.model_copy(update={"parameters": parameters, "expected_revision": outer_expected}),
        HUMAN,
        challenge_id=challenge.result["challenge_id"],
        input_digest=challenge.result["basis"]["input_digest"],
    )
    assert result.error is not None
    assert core.unit_of_work.current_commit_sequence() == before
    assert (
        core.unit_of_work.repo.current_revision(
            "approval_challenge", challenge.result["challenge_id"]
        )
        == 1
    )


def test_original_binding_save_cannot_be_redirected_and_reads_after_restart(
    binding_core, monkeypatch
):
    core, binding = binding_core
    base = command(binding)
    assert controlled_binding_confirm(core, base, session=HUMAN).error is None
    next_command = command(
        replace(binding, binding_revision=7), intent="another-binding", request="other-save"
    )
    next_command = next_command.model_copy(
        update={
            "expected_revision": 1,
            "parameters": dict(next_command.parameters) | {"expected_revision": 1},
        }
    )
    second = controlled_binding_confirm(core, next_command, session=HUMAN)
    assert second.error is None and second.result["revision"] == 2
    unit, root = core.unit_of_work, core.workspace.root
    before = unit.current_commit_sequence()
    replay = core.api.dispatch(base.model_copy(update={"request_id": "old-binding-read"}), HUMAN)
    assert replay.error is None and replay.result["revision"] == 1
    read = unit.repo.read
    other = read(aggregate_kind="binding", record_id="binding", revision=2).payload

    def redirected(**kwargs):
        record = read(**kwargs)
        raw = dict(record.payload)
        if (raw.get("schema_version"), raw.get("intent_id")) == (
            "aitest.controlled-write-intent/1.0",
            "binding-intent",
        ):
            raw.update(
                record_revision=2,
                record_digest=_digest(other),
                confirmation_id=other["approval_confirmation_id"],
            )
        return replace(record, payload=raw)

    monkeypatch.setattr(unit.repo, "read", redirected)
    refused = core.api.dispatch(base.model_copy(update={"request_id": "wrong-result-read"}), HUMAN)
    assert refused.error is not None and refused.error.code == "AWAITING_USER_CONFIRMATION"
    assert unit.current_commit_sequence() == before
    monkeypatch.setattr(unit.repo, "read", read)
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(root, instance_id="binding-origin-restart")
    try:
        recalled = restarted.api.dispatch(
            base.model_copy(update={"request_id": "restarted-read"}), HUMAN
        )
        assert recalled.error is None and recalled.result["revision"] == 1
        assert restarted.unit_of_work.current_commit_sequence() == before
    finally:
        restarted.lifetime_lock.release()


def test_saved_snapshot_check_revalidates_binding_before_actual_files(binding_core, monkeypatch):
    core, binding = binding_core
    assert controlled_binding_confirm(core, command(binding), session=HUMAN).error is None
    pinned = analyzed(core)
    assert pinned.error is None
    read = core.unit_of_work.repo.read

    def damaged(**kwargs):
        record = read(**kwargs)
        if record.aggregate_kind == "binding":
            return replace(
                record, payload=dict(record.payload) | {"approval_confirmation_id": "missing"}
            )
        return record

    monkeypatch.setattr(core.unit_of_work.repo, "read", damaged)

    def forbidden(*args, **kwargs):
        pytest.fail("snapshot bytes must not be verified through unproved binding permission")

    monkeypatch.setattr(core.snapshot_store, "verify_pinned", forbidden)
    before = core.unit_of_work.current_commit_sequence()
    result = core.api.dispatch(
        Command(
            action="check_source",
            project_id="project",
            request_id="check-saved-binding",
            parameters={"snapshot_id": pinned.result["snapshot_id"], "revision": 1},
        ),
        Session("binding-check-relay", EntryKind.AGENT_RELAY),
    )
    assert result.error is not None and result.error.code == "B_SOURCE_UNVERIFIED"
    assert core.unit_of_work.current_commit_sequence() == before


def test_pin_publish_revalidates_origin_inside_short_transaction(binding_core, monkeypatch):
    core, binding = binding_core
    assert controlled_binding_confirm(core, command(binding), session=HUMAN).error is None
    unit = core.unit_of_work
    read, pin = unit.repo.read, core.snapshot_store.pin

    def damaged(**kwargs):
        record = read(**kwargs)
        raw = dict(record.payload)
        if raw.get("schema_version") == "aitest.controlled-write-intent/1.0":
            raw["record_digest"] = "sha256:" + "0" * 64
        return replace(record, payload=raw)

    def after_pin(**kwargs):
        result = pin(**kwargs)
        monkeypatch.setattr(unit.repo, "read", damaged)
        return result

    monkeypatch.setattr(core.snapshot_store, "pin", after_pin)
    before = unit.current_commit_sequence()
    result = analyzed(core)
    assert result.error is not None and result.error.code == "B_SOURCE_UNVERIFIED"
    assert unit.current_commit_sequence() == before


def test_default_prepare_rejects_legacy_binding_even_with_current_valid_bytes(
    authoritative, monkeypatch
):
    core, inputs, _ = authoritative
    read = core.unit_of_work.repo.read

    def legacy(**kwargs):
        record = read(**kwargs)
        if record.aggregate_kind == "binding":
            payload = {
                key: value
                for key, value in record.payload.items()
                if not key.startswith("approval_") and key != "workspace_id"
            }
            return replace(record, payload=payload)
        return record

    monkeypatch.setattr(core.unit_of_work.repo, "read", legacy)
    before = core.unit_of_work.current_commit_sequence()
    result = prepare(
        core, inputs, request="legacy-binding-prepare-request", intent="legacy-binding-prepare"
    )
    assert result.error is None and result.result["status"] == "blocked"
    assert any(reason["code"] == "basis_unverified" for reason in result.result["blocking_reasons"])
    assert core.unit_of_work.current_commit_sequence() == before


def test_original_binding_intent_with_different_input_is_conflict(binding_core):
    core, binding = binding_core
    assert controlled_binding_confirm(core, command(binding), session=HUMAN).error is None
    before = core.unit_of_work.current_commit_sequence()
    changed = command(replace(binding, confirmed=False), request="changed-binding-input")
    result = core.api.dispatch(changed, HUMAN)
    assert result.error is not None and result.error.code == "INTENT_CONFLICT"
    assert core.unit_of_work.current_commit_sequence() == before
