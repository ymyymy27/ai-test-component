from pathlib import Path

import pytest

from aitest.domain.execution.runs import CapturedOutputBlock, OutputStreamName
from aitest.infrastructure.file_store.spool import FileSpoolStore


def _block(
    content: bytes,
    *,
    block_index: int = 0,
    offset: int = 0,
    stream_name: OutputStreamName = OutputStreamName.STDOUT,
    complete: bool = True,
) -> CapturedOutputBlock:
    return CapturedOutputBlock(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=stream_name,
        block_index=block_index,
        offset=offset,
        content=content,
        complete=complete,
        capture_source="fake",
    )


def test_spool_persists_sealed_blocks_and_manifest(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    manifest = store.persist_blocks(
        (
            _block(b"hello", block_index=0, offset=0),
            _block(b"world", block_index=1, offset=5),
        )
    )

    assert manifest.attempt_id == "attempt-1"
    assert manifest.run_id == "run-1"
    assert manifest.step_id == "step-1"
    assert [block.length for block in manifest.blocks] == [5, 5]
    assert len(manifest.cursors) == 1
    assert manifest.cursors[0].offset == 10
    assert store.read_manifest("attempt-1") == manifest
    assert store.read_block(manifest.blocks[0]) == b"hello"
    assert store.read_block(manifest.blocks[1]) == b"world"
    assert (tmp_path / "spool" / "attempt-1" / "stdout.log").read_bytes() == b"helloworld"
    assert (tmp_path / "spool" / "attempt-1" / "manifest.json").exists()


def test_spool_appends_while_stream_is_still_open(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
        block_size=5,
    )
    assert writer.append(b"he") == ()
    sealed = writer.append(b"llo")
    assert len(sealed) == 1

    manifest = store.read_manifest("attempt-1")
    assert manifest.cursors[0].offset == 5
    assert store.read_block(manifest.blocks[0]) == b"hello"
    writer.close()


def test_spool_tracks_stdout_and_stderr_cursors_independently(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    stdout = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
    )
    stderr = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDERR,
    )
    stdout.append(b"out")
    stderr.append(b"error")
    stdout.close()
    stderr.close()

    manifest = store.read_manifest("attempt-1")
    cursors = {cursor.stream_name: cursor for cursor in manifest.cursors}
    assert cursors[OutputStreamName.STDOUT].offset == 3
    assert cursors[OutputStreamName.STDERR].offset == 5
    blocks = {block.stream_name: block for block in manifest.blocks}
    assert store.read_block(blocks[OutputStreamName.STDOUT]) == b"out"
    assert store.read_block(blocks[OutputStreamName.STDERR]) == b"error"


def test_spool_rejects_unsealed_block(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    with pytest.raises(ValueError, match="sealed"):
        store.persist_blocks((_block(b"partial", complete=False),))


def test_spool_rejects_conflicting_immutable_block(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    store.persist_blocks((_block(b"first"),))
    with pytest.raises(ValueError, match="conflicts"):
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
