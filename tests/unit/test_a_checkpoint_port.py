"""AC-001 §7.3：CheckpointPort 端口合同测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from aitest.application.ports import CheckpointPort
from aitest.domain.execution.runs import (
    AdapterKind,
    Attempt,
    RecoveryCheckpoint,
    RecoveryRecord,
    SideEffectClass,
    StepRevisionRef,
)
from aitest.infrastructure.file_store.checkpoints import FileCheckpointStore


def _attempt(attempt_id: str = "attempt-1", run_id: str = "run-1") -> Attempt:
    return Attempt(
        attempt_id=attempt_id,
        run_id=run_id,
        step_id="step-1",
        attempt_index=1,
        resolved_input_digest="sha256:abc",
        step_revision_ref=StepRevisionRef(
            step_revision_id="steprev-1", revision_no=1, digest="sha256:sr"
        ),
        source_binding_digest="sha256:def",
        side_effect_class=SideEffectClass.UNKNOWN,
        adapter_kind=AdapterKind.PYTHON_CHECKS,
        adapter_version="python-checks/1.0",
    )


def _checkpoint(
    attempt_id: str = "attempt-1", run_id: str = "run-1"
) -> RecoveryCheckpoint:
    return RecoveryCheckpoint(
        run_id=run_id,
        step_id="step-1",
        attempt_id=attempt_id,
        last_committed_stage="staged",
        side_effect_class=SideEffectClass.UNKNOWN,
    )


def _record(attempt_id: str = "attempt-1") -> RecoveryRecord:
    return RecoveryRecord(
        checkpoint=_checkpoint(attempt_id=attempt_id),
        attempt=_attempt(attempt_id=attempt_id),
    )


def test_port_signatures_match_file_checkpoint_store(tmp_path: Path) -> None:
    """FileCheckpointStore 的 persist/load/scan 签名满足 CheckpointPort 合同。"""
    store = FileCheckpointStore(tmp_path)
    # 结构检查：三个方法均存在且可调用
    assert callable(store.persist)
    assert callable(store.load)
    assert callable(store.scan)

    record = _record()
    path = store.persist(record)
    assert isinstance(path, Path)
    assert path.exists()

    loaded = store.load("attempt-1")
    assert loaded.attempt.attempt_id == "attempt-1"
    assert loaded.checkpoint.run_id == "run-1"

    scanned = store.scan()
    assert isinstance(scanned, tuple)
    assert len(scanned) == 1
    assert scanned[0].attempt.attempt_id == "attempt-1"


def test_checkpoint_record_version_is_pinned(tmp_path: Path) -> None:
    """落盘 payload 固定携带 aitest.recovery-checkpoint/1.0。"""
    import json

    store = FileCheckpointStore(tmp_path)
    path = store.persist(_record())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "aitest.recovery-checkpoint/1.0"


def test_scan_is_read_only(tmp_path: Path) -> None:
    """scan() 只读：不创建目录、不修改已有文件。"""
    store = FileCheckpointStore(tmp_path)
    assert store.scan() == ()
    assert not (tmp_path / "checkpoints").exists()

    store.persist(_record("attempt-a"))
    store.persist(_record("attempt-b"))
    before = sorted(p.name for p in (tmp_path / "checkpoints").glob("*.json"))
    scanned = store.scan()
    after = sorted(p.name for p in (tmp_path / "checkpoints").glob("*.json"))
    assert before == after
    assert {r.attempt.attempt_id for r in scanned} == {"attempt-a", "attempt-b"}


def test_caller_cannot_instantiate_store_directly_via_port() -> None:
    """合同约束：CheckpointPort 是 Protocol，调用方不得实例化基础设施实现。"""
    import typing

    # Protocol 在 Python 运行时是 class，但本身不能被实例化。
    assert isinstance(CheckpointPort, type)
    assert issubclass(CheckpointPort, typing.Protocol)
    # 试图直接实例化 Protocol 会抛出 TypeError
    try:
        CheckpointPort()  # type: ignore[misc]
    except TypeError:
        pass
    else:
        raise AssertionError("CheckpointPort must not be instantiable directly")
