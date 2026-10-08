"""Read the saved bytes of a historic case, without granting current eligibility."""

import hashlib
import re
from typing import TYPE_CHECKING

from aitest.application.execution.output_material import require_saved_output_material
from aitest.application.ports import EvidenceObjectStore, SpoolStore
from aitest.contracts.execution_facts import EvidenceFact, EvidenceKindFact
from aitest.domain.evidence.evidence import StoredObjectRef

if TYPE_CHECKING:
    from aitest.application.execution.reuse_sources import CaseReuseSource


def validate_source_material(
    source: "CaseReuseSource", objects: EvidenceObjectStore | None,
    spool: SpoolStore | None = None,
) -> None:
    attempts = {item.attempt.attempt_id: item.attempt for item in source.steps if item.attempt}
    evidence = tuple(item for item in source.facts.evidence_refs if item.attempt_id in attempts)
    if not evidence and not any(attempt.output_blocks for attempt in attempts.values()):
        return
    if len({item.evidence_id for item in evidence}) != len(evidence):
        raise ValueError("case source evidence identities are ambiguous")
    project = source.facts.project_id
    run = source.facts.run_id
    checked: set[tuple[str, int]] = set()

    def read(digest: str, size: int, media_type: str) -> None:
        if objects is None:
            raise ValueError("case source saved bytes require an object reader")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or type(size) is not int or size < 0
        ):
            raise ValueError("case source object identity cannot be verified")
        if (digest, size) in checked:
            return
        ref = StoredObjectRef(
            project, digest, size, media_type,
            f"objects/{project}/{digest.removeprefix('sha256:')}",
        )
        try:
            content = objects.read_bytes(ref)
        except (OSError, ValueError) as exc:
            raise ValueError("case source saved bytes are unreadable") from exc
        if (
            type(content) is not bytes or len(content) != size
            or "sha256:" + hashlib.sha256(content).hexdigest() != digest
        ):
            raise ValueError("case source saved bytes differ from their exact reference")
        checked.add((digest, size))

    def read_evidence(item: EvidenceFact) -> None:
        attempt = attempts[item.attempt_id]
        if (item.project_id, item.run_id, item.step_id) != (project, run, attempt.step_id):
            raise ValueError("case source evidence belongs to another project, run or step")
        read(item.object_digest, item.object_size, item.media_type or "application/octet-stream")

    by_evidence = {item.evidence_id: item for item in evidence}
    for step_source in source.steps:
        if step_source.attempt is None or not step_source.attempt.output_blocks:
            continue
        if step_source.checkpoint is None:
            raise ValueError("case source output lacks its exact checkpoint")
        actual = step_source.checkpoint.attempt
        if (actual.attempt_id, actual.step_id, actual.run_id) != (
            step_source.attempt.attempt_id, step_source.attempt.step_id, source.facts.run_id,
        ):
            raise ValueError("case source output belongs to another attempt")
        unpublished = []
        for block in actual.output_block_refs:
            if type(block.length) is not int or block.length < 0:
                raise ValueError("case source output length cannot be verified")
            published = by_evidence.get(
                f"evidence:{actual.attempt_id}:{block.stream_name.value}:{block.block_index}"
            )
            if published is not None and (
                published.evidence_kind is EvidenceKindFact.COMMAND_OUTPUT
                and (published.attempt_id, published.step_id) == (actual.attempt_id, actual.step_id)
                and (published.object_digest, published.object_size) == (block.digest, block.length)
            ):
                read_evidence(published)
            elif published is not None:
                raise ValueError("published case output differs from its exact checkpoint")
            else:
                unpublished.append(block)
        require_saved_output_material(spool, actual, tuple(unpublished))
    for item in evidence:
        read_evidence(item)
