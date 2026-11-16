"""A 包本地核心底座独立验收用例。

这些用例只触碰 A 包的文件存储原语。索引分页使用已有的端口合同夹具；
真实 file_store 的 records/index 实现完成后，应将同一断言接到文件后端。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import aitest
from aitest.application.planning.substrate import RecordQuery
from aitest.infrastructure.file_store import atomic
from aitest.infrastructure.file_store.locking import writer_lock
from aitest.infrastructure.file_store.objects import FileObjectStore
from tests.support.memory_substrate import MemoryReader, MemoryStore, MemoryUnitOfWork


def _child_env() -> dict[str, str]:
    """构造子进程环境，显式把 aitest 所在的 src 目录注入 ``PYTHONPATH``。

    pytest 的 ``pythonpath`` 配置只对当前解释器生效；subprocess 派生的
    独立解释器只继承环境变量，未 pip 安装本包时必须显式传入 src 路径，
    子进程才能 ``import aitest``。
    """
    src_dir = str(Path(aitest.__file__).resolve().parent.parent)
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        os.pathsep.join([src_dir, existing]) if existing else src_dir
    )
    return env


def test_single_writer_lock_is_exclusive_and_recoverable(tmp_path: Path) -> None:
    lock_path = tmp_path / "writer.lock"
    child = (
        "from pathlib import Path; "
        "from aitest.infrastructure.file_store.locking import writer_lock; "
        "import sys\n"
        "with writer_lock(Path(sys.argv[1])): pass\n"
    )

    with writer_lock(lock_path):
        blocked = subprocess.run(
            [sys.executable, "-c", child, str(lock_path)],
            capture_output=True,
            text=True,
            env=_child_env(),
        )
        assert blocked.returncode != 0
        assert "WorkspaceInUse" in blocked.stderr

    recovered = subprocess.run(
        [sys.executable, "-c", child, str(lock_path)],
        capture_output=True,
        text=True,
        env=_child_env(),
    )
    assert recovered.returncode == 0


def test_crash_before_pointer_publish_keeps_last_committed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current.json"
    atomic.write_json(current, {"commit": "committed-1"})

    def crash_at_publish(*args: object, **kwargs: object) -> None:
        raise OSError("simulated process loss before current replacement")

    monkeypatch.setattr(atomic.os, "replace", crash_at_publish)
    with pytest.raises(OSError, match="process loss"):
        atomic.write_json(current, {"commit": "committed-2"})

    assert json.loads(current.read_text(encoding="utf-8")) == {"commit": "committed-1"}
    assert list(tmp_path.glob(".current.json.*")) == []


def test_content_addressed_history_is_immutable_and_integrity_checked(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path)
    first = store.publish_bytes("project-1", b"first")

    with pytest.raises(ValueError, match="conflicts"):
        store._write_immutable(tmp_path / first.relative_path, b"different")
    assert store.read_bytes(first) == b"first"

    object_path = tmp_path / first.relative_path
    object_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        store.read_bytes(first)


def test_query_contract_exposes_a_bounded_page_marker() -> None:
    store = MemoryStore()
    writer = MemoryUnitOfWork(store)
    reader = MemoryReader(store)
    for index in range(3):
        writer.open("project-1")
        writer.stage_record(
            aggregate_kind="case",
            record_id=f"case-{index}",
            expected_revision=None,
            payload={"project_id": "project-1", "summary": f"case-{index}"},
        )
        writer.commit()

    first = reader.query(RecordQuery(project_id="project-1", aggregate_kind="case", limit=2))
    assert [item.record_id for item in first.items] == ["case-0", "case-1"]
    assert first.next_cursor == "2"


def test_storage_publish_error_preserves_existing_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current.json"
    atomic.write_json(current, {"commit": "safe"})

    def no_space(*args: object, **kwargs: object) -> None:
        raise OSError("simulated storage unavailable")

    monkeypatch.setattr(atomic.os, "replace", no_space)
    with pytest.raises(OSError, match="storage unavailable"):
        atomic.write_json(current, {"commit": "lost"}, attempts=1)

    assert json.loads(current.read_text(encoding="utf-8")) == {"commit": "safe"}
