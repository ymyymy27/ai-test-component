"""Real store and pinned bytes, with fixture gestures only (not actual user acceptance)."""

from dataclasses import replace

import pytest

from aitest.application.project.serialization import delivery_to_payload, task_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.project.context import AcceptanceItem, Delivery, SelfReport, Task
from aitest.interfaces.local.api import EntryKind, Session
from tests.support.controlled_binding import controlled_binding_confirm
from tests.unit.test_binding_controlled_origin import (
    analyzed,
    make_core,
)
from tests.unit.test_binding_controlled_origin import (
    command as binding_command,
)

HUMAN = Session("delivery-origin-fixture", EntryKind.HUMAN_UI, True)
RELAY = Session("delivery-agent", EntryKind.AGENT_RELAY)


def write(core, kind, identity, payload, *, expected=0, project="project"):
    unit = core.unit_of_work
    request = f"fixture-{kind}-{expected}"
    unit.begin(request, project, intent_id=f"intent-{request}")
    unit.stage_record(
        aggregate_kind=kind, record_id=identity, expected_revision=expected, payload=payload
    )
    unit.commit(request)


@pytest.fixture
def delivery_core(tmp_path):
    core, binding = make_core(tmp_path)
    assert controlled_binding_confirm(core, binding_command(binding), session=HUMAN).error is None
    source = analyzed(core)
    assert source.error is None
    task = Task(
        "task",
        "project",
        "verify ticket",
        "ticket scope",
        (
            AcceptanceItem("ticket-created", "actual ticket exists"),
            AcceptanceItem("ticket-rejected", "no rejected ticket is created", required=False),
        ),
    )
    draft = Delivery(
        "delivery",
        "task",
        "release-0.4",
        "python main.py",
        self_report=SelfReport(completed=("developer says ticket is complete",)),
        self_test_evidence=("developer log, not independently verified",),
    )
    task_response = core.api.dispatch(
        Command(
            action="save_task",
            project_id="project",
            request_id="save-task",
            intent_id="task-intent",
            expected_revision=0,
            parameters={"task": task_to_payload(task)},
        ),
        RELAY,
    )
    assert task_response.error is None
    delivery_response = core.api.dispatch(
        Command(
            action="save_delivery",
            project_id="project",
            request_id="save-draft",
            intent_id="draft-intent",
            expected_revision=0,
            parameters={
                "delivery": delivery_to_payload(draft, project_id="project"),
                "task_revision": 1,
            },
        ),
        RELAY,
    )
    assert delivery_response.error is None
    base = Command(
        action="submit_delivery",
        target="submission",
        project_id="project",
        request_id="submit-request",
        intent_id="submit-intent",
        expected_revision=0,
        parameters={
            "project_revision": 1,
            "expected_revision": 0,
            "submission_id": "submission",
            "delivery_ref": {"delivery_id": "delivery", "record_revision": 1},
            "source_ref": {"snapshot_id": source.result["snapshot_id"], "record_revision": 1},
        },
    )
    try:
        yield core, base, binding, draft, task, source.result
    finally:
        core.lifetime_lock.release()


def prepare(core, base):
    return core.api.dispatch(
        Command(
            action="prepare_approval",
            project_id="project",
            request_id="review-" + base.request_id,
            intent_id="review-" + base.intent_id,
            expected_revision=0,
            parameters={
                "action": base.action,
                "action_intent_id": base.intent_id,
                "target": base.target,
                "parameters": base.parameters,
            },
        ),
        HUMAN,
    )


def confirm(core, base, challenge):
    approved = base.model_copy(
        update={
            "parameters": dict(base.parameters)
            | {"approval_challenge_id": challenge["challenge_id"]}
        }
    )
    return core.api.dispatch_user_confirmation(
        approved,
        HUMAN,
        challenge_id=challenge["challenge_id"],
        input_digest=challenge["basis"]["input_digest"],
    )


def test_default_submission_is_negotiated_but_labels_and_agents_cannot_confirm(delivery_core):
    core, base, *_ = delivery_core
    doctor = core.api.dispatch(Command(action="doctor", request_id="doctor"), RELAY)
    assert "submit_delivery" in doctor.result["supported_actions"]
    for session in (RELAY, HUMAN):
        response = core.api.dispatch(base, session)
        assert response.error is not None
    assert core.unit_of_work.repo.current_revision("delivery_submission", "submission") == 0


