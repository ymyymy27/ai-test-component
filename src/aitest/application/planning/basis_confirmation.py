"""Append an exact basis confirmation; never rewrite plans or execution facts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from aitest.application.planning.draft import text_digest
from aitest.application.planning.publish import payload_digest
from aitest.application.planning.serialization import (
    case_from_payload,
    confirmation_from_payload,
    confirmation_to_payload,
)
from aitest.application.planning.substrate import RecordReader, current_record
from aitest.application.ports import StageableWorkspaceUnitOfWork
from aitest.domain.planning.plans import AssertionBasisState, ConfirmationRecord


class BasisConfirmationError(ValueError):
    code = "B_BASIS_UNVERIFIED"


class BasisConfirmationConflict(BasisConfirmationError):
    code = "INTENT_CONFLICT"


@dataclass(frozen=True, slots=True)
class BasisConfirmationService:
    reader: RecordReader
    unit: StageableWorkspaceUnitOfWork

    def confirm(
        self,
        *,
        request_id: str,
        intent_id: str,
        project_id: str,
        case_id: str,
        case_revision: int,
        basis_revision: int,
        basis_text_digest: str,
    ) -> Mapping[str, object]:
        identity = "confirmation-" + payload_digest([project_id, intent_id])[7:]
        fingerprint = payload_digest(
            [project_id, case_id, case_revision, basis_revision, basis_text_digest]
        )
        original = self._original(project_id, identity, fingerprint)
        if original is not None:
            return original
        self.unit.begin(request_id, project_id, intent_id=intent_id)
        try:
            original = self._original(project_id, identity, fingerprint)
            if original is not None:
                self.unit.rollback(request_id)
                return original
            saved = self.reader.read(
                aggregate_kind="case", record_id=case_id, revision=case_revision
            )
            if saved.payload.get("project_id") != project_id:
                raise BasisConfirmationError("case belongs to another or unknown project")
            current = current_record(
                self.reader, project_id=project_id, aggregate_kind="case", record_id=case_id
            )
            if current != saved:
                raise BasisConfirmationError(
                    "case changed; read its current basis before confirming"
                )
            case = case_from_payload(saved.payload)
            basis = case.assertion_basis
            if (
                case.case_id != case_id
                or basis.state is AssertionBasisState.MISSING
                or basis.revision != basis_revision
                or basis.text_digest != basis_text_digest
                or text_digest(basis.text) != basis_text_digest
            ):
                raise BasisConfirmationError("confirmation does not match the exact saved basis")
            confirmation = ConfirmationRecord(
                identity, case_id, basis_revision, basis_text_digest, self.unit.next_commit_seq()
            )
            payload = confirmation_to_payload(confirmation, project_id=project_id)
            payload.update(
                case_revision=case_revision,
                input_digest=fingerprint,
                intent_id=intent_id,
                request_id=request_id,
            )
            self.unit.stage_record(
                aggregate_kind="case_link", record_id=identity, expected_revision=0, payload=payload
            )
            self.unit.commit(request_id)
            return confirmation_to_payload(confirmation, project_id=project_id)
        except BaseException:
            self.unit.rollback(request_id)
            raise

    def _original(self, project: str, record: str, fingerprint: str) -> Mapping[str, object] | None:
        saved = current_record(
            self.reader, project_id=project, aggregate_kind="case_link", record_id=record
        )
        if saved is None:
            return None
        if saved.revision != 1 or saved.payload.get("project_id") != project:
            raise BasisConfirmationError("immutable confirmation has an unknown owner or revision")
        if saved.payload.get("input_digest") != fingerprint:
            raise BasisConfirmationConflict("same confirmation intent has different inputs")
        confirmation = confirmation_from_payload(saved.payload)
        if confirmation.confirmation_id != record:
            raise BasisConfirmationError("saved confirmation identity differs")
        return confirmation_to_payload(confirmation, project_id=project)
