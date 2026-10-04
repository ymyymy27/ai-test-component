"""Default API source pinning uses actual files and durable business identity."""

from __future__ import annotations

import subprocess

import pytest

from aitest.application.planning.substrate_adapter import PortsRecordReader
from aitest.application.project.persistence import load_source_snapshot
from aitest.application.project.serialization import binding_to_payload, project_to_payload
from aitest.bootstrap import assemble_workspace_core
from aitest.contracts.commands import Command
from aitest.domain.project.context import BindingForm, LocalProject, LocalProjectBinding
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.interfaces.local.api import EntryKind, Session

HUMAN = Session("controlled-fixture", EntryKind.HUMAN_UI)
AGENT = Session("relay-fixture", EntryKind.AGENT_RELAY)


def dispatch(
    core,
    action,
    *,
    project="project",
    parameters=None,
    intent=None,
    request=None,
    expected=0,
    binding_revision=None,
    session=HUMAN,
):
    return core.api.dispatch(
        Command(
            action=action,
            request_id=request or action + "-request",
            project_id=project,
            intent_id=intent or action + "-intent",
            expected_revision=expected,
            binding_revision=binding_revision,
            parameters=parameters or {},
        ),
        session,
    )


def seed(core, source, *, project="project", form=BindingForm.PLAIN, head=None):
    local = LocalProject(project, core.workspace.workspace_id, project, "fixture goal", "1")
    response = dispatch(
        core,
        "save_context",
        project=project,
        intent="save-" + project,
        request="request-" + project,
        parameters={"project": project_to_payload(local)},
    )
    assert response.error is None
    binding = LocalProjectBinding(
        "binding-" + project,
        1,
        project,
        source.as_posix(),
        form,
        repository_id="local-repository" if form is BindingForm.GIT else None,
        branch="main" if form is BindingForm.GIT else None,
        base_commit=head if form is BindingForm.GIT else None,
        manifest_digest="declared-baseline" if form is BindingForm.PLAIN else None,
        confirmed=True,
    )
    response = dispatch(
        core,
        "save_binding",
        project=project,
        intent="bind-" + project,
        request="request-bind-" + project,
        parameters={"binding": binding_to_payload(binding)},
    )
    assert response.error is None
    return binding


def analyze(
    core,
    *,
    project="project",
    purpose="prepare",
    intent="pin-intent",
    request="pin-request",
    expected=0,
):
    return dispatch(
        core,
        "analyze_project",
        project=project,
        intent=intent,
        request=request,
        binding_revision=1,
        expected=expected,
        parameters={
            "binding_id": "binding-" + project,
            "purpose": purpose,
            "source_scope": ".",
            "exclusion_rules": [".git"],
        },
    )


