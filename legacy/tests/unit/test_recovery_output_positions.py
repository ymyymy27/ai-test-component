import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from aitest.application.execution.recovery import recover_attempt
from aitest.application.execution.runner import SerialRunner
from aitest.domain.execution.runs import (
    CapturedOutputBlock,
    ExecutionInspectionState,
    OutputStreamName,
)
from aitest.infrastructure.file_store.spool import FileSpoolStore
from tests.unit.test_execution_observation_identity import observation


@pytest.mark.parametrize("fault", ["not_durable", "missing_prefix", "offset_gap"])
@pytest.mark.parametrize(
    "state", [ExecutionInspectionState.RUNNING, ExecutionInspectionState.EXITED]
)
def test_recovery_rejects_unproven_positions(tmp_path, fault, state):
    attempt, inspection, _ = observation()
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
    spool.persist_blocks((block, replace(block, block_index=1, offset=6, content=b"tail")))
    path = tmp_path / "spool" / attempt.attempt_id / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if fault == "not_durable":
        raw["cursors"][0]["durable"] = False
    elif fault == "missing_prefix":
        raw["blocks"] = raw["blocks"][1:]
    else:
        raw["blocks"] = raw["blocks"][1:]
        raw["blocks"][0]["block_index"] = 0
        raw["cursors"][0]["last_block_index"] = 0
    path.write_text(json.dumps(raw), encoding="utf-8")
    original_bytes = path.read_bytes()
    spool.salvage_streams = Mock(wraps=spool.salvage_streams)
    checkpoint = SerialRunner._checkpoint_record(attempt, stage="running").checkpoint
    with pytest.raises(ValueError):
        recover_attempt(
            checkpoint,
            attempt,
            spool,
            inspection=replace(
                inspection, state=state, process_reachable=state is ExecutionInspectionState.RUNNING
            ),
        )
    spool.salvage_streams.assert_not_called()
    assert path.read_bytes() == original_bytes


def test_recovery_validates_original_positions_and_keeps_later_real_tail(tmp_path):
    attempt, inspection, _ = observation()
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
    attempt = replace(attempt, output_block_refs=prefix.blocks, output_cursors=prefix.cursors)
    checkpoint = SerialRunner._checkpoint_record(attempt, stage="running").checkpoint
    active = replace(inspection, state=ExecutionInspectionState.RUNNING, process_reachable=True)
    result = recover_attempt(checkpoint, attempt, spool, inspection=active)
    assert result.action.value == "reattach"
    assert result.attempt.output_block_refs == advanced.blocks
    assert result.attempt.output_cursors == advanced.cursors
    assert b"".join(spool.read_block(ref) for ref in result.recovered_blocks) == b"prefixtail"
    with pytest.raises(ValueError):
        recover_attempt(
            replace(checkpoint, output_cursors=(replace(prefix.cursors[0], offset=999),)),
            attempt,
            spool,
            inspection=active,
        )
