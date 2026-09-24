from pathlib import Path

import pytest

from aitest.domain.execution.runs import CapturedOutputBlock, OutputStreamName
from aitest.infrastructure.file_store.spool import FileSpoolStore


def _block(
    content: bytes,
    *,
    block_index: int = 0,
    stream_name: OutputStreamName = OutputStreamName.STDOUT,
    complete: bool = True,
) -> CapturedOutputBlock:
    return CapturedOutputBlock(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=stream_name,
        block_index=block_index,
        offset=block_index * 16,
        content=content,
        complete=complete,
        capture_source="fake",
    )


def test_spool_persists_sealed_blocks_and_manifest(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    manifest = store.persist_blocks(
        (
            _block(b"hello"),
            _block(b"world", block_index=1),
        )
    )

    assert manifest.attempt_id == "attempt-1"
    assert manifest.run_id == "run-1"
    assert manifest.step_id == "step-1"
    assert [block.length for block in manifest.blocks] == [5, 5]
    assert store.read_manifest("attempt-1") == manifest
    assert store.read_block(manifest.blocks[0]) == b"hello"
    assert (tmp_path / "spool" / "attempt-1" / "stdout-0.bin").read_bytes() == b"hello"
    assert (tmp_path / "spool" / "attempt-1" / "manifest.json").exists()


def test_spool_rejects_unsealed_block(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    with pytest.raises(ValueError, match="sealed"):
        store.persist_blocks((_block(b"partial", complete=False),))


def test_spool_rejects_conflicting_immutable_block(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    store.persist_blocks((_block(b"first"),))
    with pytest.raises(ValueError, match="different bytes"):
        store.persist_blocks((_block(b"second"),))


def test_spool_rejects_path_traversal_identity(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    block = CapturedOutputBlock(
        run_id="run-1",
        step_id="step-1",
        attempt_id="../outside",
        stream_name=OutputStreamName.STDOUT,
        block_index=0,
        offset=0,
        content=b"unsafe",
    )
    with pytest.raises(ValueError, match="safe path"):
        store.persist_blocks((block,))
