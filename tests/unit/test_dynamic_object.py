"""Dynamic extraction fails closed on identity, truncation and redaction boundaries."""

import hashlib
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from aitest.application.evidence.dynamic_object import saved_object_id


def inputs(raw=b'{"created":{"id":"order-2"}}'):
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    block = NS(
        stream_name="stdout",
        block_index=0,
        attempt_id="a",
        offset=0,
        length=len(raw),
        digest=digest,
        complete=True,
    )
    evidence = NS(
        evidence_id="evidence:a:stdout:0",
        project_id="p",
        run_id="r",
        step_id="s",
        attempt_id="a",
        evidence_kind="command_output",
        integrity="complete",
        gap_ids=(),
        redaction_summary=NS(completeness="complete", replacement_count=0, gap_reasons=()),
        object_digest=digest,
        object_size=len(raw),
        media_type="text/plain",
    )
    exit_fact = NS(
        attempt_id="a",
        capture_completeness="complete",
        saved_bytes_by_stream={"stdout": len(raw)},
        last_block_index_by_stream={"stdout": 0},
    )
    attempt = NS(
        attempt_id="a",
        is_current=True,
        run_id="r",
        step_id="s",
        capture_completeness="complete",
        exit_fact=exit_fact,
        output_blocks=[block],
    )
    facts = NS(
        attempts=[attempt],
        current_attempt_by_step={"s": "a"},
        run_id="r",
        project_id="p",
        evidence_refs=[evidence],
    )
    return facts, Mock(read_bytes=Mock(return_value=raw))


def extract(facts, objects, source=None):
    return saved_object_id(
        source or {"stream": "stdout", "json_path": ["created", "id"]}, facts, "s", "a", objects
    )


def test_extracts_identity_and_exact_evidence_without_deciding_result():
    facts, objects = inputs()
    assert extract(facts, objects) == ("order-2", ("evidence:a:stdout:0",))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda f: f.current_attempt_by_step.update(s="old"),
        lambda f: setattr(f.attempts[0], "is_current", False),
        lambda f: setattr(f.attempts[0], "capture_completeness", "partial"),
        lambda f: setattr(f.attempts[0], "exit_fact", None),
        lambda f: setattr(f.attempts[0].output_blocks[0], "offset", 1),
        lambda f: setattr(f.attempts[0].output_blocks[0], "block_index", 1),
        lambda f: setattr(f.attempts[0].output_blocks[0], "complete", False),
        lambda f: setattr(f.evidence_refs[0], "project_id", "another"),
        lambda f: setattr(f.evidence_refs[0], "attempt_id", "old"),
        lambda f: setattr(f.evidence_refs[0], "gap_ids", ("missing",)),
        lambda f: setattr(f.evidence_refs[0].redaction_summary, "replacement_count", 1),
        lambda f: setattr(f.evidence_refs[0].redaction_summary, "completeness", "unknown"),
        lambda f: f.attempts[0].exit_fact.saved_bytes_by_stream.update(stdout=999),
        lambda f: f.attempts[0].exit_fact.last_block_index_by_stream.update(stdout=2),
        lambda f: f.evidence_refs.append(f.evidence_refs[0]),
    ],
)
def test_unproven_output_is_rejected(mutation):
    facts, objects = inputs()
    mutation(facts)
    with pytest.raises(ValueError):
        extract(facts, objects)


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"created":{"id":1}}',
        b'{"created":{"id":""}}',
        b'{"created":{"id":"x","id":"y"}}',
        b'{"created":',
    ],
)
def test_missing_nonstring_duplicate_or_truncated_json_rejected(raw):
    with pytest.raises(ValueError):
        extract(*inputs(raw))


def test_modified_saved_bytes_rejected():
    facts, objects = inputs()
    objects.read_bytes.return_value = b'{"created":{"id":"order-9"}}'
    with pytest.raises(ValueError):
        extract(facts, objects)


@pytest.mark.parametrize(
    "source",
    [
        {"stream": "stderr", "json_path": ["id"]},
        {"stream": "stdout", "json_path": []},
        {"stream": "stdout", "json_path": ["id"], "fallback": "x"},
    ],
)
def test_selector_is_finite_and_has_no_fallback(source):
    with pytest.raises(ValueError):
        extract(*inputs(), source)


def test_multiblock_json_is_reconstructed_in_order_with_each_evidence_reference():
    from copy import deepcopy

    raw = b'{"created":{"id":"order-2"}}'
    facts, objects = inputs(raw)
    chunks = [raw[:12], raw[12:]]
    template_block = facts.attempts[0].output_blocks[0]
    template_evidence = facts.evidence_refs[0]
    blocks, evidence = [], []
    by_digest = {}
    offset = 0
    for index, chunk in enumerate(chunks):
        digest = "sha256:" + hashlib.sha256(chunk).hexdigest()
        block, item = deepcopy(template_block), deepcopy(template_evidence)
        block.block_index, block.offset, block.length, block.digest = (
            index,
            offset,
            len(chunk),
            digest,
        )
        item.evidence_id = f"evidence:a:stdout:{index}"
        item.object_digest, item.object_size = digest, len(chunk)
        blocks.append(block)
        evidence.append(item)
        by_digest[digest] = chunk
        offset += len(chunk)
    facts.attempts[0].output_blocks = list(reversed(blocks))
    facts.evidence_refs = evidence
    facts.attempts[0].exit_fact.last_block_index_by_stream["stdout"] = 1
    objects.read_bytes.side_effect = lambda ref: by_digest[ref.digest]
    assert extract(facts, objects) == ("order-2", ("evidence:a:stdout:0", "evidence:a:stdout:1"))