def test_formal_record_freezes_five_materials_and_keeps_every_scope_unverified(delivery_core):
    core, base, _, draft, *_ = delivery_core
    challenge = prepare(core, base)
    assert challenge.error is None
    materials = challenge.result["basis"]["materials"]
    assert {item["aggregate_kind"] for item in materials} == {
        "project",
        "delivery",
        "task",
        "source_snapshot",
        "binding",
    }
    before = core.unit_of_work.current_commit_sequence()
    response = confirm(core, base, challenge.result)
    assert response.error is None
    assert response.result["status"] == "submitted"
    assert response.result["verification_state"] == "unverified"
    assert response.result["verified_in_scope"] == []
    assert response.result["unverified_scope"] == ["ticket-created", "ticket-rejected"]
    assert core.unit_of_work.current_commit_sequence() == before + 6
    saved = core.unit_of_work.repo.read(
        aggregate_kind="delivery_submission", record_id="submission", revision=1
    ).payload
    assert saved["version"] == draft.version
    assert saved["content_identity"] == response.result["content_identity"]
    assert saved["delivery_record_revision"] == saved["task_record_revision"] == 1
    assert saved["approval_commit_seq"] == str(before + 6)
    assert "verified_in_scope" not in saved
    assert core.unit_of_work.repo.current_revision("delivery", "delivery") == 1
    query = core.api.dispatch(
        Command(
            action="query",
            project_id="project",
            request_id="list-submissions",
            parameters={"aggregate_kind": "delivery_submission", "limit": 1},
        ),
        RELAY,
    )
    assert query.error is None
    assert query.result["items"] == [
        {"aggregate_kind": "delivery_submission", "record_id": "submission", "revision": 1}
    ]


@pytest.mark.parametrize("changed", ["delivery", "task", "binding", "project"])
def test_changed_material_invalidates_gesture_without_consuming_challenge(delivery_core, changed):
    core, base, *_ = delivery_core
    challenge = prepare(core, base)
    assert challenge.error is None
    identity = {"delivery": "delivery", "task": "task", "binding": "binding", "project": "project"}[
        changed
    ]
    raw = core.unit_of_work.repo.read(
        aggregate_kind=changed, record_id=identity, revision=1
    ).payload
    write(core, changed, identity, dict(raw) | {"revision": 2}, expected=1)
    before = core.unit_of_work.current_commit_sequence()
    response = confirm(core, base, challenge.result)
    assert response.error is not None
    assert core.unit_of_work.current_commit_sequence() == before
    assert (
        core.unit_of_work.repo.current_revision(
            "approval_challenge", challenge.result["challenge_id"]
        )
        == 1
    )


@pytest.mark.parametrize("fault", [1, 2, 3, 4, 5, 6, "before_commit"])
def test_each_batch_fault_rolls_back_formal_record_and_gesture(delivery_core, monkeypatch, fault):
    core, base, *_ = delivery_core
    challenge = prepare(core, base)
    assert challenge.error is None
    unit, count = core.unit_of_work, 0
    stage, commit = unit.stage_record, unit.commit
    before = unit.current_commit_sequence()

    def staged(**kwargs):
        nonlocal count
        count += 1
        if count == fault:
            raise OSError("synthetic submission stage fault")
        return stage(**kwargs)

    def committed(*args, **kwargs):
        if fault == "before_commit":
            raise OSError("synthetic submission publish fault")
        return commit(*args, **kwargs)

    monkeypatch.setattr(unit, "stage_record", staged)
    monkeypatch.setattr(unit, "commit", committed)
    assert confirm(core, base, challenge.result).error is not None
    assert unit.current_commit_sequence() == before
    assert unit.repo.current_revision("delivery_submission", "submission") == 0
    assert unit.repo.current_revision("approval_challenge", challenge.result["challenge_id"]) == 1
    monkeypatch.setattr(unit, "stage_record", stage)
    monkeypatch.setattr(unit, "commit", commit)
    assert (
        confirm(
            core, base.model_copy(update={"request_id": "retry-submit"}), challenge.result
        ).error
        is None
    )
    assert unit.current_commit_sequence() == before + 6


