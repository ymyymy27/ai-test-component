"""Untrusted redaction metadata and actual unresolved output bytes."""

import json
import time
from dataclasses import replace

import pytest

from aitest.domain.evidence.evidence import RedactionSummary
from aitest.domain.execution.runs import CaptureCompleteness, OutputStreamName
from aitest.infrastructure.adapters.execution.command import CommandAdapter
from aitest.infrastructure.adapters.execution.redaction import StreamingRedactor
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.security import KnownSecretRegistry, UnsafeMaterialError
from tests.unit.test_command_adapter import _adapter, _request


def summary():
    return RedactionSummary(
        "aitest.redaction/1.0", filtered_streams=("stdout",),
        filtered_ranges=("stdout:0-10",), completeness="complete",
    )


@pytest.mark.parametrize("field,value", [
    ("summary_id", "redaction:another-attempt:stdout"),
    ("stream_name", "stderr"),
    ("filtered_streams", ["stderr"]),
    ("filtered_ranges", ["stderr:0-10"]),
    ("completeness", "passed"),
    ("gap_reasons", ["missing_tail"]),
    ("unknown", "unverified"),
])
def test_summary_with_wrong_identity_or_semantics_is_not_readable(tmp_path, field, value):
    store = FileSpoolStore(tmp_path)
    store.persist_redaction_summary("attempt-1", OutputStreamName.STDOUT, summary())
    path = tmp_path / "spool/attempt-1/redaction-stdout.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        store.read_redaction_summary("attempt-1", OutputStreamName.STDOUT)


def test_no_newline_output_cannot_grow_unresolved_memory_forever():
    redactor = StreamingRedactor()
    with pytest.raises(UnsafeMaterialError):
        for _ in range(257):
            redactor.feed(b"a" * 4096)


def test_spool_never_dumps_an_oversized_unresolved_secret_tail(tmp_path):
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1", step_id="step-1", attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
    )
    try:
        with pytest.raises(UnsafeMaterialError):
            for _ in range(257):
                writer.append(b"a" * 4096)
    finally:
        writer.abort()
    assert (tmp_path / "spool/attempt-1/stdout.log").read_bytes() == b""


@pytest.mark.parametrize("change", ["duplicate", "non_json", "utf16", "oversize"])
def test_ambiguous_or_oversized_summary_bytes_are_rejected(tmp_path, change):
    store = FileSpoolStore(tmp_path)
    store.persist_redaction_summary("attempt-1", OutputStreamName.STDOUT, summary())
    path = tmp_path / "spool/attempt-1/redaction-stdout.json"
    raw = path.read_bytes()
    if change == "duplicate":
        raw = raw.replace(
            b'"stream_name": "stdout"', b'"stream_name":"stderr","stream_name":"stdout"',
        )
    elif change == "non_json":
        raw = raw.replace(b'"replacement_count": 0', b'"replacement_count": NaN')
    elif change == "utf16":
        raw = raw.decode("utf-8").encode("utf-16")
    else:
        raw += b" " * (64 * 1024)
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        store.read_redaction_summary("attempt-1", OutputStreamName.STDOUT)


