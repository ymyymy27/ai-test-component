"""A-02：全生命周期唯一写入者锁、入口归类、停机协作与在途抢救。

跨平台纯单测（不启动命名管道/子进程）：
- ``LifetimeWriterLock``：同进程同根唯一准入、释放后可重新准入、
  持生命周期锁期间短事务走进程内串行不与 OS 锁自冲突；
- ``classify_entry_kind``：可信宿主/未知映像/取证失败三种归类；
- ``ShutdownCoordinator``：父进程消亡不直接终止进程，而是置退出请求
  并调用阻塞取消回调；
- ``seal_inflight_spool``：父死后退出前补封未封口 spool 尾部。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.application.errors import WorkspaceInUse
from aitest.domain.execution.runs import OutputStreamName
from aitest.infrastructure.file_store.locking import (
    LifetimeWriterLock,
    writer_lock,
)
from aitest.infrastructure.file_store.recovery import seal_inflight_outputs
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.interfaces.local.api import EntryKind
from aitest.interfaces.local.core_worker import (
    ShutdownCoordinator,
    classify_entry_kind,
)

_HUMAN = frozenset({"trae.exe"})


# ------------------------------------------------------------ 生命周期写锁


def test_lifetime_lock_rejects_second_admission_same_root(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "writer.lock"
    first = LifetimeWriterLock(lock_path)
    first.acquire()
    try:
        assert first.held
        with pytest.raises(WorkspaceInUse):
            LifetimeWriterLock(lock_path).acquire()
        # 不同根目录互不影响。
        other = LifetimeWriterLock(tmp_path / "other" / "writer.lock")
        other.acquire()
        other.release()
    finally:
        first.release()
    assert not first.held
    # 释放后同根可重新准入。
    restarted = LifetimeWriterLock(lock_path)
    restarted.acquire()
    restarted.release()


def test_release_is_idempotent(tmp_path: Path) -> None:
    lock = LifetimeWriterLock(tmp_path / "writer.lock")
    lock.release()  # 未取得也不报错
    lock.acquire()
    lock.release()
    lock.release()


def test_short_transaction_lock_serializes_under_lifetime_lock(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "writer.lock"
    lifetime = LifetimeWriterLock(lock_path)
    lifetime.acquire()
    try:
        order: list[str] = []

        def short_txn(name: str) -> None:
            with writer_lock(lock_path):
                order.append(f"{name}-enter")
                order.append(f"{name}-leave")

        t1 = threading.Thread(target=short_txn, args=("a",))
        t2 = threading.Thread(target=short_txn, args=("b",))
        t1.start()
        t2.start()
        t1.join(5)
        t2.join(5)
        assert not t1.is_alive() and not t2.is_alive()
        # 每个事务的 enter/leave 必须成对且不交错。
        joined = "".join(order)
        assert "a-entera-leave" in joined
        assert "b-enterb-leave" in joined
    finally:
        lifetime.release()


# ------------------------------------------------------------ 入口归类


def test_classify_entry_kind_branches() -> None:
    assert classify_entry_kind("Trae.exe".lower(), _HUMAN) is EntryKind.HUMAN_UI
    assert (
        classify_entry_kind("python.exe", _HUMAN) is EntryKind.AGENT_RELAY
    )
    # 映像取证失败（None）按最小权限归类为 agent_relay。
    assert classify_entry_kind(None, _HUMAN) is EntryKind.AGENT_RELAY
    # 空集合配置下一律 relay（无白名单=无人机入口）。
    assert classify_entry_kind("trae.exe", frozenset()) is EntryKind.AGENT_RELAY


# ------------------------------------------------------------ 停机协作


def test_shutdown_coordinator_parent_exit_invokes_cancel() -> None:
    cancelled = threading.Event()
    coordinator = ShutdownCoordinator(
        parent_pid=4242, is_alive=lambda _pid: False
    )
    coordinator.bind_accept(cancelled.set)
    thread = coordinator.start_watchdog()
    thread.join(5)
    assert not thread.is_alive()
    assert coordinator.exit_requested
    assert coordinator.reason == "parent_exited"
    assert cancelled.is_set()


def test_shutdown_coordinator_keeps_running_while_parent_alive() -> None:
    coordinator = ShutdownCoordinator(
        parent_pid=4242,
        is_alive=lambda _pid: True,
        interval_seconds=0.01,
    )
    thread = coordinator.start_watchdog()
    coordinator.request_exit("shutdown_frame")
    thread.join(5)
    assert coordinator.reason == "shutdown_frame"


def test_shutdown_coordinator_cancel_failure_is_swallowed() -> None:
    def bad_cancel() -> None:
        raise OSError("pipe already gone")

    coordinator = ShutdownCoordinator(
        parent_pid=1, is_alive=lambda _pid: True
    )
    coordinator.bind_accept(bad_cancel)
    # 取消回调异常不得阻止退出请求落地。
    coordinator.request_exit("parent_exited")
    assert coordinator.exit_requested


# ------------------------------------------------------------ 在途 spool 抢救


def test_seal_inflight_spool_appends_unsealed_tail(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    # 建立 attempt 清单（崩溃前包装器已创建）。
    stream = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_size=4096,
    )
    stream.abort()  # 写入端进程崩溃；未入清单的在途字节按崩溃留在盘上建模。
    tail = b"partial output without seal"
    log = tmp_path / "spool" / "attempt-1" / "stdout.log"
    log.write_bytes(tail)

    manifest_before = store.read_manifest("attempt-1")
    assert not manifest_before.blocks

    sealed = seal_inflight_outputs(tmp_path)
    assert sealed == ("attempt-1",)

    manifest_after = store.read_manifest("attempt-1")
    assert len(manifest_after.blocks) == 1
    assert manifest_after.blocks[0].length == len(tail)

    # 二次抢救无新增块，不再重复声称。
    assert seal_inflight_outputs(tmp_path) == ()


def test_seal_inflight_spool_ignores_empty_and_missing_manifest(
    tmp_path: Path,
) -> None:
    # 无 spool 目录：空结论。
    assert seal_inflight_outputs(tmp_path) == ()

    spool = tmp_path / "spool"
    # 有清单但无任何流字节：不抢救。
    quiet = spool / "attempt-quiet"
    quiet.mkdir(parents=True)
    (quiet / "manifest.json").write_text(
        '{"attempt_id":"attempt-quiet","run_id":"r","step_id":"s",'
        '"blocks":[],"cursors":[]}',
        encoding="utf-8",
    )
    # 无清单的游离目录：跳过（身份事实不足，留启动恢复）。
    orphan = spool / "attempt-orphan"
    orphan.mkdir()
    (orphan / "stdout.log").write_bytes(b"x")

    assert seal_inflight_outputs(tmp_path) == ()
