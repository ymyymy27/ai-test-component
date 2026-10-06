from dataclasses import replace

import pytest

from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import CaptureCompleteness, CapturedOutputBlock, OutputStreamName
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_execution_observation_identity import observation


@pytest.mark.parametrize(
    "field", ["offset", "last_block_index", "last_committed_digest", "durable"]
)
def test_partial_cursor_requires_saved_prefix(tmp_path, field):
    attempt, inspection, base = observation()
    spool = FileSpoolStore(tmp_path)
    saved = spool.persist_blocks(
        (
            CapturedOutputBlock(
                attempt.run_id,
                attempt.step_id,
                attempt.attempt_id,
                OutputStreamName.STDOUT,
                0,
                0,
                b"prefix",
            ),
        )
    )
    values = {
        "offset": 999,
        "last_block_index": 999,
        "last_committed_digest": "sha256:foreign",
        "durable": False,
    }
    forged = replace(saved.cursors[0], **{field: values[field]})
    collection = replace(
        base,
        output_blocks=saved.blocks,
        output_cursors=(forged,),
        capture_completeness=CaptureCompleteness.PARTIAL,
    )
    with pytest.raises(ValueError):
        SerialRunner(None, spool)._apply_collection(attempt, inspection, collection)


@pytest.mark.parametrize(
    "field,value", [("offset", True), ("last_block_index", False), ("durable", 1)]
)
def test_boolean_or_integer_alias_does_not_establish_durable_position(tmp_path, field, value):
    attempt, inspection, base = observation()
    spool = FileSpoolStore(tmp_path)
    saved = spool.persist_blocks(
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
    with pytest.raises(ValueError, match="output_cursor_material_unverified"):
        SerialRunner(None, spool)._apply_collection(
            attempt,
            inspection,
            replace(
                base,
                output_blocks=saved.blocks,
                output_cursors=(replace(saved.cursors[0], **{field: value}),),
                capture_completeness=CaptureCompleteness.PARTIAL,
            ),
        )


def test_cursor_cannot_skip_unattached_material_even_when_manifest_has_it(tmp_path):
    attempt, inspection, base = observation()
    spool = FileSpoolStore(tmp_path)
    block = CapturedOutputBlock(
        attempt.run_id,
        attempt.step_id,
        attempt.attempt_id,
        OutputStreamName.STDOUT,
        0,
        0,
        b"prefix",
    )
    prefix = spool.persist_blocks((block,))
    advanced = spool.persist_blocks((replace(block, block_index=1, offset=6, content=b"tail"),))
    with pytest.raises(ValueError, match="output_cursor_material_unverified"):
        SerialRunner(None, spool)._apply_collection(
            attempt,
            inspection,
            replace(
                base,
                output_blocks=prefix.blocks,
                output_cursors=advanced.cursors,
                capture_completeness=CaptureCompleteness.PARTIAL,
            ),
        )
    result = SerialRunner(None, spool)._apply_collection(
        attempt,
        inspection,
        replace(
            base,
            output_blocks=prefix.blocks,
            output_cursors=prefix.cursors,
            capture_completeness=CaptureCompleteness.PARTIAL,
        ),
    )
    assert result.output_cursors == prefix.cursors
    assert result.output_block_refs == prefix.blocks


def test_memory_capture_observation_becomes_durable_only_from_saved_manifest(tmp_path):
    from aitest.domain.execution.runs import OutputCursor

    attempt, inspection, base = observation()
    block = CapturedOutputBlock(
        attempt.run_id,
        attempt.step_id,
        attempt.attempt_id,
        OutputStreamName.STDOUT,
        0,
        0,
        b"prefix",
    )
    memory_cursor = OutputCursor(
        attempt.attempt_id, OutputStreamName.STDOUT, 6, 0, block.digest, False
    )
    spool = FileSpoolStore(tmp_path)
    result = SerialRunner(None, spool)._apply_collection(
        attempt,
        inspection,
        replace(
            base,
            captured_blocks=(block,),
            output_cursors=(memory_cursor,),
            capture_completeness=CaptureCompleteness.PARTIAL,
        ),
    )
    assert result.output_cursors == spool.read_manifest(attempt.attempt_id).cursors
    assert result.output_cursors[0].durable is True
    assert spool.read_block(result.output_block_refs[0]) == b"prefix"
    assert result.capture_completeness is CaptureCompleteness.PARTIAL


def test_invalid_partial_cursor_is_saved_as_gap_without_restarting(tmp_path):
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
    saved = spool.persist_blocks(
        (
            CapturedOutputBlock(
                "run-1", "step-1", "attempt-1", OutputStreamName.STDOUT, 0, 0, b"prefix"
            ),
        )
    )
    original = port.collect
    port.collect = lambda handle, cursors=None: replace(
        original(handle, cursors),
        captured_blocks=(),
        output_blocks=saved.blocks,
        output_cursors=(replace(saved.cursors[0], offset=99),),
        capture_completeness=CaptureCompleteness.PARTIAL,
    )
    coordinator = fixture_coordinator(unit, ((_attempt(), _request()),))
    runner = SerialRunner(port, spool, commit_coordinator=coordinator)
    for _ in range(2):
        result = runner.execute_attempt(_attempt(), _request())
        assert result.state.value == "pending_verification"
        assert result.capture_completeness is CaptureCompleteness.GAP
        assert result.unknown_reason_ref == "output_cursor_material_unverified"
        assert not result.output_block_refs and not result.output_cursors
        assert result.exit_fact_ref is None
        assert (
            coordinator.read_checkpoint(project_id="project-1", attempt_id="attempt-1").attempt
            == result
        )
    assert port.execution_order == ["attempt-1"]
    assert not coordinator.read_current_facts(
        project_id="project-1", run_id="run-1"
    ).coverage.executed_attempt_ids
