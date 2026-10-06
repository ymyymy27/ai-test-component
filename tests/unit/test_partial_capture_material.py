"""Partial capture retains the same physical ownership and integrity requirements."""

from dataclasses import replace

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import CaptureCompleteness, CapturedOutputBlock, OutputStreamName
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_execution_observation_identity import observation


@pytest.mark.parametrize(
    "fault", ["foreign_run", "foreign_step", "changed_reference", "changed_bytes"]
)
def test_partial_reference_is_checked_before_becoming_attempt_material(tmp_path, fault):
    attempt, inspection, base = observation()
    spool = FileSpoolStore(tmp_path)
    run = "foreign-run" if fault == "foreign_run" else attempt.run_id
    step = "foreign-step" if fault == "foreign_step" else attempt.step_id
    manifest = spool.persist_blocks(
        (
            CapturedOutputBlock(
                run, step, attempt.attempt_id, OutputStreamName.STDOUT, 0, 0, b"original"
            ),
        )
    )
    blocks = manifest.blocks
    if fault == "changed_reference":
        blocks = (replace(blocks[0], length=1),)
    if fault == "changed_bytes":
        spool._stream_path(attempt.attempt_id, OutputStreamName.STDOUT).write_bytes(b"changed!")
    collection = replace(
        base,
        output_blocks=blocks,
        output_cursors=manifest.cursors,
        capture_completeness=CaptureCompleteness.PARTIAL,
    )
    runner = SerialRunner(None, spool)
    with pytest.raises(ValueError):
        runner._apply_collection(attempt, inspection, collection)


@pytest.mark.parametrize(
    "capture", [CaptureCompleteness.PARTIAL, CaptureCompleteness.GAP, CaptureCompleteness.UNKNOWN]
)
@pytest.mark.parametrize("later_tail", [False, True])
def test_saved_prefix_is_valid_after_the_manifest_advances(tmp_path, capture, later_tail):
    attempt, inspection, base = observation()
    spool = FileSpoolStore(tmp_path)
    block = CapturedOutputBlock(
        attempt.run_id,
        attempt.step_id,
        attempt.attempt_id,
        OutputStreamName.STDOUT,
        0,
        0,
        b"original",
    )
    prefix = spool.persist_blocks((block,))
    if later_tail:
        spool.persist_blocks((replace(block, block_index=1, offset=8, content=b"tail"),))
    collection = replace(
        base,
        output_blocks=prefix.blocks,
        output_cursors=prefix.cursors,
        capture_completeness=capture,
    )
    result = SerialRunner(None, spool)._apply_collection(attempt, inspection, collection)
    assert result.output_block_refs == prefix.blocks
    assert result.output_cursors == prefix.cursors
    assert result.capture_completeness is capture
    assert spool.read_block(result.output_block_refs[0]) == b"original"


@pytest.mark.parametrize("field", ["block_index", "offset", "length"])
def test_boolean_reference_cannot_match_integer_material(tmp_path, field):
    attempt, inspection, base = observation()
    spool = FileSpoolStore(tmp_path)
    manifest = spool.persist_blocks(
        (
            CapturedOutputBlock(
                attempt.run_id,
                attempt.step_id,
                attempt.attempt_id,
                OutputStreamName.STDOUT,
                0,
                0,
                b"x",
            ),
        )
    )
    ref = replace(manifest.blocks[0], **{field: field == "length"})
    with pytest.raises(ValueError, match="output_material_unverified"):
        SerialRunner(None, spool)._apply_collection(
            attempt,
            inspection,
            replace(base, output_blocks=(ref,), capture_completeness=CaptureCompleteness.PARTIAL),
        )


def test_foreign_reference_rejected_before_writing_new_capture(tmp_path):
    from unittest.mock import Mock

    attempt, inspection, base = observation()
    spool = FileSpoolStore(tmp_path)
    block = CapturedOutputBlock(
        "foreign-run",
        attempt.step_id,
        attempt.attempt_id,
        OutputStreamName.STDOUT,
        0,
        0,
        b"original",
    )
    manifest = spool.persist_blocks((block,))
    captured = replace(block, run_id=attempt.run_id, block_index=1, offset=8, content=b"tail")
    recorder = Mock(wraps=spool)
    with pytest.raises(ValueError, match="output_material_unverified"):
        SerialRunner(None, recorder)._apply_collection(
            attempt,
            inspection,
            replace(
                base,
                output_blocks=manifest.blocks,
                captured_blocks=(captured,),
                capture_completeness=CaptureCompleteness.PARTIAL,
            ),
        )
    recorder.persist_blocks.assert_not_called()


def test_partial_material_failure_is_saved_without_publishing_bad_refs_or_restarting(tmp_path):
    from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
    from tests.support.execution_authority import fixture_coordinator
    from tests.support.fake_execution import FakeExecutionPort, FakeExecutionSpec
    from tests.unit.test_serial_runner import _attempt, _request

    unit = FileUnitOfWork(tmp_path)
    spool = FileSpoolStore(tmp_path)
    port = FakeExecutionPort()
    port.register(
        FakeExecutionSpec("attempt-1", "run-1", "step-1", running_observations_before_exit=0)
    )
    manifest = spool.persist_blocks(
        (
            CapturedOutputBlock(
                "foreign-run", "step-1", "attempt-1", OutputStreamName.STDOUT, 0, 0, b"foreign"
            ),
        )
    )
    original = port.collect
    port.collect = lambda handle, cursors=None: replace(
        original(handle, cursors),
        captured_blocks=(),
        output_blocks=manifest.blocks,
        capture_completeness=CaptureCompleteness.PARTIAL,
    )
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    runner = SerialRunner(port, spool, commit_coordinator=coordinator)
    for _ in range(2):
        result = runner.execute_attempt(_attempt(), _request())
        assert result.state.value == "pending_verification"
        assert not result.output_block_refs and result.exit_fact_ref is None
        assert result.unknown_reason_ref == "output_material_unverified"
        assert (
            coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-1").attempt
            == result
        )
    assert port.execution_order == ["attempt-1"]
    assert not coordinator.read_current_facts(
        project_id="project-1", run_id="run-1"
    ).coverage.executed_attempt_ids