def test_lost_reply_and_restart_read_original_after_draft_and_current_source_change(
    delivery_core, monkeypatch
):
    core, base, binding, draft, *_ = delivery_core
    challenge = prepare(core, base)
    assert challenge.error is None
    unit, commit = core.unit_of_work, core.unit_of_work.commit

    def lost(*args, **kwargs):
        commit(*args, **kwargs)
        raise OSError("synthetic lost submission response")

    monkeypatch.setattr(unit, "commit", lost)
    assert confirm(core, base, challenge.result).error is not None
    monkeypatch.setattr(unit, "commit", commit)
    raw = delivery_to_payload(
        replace(draft, version="later-version", revision=2), project_id="project"
    )
    write(core, "delivery", "delivery", raw | {"task_revision": 1}, expected=1)
    newer_binding = binding_command(
        replace(binding, binding_revision=7), intent="later-binding-intent", request="later-binding"
    )
    newer_binding = newer_binding.model_copy(
        update={
            "expected_revision": 1,
            "parameters": dict(newer_binding.parameters) | {"expected_revision": 1},
        }
    )
    assert controlled_binding_confirm(core, newer_binding, session=HUMAN).error is None
    source_path = core.workspace.root.parent / "tested" / "main.py"
    source_path.write_text("VALUE = 2\n", encoding="utf-8")
    root = core.workspace.root
    core.lifetime_lock.release()
    restarted = assemble_workspace_core(root, instance_id="delivery-restarted")
    try:
        # A historical replay must neither inspect the changed directory nor pin it again.
        def forbidden(*args, **kwargs):
            raise AssertionError("original submission must only read original saved source")

        monkeypatch.setattr(restarted.snapshot_store, "pin", forbidden)
        monkeypatch.setattr(restarted.snapshot_store, "detect_changes", forbidden)
        before = restarted.unit_of_work.current_commit_sequence()
        replay = restarted.api.dispatch(
            base.model_copy(update={"request_id": "original-after-restart"}), HUMAN
        )
        assert replay.error is None
        assert replay.result["version"] == draft.version
        assert replay.result["verification_state"] == "unverified"
        assert restarted.unit_of_work.current_commit_sequence() == before
        assert (
            restarted.unit_of_work.repo.current_revision("delivery_submission", "submission") == 1
        )
    finally:
        restarted.lifetime_lock.release()


@pytest.mark.parametrize(
    "invalid",
    [
        "unknown_field",
        "bool_revision",
        "missing_task_ref",
        "self_verified",
        "foreign_delivery",
        "wrong_target",
    ],
)
def test_invalid_or_legacy_material_cannot_upgrade_to_formal(delivery_core, invalid):
    core, base, _, draft, *_ = delivery_core
    params = dict(base.parameters)
    if invalid == "unknown_field":
        params["verified_in_scope"] = ["ticket-created"]
    elif invalid == "bool_revision":
        params["delivery_ref"] = {"delivery_id": "delivery", "record_revision": True}
    elif invalid in {"missing_task_ref", "self_verified", "foreign_delivery"}:
        raw = delivery_to_payload(
            draft, project_id="other" if invalid == "foreign_delivery" else "project"
        )
        if invalid != "missing_task_ref":
            raw["task_revision"] = 1
        if invalid == "self_verified":
            raw["verified_in_scope"] = ["ticket-created"]
        write(
            core,
            "delivery",
            "legacy",
            raw | {"delivery_id": "legacy"},
            project="other" if invalid == "foreign_delivery" else "project",
        )
        params["delivery_ref"] = {"delivery_id": "legacy", "record_revision": 1}
    bad = base.model_copy(update={"parameters": params})
    if invalid == "wrong_target":
        bad = bad.model_copy(update={"target": "another-submission"})
        assert core.api.dispatch(bad, HUMAN).error is not None
        return
    before = core.unit_of_work.current_commit_sequence()
    assert prepare(core, bad).error is not None
    assert core.unit_of_work.current_commit_sequence() == before


