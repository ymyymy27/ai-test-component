"""A malformed identity must never be normalized by admitting a new writer."""

import json

import pytest

from aitest.bootstrap import assemble_workspace_core
from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.file_store.workspace import Workspace


@pytest.mark.parametrize(
    "raw",
    [
        b'{"workspace_id":"ws","writer_epoch":0,"writer_epoch":4,"schema_version":"1.0"}',
        b'{"workspace_id":42,"writer_epoch":0,"schema_version":"1.0"}',
        b'{"workspace_id":"ws","writer_epoch":false,"schema_version":"1.0"}',
        b'{"workspace_id":"ws","writer_epoch":-1,"schema_version":"1.0"}',
        b'{"workspace_id":"ws","writer_epoch":0,"schema_version":false}',
        json.dumps(
            {
                "workspace_id": "ws",
                "writer_epoch": 0,
                "schema_version": "1.0",
                "padding": "x" * 16384,
            }
        ).encode(),
    ],
)
def test_invalid_identity_is_rejected_before_any_admission_write(tmp_path, raw):
    identity = tmp_path / "workspace.json"
    identity.write_bytes(raw)
    with pytest.raises(ValueError):
        core = assemble_workspace_core(tmp_path, instance_id="identity-negative")
        core.lifetime_lock.release()
    assert identity.read_bytes() == raw
    assert not (tmp_path / "current.json").exists()


def test_replaced_identity_with_duplicate_epoch_is_not_overwritten(tmp_path):
    workspace = Workspace(tmp_path)
    raw = (
        '{"workspace_id":"'
        + workspace.workspace_id
        + '","schema_version":"1.0","writer_epoch":0,"writer_epoch":3}'
    ).encode()
    workspace.identity_path.write_bytes(raw)
    with pytest.raises(ValueError):
        lock = workspace.admit_lifetime()
        lock.release()
    assert workspace.identity_path.read_bytes() == raw


def test_bootstrap_rejects_raw_root_junction_before_resolving(tmp_path, monkeypatch):
    root = tmp_path / "junction"
    root.mkdir()
    original = compat.is_junction
    monkeypatch.setattr(compat, "is_junction", lambda path: path == root or original(path))
    with pytest.raises(ValueError, match="link"):
        core = assemble_workspace_core(root, instance_id="junction-negative")
        core.lifetime_lock.release()
    assert not (root / "workspace.json").exists()
