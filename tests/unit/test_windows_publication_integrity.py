"""Canonical pointer, real Win32 file operations and uncertain-publication cuts."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from aitest.bootstrap import CoreAssemblyBlocked, assemble_workspace_core
from aitest.infrastructure import security
from aitest.infrastructure.file_store import atomic
from aitest.infrastructure.file_store.commit_manifest import (
    CommitMaterialError,
    FileCommitStore,
    canonical_bytes,
)
from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.infrastructure.file_store.publication_backend import (
    FilePublicationBackend,
    PublicationUncertainError,
    WindowsFileAPI,
)
from aitest.infrastructure.file_store.records import FileRecordRepository
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.file_store.workspace import Workspace
from tests.unit.test_complete_commit_closure import commit, facts


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    identity = Workspace(root)
    FileEventJournal(root, instance_id=identity.workspace_id)
    manager = FileMigrationManager(root)
    plan = manager.plan(
        (
            "0003-sharded-record-authority",
            "0004-bounded-query-directory",
            "0005-complete-commit-closure",
        )
    )
    assert manager.apply(plan.plan_id).state == "applied"
    return root


def test_canonical_pointer_binds_all_seven_contract_fields(workspace):
    commit(workspace, ("one", "two"), request="request", intent="intent")
    current = FileCommitStore(workspace).read_current(verify_material=True)
    pointer, manifest = current["pointer"], current["manifest"]
    assert set(pointer) == {
        "schema_version",
        "workspace_id",
        "generation",
        "commit_id",
        "manifest_digest",
        "index_root",
        "event_cursor",
    }
    assert pointer["commit_id"] == 2
    assert pointer["generation"] == manifest["generation_id"]
    assert pointer["index_root"] == manifest["index_root"]
    assert pointer["event_cursor"] == facts(workspace, "two")[5]
    assert facts(workspace, "two")[:5] == (2, 1, "ok", 1, 2)
    assert FilePublicationBackend(workspace).inspect_publication() == "new"


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit_id", True),
        ("commit_id", -1),
        ("commit_id", 99),
        ("generation", "other"),
        ("workspace_id", "other"),
        ("index_root", {}),
        ("event_cursor", "unknown"),
        ("schema_version", "unknown"),
        ("extra", "unsupported"),
    ],
)
def test_each_canonical_pointer_field_is_checked_against_sealed_material(workspace, field, value):
    backend = FilePublicationBackend(workspace)
    before = (workspace / "current.json").read_bytes()
    pointer = json.loads(before)
    pointer[field] = value
    # Fixture simulates sealed but invalid storage; rejection must come from
    # the pointer codec/material check, not merely an unhashed manual rewrite.
    backend.replace_current(canonical_bytes(pointer), previous=before)
    with pytest.raises(CommitMaterialError):
        FileCommitStore(workspace).read_current(verify_material=True)
    with pytest.raises(ValueError):
        FileRecordRepository(workspace).current_commit_sequence()
    assert backend.inspect_publication() == "new"


@pytest.mark.parametrize(
    "field,value", [("generation", True), ("version", 3.0), ("last_commit_sequence", False)]
)
def test_nested_index_pointer_does_not_accept_bool_or_float_as_integer(workspace, field, value):
    backend = FilePublicationBackend(workspace)
    before = (workspace / "current.json").read_bytes()
    pointer = json.loads(before)
    pointer["index_root"][field] = value
    backend.replace_current(canonical_bytes(pointer), previous=before)
    assert backend.inspect_publication() == "new"
    with pytest.raises(CommitMaterialError):
        FileCommitStore(workspace).read_current()


def test_backed_up_upgrade_preserves_old_pointer_manifest_and_business_roots(workspace):
    commit(workspace, ("one",), request="request", intent="intent")
    store = FileCommitStore(workspace)
    old = store.read_current(verify_material=True)
    legacy = canonical_bytes(
        dict(
            schema="aitest.current-commit/1",
            workspace_id=old["manifest"]["workspace_id"],
            commit_sequence=1,
            manifest_digest=old["pointer"]["manifest_digest"],
        )
    )
    FilePublicationBackend(workspace).replace_current(
        legacy, previous=(workspace / "current.json").read_bytes()
    )
    assert store.read_current()["pointer"]["schema"] == "aitest.current-commit/1"
    proof = facts(workspace, "one")
    manager = FileMigrationManager(workspace)
    plan = manager.plan(("0006-canonical-current-publication",))
    report = manager.apply(plan.plan_id)
    assert report.state == "applied"
    assert (report.backup_path / "current.json").read_bytes() == legacy
    now = store.read_current(verify_material=True)
    assert now["manifest"] == old["manifest"]
    assert now["pointer"]["manifest_digest"] == old["pointer"]["manifest_digest"]
    assert facts(workspace, "one") == proof
    assert any(
        p.read_bytes() == legacy
        for p in (workspace / "transactions/publications").glob("*.pointer")
    )
    frozen = (workspace / "current.json").read_bytes()
    assert manager.resume(plan.plan_id).state == "nothing_to_apply"
    assert (workspace / "current.json").read_bytes() == frozen


@pytest.mark.skipif(os.name != "nt", reason="real Windows backend")
def test_real_replacefilew_retains_previous_pointer_and_checked_flush(workspace, monkeypatch):
    old = (workspace / "current.json").read_bytes()
    calls = []
    original = WindowsFileAPI.replace

    def replace(self, current, candidate, backup):
        calls.append((current, candidate, backup))
        return original(self, current, candidate, backup)

    monkeypatch.setattr(WindowsFileAPI, "replace", replace)
    commit(workspace, ("one",), request="one")
    assert len(calls) == 1
    current, candidate, backup = calls[0]
    assert current == workspace / "current.json"
    assert backup.read_bytes() == old
    assert not candidate.exists()
    assert facts(workspace, "one")[:5] == (1, 1, "ok", 1, 1)
    assert FilePublicationBackend(workspace).inspect_publication() == "new"


def test_replace_denial_keeps_old_whole_commit_and_later_intent_can_run(workspace):
    old = (workspace / "current.json").read_bytes()
    with (
        patch.object(FilePublicationBackend, "_replace", side_effect=OSError("denied")),
        pytest.raises(OSError, match="denied"),
    ):
        commit(workspace, ("one",), request="one", intent="intent")
    assert (workspace / "current.json").read_bytes() == old
    assert FilePublicationBackend(workspace).inspect_publication() == "old"
    assert facts(workspace, "one")[:5] == (0, 0, "ok", 0, 0)
    _, result = commit(workspace, ("one",), request="retry", intent="intent")
    assert result["commit_sequence"] == 1


def test_replace_error_after_actual_switch_is_resolved_as_saved_success(workspace):
    original = FilePublicationBackend._replace

    def lost_response(self, candidate, backup, *, initial):
        original(self, candidate, backup, initial=initial)
        raise OSError("controlled lost replacement response")

    with patch.object(FilePublicationBackend, "_replace", lost_response):
        _, result = commit(workspace, ("one",), request="one", intent="intent")
    assert result["commit_sequence"] == 1
    _, retried = commit(workspace, ("one",), request="retry", intent="intent")
    assert retried["commit_sequence"] == 1
    assert len(facts(workspace, "one")[6]) == 1


def test_missing_current_with_retained_backup_blocks_without_restoring_or_promoting(workspace):
    old = (workspace / "current.json").read_bytes()

    def intermediate(self, candidate, backup, *, initial):
        assert not initial
        self.current.rename(backup)  # Inject documented intermediate name state.
        raise OSError("controlled replacement intermediate error")

    with (
        patch.object(FilePublicationBackend, "_replace", intermediate),
        pytest.raises(PublicationUncertainError),
    ):
        commit(workspace, ("one",), request="one", intent="intent")
    assert not (workspace / "current.json").exists()
    assert FilePublicationBackend(workspace).inspect_publication() == "unknown"
    backups = list((workspace / "transactions/publications").glob("*/backup.json"))
    assert any(p.read_bytes() == old for p in backups)
    assert list((workspace / "transactions/publications").glob("*/replacement.json"))
    for action in (
        lambda: FileCommitStore(workspace).read_current(),
        lambda: commit(workspace, ("two",), request="two"),
    ):
        with pytest.raises(ValueError):
            action()
    with pytest.raises(CoreAssemblyBlocked):
        assemble_workspace_core(workspace, instance_id="restart")
    assert not (workspace / "current.json").exists()


def test_post_switch_flush_failure_never_acks_or_reexecutes_saved_intent(workspace):
    original = FilePublicationBackend.confirm_current
    unit = FileUnitOfWork(workspace)
    unit.begin("one", "project", intent_id="intent")
    unit.stage_record(
        aggregate_kind="case",
        record_id="one",
        expected_revision=0,
        payload={"project_id": "project", "summary": "one"},
    )

    def failing_new(self):
        pointer = json.loads(self.current.read_bytes())
        if pointer.get("commit_id") == 1:
            raise OSError("controlled post-switch flush failure")
        return original(self)

    with patch.object(FilePublicationBackend, "confirm_current", failing_new):
        with pytest.raises(OSError, match="flush failure"):
            unit.commit("one")
        assert facts(workspace, "one")[:5] == (1, 1, "ok", 1, 1)
        with pytest.raises(OSError, match="flush failure"):
            unit.rollback("one")
        with pytest.raises(OSError, match="flush failure"):
            commit(workspace, ("one",), request="retry", intent="intent")
        with pytest.raises(CoreAssemblyBlocked):
            assemble_workspace_core(workspace, instance_id="restart")
    assert unit.rollback("one")["state"] == "committed"
    _, retried = commit(workspace, ("one",), request="retry", intent="intent")
    assert retried["commit_sequence"] == 1
    assert len(facts(workspace, "one")[6]) == 1


@pytest.mark.parametrize("material", ["marker", "descriptor", "candidate", "previous", "backup"])
def test_corrupt_recovery_material_blocks_new_writes_and_preserves_current(workspace, material):
    commit(workspace, ("one",), request="one")
    backend = FilePublicationBackend(workspace)
    marker = json.loads(backend.marker.read_bytes())
    descriptor_path = backend.directory / f"{marker['descriptor_digest']}.json"
    descriptor = json.loads(descriptor_path.read_bytes())
    paths = dict(
        marker=backend.marker,
        descriptor=descriptor_path,
        candidate=backend.directory / f"{descriptor['candidate_digest']}.pointer",
        previous=backend.directory / f"{descriptor['previous_digest']}.pointer",
        backup=backend.directory / descriptor["attempt"] / "backup.json",
    )
    saved = (workspace / "current.json").read_bytes()
    paths[material].write_bytes(b"corrupt recovery material")
    assert backend.inspect_publication() == "unknown"
    with pytest.raises(CommitMaterialError):
        FileCommitStore(workspace).read_current()
    with pytest.raises(ValueError):
        commit(workspace, ("two",), request="two")
    assert (workspace / "current.json").read_bytes() == saved


def test_recovery_marker_must_be_saved_before_replacement(workspace):
    saved = (workspace / "current.json").read_bytes()
    original = atomic.write_json

    def fail_marker(path, value, **kwargs):
        if path == workspace / "transactions/publication.json":
            raise OSError("controlled marker failure")
        return original(path, value, **kwargs)

    with (
        patch.object(atomic, "write_json", fail_marker),
        patch.object(FilePublicationBackend, "_replace") as replace,
    ):
        with pytest.raises(OSError, match="marker failure"):
            commit(workspace, ("one",), request="one")
        replace.assert_not_called()
    assert (workspace / "current.json").read_bytes() == saved
    assert facts(workspace, "one")[:5] == (0, 0, "ok", 0, 0)


def test_immutable_material_never_overwrites_a_collision(tmp_path):
    backend = FilePublicationBackend(tmp_path)
    path = tmp_path / "immutable.pointer"
    backend.publish_immutable(path, b"original")
    backend.publish_immutable(path, b"original")
    with pytest.raises(PublicationUncertainError):
        backend.publish_immutable(path, b"different")
    assert path.read_bytes() == b"original"


def test_known_credentials_never_reach_candidate_previous_or_manifest(tmp_path, monkeypatch):
    registry = security.KnownSecretRegistry()
    registry.register("fictional-publication-credential")
    monkeypatch.setattr(security, "_GLOBAL_REGISTRY", registry)
    backend = FilePublicationBackend(tmp_path)
    with pytest.raises(PublicationUncertainError):
        backend.replace_current(b"fictional-publication-credential", previous=None)
    assert not backend.marker.exists()
    assert not backend.directory.exists()


@pytest.mark.skipif(os.name != "nt", reason="real Windows FlushFileBuffers")
def test_flushfilebuffers_rejects_a_read_only_handle(tmp_path):
    path = tmp_path / "read-only.bin"
    path.write_bytes(b"content")
    with path.open("rb") as handle, pytest.raises(OSError):
        WindowsFileAPI().flush(handle.fileno())
    assert path.read_bytes() == b"content"


@pytest.mark.skipif(os.name != "nt", reason="real Windows exclusive sharing handle")
def test_real_sharing_denial_preserves_current_until_handle_is_released(workspace):
    from ctypes import wintypes

    api = WindowsFileAPI()
    create, close = api.kernel.CreateFileW, api.kernel.CloseHandle
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    old = (workspace / "current.json").read_bytes()
    handle = create(str(workspace / "current.json"), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        with pytest.raises((ValueError, OSError)):
            commit(workspace, ("one",), request="one", intent="intent")
    finally:
        assert close(handle)
    assert (workspace / "current.json").read_bytes() == old
    _, result = commit(workspace, ("one",), request="retry", intent="intent")
    assert result["commit_sequence"] == 1


def test_deleted_replacement_backup_is_not_silently_accepted(workspace):
    commit(workspace, ("one",), request="one")
    backend = FilePublicationBackend(workspace)
    marker = json.loads(backend.marker.read_bytes())
    descriptor = json.loads(
        (backend.directory / f"{marker['descriptor_digest']}.json").read_bytes()
    )
    backup = backend.directory / descriptor["attempt"] / "backup.json"
    backup.unlink()
    assert backend.inspect_publication() == "unknown"
    with pytest.raises(CommitMaterialError):
        FileCommitStore(workspace).read_current()


def test_failed_initial_publication_does_not_allow_legacy_empty_fallback(tmp_path):
    identity = Workspace(tmp_path)
    FileEventJournal(tmp_path, instance_id=identity.workspace_id)
    manager = FileMigrationManager(tmp_path)
    plan = manager.plan(
        (
            "0003-sharded-record-authority",
            "0004-bounded-query-directory",
            "0005-complete-commit-closure",
        )
    )
    with (
        patch.object(FilePublicationBackend, "_replace", side_effect=OSError("initial denial")),
        pytest.raises(PublicationUncertainError),
    ):
        manager.apply(plan.plan_id)
    assert not (tmp_path / "current.json").exists()
    assert FilePublicationBackend(tmp_path).inspect_publication() == "unknown"
    with pytest.raises(CommitMaterialError):
        FileCommitStore(tmp_path).read_current()
    with pytest.raises(CoreAssemblyBlocked):
        assemble_workspace_core(tmp_path, instance_id="restart")
    assert not (tmp_path / "current.json").exists()


def test_prepare_flush_failure_never_reaches_replace_or_business_publication(workspace):
    before = (workspace / "current.json").read_bytes()
    with (
        patch.object(FilePublicationBackend, "_flush", side_effect=OSError("flush denied")),
        patch.object(FilePublicationBackend, "_replace") as replace,
        pytest.raises(OSError, match="flush denied"),
    ):
        commit(workspace, ("one",), request="one")
    replace.assert_not_called()
    assert (workspace / "current.json").read_bytes() == before
    assert facts(workspace, "one")[:5] == (0, 0, "ok", 0, 0)


@pytest.mark.skipif(os.name != "nt", reason="Windows volume check")
def test_cross_volume_result_is_rejected_before_native_replace(workspace, monkeypatch):
    old = (workspace / "current.json").read_bytes()
    monkeypatch.setattr(WindowsFileAPI, "volume", lambda self, parent: str(parent))
    with pytest.raises(OSError, match="different volumes"):
        commit(workspace, ("one",), request="one")
    assert (workspace / "current.json").read_bytes() == old
    assert FilePublicationBackend(workspace).inspect_publication() == "old"


@pytest.mark.parametrize("cut", ["before", "after"])
def test_real_process_exit_with_recovery_material_resolves_whole_root(workspace, cut):
    script = """
