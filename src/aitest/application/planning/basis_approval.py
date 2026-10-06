"""Freeze the exact saved assertion and owner for a controlled basis challenge."""

from collections.abc import Mapping

from aitest.application.approval_service import _digest
from aitest.application.planning.basis_confirmation import BasisConfirmationError
from aitest.application.planning.draft import text_digest
from aitest.application.planning.serialization import case_from_payload
from aitest.application.planning.substrate import require_scoped_record
from aitest.application.ports import ApprovalRecords
from aitest.domain.approvals import ActionBasis, ApprovalMaterialRef, ApprovalRequired
from aitest.domain.planning.plans import AssertionBasisState


class SavedBasisApprovalResolver:
    def __init__(self, records: ApprovalRecords, workspace_id: str) -> None:
        self.records, self.workspace_id = records, workspace_id

    def resolve(
        self,
        *,
        project_id: str,
        intent_id: str,
        action: str,
        target: str,
        parameters: Mapping[str, object],
    ) -> ActionBasis:
        if action != "confirm_basis":
            raise ApprovalRequired("this action has no registered atomic confirmation adapter")
        if set(parameters) != {"case_id", "case_revision", "basis_revision", "basis_text_digest"}:
            raise BasisConfirmationError("basis confirmation needs its exact declared fields")
        case_id, revision = parameters["case_id"], parameters["case_revision"]
        if not isinstance(case_id, str) or target != case_id or type(revision) is not int:
            raise BasisConfirmationError("basis confirmation target/revision differs")
        current = self.records.current_revision(aggregate_kind="case", record_id=case_id)
        if type(current) is not int or current < 1 or current != revision:
            raise BasisConfirmationError("case changed; review its current saved assertion")
        raw = self._read("case", case_id, revision, project_id)
        case = case_from_payload(raw)
        basis = case.assertion_basis
        if (
            case.case_id != case_id
            or basis.state is AssertionBasisState.MISSING
            or type(parameters["basis_revision"]) is not int
            or basis.revision != parameters["basis_revision"]
            or basis.text_digest != parameters["basis_text_digest"]
            or text_digest(basis.text) != basis.text_digest
        ):
            raise BasisConfirmationError("confirmation differs from the exact saved assertion")
        project_revision = self.records.current_revision(
            aggregate_kind="project", record_id=project_id
        )
        project = self._read("project", project_id, project_revision, project_id)
        if project.get("workspace_id") != self.workspace_id:
            raise ApprovalRequired("the saved project belongs to another workspace")
        return ActionBasis(
            self.workspace_id,
            project_id,
            intent_id,
            action,
            target,
            _digest(dict(parameters)),
            "none",
            (
                ApprovalMaterialRef("case", case_id, revision, _digest(raw)),
                ApprovalMaterialRef("project", project_id, project_revision, _digest(project)),
            ),
        )

    def _read(self, kind: str, identity: str, revision: int, project: str) -> dict[str, object]:
        if type(revision) is not int or revision < 1:
            raise ApprovalRequired("approval requires an exact saved owner/material revision")
        try:
            record = self.records.read(aggregate_kind=kind, record_id=identity, revision=revision)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ApprovalRequired("the exact saved approval material is unavailable") from error
        payload = getattr(record, "payload", None)
        if (
            (
                getattr(record, "aggregate_kind", None),
                getattr(record, "record_id", None),
                getattr(record, "revision", None),
            )
            != (kind, identity, revision)
            or type(getattr(record, "revision", None)) is not int
            or not isinstance(payload, Mapping)
            or payload.get("project_id", payload.get("local_project_id")) != project
        ):
            raise ApprovalRequired("saved approval material envelope/owner differs")
        try:
            require_scoped_record(
                record,
                project_id=project,
                aggregate_kind=kind,
                record_id=identity,
                revision=revision,
            )
        except ValueError as error:
            raise ApprovalRequired("saved approval material body identity differs") from error
        return dict(payload)
