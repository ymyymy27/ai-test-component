"""A-06：目录白名单、sha256 永久引用闭包与恢复目标边界验证。

覆盖：
- 根目录白名单（陌生文件/目录、符号链接、objects 布局）；
- 记录/诊断/导出/报告/历史世代/JSONL 台账中 sha256 引用的可达闭包；
- 撕裂 JSONL 永久段报错，活动暂存撕裂不误判；
- spool 清单块摘要/游标边界（篡改、截断、崩溃后抢救）；
- 备份闭包双向核对、portable 制品恢复后再核对、空目标恢复、
  越界清单拒绝、符号链接目标拒绝。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.domain.execution.runs import CapturedOutputBlock, OutputStreamName
from aitest.infrastructure.file_store.backup import BackupError, FileBackupStore
from aitest.infrastructure.file_store.integrity import check_workspace
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.workspace import Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    Workspace(tmp_path)
    return tmp_path


def _publish_object(root: Path, content: bytes, *, project: str = "project-a") -> str:
    return FileObjectStore(root).publish_bytes(project, content).digest


def _errors(root: Path) -> list[str]:
    report = check_workspace(root)
    assert report["ok"] is False
    return list(report["errors"])  # type: ignore[arg-type]


# ------------------------------------------------------------ 白名单


def test_healthy_workspace_passes(root: Path) -> None:
    digest = _publish_object(root, b"evidence-bytes")
    records = root / "records.json"
    records.write_text(
        json.dumps({"records": {"x": {"r": [{"object_digest": digest}]}}, "commit": 1}),
        encoding="utf-8",
    )
    report = check_workspace(root)
    assert report["ok"] is True
    assert report["objects"] == 1


def test_unknown_top_level_entries_are_reported(root: Path) -> None:
    (root / "rogue.txt").write_text("x", encoding="utf-8")
    rogue_dir = root / "rogue-dir"
    rogue_dir.mkdir()
    (rogue_dir / "n.txt").write_text("y", encoding="utf-8")
    errors = _errors(root)
    assert any("outside whitelist: rogue.txt" in error for error in errors)
    assert any("outside whitelist: rogue-dir" in error for error in errors)


def test_object_must_live_under_project_directory(root: Path) -> None:
    digest = hashlib.sha256(b"orphan").hexdigest()
    path = root / "objects" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"orphan")
    errors = _errors(root)
    assert any("object outside objects/<project>/<sha256>" in error for error in errors)


def test_hidden_atomic_leftovers_are_not_integrity_errors(root: Path) -> None:
    (root / ".records.json.abcd1234").write_bytes(b"stale")
    assert check_workspace(root)["ok"] is True


def _can_make_symlink(target: Path, link: Path) -> bool:
    try:
        target.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        return False
    return True


def test_symlink_anywhere_is_reported(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside-dir"
    link = root / "objects-link"
    if not _can_make_symlink(outside, link):
        pytest.skip("当前会话无权创建符号链接")
    errors = _errors(root)
    assert any("symlink" in error for error in errors)
    link.unlink()
    outside.rmdir()


# ------------------------------------------------------------ 引用闭包


@pytest.mark.parametrize(
    "record_location",
    [
        "diagnostics/health-1.json",
        "exports/manifest.json",
        "reports/latest.json",
        "generations/gen-1/manifest.json",
        "checkpoints/cp-1.json",
    ],
)
def test_references_in_all_permanent_records_must_resolve(
    root: Path, record_location: str
) -> None:
    digest = _publish_object(root, b"attachment")
    path = root / record_location
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"attachments": [{"artifact_digest": digest, "name": "a.png"}]}),
        encoding="utf-8",
    )
    assert check_workspace(root)["ok"] is True

    # 删除对象后，任意永久记录中的引用都必须被闭包核对发现。
    hexed = digest.split(":", 1)[1]
    object_path = next((root / "objects").rglob(hexed))
    object_path.unlink()
    errors = _errors(root)
    assert any(
        f"unreachable object reference: sha256:{hexed}" in error for error in errors
    )


def test_jsonl_ledgers_are_line_validated_and_closure_checked(root: Path) -> None:
    digest = _publish_object(root, b"in-ledger")
    ledger = root / "diagnostics" / "connection-facts.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"fact": "x", "artifact_digest": digest}) + "\n", encoding="utf-8"
    )
    assert check_workspace(root)["ok"] is True

    with ledger.open("a", encoding="utf-8") as handle:
        handle.write('{"fact": "torn"')  # 撕裂尾行
    errors = _errors(root)
    assert any("connection-facts.jsonl:2" in error for error in errors)


def test_torn_active_staging_is_not_an_integrity_failure(root: Path) -> None:
    staging = root / "event-log" / "staging" / "99.jsonl"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_text('{"partial": ', encoding="utf-8")
    assert check_workspace(root)["ok"] is True


def test_digest_mismatch_is_reported(root: Path) -> None:
    digest = _publish_object(root, b"original")
    hexed = digest.split(":", 1)[1]
    object_path = next((root / "objects").rglob(hexed))
    object_path.write_bytes(b"tampered bytes")
    errors = _errors(root)
    assert any("digest mismatch" in error for error in errors)


# ------------------------------------------------------------ spool 清单


def _one_block_spool(root: Path, content: bytes = b"hello spool\n") -> Path:
    store = FileSpoolStore(root)
    block = CapturedOutputBlock(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_index=0,
        offset=0,
        content=content,
    )
    store.persist_blocks([block])
    return root / "spool" / "attempt-1" / "stdout.log"


def test_spool_manifest_blocks_are_verified(root: Path) -> None:
    stream = _one_block_spool(root)
    assert check_workspace(root)["ok"] is True

    raw = bytearray(stream.read_bytes())
    raw[0] ^= 0xFF
    stream.write_bytes(bytes(raw))
    errors = _errors(root)
    assert any("spool block digest mismatch" in error for error in errors)


def test_spool_cursor_beyond_durable_bytes_is_reported(root: Path) -> None:
    stream = _one_block_spool(root)
    stream.write_bytes(b"")  # 截断已持久化流
    errors = _errors(root)
    assert any("exceeds durable stream bytes" in error for error in errors)
    assert any("beyond durable bytes" in error for error in errors)


def test_salvaged_partial_output_keeps_closure_verifiable(root: Path) -> None:
    """崩溃抢救产生的不完整块仍受清单闭包核对（A-06 活动输出抢救证据）。"""
    store = FileSpoolStore(root)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-crash",
        stream_name=OutputStreamName.STDOUT,
        block_size=4096,
    )
    writer.append(b"sealed prefix")
    # 不足一个块；模拟进程崩溃：不 seal 直接 abort，字节已 fsync。
    writer.abort()

    manifest = store.salvage_streams("attempt-crash")
    assert any(not block.complete for block in manifest.blocks)
    assert check_workspace(root)["ok"] is True


# ------------------------------------------------------------ 备份/恢复边界


def test_portable_backup_closure_verifies_and_restores(root: Path, tmp_path: Path) -> None:
    digest = _publish_object(root, b"portable evidence")
    (root / "records.json").write_text(
        json.dumps({"records": {"e": {"r": [{"object_digest": digest}]}}, "commit": 1}),
        encoding="utf-8",
    )
    store = FileBackupStore(root)
    backup = store.create(tmp_path / "backup")

    assert store.verify(backup)["ok"] is True

    target = tmp_path / "restored"
    report = store.restore(backup=backup, target=target)
    assert report.state == "restored"
    assert report.verified is True
    # 恢复副本是自洽 portable 制品：闭包与摘要在新目录同样成立。
    assert check_workspace(target)["ok"] is True


def test_backup_with_manifested_digest_mismatch_fails(root: Path, tmp_path: Path) -> None:
    store = FileBackupStore(root)
    backup = store.create(tmp_path / "backup")
    (backup / "workspace.json").write_text('{"tampered": true}', encoding="utf-8")
    result = store.verify(backup)
    assert result["ok"] is False
    assert "workspace.json" in result["errors"]


def test_backup_with_unmanifested_extra_file_fails(root: Path, tmp_path: Path) -> None:
    store = FileBackupStore(root)
    backup = store.create(tmp_path / "backup")
    extra = backup / "sneaked.bin"
    extra.write_bytes(b"not in manifest")
    result = store.verify(backup)
    assert result["ok"] is False
    assert "sneaked.bin" in result["errors"]


def test_restore_into_empty_target_succeeds(root: Path, tmp_path: Path) -> None:
    store = FileBackupStore(root)
    backup = store.create(tmp_path / "backup")
    target = tmp_path / "fresh-target"
    assert not target.exists()
    report = store.restore(backup=backup, target=target)
    assert report.verified is True
    assert report.state in {"restored", "empty"}
    assert (target / "workspace.json").exists()


def test_restore_rejects_path_traversal_manifest(root: Path, tmp_path: Path) -> None:
    store = FileBackupStore(root)
    backup = store.create(tmp_path / "backup")
    (backup / "backup.json").write_text(
        json.dumps({"files": {"../evil.txt": "0" * 64}}), encoding="utf-8"
    )
    target = tmp_path / "must-not-exist"
    report = store.restore(backup=backup, target=target)
    assert report.state == "rejected"
    assert not target.exists()
    assert not (tmp_path / "evil.txt").exists()


def test_restore_rejects_symlink_target(root: Path, tmp_path: Path) -> None:
    store = FileBackupStore(root)
    backup = store.create(tmp_path / "backup")
    destination_dir = tmp_path / "link-points-here"
    target = tmp_path / "linked-target"
    if not _can_make_symlink(destination_dir, target):
        pytest.skip("当前会话无权创建符号链接")
    with pytest.raises(BackupError):
        store.restore(backup=backup, target=target)