import os,sys
from pathlib import Path
from aitest.infrastructure.file_store.publication_backend import FilePublicationBackend
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
original=FilePublicationBackend._replace
def cut(self,candidate,backup,*,initial):
    if sys.argv[2]=='after': original(self,candidate,backup,initial=initial)
    os._exit(78)
FilePublicationBackend._replace=cut
u=FileUnitOfWork(Path(sys.argv[1]))
u.begin('one','project',intent_id='intent')
u.stage_record(aggregate_kind='case',record_id='one',expected_revision=0,
               payload={'project_id':'project','summary':'one'})
u.commit('one')
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script, str(workspace), cut],
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 78, result.stderr.decode(errors="replace")
    expected = 0 if cut == "before" else 1
    proof = facts(workspace, "one")
    assert proof[:5] == (expected, expected, "ok", expected, expected)
    restart = assemble_workspace_core(workspace, instance_id="restart")
    try:
        assert restart.recovery.state == "healthy"
        assert facts(workspace, "one") == proof
        _, retry = commit(workspace, ("one",), request="retry", intent="intent")
        assert retry["commit_sequence"] == 1
        assert len(facts(workspace, "one")[6]) == 1
    finally:
        restart.lifetime_lock.release()


def test_inspection_does_not_scan_unrelated_publication_history(workspace, monkeypatch):
    backend = FilePublicationBackend(workspace)
    for number in range(20):
        (backend.directory / f"unrelated-{number}.json").write_bytes(b"bad old diagnostic")
    with (
        patch.object(Path, "glob", side_effect=AssertionError("history scan")),
        patch.object(Path, "rglob", side_effect=AssertionError("history scan")),
    ):
        assert backend.inspect_publication() == "new"
        assert FileCommitStore(workspace).read_current() is not None