def test_summary_validation_precedes_atomic_write_and_preserves_old_material(tmp_path):
    registry = KnownSecretRegistry()
    store = FileSpoolStore(tmp_path, registry=registry)
    store.persist_redaction_summary("attempt-1", OutputStreamName.STDOUT, summary())
    path = tmp_path / "spool/attempt-1/redaction-stdout.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store.persist_redaction_summary(
            "attempt-1", OutputStreamName.STDOUT,
            replace(summary(), filtered_ranges=("stderr:0-10",)),
        )
    registry.register("synthetic-summary-credential")
    with pytest.raises(UnsafeMaterialError):
        store.persist_redaction_summary(
            "attempt-1", OutputStreamName.STDOUT,
            replace(summary(), policy_version="synthetic-summary-credential"),
        )
    with pytest.raises(ValueError, match="budget"):
        store.persist_redaction_summary(
            "attempt-1", OutputStreamName.STDOUT,
            replace(summary(), policy_version="a" * (64 * 1024)),
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("budget", [True, 0, -1, 1.5, "100", 1024 * 1024 + 1])
def test_capture_budgets_require_exact_positive_bounded_integers(tmp_path, budget):
    with pytest.raises(ValueError):
        StreamingRedactor(max_pending_bytes=budget)
    with pytest.raises(ValueError):
        FileSpoolStore(tmp_path, max_pending_bytes=budget)
    with pytest.raises(ValueError):
        CommandAdapter(max_in_memory_bytes=budget)
    assert not (tmp_path / "spool").exists()


def test_feed_can_stream_many_safe_lines_and_failed_tail_is_not_flushed():
    redactor = StreamingRedactor(max_pending_bytes=16)
    assert redactor.feed(b"safe\n" * 100) == b"safe\n" * 100
    assert redactor.feed(b"a" * 16) == b""
    with pytest.raises(UnsafeMaterialError):
        redactor.feed(b"b")
    assert not redactor._pending
    with pytest.raises(RuntimeError):
        redactor.finish()
    assert redactor.stats.output_bytes == 500


def test_spool_overflow_retains_safe_prefix_as_partial_and_disallows_continuation(tmp_path):
    store = FileSpoolStore(tmp_path, max_pending_bytes=16)
    writer = store.open_stream(
        run_id="run-1", step_id="step-1", attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
    )
    assert writer.append(b"safe prefix\n") == ()
    try:
        with pytest.raises(UnsafeMaterialError):
            writer.append(b"sk-" + b"a" * 100)
        with pytest.raises(UnsafeMaterialError):
            writer.append(b"after rejected output\n")
        refs = writer.close()
    finally:
        writer.abort()
    assert len(refs) == 1 and refs[0].complete is False
    assert store.read_block(refs[0]) == b"safe prefix\n"
    assert (tmp_path / "spool/attempt-1/stdout.log").read_bytes() == b"safe prefix\n"


def test_actual_command_no_newline_overflow_is_gap_with_no_unresolved_disk_output(tmp_path):
    store = FileSpoolStore(tmp_path)
    adapter = _adapter(store)
    handle = adapter.start(_request("python", (
        "-c", "import sys;sys.stdout.buffer.write(b'a'*(2*1024*1024));sys.stdout.flush()",
    )))
    try:
        deadline = time.monotonic() + 10
        while adapter.inspect(handle).process_reachable and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not adapter.inspect(handle).process_reachable
        collected = adapter.collect(handle)
        assert collected.capture_completeness is CaptureCompleteness.GAP
        assert not collected.complete and collected.exit_fact_ref is None
        actual = store.read_redaction_summary("attempt-1", OutputStreamName.STDOUT)
        assert actual.completeness == "gap" and actual.gap_reasons == ("reader_error",)
        assert (tmp_path / "spool/attempt-1/stdout.log").read_bytes() == b""
    finally:
        adapter.request_stop(handle)


def test_command_without_spool_cannot_accumulate_unbounded_complete_lines():
    adapter = _adapter()
    handle = adapter.start(_request("python", (
        "-c", "import sys;sys.stdout.buffer.write(b'safe\\n'*(500000));sys.stdout.flush()",
    )))
    try:
        deadline = time.monotonic() + 10
        while adapter.inspect(handle).process_reachable and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not adapter.inspect(handle).process_reachable
        result = adapter.collect(handle)
        assert result.capture_completeness is CaptureCompleteness.GAP
        runtime = adapter._runtimes[handle.handle_id]
        assert sum(len(value) for value in runtime.captured_buffers.values()) <= 1024 * 1024
        assert not result.complete and result.exit_fact_ref is None
    finally:
        adapter.request_stop(handle)


def test_secondary_filter_cannot_claim_complete_zero_replacement_summary(tmp_path):
    registry = KnownSecretRegistry()
    secret = "0123456789"  # Same length as [REDACTED]; byte totals cannot detect this change.
    registry.register(secret)
    store = FileSpoolStore(tmp_path, registry=registry)
    adapter = _adapter(store)  # Only the spool policy knows this synthetic credential.
    handle = adapter.start(_request("python", ("-c", "print('hello 0123456789')")))
    try:
        deadline = time.monotonic() + 10
        while adapter.inspect(handle).process_reachable and time.monotonic() < deadline:
            time.sleep(0.01)
        result = adapter.collect(handle)
        assert result.complete and result.capture_completeness is CaptureCompleteness.COMPLETE
        manifest = store.read_manifest("attempt-1")
        assert b"".join(
            store.read_block(block) for block in manifest.blocks
            if block.stream_name is OutputStreamName.STDOUT
        ).rstrip(b"\r\n") == b"hello [REDACTED]"
        actual = store.read_redaction_summary("attempt-1", OutputStreamName.STDOUT)
        assert actual.completeness != "complete" or actual.replacement_count >= 1
    finally:
        adapter.request_stop(handle)


def test_redaction_inspection_only_reports_after_reliable_sealing(tmp_path):
    registry = KnownSecretRegistry()
    registry.register("0123456789")
    store = FileSpoolStore(tmp_path, registry=registry)
    writer = store.open_stream(
        run_id="run-1", step_id="step-1", attempt_id="attempt-1",
        stream_name=OutputStreamName.STDOUT,
    )
    assert writer.redaction_changed is None
    writer.append(b"hello 0123456789\n")
    assert writer.redaction_changed is None
    writer.close()
    assert writer.redaction_changed is True
    abandoned = store.open_stream(
        run_id="run-1", step_id="step-1", attempt_id="attempt-2",
        stream_name=OutputStreamName.STDOUT,
    )
    abandoned.append(b"unresolved")
    abandoned.abort()
    assert abandoned.redaction_changed is None


def test_primary_statistics_remain_complete_when_secondary_layer_did_not_change_bytes(tmp_path):
    store = FileSpoolStore(tmp_path, registry=KnownSecretRegistry())
    adapter = _adapter(store)
    handle = adapter.start(_request("python", ("-c", "print('hello secret-'+'value')")))
    try:
        deadline = time.monotonic() + 10
        while adapter.inspect(handle).process_reachable and time.monotonic() < deadline:
            time.sleep(0.01)
        result = adapter.collect(handle)
        assert result.complete
        actual = store.read_redaction_summary("attempt-1", OutputStreamName.STDOUT)
        assert actual.completeness == "complete" and actual.replacement_count == 1
        assert actual.gap_reasons == ()
    finally:
        adapter.request_stop(handle)


@pytest.mark.parametrize("observation", [None, 0, 1, "false", "missing", "error"])
def test_missing_or_invalid_secondary_observation_is_unknown_not_false(
    tmp_path, monkeypatch, observation,
):
    store = FileSpoolStore(tmp_path)
    original = store.open_stream

    class OldWriter:
        def __init__(self, writer):
            self.writer = writer

        def append(self, content):
            return self.writer.append(content)

        def close(self, **kwargs):
            return self.writer.close(**kwargs)

        def abort(self):
            self.writer.abort()

        @property
        def redaction_changed(self):
            if observation == "missing":
                raise AttributeError("legacy writer has no inspection")
            if observation == "error":
                raise OSError("synthetic unavailable observation")
            return observation

    monkeypatch.setattr(store, "open_stream", lambda **kw: OldWriter(original(**kw)))
    adapter = _adapter(store)
    handle = adapter.start(_request("python", ("-c", "print('safe output')")))
    try:
        deadline = time.monotonic() + 10
        while adapter.inspect(handle).process_reachable and time.monotonic() < deadline:
            time.sleep(0.01)
        result = adapter.collect(handle)
        assert result.complete and result.capture_completeness is CaptureCompleteness.COMPLETE
        actual = store.read_redaction_summary("attempt-1", OutputStreamName.STDOUT)
        assert actual.completeness == "unknown"
        assert actual.gap_reasons == ("secondary_filter_observation_unavailable",)
    finally:
        adapter.request_stop(handle)
