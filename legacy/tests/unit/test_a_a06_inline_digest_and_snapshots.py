"""A-06：内联指纹不闭包、快照 blob 闭包、恢复拒绝如实阻塞。

- content_digest/projection_digest/规则计划 digest 等内联 ``sha256:<hex>``
  不要求存在同名 objects/ 对象；
- 白名单对象引用键（object_digest/output_object_digest/artifact_digest）
  仍然闭包；
- snapshots/<id>.json 清单的 files[].sha256 必须在 snapshots/blobs 可达，
  blob 字节摘要必须与名一致；
- RecoveryOrchestrator.restore_backup 遇 rejected 必须上报 blocked，
  integrity_ok=False，不得谎报 repaired。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore
from aitest.infrastructure.file_store.backup import FileBackupStore
from aitest.infrastructure.file_store.integrity import check_workspace
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.workspace import Workspace


def _hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    Workspace(workspace)
    return workspace


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir(exist_ok=True)
    (source / "a.txt").write_text("a", encoding="utf-8")
    return source


def _write(root: Path, relative: str, payload: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ------------------------------------------------------------ 内联摘要


def test_inline_digests_do_not_require_objects(root: Path) -> None:
    phantom = "sha256:" + _hex(b"never stored")
    _write(
        root,
        "reports/r1.json",
        {
            "content_digest": phantom,
            "projection_digest": phantom,
            "rule": {"digest": phantom},
            "plan": {"manifest_digest": phantom},
            "nested": [{"resolved_input_digest": phantom}],
        },
    )
    assert check_workspace(root)["ok"] is True


@pytest.mark.parametrize(
    "key",
    ["object_digest", "output_object_digest", "artifact_digest"],
)
def test_whitelisted_object_ref_keys_still_close(root: Path, key: str) -> None:
    phantom = "sha256:" + _hex(b"missing object")
    _write(root, "diagnostics/d1.json", {"ref": {key: phantom}})
    errors = check_workspace(root)["errors"]
    assert any(
        f"unreachable object reference: {phantom}" in error for error in errors
    )


def test_inline_digest_inside_list_inherits_key(root: Path) -> None:
    phantom = "sha256:" + _hex(b"inline only")
    _write(root, "exports/x.json", {"items": [{"content_digest": phantom}]})
    assert check_workspace(root)["ok"] is True


# ------------------------------------------------------------ 快照闭包


def test_healthy_snapshot_manifest_blob_closure(
    root: Path, tmp_path: Path
) -> None:
    source = _source(tmp_path)
    store = FileSourceSnapshotStore(root)
    record = store.pin(canonical_path=str(source), purpose="analysis")
    snapshot_id = str(record["snapshot_id"])
    assert (root / "snapshots" / f"{snapshot_id}.json").exists()
    assert check_workspace(root)["ok"] is True


def test_missing_snapshot_blob_is_reported(root: Path, tmp_path: Path) -> None:
    source = _source(tmp_path)
    store = FileSourceSnapshotStore(root)
    record = store.pin(canonical_path=str(source), purpose="analysis")
    blob = root / "snapshots" / "blobs" / str(record["files"][0]["sha256"])
    blob.unlink()
    errors = check_workspace(root)["errors"]
    assert any("unreachable snapshot blob" in error for error in errors)


def test_tampered_snapshot_blob_is_reported(root: Path, tmp_path: Path) -> None:
    source = _source(tmp_path)
    store = FileSourceSnapshotStore(root)
    record = store.pin(canonical_path=str(source), purpose="analysis")
    blob = root / "snapshots" / "blobs" / str(record["files"][0]["sha256"])
    blob.write_bytes(b"tampered")
    errors = check_workspace(root)["errors"]
    assert any("digest mismatch" in error for error in errors)


# ------------------------------------------------------------ 恢复拒绝传播


def test_restore_rejected_is_blocked_not_repaired(
    root: Path, tmp_path: Path
) -> None:
    backup = FileBackupStore(root).create(tmp_path / "backup")
    (backup / "backup.json").write_text(
        json.dumps({"files": {"../evil.txt": "0" * 64}}), encoding="utf-8"
    )
    target = tmp_path / "must-not-exist"
    state = RecoveryOrchestrator(root, instance_id="inst-1").restore_backup(
        backup=backup, target=target
    )
    assert state.state == "blocked"
    assert state.integrity_ok is False
    assert state.restore is not None
    assert state.restore.state == "rejected"
    assert not target.exists()
