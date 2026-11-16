"""Invalid capture batches are rejected before output bytes or writer ownership change."""

import json
from dataclasses import replace

import pytest

from aitest.domain.execution.runs import OutputStreamName
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.recovery.test_spool_store import _block


def _open(store, **changes):
    arguments = dict(
        run_id="run-1",
        step_id="step-1",
        attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
    )
    return store.open_stream(**(arguments | changes))


def test_foreign_namespace_is_rejected_before_appending_to_existing_output(tmp_path):
    store = FileSpoolStore(tmp_path)
    original = store.persist_blocks((_block(b"saved"),))
    path = tmp_path / "spool/attempt-1/stdout.log"
    with pytest.raises(ValueError, match="identity"):
        store.persist_blocks(
            (replace(_block(b"foreign", block_index=1, offset=5), run_id="other-run"),)
        )
    assert path.read_bytes() == b"saved"
    assert store.read_manifest("attempt-1") == original


@pytest.mark.parametrize(
    "change",
    [
        {"offset": 5},
        {"block_index": 2},
        {"complete": "false"},
        {"offset": False},
        {"block_index": False},
        {"content": bytearray(b"mutable")},
    ],
)
def test_invalid_first_block_never_creates_output_bytes(tmp_path, change):
    store = FileSpoolStore(tmp_path)
    with pytest.raises(ValueError):
        store.persist_blocks((replace(_block(b"invalid"), **change),))
    assert not (tmp_path / "spool/attempt-1/stdout.log").exists()


def test_later_invalid_block_is_detected_before_first_batch_byte(tmp_path):
    store = FileSpoolStore(tmp_path)
    with pytest.raises(ValueError, match="contiguous"):
        store.persist_blocks((_block(b"first"), _block(b"bad", block_index=1, offset=99)))
    assert not (tmp_path / "spool/attempt-1/stdout.log").exists()


def test_conflicting_metadata_does_not_leave_unclaimed_tail(tmp_path):
    store = FileSpoolStore(tmp_path)
    original = store.persist_blocks((_block(b"saved"),))
    with pytest.raises(ValueError, match="conflict"):
        store.persist_blocks((_block(b"tail", block_index=0, offset=5),))
    assert (tmp_path / "spool/attempt-1/stdout.log").read_bytes() == b"saved"
    assert store.read_manifest("attempt-1") == original


def test_duplicate_block_retry_retains_exact_bytes_and_metadata(tmp_path):
    store = FileSpoolStore(tmp_path)
    batch = (_block(b"one"), _block(b"two", block_index=1, offset=3))
    first = store.persist_blocks(batch)
    assert store.persist_blocks(batch) == first
    assert (tmp_path / "spool/attempt-1/stdout.log").read_bytes() == b"onetwo"


def test_retrying_only_an_older_block_does_not_rewind_the_committed_cursor(tmp_path):
    store = FileSpoolStore(tmp_path)
    first = _block(b"one")
    original = store.persist_blocks((first, _block(b"two", block_index=1, offset=3)))
    assert store.persist_blocks((first,)) == original


@pytest.mark.parametrize("field", ["offset", "last_committed_digest"])
def test_bad_existing_cursor_is_rejected_before_another_append(tmp_path, field):
    store = FileSpoolStore(tmp_path)
    store.persist_blocks((_block(b"saved"),))
    path = tmp_path / "spool/attempt-1/manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cursors"][0][field] = 99 if field == "offset" else "sha256:foreign"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="cursor"):
        store.persist_blocks((_block(b"tail", block_index=1, offset=5),))
    assert (tmp_path / "spool/attempt-1/stdout.log").read_bytes() == b"saved"


@pytest.mark.parametrize("action", ["open", "persist"])
def test_every_manifest_write_path_uses_strict_duplicate_field_reader(tmp_path, action):
    store = FileSpoolStore(tmp_path)
    _open(store).close()
    path = tmp_path / "spool/attempt-1/manifest.json"
    raw = path.read_text(encoding="utf-8")
    value = json.dumps("step-1")
    needle = '"step_id": ' + value
    assert needle in raw
    path.write_text(raw.replace(needle, needle + ", " + needle, 1), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate field"):
        if action == "open":
            _open(store).abort()
        else:
            store.persist_blocks((_block(b"should-not-write"),))
    assert (tmp_path / "spool/attempt-1/stdout.log").read_bytes() == b""


def test_failed_writer_initialization_releases_os_capture_lease(tmp_path, monkeypatch):
    store = FileSpoolStore(tmp_path)
    retained_lease = store._capture_lock("attempt-1", OutputStreamName.STDOUT)
    monkeypatch.setattr(store, "_capture_lock", lambda *args: retained_lease)

    def fail(*args):
        raise RuntimeError("injected metadata read failure")

    monkeypatch.setattr(store, "_next_block_index", fail)
    with pytest.raises(RuntimeError, match="injected"):
        _open(store)
    try:
        restarted = _open(FileSpoolStore(tmp_path))
        restarted.close()
    finally:
        retained_lease.release()


def test_unsealed_prior_tail_requires_recovery_before_a_new_writer(tmp_path):
    store = FileSpoolStore(tmp_path)
    writer = _open(store, block_size=100)
    writer.append(b"unsealed prior output")
    writer.abort()
    with pytest.raises(ValueError, match="boundary"):
        _open(FileSpoolStore(tmp_path)).abort()
    recovered = store.salvage_streams("attempt-1")
    assert store.read_block(recovered.blocks[0]) == b"unsealed prior output"
    _open(FileSpoolStore(tmp_path)).close()


@pytest.mark.parametrize("size", [True, 1.5, 0])
def test_writer_requires_exact_positive_integer_block_size(tmp_path, size):
    with pytest.raises(ValueError, match="block_size"):
        _open(FileSpoolStore(tmp_path), block_size=size).abort()
    assert not (tmp_path / "spool/attempt-1/manifest.json").exists()