@pytest.fixture
def stack(tmp_path):
    source = tmp_path / "tested"
    source.mkdir()
    (source / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    root = tmp_path / "workspace"
    core = assemble_workspace_core(root, instance_id="source-core")
    yield core, source, root
    core.lifetime_lock.release()


def test_default_plain_pin_saved_identity_matches_actual_byte_digest(stack, monkeypatch):
    core, source, root = stack
    seed(core, source)
    from aitest.infrastructure.adapters.source_control import GitSourceControl

    def no_git(*args, **kwargs):
        pytest.fail("plain source must not invoke Git")

    monkeypatch.setattr(GitSourceControl, "snapshot_identity", no_git)
    response = analyze(core)
    assert response.error is None
    result = response.result
    manifest = load_source_snapshot(
        PortsRecordReader(core.unit_of_work.repo),
        project_id="project",
        snapshot_id=result["snapshot_id"],
        revision=1,
    )
    import hashlib

    actual = (source / "main.py").read_bytes()
    assert manifest.files[0].content_digest == "sha256:" + hashlib.sha256(actual).hexdigest()
    assert manifest.files[0].size == len(actual)
    pinned = FileSourceSnapshotStore(root).read_pinned(result["pinned_snapshot_id"])
    assert pinned["files"][0]["sha256"] == manifest.files[0].content_digest[7:]


def test_original_source_intent_reads_after_restart_without_repin(stack, monkeypatch):
    core, source, root = stack
    seed(core, source)
    first = analyze(core)
    assert first.error is None
    core.lifetime_lock.release()
    second = assemble_workspace_core(root, instance_id="restarted-core")
    try:
        (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")

        def no_repin(*args, **kwargs):
            pytest.fail("same intent must read original result")

        monkeypatch.setattr(FileSourceSnapshotStore, "pin", no_repin)
        sequence = second.unit_of_work.current_commit_sequence()
        repeated = analyze(second, request="second-transport-request")
        assert repeated.error is None
        assert repeated.result == {**first.result, "reused": True}
        assert second.unit_of_work.current_commit_sequence() == sequence
    finally:
        second.lifetime_lock.release()


def test_source_intent_different_input_conflicts_without_new_commit(stack):
    core, source, _root = stack
    seed(core, source)
    assert analyze(core).error is None
    sequence = core.unit_of_work.current_commit_sequence()
    changed = analyze(core, purpose="analysis", request="different-request")
    assert changed.error.code == "INTENT_CONFLICT"
    assert core.unit_of_work.current_commit_sequence() == sequence


def test_same_physical_source_has_project_owned_business_snapshot_ids(stack):
    core, source, _root = stack
    seed(core, source)
    seed(core, source, project="other")
    one = analyze(core)
    two = analyze(core, project="other", intent="other-pin", request="other-pin-request")
    assert one.error is None and two.error is None
    assert one.result["pinned_snapshot_id"] == two.result["pinned_snapshot_id"]
    assert one.result["snapshot_id"] != two.result["snapshot_id"]
    cross = dispatch(
        core,
        "check_source",
        project="other",
        parameters={"snapshot_id": one.result["snapshot_id"], "revision": 1},
    )
    assert cross.error.code == "B_SOURCE_UNVERIFIED"


def test_check_source_reports_actual_current_byte_drift(stack):
    core, source, _root = stack
    seed(core, source)
    result = analyze(core)
    assert result.error is None
    (source / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    checked = dispatch(
        core,
        "check_source",
        parameters={"snapshot_id": result.result["snapshot_id"], "revision": 1},
    )
    assert checked.error is None
    assert checked.result["changes"]["state"] == "changed"
    assert checked.result["changes"]["modified"] == ["main.py"]


def test_agent_cannot_confirm_a_binding(stack):
    core, source, _root = stack
    binding = LocalProjectBinding(
        "binding",
        1,
        "project",
        source.as_posix(),
        BindingForm.PLAIN,
        manifest_digest="declared",
        confirmed=True,
    )
    response = dispatch(
        core, "save_binding", session=AGENT, parameters={"binding": binding_to_payload(binding)}
    )
    assert response.error.code == "AWAITING_USER_CONFIRMATION"
    assert core.unit_of_work.repo.current_revision("binding", "binding") == 0


def test_binding_revision_changed_during_pin_is_not_published(stack, monkeypatch):
    core, source, _root = stack
    binding = seed(core, source)
    original = FileSourceSnapshotStore.pin

    def changed_pin(self, **kwargs):
        pinned = original(self, **kwargs)
        payload = binding_to_payload(binding)
        payload["binding_revision"] = 2
        response = dispatch(
            core,
            "save_binding",
            expected=1,
            intent="changed-binding",
            request="changed-binding-request",
            parameters={"binding": payload},
        )
        assert response.error is None
        return pinned

    monkeypatch.setattr(FileSourceSnapshotStore, "pin", changed_pin)
    result = analyze(core)
    assert result.error.code == "B_SOURCE_UNVERIFIED"
    assert core.unit_of_work.repo._load()["intents"].get("pin-intent") is None


def test_real_git_identity_includes_untracked_content_and_rejects_head_drift(stack):
    core, source, _root = stack

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=source, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("add", "main.py")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    seed(core, source, form=BindingForm.GIT, head=git("rev-parse", "HEAD"))
    from aitest.infrastructure.adapters.source_control import GitSourceControl

    control = GitSourceControl()
    first = control.snapshot_identity(source)
    (source / "untracked.txt").write_bytes(b"first")
    second = control.snapshot_identity(source)
    (source / "untracked.txt").write_bytes(b"second")
    third = control.snapshot_identity(source)
    assert len({fact["git_diff_digest"] for fact in (first, second, third)}) == 3
    assert analyze(core).error is None
    git("add", "untracked.txt")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "changed head",
    )
    rejected = analyze(core, intent="changed-head", request="changed-head-request", expected=1)
    assert rejected.error.code == "B_SOURCE_UNVERIFIED"


def test_source_pause_blocks_new_pin_but_original_intent_remains_readable(stack):
    from aitest.infrastructure.capabilities import SOURCE

    core, source, _root = stack
    seed(core, source)
    first = analyze(core)
    assert first.error is None
    core.gate.degrade(SOURCE, "fixture manual pause")
    original = analyze(core, request="recall-transport")
    assert original.error is None and original.result["reused"] is True
    fresh = analyze(core, request="fresh-request", intent="fresh-intent", expected=1)
    assert fresh.error.code == "CAPABILITY_UNAVAILABLE"
    core.gate.restore(SOURCE)
    restored = analyze(core, request="restored-request", intent="fresh-intent", expected=1)
    assert restored.error is None
