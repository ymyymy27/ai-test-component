"""Immutable checkpoint provenance alongside snapshots; never a reuse grant."""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import TypeAdapter

from aitest.application.execution.facts import (
    execution_payload_digest,
    validate_attempt_projection,
)
from aitest.application.ports import RecordRepository
from aitest.contracts.execution_facts import AttemptFact, ExecutionFacts
from aitest.domain.execution.runs import RecoveryRecord
from aitest.domain.json_material import require_json_text

KIND = "execution_checkpoint_refs"
SCHEMA = "aitest.execution-checkpoint-refs/1.0"
StagedCheckpoints = Mapping[str, tuple[object, Mapping[str, object]]]
_ADAPTER = TypeAdapter(RecoveryRecord)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("checkpoint reference requires a nonempty text identity")
    require_json_text(value)
    return value


def _digest(value: object) -> str:
    text = _text(value)
    if not text.startswith("sha256:") or len(text) != 71 or any(
        char not in "0123456789abcdef" for char in text[7:]
    ):
        raise ValueError("checkpoint reference digest cannot be verified")
    return text


def _revision(value: object) -> int:
    if not isinstance(value, int) or type(value) is not int or value < 1:
        raise ValueError("checkpoint reference requires an exact positive warehouse revision")
    return value


@dataclass(frozen=True, slots=True)
class CheckpointRef:
    record_id: str
    revision: int
    digest: str

    def __post_init__(self) -> None:
        _text(self.record_id)
        _digest(self.digest)
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("checkpoint reference requires an exact positive warehouse revision")

    def payload(self) -> dict[str, object]:
        return {"record_id": self.record_id, "revision": self.revision, "digest": self.digest}


def checkpoint_map_id(facts: ExecutionFacts) -> str:
    identity = execution_payload_digest({
        "project_id": facts.project_id, "run_id": facts.run_id,
        "snapshot_commit_id": facts.snapshot_commit_id,
    })
    return "checkpoint-refs:" + identity[7:]


def _envelope(record: object, kind: str, identity: str, revision: int) -> Mapping[str, object]:
    payload = getattr(record, "payload", None)
    if (
        getattr(record, "aggregate_kind", None), getattr(record, "record_id", None),
        getattr(record, "revision", None),
    ) != (kind, identity, revision) or (
        type(getattr(record, "revision", None)) is not int or not isinstance(payload, Mapping)
    ):
        raise ValueError("checkpoint reference envelope cannot be verified")
    return payload


def read_checkpoint_refs(
    records: RecordRepository, facts: ExecutionFacts, *, allow_absent: bool = False
) -> dict[str, CheckpointRef]:
    identity = checkpoint_map_id(facts)
    revision = records.current_revision(aggregate_kind=KIND, record_id=identity)
    if type(revision) is int and revision == 0 and allow_absent:
        return {}
    if type(revision) is not int or revision != 1:
        raise ValueError("checkpoint reference map is missing or is not immutable at revision 1")
    raw = _envelope(
        records.read(aggregate_kind=KIND, record_id=identity, revision=1), KIND, identity, 1
    )
    expected = {
        "schema_version": SCHEMA, "project_id": facts.project_id, "run_id": facts.run_id,
        "origin_workspace_id": facts.run.origin_workspace_id,
        "snapshot_commit_id": facts.snapshot_commit_id, "snapshot_revision": 1,
        "snapshot_cursor": facts.snapshot_cursor,
        "snapshot_digest": execution_payload_digest(facts.model_dump(mode="json")),
    }
    if set(raw) != set(expected) | {"checkpoint_refs"} or any(
        raw.get(key) != value for key, value in expected.items()
    ) or type(raw.get("snapshot_revision")) is not int or (
        type(raw.get("snapshot_cursor")) is not int
    ):
        raise ValueError("checkpoint reference map differs from its exact execution snapshot")
    refs = raw["checkpoint_refs"]
    if not isinstance(refs, Mapping) or set(refs) != {item.attempt_id for item in facts.attempts}:
        raise ValueError("checkpoint reference map omits or adds an attempt")
    result = {}
    for attempt_id, value in refs.items():
        if not isinstance(value, Mapping) or set(value) != {"record_id", "revision", "digest"}:
            raise ValueError("checkpoint reference has missing or unknown fields")
        reference = CheckpointRef(
            _text(value["record_id"]), _revision(value["revision"]), _digest(value["digest"])
        )
        if reference.record_id != attempt_id:
            raise ValueError("checkpoint reference changed the attempt identity")
        result[attempt_id] = reference
    return result


