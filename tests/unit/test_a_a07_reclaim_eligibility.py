"""A-07：回收资格必须由真实活动/发布/引用事实证明。

覆盖：
- 仅凭同名目标存在不够：在途（比目标新）暂存不回收；
- 候选字节被永久记录引用时拒绝回收；
- spool/workdirs 活动材料目录、陌生目录永不扫描；
- core 启动暂存按 PID 存活事实判定；
- 活动标记与非空事件暂存都阻塞回收与迁移（迁移/恢复协作）；
- 恢复编排核实并清除活动后，回收才可执行。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.file_store.events import FileEventJournal
from aitest.infrastructure.file_store.maintenance import (
    FileMaintenanceService,
    detect_activity_blocker,
)
from aitest.infrastructure.file_store.migrations import FileMigrationManager
from aitest.infrastructure.file_store.recovery import RecoveryOrchestrator
from aitest.infrastructure.file_store.workspace import Workspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    Workspace(tmp_path)
    FileEventJournal(tmp_path, instance_id="instance-1")
    return tmp_path


def _service(root: Path) -> FileMaintenanceService:
    return FileMaintenanceService(root)


def _set_mtime(path: Path, *, ns_delta: int) -> None:
    base = path.stat().st_mtime_ns
    os.utime(path, ns=(base + ns_delta, base + ns_delta))


def _stale_leftover(root: Path, name: str, *, target: str, content: bytes) -> Path:
    target_path = root / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("{}", encoding="utf-8")
    path = target_path.parent / name
    path.write_bytes(content)
    _set_mtime(path, ns_delta=-10_000_000)
    return path


# ------------------------------------------------------------ 发布事实


def test_inflight_leftover_newer_than_target_is_not_reclaimable(root: Path) -> None:
    target = root / "records.json"
    target.write_text("{}", encoding="utf-8")
    inflight = root / ".records.json.tmp"
    inflight.write_bytes(b"writing")
    _set_mtime(inflight, ns_delta=10_000_000)  # 比已发布目标新 = 在途

    candidates = {c.relative_path for c in _service(root).reclaimable()}
    assert ".records.json.tmp" not in candidates


def test_stale_published_leftover_is_reclaimable(root: Path) -> None:
    leftover = _stale_leftover(
        root,
        ".records.json.tmp",
        target="records.json",
        content=b"stale",
    )
    candidates = {c.relative_path for c in _service(root).reclaimable()}
    assert leftover.relative_to(root).as_posix() in candidates


def test_mkstemp_leftover_without_published_target_is_kept(root: Path) -> None:
    orphan = root / ".current.json.AbCdEf12"
    orphan.write_bytes(b"{}")
    assert _service(root).reclaimable() == ()


# ------------------------------------------------------------ 引用事实


def test_candidate_bytes_referenced_by_permanent_record_is_refused(root: Path) -> None:
    content = b"still referenced bytes"
    digest = hashlib.sha256(content).hexdigest()
    (root / "records.json").write_text(
        json.dumps({"records": {}, "evidence": f"sha256:{digest}"}),
        encoding="utf-8",
    )
    leftover = root / ".records.json.AbCdEf12"
    leftover.write_bytes(content)
    _set_mtime(leftover, ns_delta=-10_000_000)
    # 同名目标存在 + 暂存更旧仍不够：字节被永久引用，拒绝回收。
    assert _service(root).reclaimable() == ()


# ------------------------------------------------------------ 位置白名单


@pytest.mark.parametrize("directory", ["spool/attempt-1", "workdirs/run-1", "tmpdata"])
def test_activity_and_unknown_directories_are_never_scanned(
    root: Path, directory: str
) -> None:
    path = root / directory / ".something.tmp"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    assert _service(root).reclaimable() == ()


# ------------------------------------------------------------ PID 事实


def test_core_tmp_reclaim_requires_dead_pid(root: Path) -> None:
    core_dir = root / "core"
    core_dir.mkdir(parents=True, exist_ok=True)
    live = core_dir / f".instance-id.{os.getpid()}.tmp"
    live.write_text("core-live", encoding="utf-8")
    assert _service(root).reclaimable() == ()

    proc = subprocess.Popen(  # noqa: S603 - 仅取一个已退出 PID
        [sys.executable, "-c", "print('done')"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    dead_pid = proc.pid
    assert proc.wait() == 0
    dead = core_dir / f".instance-id.{dead_pid}.tmp"
    dead.write_text("core-dead", encoding="utf-8")
    candidates = {c.relative_path for c in _service(root).reclaimable()}
    assert f"core/.instance-id.{dead_pid}.tmp" in candidates


# ------------------------------------------------------------ 活动阻塞


def test_non_empty_staging_blocks_reclaim_even_without_marker(root: Path) -> None:
    leftover = _stale_leftover(
        root, ".records.json.tmp", target="records.json", content=b"stale"
    )
    staging = root / "event-log" / "staging" / "7.jsonl"
    staging.write_text('{"event": 1}\n', encoding="utf-8")

    assert detect_activity_blocker(root) is not None
    report = _service(root).reclaim(dry_run=False)
    assert report.state == "blocked"
    assert "暂存" in report.blocked_reason
    assert leftover.exists()


def test_migration_is_blocked_by_unsealed_staging(root: Path) -> None:
    (root / "event-log" / "staging" / "8.jsonl").write_text(
        '{"event": 1}\n', encoding="utf-8"
    )
    manager = FileMigrationManager(root)
    plan = manager.plan(("0001-workspace-schema-version",))

    report = manager.apply(plan.plan_id)

    assert report.state == "blocked"
    # 阻塞期间不得创建迁移前备份。
    assert not (root / "migrations" / "backups" / plan.plan_id).exists()


def test_recovery_then_maintenance_cooperation_unblocks_reclaim(root: Path) -> None:
    """C（恢复）先核实并清除活动事实，A（维护）随后才判回收资格。"""
    leftover = _stale_leftover(
        root, ".records.json.tmp", target="records.json", content=b"stale-12345"
    )
    marker = root / "transactions" / "active.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"request_id": "req-9", "commit_sequence": 9}), encoding="utf-8"
    )
    assert _service(root).reclaim(dry_run=False).state == "blocked"

    RecoveryOrchestrator(root, instance_id="instance-1").run()
    assert not marker.exists()
    assert detect_activity_blocker(root) is None

    relative = leftover.relative_to(root).as_posix()
    report = _service(root).reclaim(relative_paths=(relative,), dry_run=False)
    assert report.state == "reclaimed"
    assert not leftover.exists()