def test_descriptor_hash_is_checked_without_trusting_its_paths(workspace):
    backend = FilePublicationBackend(workspace)
    marker = json.loads(backend.marker.read_bytes())
    path = backend.directory / f"{marker['descriptor_digest']}.json"
    descriptor = json.loads(path.read_bytes())
    descriptor["attempt"] = "../outside"
    raw = canonical_bytes(descriptor)
    digest = hashlib.sha256(raw).hexdigest()
    # Even a matching digest cannot authorize a descriptor path escape.
    backend.publish_immutable(backend.directory / f"{digest}.json", raw)
    atomic.write_json(
        backend.marker, dict(schema="aitest.publication-marker/1", descriptor_digest=digest)
    )
    assert backend.inspect_publication() == "unknown"


@pytest.mark.parametrize("material", ["marker", "manifest"])
def test_deeply_nested_storage_json_is_a_finite_block_not_a_recursion_crash(workspace, material):
    depth = 1100 if material == "marker" else 100000
    raw = b'{"unknown":' + b"[" * depth + b"0" + b"]" * depth + b"}"
    backend = FilePublicationBackend(workspace)
    if material == "marker":
        backend.marker.write_bytes(raw)
    else:
        digest = hashlib.sha256(raw).hexdigest()
        path = workspace / "manifests" / f"{digest}.json"
        path.write_bytes(raw)  # Corrupt fixture with a matching immutable digest.
        before = (workspace / "current.json").read_bytes()
        pointer = json.loads(before)
        pointer["manifest_digest"] = digest
        backend.replace_current(canonical_bytes(pointer), previous=before)
    # Exercise the actual startup boundary in an ordinary Python child.
    script = """
import sys
from pathlib import Path
from aitest.bootstrap import CoreAssemblyBlocked,assemble_workspace_core
from aitest.infrastructure.file_store.commit_manifest import CommitMaterialError,FileCommitStore
sys.setrecursionlimit(1000)
root=Path(sys.argv[1])
try: FileCommitStore(root).read_current()
except CommitMaterialError: pass
else: raise AssertionError('unsafe material accepted')
try: assemble_workspace_core(root,instance_id='restart')
except CoreAssemblyBlocked: pass
else: raise AssertionError('unsafe core started')
print('blocked')
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    result = subprocess.run(
        [sys.executable, "-c", script, str(workspace)],
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.strip() == b"blocked"
