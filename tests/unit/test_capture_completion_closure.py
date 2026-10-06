"""Complete capture requires the exit totals to match actual saved output."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from aitest.application.execution.current import project_current_update
from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    AttemptState,
    CaptureCompleteness,
    CapturedOutputBlock,
    OutputBlockRef,
    OutputCursor,
    OutputStreamName,
    has_complete_capture,
)
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_execution_observation_identity import observation


def _material():
    first = CapturedOutputBlock(
        "run-1", "step-1", "attempt-1", OutputStreamName.STDOUT, 0, 0, b"first"
    )
    second = replace(first, block_index=1, offset=5, content=b"tail")
    cursor = OutputCursor("attempt-1", OutputStreamName.STDOUT, 9, 1, second.digest, True)
    return (first, second), cursor


def _observation():
    attempt, inspection, collection = observation()
    blocks, cursor = _material()
    return (
        attempt,
        inspection,
        replace(
            collection,
            captured_blocks=blocks,
            output_cursors=(cursor,),
            exit_fact_ref=replace(
                collection.exit_fact_ref,
                last_block_index_by_stream=((OutputStreamName.STDOUT, 1),),
                saved_bytes_by_stream=((OutputStreamName.STDOUT, 9),),
            ),
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        "bytes",
        "last_index",
        "missing_bytes",
        "missing_indexes",
        "duplicate_stream",
        "boolean_count",
        "exit_capture",
        "partial_block",
        "cursor_digest",
        "cursor_offset",
        "cursor_missing",
    ],
)
def test_inconsistent_capture_cannot_qualify_as_complete(tmp_path, change):
    attempt, inspection, collection = _observation()
    fact = collection.exit_fact_ref
    if change == "bytes":
        fact = replace(fact, saved_bytes_by_stream=((OutputStreamName.STDOUT, 10),))
    elif change == "last_index":
        fact = replace(fact, last_block_index_by_stream=((OutputStreamName.STDOUT, 2),))
    elif change == "missing_bytes":
        fact = replace(fact, saved_bytes_by_stream=())
    elif change == "missing_indexes":
        fact = replace(fact, last_block_index_by_stream=())
    elif change == "duplicate_stream":
        fact = replace(fact, saved_bytes_by_stream=fact.saved_bytes_by_stream * 2)
    elif change == "boolean_count":
        fact = replace(fact, last_block_index_by_stream=((OutputStreamName.STDOUT, True),))
    elif change == "exit_capture":
        fact = replace(fact, capture_completeness=CaptureCompleteness.UNKNOWN)
    elif change == "partial_block":
        # Already persisted partial recovery material is readable, but cannot prove full capture.
        store = FileSpoolStore(tmp_path)
        writer = store.open_stream(
            run_id="run-1",
            step_id="step-1",
            attempt_id="attempt-1",
            stream_name=OutputStreamName.STDOUT,
        )
        writer.append(b"firsttail")
        refs = writer.close(complete=False)
        cursor = store.read_manifest("attempt-1").cursors[0]
        collection = replace(
            collection, captured_blocks=(), output_blocks=refs, output_cursors=(cursor,)
        )
        fact = replace(fact, last_block_index_by_stream=((OutputStreamName.STDOUT, 0),))
    else:
        cursor = collection.output_cursors[0]
        collection = replace(
            collection,
            output_cursors=()
            if change == "cursor_missing"
            else (
                replace(
                    cursor,
                    **(
                        {"last_committed_digest": "sha256:foreign"}
                        if change == "cursor_digest"
                        else {"offset": 10}
                    ),
                ),
            ),
        )
    collection = replace(collection, exit_fact_ref=fact)
    result = SerialRunner(Mock(), FileSpoolStore(tmp_path))._apply_collection(
        attempt, inspection, collection
    )
    assert result.capture_completeness is not CaptureCompleteness.COMPLETE
    assert result.exit_fact_ref == fact  # Execution termination and capture proof remain separate.
    assert result.unknown_reason_ref
    assert result.state is AttemptState.COMPLETED


def test_matching_totals_and_bytes_preserve_complete_capture(tmp_path):
    attempt, inspection, collection = _observation()
    result = SerialRunner(Mock(), FileSpoolStore(tmp_path))._apply_collection(
        attempt, inspection, collection
    )
    assert result.capture_completeness is CaptureCompleteness.COMPLETE
    assert len(result.output_block_refs) == 2
    assert result.output_cursors[0].offset == 9


def test_damaged_persisted_bytes_do_not_remain_complete(tmp_path):
    attempt, inspection, collection = _observation()
    store = FileSpoolStore(tmp_path)
    manifest = store.persist_blocks(collection.captured_blocks)
    (tmp_path / "spool/attempt-1/stdout.log").write_bytes(b"corrupted")
    collection = replace(collection, captured_blocks=(), output_blocks=manifest.blocks)
    result = SerialRunner(Mock(), store)._apply_collection(attempt, inspection, collection)
    assert result.capture_completeness is not CaptureCompleteness.COMPLETE
    assert result.unknown_reason_ref


def test_nonempty_references_without_a_material_reader_do_not_prove_complete_capture():
    attempt, inspection, collection = _observation()
    refs = tuple(
        OutputBlockRef(
            f"block-{b.block_index}",
            b.attempt_id,
            b.stream_name,
            b.block_index,
            b.offset,
            b.length,
            b.digest,
            True,
            b.capture_source,
        )
        for b in collection.captured_blocks
    )
    result = SerialRunner(Mock())._apply_collection(
        attempt, inspection, replace(collection, captured_blocks=(), output_blocks=refs)
    )
    assert result.capture_completeness is not CaptureCompleteness.COMPLETE


def test_conflicting_repeated_block_is_rejected_before_any_new_spool_write(tmp_path):
    attempt, inspection, collection = _observation()
    store = FileSpoolStore(tmp_path)
    manifest = store.persist_blocks(collection.captured_blocks)
    attempt = replace(attempt, output_block_refs=manifest.blocks, output_cursors=manifest.cursors)
    forged = replace(manifest.blocks[0], digest="sha256:foreign")
    recorder = Mock(wraps=store)
    with pytest.raises(ValueError, match="conflict"):
        SerialRunner(Mock(), recorder)._apply_collection(
            attempt, inspection, replace(collection, output_blocks=(forged,))
        )
    recorder.persist_blocks.assert_not_called()


def test_empty_output_with_matching_empty_totals_is_a_complete_capture():
    attempt, inspection, collection = observation()
    result = SerialRunner(Mock())._apply_collection(attempt, inspection, collection)
    assert result.state is AttemptState.COMPLETED and has_complete_capture(result)


def test_memory_capture_becomes_durable_only_after_actual_spool_save(tmp_path):
    attempt, inspection, collection = _observation()
    collection = replace(
        collection,
        output_cursors=(replace(collection.output_cursors[0], durable=False),),
    )
    store = FileSpoolStore(tmp_path)
    result = SerialRunner(Mock(), store)._apply_collection(attempt, inspection, collection)
    assert has_complete_capture(result)
    assert result.output_cursors[0].durable is True
    assert b"".join(store.read_block(block) for block in result.output_block_refs) == b"firsttail"


def test_unreported_saved_stream_cannot_be_omitted_from_complete_capture(tmp_path):
    attempt, inspection, collection = observation()
    store = FileSpoolStore(tmp_path)
    blocks, _ = _material()
    store.persist_blocks(blocks)
    result = SerialRunner(Mock(), store)._apply_collection(attempt, inspection, collection)
    assert result.state is AttemptState.COMPLETED
    assert result.capture_completeness is CaptureCompleteness.GAP
    assert result.unknown_reason_ref == "capture_material_unverified"


def test_both_stream_totals_use_cumulative_filtered_bytes(tmp_path):
    attempt, inspection, collection = _observation()
    stderr = replace(
        collection.captured_blocks[0], stream_name=OutputStreamName.STDERR, content=b"error"
    )
    cursor = OutputCursor(attempt.attempt_id, OutputStreamName.STDERR, 5, 0, stderr.digest, True)
    fact = replace(
        collection.exit_fact_ref,
        saved_bytes_by_stream=((OutputStreamName.STDOUT, 9), (OutputStreamName.STDERR, 5)),
        last_block_index_by_stream=((OutputStreamName.STDOUT, 1), (OutputStreamName.STDERR, 0)),
    )
    result = SerialRunner(Mock(), FileSpoolStore(tmp_path))._apply_collection(
        attempt,
        inspection,
        replace(
            collection,
            captured_blocks=(*collection.captured_blocks, stderr),
            output_cursors=(*collection.output_cursors, cursor),
            exit_fact_ref=fact,
        ),
    )
    assert has_complete_capture(result)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_block",
        "index_gap",
        "offset_gap",
        "boolean_size",
        "boolean_length",
        "cursor_duplicate",
        "non_durable",
        "extra_stream",
        "timed_out",
    ],
)
def test_domain_and_current_projection_reject_inconsistent_saved_capture(tmp_path, change):
    from tests.unit.test_current_execution_snapshot import _batch

    attempt, inspection, collection = _observation()
    saved = SerialRunner(Mock(), FileSpoolStore(tmp_path))._apply_collection(
        attempt, inspection, collection
    )
    block = saved.output_block_refs[-1]
    if change == "duplicate_block":
        bad = replace(saved, output_block_refs=saved.output_block_refs + (block,))
    elif change in {"index_gap", "offset_gap", "boolean_length"}:
        modified = replace(
            block,
            **(
                {"block_index": 2}
                if change == "index_gap"
                else {"offset": 6}
                if change == "offset_gap"
                else {"length": True}
            ),
        )
        bad = replace(saved, output_block_refs=(saved.output_block_refs[0], modified))
    elif change in {"boolean_size", "extra_stream"}:
        totals = (
            ((OutputStreamName.STDOUT, True),)
            if change == "boolean_size"
            else (*saved.exit_fact_ref.saved_bytes_by_stream, (OutputStreamName.STDERR, 0))
        )
        bad = replace(
            saved, exit_fact_ref=replace(saved.exit_fact_ref, saved_bytes_by_stream=totals)
        )
    elif change == "timed_out":
        bad = replace(saved, exit_fact_ref=replace(saved.exit_fact_ref, timed_out=True))
    else:
        cursors = (
            saved.output_cursors * 2
            if change == "cursor_duplicate"
            else (replace(saved.output_cursors[0], durable=False),)
        )
        bad = replace(saved, output_cursors=cursors)
    if change != "timed_out":
        assert not has_complete_capture(bad)
    after = project_current_update(_batch().facts, bad, committed_at=datetime.now(UTC))
    assert bad.attempt_id not in after.coverage.executed_attempt_ids


def test_replayed_older_cursor_cannot_rewind_or_prove_complete_capture(tmp_path):
    attempt, inspection, collection = _observation()
    store = FileSpoolStore(tmp_path)
    saved = SerialRunner(Mock(), store)._apply_collection(attempt, inspection, collection)
    first = saved.output_block_refs[0]
    old_cursor = OutputCursor(saved.attempt_id, first.stream_name, 5, 0, first.digest, True)
    replay = replace(collection, captured_blocks=(), output_cursors=(old_cursor,))
    result = SerialRunner(Mock(), store)._apply_collection(saved, inspection, replay)
    assert result.output_cursors == saved.output_cursors
    assert result.capture_completeness is CaptureCompleteness.GAP


def test_unknown_collection_cannot_inherit_previous_complete_capture(tmp_path):
    attempt, inspection, collection = _observation()
    runner = SerialRunner(Mock(), FileSpoolStore(tmp_path))
    saved = runner._apply_collection(attempt, inspection, collection)
    result = runner._apply_collection(
        saved, inspection, replace(collection, capture_completeness=CaptureCompleteness.UNKNOWN)
    )
    assert result.capture_completeness is CaptureCompleteness.UNKNOWN


def test_capture_gap_survives_atomic_publication_and_restart_without_reexecution(tmp_path):
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
    from tests.support.execution_authority import fixture_coordinator
    from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
    from tests.unit.test_serial_runner import _attempt, _request

    port = FakeExecutionPort()
    blocks, _ = _material()
    port.register(
        FakeExecutionSpec(
            "attempt-1", "run-1", "step-1", captures=blocks, running_observations_before_exit=0
        )
    )
    original = port.collect

    def wrong_totals(handle, cursors=None):
        result = original(handle, cursors)
        return replace(
            result,
            exit_fact_ref=replace(
                result.exit_fact_ref, saved_bytes_by_stream=((OutputStreamName.STDOUT, 10),)
            ),
        )

    port.collect = wrong_totals
    store = FileSpoolStore(tmp_path)
    unit = FileUnitOfWork(tmp_path)
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    saved = SerialRunner(port, store, commit_coordinator=coordinator).execute_attempt(
        _attempt(), _request()
    )
    assert saved.state is AttemptState.COMPLETED
    assert saved.capture_completeness is CaptureCompleteness.GAP and saved.unknown_reason_ref
    assert b"".join(store.read_block(block) for block in saved.output_block_refs) == b"firsttail"
    restarted = fixture_coordinator(FileUnitOfWork(tmp_path), ((_attempt(), _request()),))
    assert (
        restarted.read_checkpoint(project_id="project-1", attempt_id=saved.attempt_id).attempt
        == saved
    )
    assert not restarted.read_current_facts(
        project_id="project-1", run_id="run-1"
    ).coverage.executed_attempt_ids
    again = SerialRunner(port, store, commit_coordinator=restarted).execute_attempt(
        _attempt(), _request()
    )
    assert again == saved and port.execution_order == ["attempt-1"]
