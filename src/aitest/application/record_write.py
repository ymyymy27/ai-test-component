"""An ordinary record and its exact original business result share one transaction."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from aitest.application.planning.substrate import (
    AggregateKind,
    RecordReader,
    StagedRevision,
    Transaction,
    current_record,
    read_scoped_record,
)
from aitest.application.ports import RecordValueProtector
from aitest.contracts.commands import Command
from aitest.contracts.redaction import redact_structure

KIND: AggregateKind = "record_write_intent"
SCHEMA = "aitest.record-write-intent/1.0"
_KINDS: Mapping[str, AggregateKind] = {
    "save_context": "project",
    "save_environment": "environment",
    "save_dependency_graph": "dependency_set",
    "save_case": "case",
    "save_acceptance": "acceptance_scope",
    "save_task": "task",
    "save_delivery": "delivery",
}
_RESULT_IDENTITIES = {
    "save_context": ("project", "local_project_id"),
    "save_environment": ("environment", "environment_id"),
    "save_case": ("case", "case_id"),
    "save_acceptance": ("acceptance_scope", "scope_id"),
    "save_task": ("task", "task_id"),
    "save_delivery": ("delivery", "delivery_id"),
}
_FIELDS = {
    "schema_version",
    "workspace_id",
    "project_id",
    "intent_id",
    "action",
    "input_digest",
    "aggregate_kind",
    "record_id",
    "record_revision",
    "record_digest",
    "created_at_commit",
}


class RecordWriteBlocked(ValueError):
    code = "B_RECORD_WRITE_UNVERIFIED"


class RecordWriteConflict(ValueError):
    code = "INTENT_CONFLICT"


def _bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_bytes(value)).hexdigest()


@dataclass(frozen=True)
class RecordWriteIntent:
    project_id: str
    intent_id: str
    action: str
    expected_revision: int
    input_digest: str
    business_record_id: object
    reader: RecordReader
    workspace_id: str | None
    protector: RecordValueProtector | None = None

    @classmethod
    def from_command(
        cls,
        command: Command,
        *,
        reader: RecordReader,
        workspace_id: str | None,
        protector: RecordValueProtector | None = None,
    ) -> RecordWriteIntent:
        if (
            command.action not in _KINDS
            or command.project_id is None
            or command.intent_id is None
            or type(command.expected_revision) is not int
        ):
            raise RecordWriteBlocked("ordinary write requires frozen project, intent and revision")
        basis = command.model_dump(mode="json", exclude={"request_id", "intent_id"})
        _require_safe(basis, protector)
        if command.action == "save_dependency_graph":
            identity: object = "graph:" + command.project_id
        else:
            parameter, field = _RESULT_IDENTITIES[command.action]
            material = command.parameters.get(parameter)
            identity = material.get(field) if isinstance(material, Mapping) else None
        return cls(
            command.project_id,
            command.intent_id,
            command.action,
            command.expected_revision,
            _digest(basis),
            identity,
            reader,
            workspace_id,
            protector,
        )

    @property
    def record_id(self) -> str:
        return "record-write-" + _digest([self.project_id, self.intent_id])[7:]

    def original(self) -> StagedRevision | None:
        try:
            record = current_record(
                self.reader,
                project_id=self.project_id,
                aggregate_kind=KIND,
                record_id=self.record_id,
            )
        except Exception as error:
            raise RecordWriteBlocked("original ordinary write receipt is unreadable") from error
        if record is None:
            return None
        receipt = record.payload
        if (
            type(record.revision) is not int
            or record.revision != 1
            or set(receipt) != _FIELDS
            or (
                receipt.get("schema_version"),
                receipt.get("workspace_id"),
                receipt.get("project_id"),
                receipt.get("intent_id"),
            )
            != (SCHEMA, self.workspace_id, self.project_id, self.intent_id)
        ):
            raise RecordWriteBlocked("original ordinary write receipt cannot be verified")
        if (receipt.get("action"), receipt.get("input_digest")) != (self.action, self.input_digest):
            raise RecordWriteConflict("business write intent has different frozen input")
        kind, identity, revision = (
            receipt.get("aggregate_kind"),
            receipt.get("record_id"),
            receipt.get("record_revision"),
        )
        sequence = receipt.get("created_at_commit")
        if (
            kind != _KINDS[self.action]
            or not isinstance(identity, str)
            or not identity.strip()
            or identity != self.business_record_id
            or type(revision) is not int
            or revision != self.expected_revision + 1
            or not isinstance(sequence, str)
            or not sequence.isascii()
            or not sequence.isdecimal()
            or sequence != str(int(sequence))
            or int(sequence) < 1
        ):
            raise RecordWriteBlocked("original ordinary write reference cannot be verified")
        try:
            saved = read_scoped_record(
                self.reader,
                project_id=self.project_id,
                aggregate_kind=kind,
                record_id=identity,
                revision=revision,
            )
        except Exception as error:
            raise RecordWriteBlocked("original ordinary write material is unreadable") from error
        if receipt.get("record_digest") != _digest(saved.payload):
            raise RecordWriteBlocked("original ordinary write material differs from its receipt")
        return StagedRevision(kind, identity, revision)

    def prepare_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        owner = payload.get("project_id", payload.get("local_project_id"))
        if owner != self.project_id:
            raise RecordWriteBlocked("ordinary write payload belongs to another project")
        _require_safe(payload, self.protector)
        # Match the actual persisted JSON containers, without sharing mutable caller data.
        return cast(dict[str, object], json.loads(_bytes(payload)))

    def stage(self, tx: Transaction, staged: StagedRevision, payload: Mapping[str, object]) -> None:
        if (
            staged.aggregate_kind != _KINDS[self.action]
            or staged.record_id != self.business_record_id
            or type(staged.revision) is not int
            or staged.revision != self.expected_revision + 1
        ):
            raise RecordWriteBlocked("staged ordinary write does not match its frozen revision")
        sequence = tx.next_commit_seq()
        if (
            not isinstance(sequence, str)
            or not sequence.isascii()
            or not sequence.isdecimal()
            or sequence != str(int(sequence))
            or int(sequence) < 1
        ):
            raise RecordWriteBlocked("ordinary write commit boundary cannot be verified")
        receipt = {
            "schema_version": SCHEMA,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "intent_id": self.intent_id,
            "action": self.action,
            "input_digest": self.input_digest,
            "aggregate_kind": staged.aggregate_kind,
            "record_id": staged.record_id,
            "record_revision": staged.revision,
            "record_digest": _digest(payload),
            "created_at_commit": sequence,
        }
        proof = tx.stage_record(
            aggregate_kind=KIND, record_id=self.record_id, expected_revision=0, payload=receipt
        )
        if type(proof.revision) is not int or proof.revision != 1:
            raise RecordWriteBlocked("ordinary write receipt revision cannot be verified")


def _require_safe(value: Mapping[str, object], protector: RecordValueProtector | None) -> None:
    guarded = protector(value) if protector is not None else redact_structure(value)[0]
    if not isinstance(guarded, Mapping) or _bytes(guarded) != _bytes(value):
        raise RecordWriteBlocked("ordinary write requires safe material before freezing its digest")
