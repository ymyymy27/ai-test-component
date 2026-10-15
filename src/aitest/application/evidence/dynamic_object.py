"""Extract identity only from complete saved output of the exact current operation."""

import hashlib

from aitest.application.ports import EvidenceObjectStore
from aitest.contracts.execution_facts import ExecutionFacts
from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.domain.json_material import decode_json


def saved_object_id(
    source: object, facts: ExecutionFacts, step_id: str, attempt_id: str,
    objects: EvidenceObjectStore,
) -> tuple[str, tuple[str, ...]]:
    if (not isinstance(source, dict) or set(source) != {"stream", "json_path"}
            or source["stream"] != "stdout" or not isinstance(source["json_path"], list)
            or not 1 <= len(source["json_path"]) <= 16
            or any(not isinstance(k, str) or not k.strip() for k in source["json_path"])):
        raise ValueError("invalid object identity selector")
    value, refs = saved_stdout_json(facts, step_id, attempt_id, objects)
    for key in source["json_path"]:
        if not isinstance(value, dict) or key not in value:
            raise ValueError("object identity field is unavailable")
        value = value[key]
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 4096:
        raise ValueError("object identity must be a bounded nonempty string")
    return value, refs


def saved_stdout_json(
    facts: ExecutionFacts, step_id: str, attempt_id: str, objects: EvidenceObjectStore,
) -> tuple[object, tuple[str, ...]]:
    """Read exact permanent output without executing or trusting an asserted result."""
    matches = [a for a in facts.attempts if a.attempt_id == attempt_id]
    if len(matches) != 1:
        raise ValueError("object identity requires one saved attempt")
    attempt = matches[0]
    exit_fact = attempt.exit_fact
    if (facts.current_attempt_by_step.get(step_id) != attempt_id or not attempt.is_current
            or (attempt.run_id, attempt.step_id) != (facts.run_id, step_id)
            or attempt.capture_completeness != "complete" or exit_fact is None
            or exit_fact.attempt_id != attempt_id or exit_fact.capture_completeness != "complete"):
        raise ValueError("object identity requires complete current output")
    blocks = sorted((b for b in attempt.output_blocks if b.stream_name == "stdout"),
                    key=lambda b: b.block_index)
    if not blocks or sum(b.length for b in blocks) > 1024 * 1024:
        raise ValueError("object identity output is empty or exceeds budget")
    raw = bytearray()
    refs = []
    for index, block in enumerate(blocks):
        candidates = [e for e in facts.evidence_refs if e.evidence_id ==
                      f"evidence:{attempt_id}:stdout:{block.block_index}"]
        if len(candidates) != 1:
            raise ValueError("object identity output lacks exact evidence")
        evidence = candidates[0]
        summary = evidence.redaction_summary
        if (block.attempt_id != attempt_id or block.block_index != index
                or block.offset != len(raw) or not block.complete
                or (evidence.project_id, evidence.run_id, evidence.step_id, evidence.attempt_id)
                != (facts.project_id, facts.run_id, step_id, attempt_id)
                or evidence.evidence_kind != "command_output" or evidence.integrity != "complete"
                or evidence.gap_ids or summary is None or summary.completeness != "complete"
                or summary.replacement_count or summary.gap_reasons
                or (evidence.object_digest, evidence.object_size) != (block.digest, block.length)):
            raise ValueError("object identity output provenance is incomplete or filtered")
        content = objects.read_bytes(StoredObjectRef(
            evidence.project_id, evidence.object_digest, evidence.object_size,
            evidence.media_type or "application/octet-stream",
            f"objects/{evidence.project_id}/{evidence.object_digest.removeprefix('sha256:')}",
        ))
        if (len(content) != block.length
                or "sha256:" + hashlib.sha256(content).hexdigest() != block.digest):
            raise ValueError("object identity bytes differ from evidence")
        raw.extend(content)
        refs.append(evidence.evidence_id)
    if (exit_fact.saved_bytes_by_stream.get("stdout") != len(raw)
            or exit_fact.last_block_index_by_stream.get("stdout") != blocks[-1].block_index):
        raise ValueError("object identity output is truncated")
    return decode_json(bytes(raw)), tuple(refs)
