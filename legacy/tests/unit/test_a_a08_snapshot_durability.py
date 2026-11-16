"""A-08：SourceSnapshot Blob 耐久发布（显式 fsync）与重启可读。

掉电语义无法在单测中真实制造，这里验证耐久发布的可观察合同：
- blob 字节在 os.replace 前完成 fsync，顺序不可颠倒；
- fsync 失败时 pin 必须失败且不留半成品 blob/tmp；
- 目录项 fsync 尽力而为，平台不支持时不得影响发布；
- “重启”（全新 store 实例）后清单/历史 blob 仍可读、可物化并复核摘要；
- materialize 落盘同样显式 fsync。
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.infrastructure.adapters import source_snapshot
from aitest.infrastructure.adapters.source_snapshot import FileSourceSnapshotStore


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("print('v1')\n", encoding="utf-8")
    sub = source / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    return source


def test_blob_bytes_fsynced_before_replace(
    tmp_path: Path, source_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fsync = os.fsync
    real_replace = os.replace
    state = {"fsync_calls": 0, "replace_observations": []}

    def counting_fsync(fd: int) -> None:
        state["fsync_calls"] += 1
        real_fsync(fd)

    def tracking_replace(
        src: str | os.PathLike[str], dst: str | os.PathLike[str]
    ) -> None:
        state["replace_observations"].append(
            (Path(str(dst)), state["fsync_calls"])
        )
        real_replace(src, dst)

    monkeypatch.setattr(source_snapshot.os, "fsync", counting_fsync)
    monkeypatch.setattr(source_snapshot.os, "replace", tracking_replace)

    store = FileSourceSnapshotStore(tmp_path / "ws")
    record = store.pin(canonical_path=str(source_tree), purpose="run-1")

    assert state["fsync_calls"] >= 2  # 两个源文件 blob 各一次

    blobs_dir = tmp_path / "ws" / "snapshots" / "blobs"
    blob_replaces = [
        (destination, fsync_before)
        for destination, fsync_before in state["replace_observations"]
        if destination.parent == blobs_dir
    ]
    assert len(blob_replaces) == 2
    # 每个 blob 进入内容寻址区之前，至少完成过一次数据 fsync。
    assert all(fsync_before >= 1 for _destination, fsync_before in blob_replaces)

    for item in record["files"]:
        blob = blobs_dir / str(item["sha256"])
        assert blob.is_file()
        assert not list(blobs_dir.glob(".*.tmp"))


def test_fsync_failure_aborts_publish_and_cleans_tmp(
    tmp_path: Path, source_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_fsync(fd: int) -> None:
        raise OSError("simulated disk failure during fsync")

    monkeypatch.setattr(source_snapshot.os, "fsync", failing_fsync)

    store = FileSourceSnapshotStore(tmp_path / "ws")
    with pytest.raises(OSError, match="disk failure"):
        store.pin(canonical_path=str(source_tree), purpose="run-fail")

    blobs = tmp_path / "ws" / "snapshots" / "blobs"
    assert list(blobs.glob("*")) == []
    assert not list(blobs.glob(".*.tmp"))


def test_directory_fsync_failure_is_best_effort(
    tmp_path: Path, source_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = os.open

    def refuse_open(path: object, flags: object, *args: object) -> int:
        # 仅拦截目录只读打开（fsync directory 路径）；mkstemp 建临时文件
        # 带 O_CREAT，必须继续放行，清单发布不受影响。
        if isinstance(flags, int) and not (flags & os.O_CREAT):
            raise PermissionError("simulated directory open refusal")
        return real_open(path, flags, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(source_snapshot.os, "open", refuse_open)
    # 直接走无平台门的实现：异常必须被吞掉。
    assert source_snapshot._try_fsync_directory(tmp_path) is None

    # 模拟 POSIX 环境下目录 fsync 不被支持：发布不得失败。
    monkeypatch.setattr(
        source_snapshot, "_fsync_directory", source_snapshot._try_fsync_directory
    )
    store = FileSourceSnapshotStore(tmp_path / "ws")
    record = store.pin(canonical_path=str(source_tree), purpose="run-dirs")
    assert record["snapshot_id"].startswith("snap-")
    assert len(record["files"]) == 2


def test_pinned_blobs_survive_restart_and_keep_history(
    tmp_path: Path, source_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fsync = os.fsync
    fsync_count = 0

    def counting_fsync(fd: int) -> None:
        nonlocal fsync_count
        fsync_count += 1
        real_fsync(fd)

    monkeypatch.setattr(source_snapshot.os, "fsync", counting_fsync)

    workspace = tmp_path / "ws"
    store = FileSourceSnapshotStore(workspace)
    first = store.pin(canonical_path=str(source_tree), purpose="run-1")
    first_id = str(first["snapshot_id"])
    first_digest = str(first["files"][0]["sha256"])

    # 源码演进后重新固定：新快照；旧 blob 必须保留为历史字节。
    (source_tree / "main.py").write_text("print('v2')\n", encoding="utf-8")
    second = FileSourceSnapshotStore(workspace).pin(
        canonical_path=str(source_tree), purpose="run-2"
    )
    second_id = str(second["snapshot_id"])
    assert second_id != first_id

    # “重启”：全新进程视角的 store 实例读取两份不可变清单。
    restarted = FileSourceSnapshotStore(workspace)
    old_record = restarted.read_pinned(first_id)
    new_record = restarted.read_pinned(second_id)
    assert old_record["purpose"] == "run-1"
    assert new_record["purpose"] == "run-2"

    # 旧快照样物化得到当时的历史字节并复核摘要通过。
    fsync_before_materialize = fsync_count
    materialized = restarted.materialize(first_id, str(tmp_path / "out-old"))
    assert materialized["state"] == "materialized"
    assert materialized["verified"] is True
    assert fsync_count > fsync_before_materialize
    old_main = tmp_path / "out-old" / "main.py"
    assert old_main.read_text(encoding="utf-8") == "print('v1')\n"
    assert hashlib.sha256(old_main.read_bytes()).hexdigest() == first_digest

    # 新快照样物化得到 v2；两份 blob 同时可达。
    new_out = restarted.materialize(second_id, str(tmp_path / "out-new"))
    assert new_out["verified"] is True
    assert (tmp_path / "out-new" / "main.py").read_text(encoding="utf-8") == (
        "print('v2')\n"
    )
    blobs = list((workspace / "snapshots" / "blobs").glob("*"))
    assert len(blobs) >= 3


def test_repeated_pin_is_idempotent_after_restart(
    tmp_path: Path, source_tree: Path
) -> None:
    workspace = tmp_path / "ws"
    first = FileSourceSnapshotStore(workspace).pin(
        canonical_path=str(source_tree), purpose="run-1"
    )
    second = FileSourceSnapshotStore(workspace).pin(
        canonical_path=str(source_tree), purpose="run-1"
    )
    # 同范围/用途：重启后重复 pin 身份相同，既有清单不被覆盖。
    assert second["snapshot_id"] == first["snapshot_id"]
    assert second["purpose"] == "run-1"
    # A-15：用途不同即使字节相同也是不同快照身份与清单。
    other = FileSourceSnapshotStore(workspace).pin(
        canonical_path=str(source_tree), purpose="run-other"
    )
    assert other["snapshot_id"] != first["snapshot_id"]
    assert other["purpose"] == "run-other"
