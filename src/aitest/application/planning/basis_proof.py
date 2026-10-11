"""Read an exact controlled confirmation; frozen state labels are not authority."""

import json
from collections.abc import Mapping
from typing import cast

from pydantic import TypeAdapter

from aitest.application.planning.draft import text_digest
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import (
    case_from_payload,
    case_to_payload,
    confirmation_to_payload,
)
from aitest.application.ports import BasisConfirmationProof, RecordRepository
from aitest.domain.planning.plans import ConfirmationRecord

_FIELDS = {
    "project_id", "confirmation_id", "case_id", "basis_revision", "basis_text_digest",
    "confirmed_at_commit", "case_revision", "input_digest", "intent_id", "request_id",
    "approval_confirmation_id",
}
_CONFIRMATION = TypeAdapter(ConfirmationRecord)


class SavedCaseBasisReader:
    def __init__(self, records: RecordRepository, proof: BasisConfirmationProof | None) -> None:
        self.records, self.proof = records, proof

    def _read(self, project: str, kind: str, identity: str, revision: int) -> Mapping[str, object]:
        record = self.records.read(aggregate_kind=kind, record_id=identity, revision=revision)
        raw = getattr(record, "payload", None)
        if (
            getattr(record, "aggregate_kind", None), getattr(record, "record_id", None),
            getattr(record, "revision", None),
        ) != (kind, identity, revision) or type(getattr(record, "revision", None)) is not int or (
            not isinstance(raw, Mapping) or raw.get("project_id") != project
        ):
            raise ValueError("basis confirmation exact envelope/owner cannot be verified")
        return raw

    def read(self, *, project_id: str, confirmation_id: str) -> ConfirmationRecord:
        if any(not isinstance(x, str) or not x.strip() for x in (project_id, confirmation_id)):
            raise ValueError("basis confirmation requires exact identities")
        if self.proof is None:
            raise ValueError("controlled basis confirmation reader is unavailable")
        revision = self.records.current_revision(
            aggregate_kind="case_link", record_id=confirmation_id,
        )
        if type(revision) is not int or revision != 1:
            raise ValueError("basis confirmation must remain immutable at warehouse revision 1")
        raw = self._read(project_id, "case_link", confirmation_id, 1)
        if set(raw) != _FIELDS or any(
            not isinstance(raw[key], str) or not cast(str, raw[key]).strip()
            for key in _FIELDS - {"basis_revision", "case_revision"}
        ) or any(type(raw[key]) is not int or cast(int, raw[key]) < 1 for key in (
            "basis_revision", "case_revision",
        )):
            raise ValueError("basis confirmation needs complete strict controlled material")
        confirmation = _CONFIRMATION.validate_json(json.dumps({
            key: raw[key] for key in ConfirmationRecord.__dataclass_fields__
        }), strict=True)
        if (
            confirmation.confirmation_id != confirmation_id
            or confirmation_id != "confirmation-" + payload_digest([
                project_id, raw["intent_id"],
            ])[7:]
            or raw["input_digest"] != payload_digest([
                project_id, confirmation.case_id, raw["case_revision"],
                confirmation.basis_revision, confirmation.basis_text_digest,
            ])
            or payload_digest({key: raw[key] for key in (
                "project_id", *ConfirmationRecord.__dataclass_fields__,
            )}) != payload_digest(confirmation_to_payload(confirmation, project_id=project_id))
        ):
            raise ValueError("basis confirmation identity/input differs from original consent")
        case_raw = self._read(
            project_id, "case", confirmation.case_id, cast(int, raw["case_revision"]),
        )
        case = case_from_payload(case_raw)
        if (
            case.case_id != confirmation.case_id or case.revision != raw["case_revision"]
            or payload_digest(case_raw) != payload_digest(
                case_to_payload(case, project_id=project_id),
            )
            or not confirmation.matches(case.assertion_basis, case_id=case.case_id)
            or text_digest(case.assertion_basis.text) != confirmation.basis_text_digest
        ):
            raise ValueError("basis confirmation differs from its original exact case content")
        self.proof.validate_basis_confirmation(project_id=project_id, payload=raw)
        return confirmation