def test_same_version_text_does_not_mean_same_source_and_fixed_corruption_blocks(delivery_core):
    core, base, _, _, _, first_source = delivery_core
    challenge = prepare(core, base)
    assert challenge.error is None
    assert confirm(core, base, challenge.result).error is None
    first = core.api.dispatch(base.model_copy(update={"request_id": "read-first"}), HUMAN)
    (core.workspace.root.parent / "tested" / "main.py").write_text("VALUE = 3\n", encoding="utf-8")
    next_source = core.api.dispatch(
        Command(
            action="analyze_project",
            project_id="project",
            request_id="new-source",
            intent_id="new-source-intent",
            expected_revision=first_source["source_current_ref"]["record_revision"],
            binding_revision=1,
            parameters={"binding_id": "binding", "purpose": "prepare", "source_scope": "."},
        ),
        RELAY,
    )
    assert next_source.error is None
    assert first_source["record_revision"] == next_source.result["record_revision"] == 1
    assert next_source.result["source_current_ref"]["record_revision"] == 2
    before = core.unit_of_work.current_commit_sequence()
    conflict = core.api.dispatch(
        base.model_copy(
            update={
                "request_id": "conflicting-original",
                "parameters": dict(base.parameters)
                | {
                    "source_ref": {
                        "snapshot_id": next_source.result["snapshot_id"],
                        "record_revision": 1,
                    },
                },
            }
        ),
        HUMAN,
    )
    assert conflict.error is not None and conflict.error.code == "INTENT_CONFLICT"
    assert core.unit_of_work.current_commit_sequence() == before
    second = base.model_copy(
        update={
            "target": "submission-two",
            "request_id": "submit-two",
            "intent_id": "submit-two-intent",
            "parameters": dict(base.parameters)
            | {
                "submission_id": "submission-two",
                "source_ref": {
                    "snapshot_id": next_source.result["snapshot_id"],
                    "record_revision": 1,
                },
            },
        }
    )
    challenge2 = prepare(core, second.model_copy(update={"request_id": "review-two"}))
    assert challenge2.error is None
    result = confirm(core, second, challenge2.result)
    assert result.error is None
    assert first.result["version"] == result.result["version"]
    assert first.result["content_identity"] != result.result["content_identity"]
    blobs = list((core.workspace.root / "snapshots" / "blobs").rglob("*"))
    files = [path for path in blobs if path.is_file()]
    assert files
    for path in files:
        path.write_bytes(b"corrupted fixed source")
    before = core.unit_of_work.current_commit_sequence()
    replay = core.api.dispatch(base.model_copy(update={"request_id": "broken-original"}), HUMAN)
    assert replay.error is not None
    assert core.unit_of_work.current_commit_sequence() == before


def test_stale_source_pointer_is_rejected_before_actual_pin(delivery_core, monkeypatch):
    core, _, _, _, _, first_source = delivery_core

    def forbidden(**kwargs):
        raise AssertionError("stale source CAS must not pin more material")

    monkeypatch.setattr(core.snapshot_store, "pin", forbidden)
    before = core.unit_of_work.current_commit_sequence()
    stale = core.api.dispatch(
        Command(
            action="analyze_project",
            project_id="project",
            request_id="stale-source",
            intent_id="stale-source-intent",
            expected_revision=0,
            binding_revision=1,
            parameters={"binding_id": "binding", "purpose": "prepare", "source_scope": "."},
        ),
        RELAY,
    )
    assert stale.error is not None and stale.error.code == "B_SOURCE_UNVERIFIED"
    assert first_source["source_current_ref"]["record_revision"] == 1
    assert core.unit_of_work.current_commit_sequence() == before


def test_source_damage_after_confirmation_cannot_publish_or_consume_gesture(
    delivery_core, monkeypatch
):
    core, base, *_ = delivery_core
    challenge = prepare(core, base)
    assert challenge.error is None
    paths = [path for path in (core.workspace.root / "snapshots/blobs").iterdir() if path.is_file()]
    assert paths
    original = {path: path.read_bytes() for path in paths}
    unit, commit = core.unit_of_work, core.unit_of_work.commit

    def damage_before_commit(*args, **kwargs):
        for path in paths:
            path.write_bytes(b"damaged after approval")
        return commit(*args, **kwargs)

    monkeypatch.setattr(unit, "commit", damage_before_commit)
    before = unit.current_commit_sequence()
    response = confirm(core, base, challenge.result)
    assert response.error is not None
    assert unit.current_commit_sequence() == before
    assert unit.repo.current_revision("delivery_submission", "submission") == 0
    assert unit.repo.current_revision("approval_challenge", challenge.result["challenge_id"]) == 1
    # Restoring this test's exact original bytes proves that the same pending gesture is reusable.
    for path, raw in original.items():
        path.write_bytes(raw)
    monkeypatch.setattr(unit, "commit", commit)
    retry = confirm(
        core, base.model_copy(update={"request_id": "after-fixture-restoration"}), challenge.result
    )
    assert retry.error is None
    from aitest.infrastructure.file_store.commit_manifest import FileCommitStore

    current = FileCommitStore(core.workspace.root).read_current(verify_material=True)
    assert current is not None
    assert {path.relative_to(core.workspace.root).as_posix() for path in paths} <= set(
        current["manifest"]["source_material_files"]
    )