def validate_checkpoint_payload(
    raw: Mapping[str, object], reference: CheckpointRef, fact: AttemptFact, facts: ExecutionFacts
) -> RecoveryRecord:
    if execution_payload_digest(raw) != reference.digest or reference.record_id != fact.attempt_id:
        raise ValueError("checkpoint reference body digest or attempt identity differs")
    checkpoint = _ADAPTER.validate_json(
        json.dumps(dict(raw), ensure_ascii=False, allow_nan=False), strict=True
    )
    if checkpoint.project_id != facts.project_id or (
        checkpoint.attempt.run_id, checkpoint.attempt.step_id, checkpoint.attempt.attempt_id
    ) != (facts.run_id, fact.step_id, fact.attempt_id) or (
        checkpoint.checkpoint.run_id, checkpoint.checkpoint.step_id,
        checkpoint.checkpoint.attempt_id,
    ) != (facts.run_id, fact.step_id, fact.attempt_id) or (
        checkpoint.checkpoint.resolved_input_digest
        and checkpoint.checkpoint.resolved_input_digest != checkpoint.attempt.resolved_input_digest
    ):
        raise ValueError("checkpoint reference body belongs to another project, run or step")
    validate_attempt_projection(checkpoint.attempt, fact)
    return checkpoint


def read_referenced_checkpoint(
    records: RecordRepository, reference: CheckpointRef, fact: AttemptFact, facts: ExecutionFacts
) -> RecoveryRecord:
    raw = _envelope(
        records.read(
            aggregate_kind="execution_checkpoint", record_id=reference.record_id,
            revision=reference.revision,
        ), "execution_checkpoint", reference.record_id, reference.revision,
    )
    return validate_checkpoint_payload(raw, reference, fact, facts)


def build_checkpoint_map(
    records: RecordRepository, facts: ExecutionFacts, previous: ExecutionFacts | None,
    staged: StagedCheckpoints,
) -> dict[str, object]:
    old_refs = read_checkpoint_refs(records, previous, allow_absent=True) if (
        previous is not None and previous.attempts
    ) else {}
    old_facts = {fact.attempt_id: fact for fact in previous.attempts} if previous else {}
    refs = {}
    for fact in facts.attempts:
        old = old_facts.get(fact.attempt_id)
        if fact.attempt_id in staged:
            revision, raw = staged[fact.attempt_id]
            reference = CheckpointRef(
                fact.attempt_id, _revision(revision), execution_payload_digest(raw)
            )
            validate_checkpoint_payload(raw, reference, fact, facts)
        elif (
            old is not None
            and old.model_copy(update={"is_current": fact.is_current}) == fact
            and fact.attempt_id in old_refs
        ):
            reference = old_refs[fact.attempt_id]
            read_referenced_checkpoint(records, reference, fact, facts)
        else:
            revision = records.current_revision(
                aggregate_kind="execution_checkpoint", record_id=fact.attempt_id
            )
            if type(revision) is not int or revision < 1:
                raise ValueError("checkpoint reference has no saved warehouse revision")
            raw = _envelope(records.read(
                aggregate_kind="execution_checkpoint", record_id=fact.attempt_id, revision=revision,
            ), "execution_checkpoint", fact.attempt_id, revision)
            reference = CheckpointRef(fact.attempt_id, revision, execution_payload_digest(raw))
            validate_checkpoint_payload(raw, reference, fact, facts)
        refs[fact.attempt_id] = reference.payload()
    return {
        "schema_version": SCHEMA, "project_id": facts.project_id, "run_id": facts.run_id,
        "origin_workspace_id": facts.run.origin_workspace_id,
        "snapshot_commit_id": facts.snapshot_commit_id, "snapshot_revision": 1,
        "snapshot_cursor": facts.snapshot_cursor,
        "snapshot_digest": execution_payload_digest(facts.model_dump(mode="json")),
        "checkpoint_refs": refs,
    }
