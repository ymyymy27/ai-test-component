"""Exact immutable summary bytes and their original output stream basis."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import cast

from pydantic import TypeAdapter

from aitest.application.evidence.publication import EvidencePublicationContext
from aitest.application.planning.publish import payload_digest
from aitest.application.ports import (
    CollectedRedactionSummary,
    EvidenceObjectStore,
    RecordRepository,
    SpoolRedactionSummaryReader,
    SpoolStore,
)
from aitest.domain.evidence.evidence import CodeIdentity, StoredObjectRef
from aitest.domain.evidence.redaction_material import (
    MAX_SUMMARY_BYTES,
    parse_redaction_summary,
    redaction_summary_payload,
)
from aitest.domain.execution.runs import (
    OutputBlockRef,
    OutputStreamName,
    RecoveryRecord,
    has_reliable_terminal_fact,
)
from aitest.domain.json_material import decode_json

KIND = "execution_redaction_summary"
_BLOCKS = TypeAdapter(tuple[OutputBlockRef, ...])
_CODE = TypeAdapter(CodeIdentity)
_OBJECT = TypeAdapter(StoredObjectRef)


def encode_summary(payload: Mapping[str, object]) -> bytes:
    content = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8",
    )
    if len(content) > MAX_SUMMARY_BYTES:
        raise ValueError("permanent redaction summary exceeds metadata budget")
    return content


def summary_basis(
    *, project_id: str, workspace_id: str, run_id: str, step_id: str, attempt_id: str,
    stream: OutputStreamName, code_identity: Mapping[str, object],
    blocks: Sequence[OutputBlockRef],
) -> dict[str, object]:
    selected = tuple(block for block in blocks if block.stream_name is stream)
    identity = f"redaction:{attempt_id}:{stream.value}"
    offset = 0
    for index, block in enumerate(selected):
        if (
            block.attempt_id != attempt_id or block.redaction_summary_id != identity
            or type(block.block_index) is not int or block.block_index != index
            or type(block.offset) is not int or block.offset != offset
            or type(block.length) is not int or block.length <= 0
        ):
            raise ValueError("permanent redaction summary output basis cannot be verified")
        offset += block.length
    if not selected:
        raise ValueError("permanent redaction summary requires its original output blocks")
    return {
        "schema_version": "aitest.execution-redaction-material/1.0",
        "summary_id": identity, "project_id": project_id, "origin_workspace_id": workspace_id,
        "run_id": run_id, "step_id": step_id, "attempt_id": attempt_id,
        "stream_name": stream.value, "code_identity": dict(code_identity),
        "output_blocks": _BLOCKS.dump_python(selected, mode="json"),
    }


def verify_redaction_material(
    *, identity: str, raw: Mapping[str, object], basis: Mapping[str, object],
    objects: EvidenceObjectStore,
) -> CollectedRedactionSummary:
    if (
        identity != basis.get("summary_id") or set(raw) != {*basis, "summary", "object_ref"}
        or payload_digest({key: raw[key] for key in basis}) != payload_digest(basis)
    ):
        raise ValueError("permanent redaction summary differs from its frozen ownership or blocks")
    attempt_id, stream_name = basis["attempt_id"], basis["stream_name"]
    if not isinstance(attempt_id, str) or not isinstance(stream_name, str):
        raise ValueError("permanent redaction summary ownership cannot be verified")
    summary = parse_redaction_summary(raw["summary"], attempt_id, OutputStreamName(stream_name))
    expected = encode_summary(redaction_summary_payload(
        summary, attempt_id, OutputStreamName(stream_name),
    ))
    stored = _OBJECT.validate_python(raw["object_ref"])
    if (
        payload_digest(raw["object_ref"]) != payload_digest(
            _OBJECT.dump_python(stored, mode="json"),
        )
        or type(stored.size) is not int or stored.size != len(expected)
        or stored.project_id != basis["project_id"]
        or stored.media_type != "application/json"
        or stored.digest != "sha256:" + hashlib.sha256(expected).hexdigest()
        or stored.relative_path != (
            f"objects/{stored.project_id}/{stored.digest.removeprefix('sha256:')}"
        )
    ):
        raise ValueError("permanent redaction summary object reference cannot be verified")
    content = objects.read_bytes(stored)
    if type(content) is not bytes or content != expected or decode_json(content) != raw["summary"]:
        raise ValueError("permanent redaction summary bytes cannot be verified")
    if summary.completeness == "complete":
        blocks = _BLOCKS.validate_python(basis["output_blocks"])
        length = sum(block.length for block in blocks)
        if any(int(value.rsplit("-", 1)[1]) > length for value in summary.filtered_ranges):
            raise ValueError("complete redaction summary range exceeds its saved stream")
    return CollectedRedactionSummary(identity, dict(raw), summary)


def read_saved_redaction(
    records: RecordRepository, objects: EvidenceObjectStore, basis: Mapping[str, object],
) -> CollectedRedactionSummary | None:
    identity = basis["summary_id"]
    if not isinstance(identity, str):
        raise ValueError("permanent redaction summary identity cannot be verified")
    revision = records.current_revision(aggregate_kind=KIND, record_id=identity)
    if type(revision) is not int or revision not in {0, 1}:
        raise ValueError("permanent redaction summary must have exactly one warehouse revision")
    if not revision:
        return None
    record = records.read(aggregate_kind=KIND, record_id=identity, revision=1)
    if (
        getattr(record, "aggregate_kind", None), getattr(record, "record_id", None),
        getattr(record, "revision", None),
    ) != (KIND, identity, 1) or type(getattr(record, "revision", None)) is not int:
        raise ValueError("permanent redaction summary exact envelope cannot be verified")
    raw = getattr(record, "payload", None)
    if not isinstance(raw, Mapping):
        raise ValueError("permanent redaction summary body cannot be verified")
    return verify_redaction_material(identity=identity, raw=raw, basis=basis, objects=objects)


class SavedRedactionMaterials:
    def __init__(
        self, records: RecordRepository, spool: SpoolStore, objects: EvidenceObjectStore,
        workspace_id: str,
    ) -> None:
        self.records, self.spool, self.objects = records, spool, objects
        self.workspace_id = workspace_id

    def materials(
        self, context: EvidencePublicationContext, checkpoint: RecoveryRecord, *, saved_only: bool,
    ) -> tuple[CollectedRedactionSummary, ...]:
        attempt = checkpoint.attempt
        streams = {block.stream_name for block in attempt.output_block_refs
                   if block.redaction_summary_id is not None}
        if not streams:
            return ()
        result = []
        for stream in sorted(streams):
            basis = summary_basis(
                project_id=context.project_id, workspace_id=self.workspace_id,
                run_id=context.run_id, step_id=context.step_id, attempt_id=context.attempt_id,
                stream=stream, code_identity=_CODE.dump_python(context.code_identity, mode="json"),
                blocks=attempt.output_block_refs,
            )
            saved = read_saved_redaction(self.records, self.objects, basis)
            if saved is not None:
                result.append(saved)
                continue
            if saved_only or not has_reliable_terminal_fact(attempt):
                continue
            if not callable(getattr(self.spool, "read_redaction_summary", None)):
                continue
            try:
                summary = cast(SpoolRedactionSummaryReader, self.spool).read_redaction_summary(
                    attempt.attempt_id, stream,
                )
            except FileNotFoundError:
                continue
            raw = redaction_summary_payload(summary, attempt.attempt_id, stream)
            content = encode_summary(raw)
            stored = self.objects.publish_bytes(
                context.project_id, content, media_type="application/json",
            )
            payload = {
                **basis, "summary": raw, "object_ref": _OBJECT.dump_python(stored, mode="json"),
            }
            result.append(verify_redaction_material(
                identity=str(basis["summary_id"]), raw=payload, basis=basis, objects=self.objects,
            ))
        return tuple(result)
