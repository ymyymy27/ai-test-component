"""Freeze an explicitly selected report's verification scope into formal delivery."""

from collections.abc import Mapping

from aitest.application.approval_service import _digest
from aitest.application.execution.frozen_plan import FrozenRunPlanReader
from aitest.application.execution.runtime_revision import (
    SavedRuntimeRevisionReader,
    SnapshotContentRef,
)
from aitest.application.ports import EvidenceObjectStore, RecordRepository
from aitest.application.review.saved_reports import SavedReportService, report_from_payload
from aitest.domain.approvals import ApprovalMaterialRef
from aitest.domain.evidence.evidence import StoredObjectRef


class DeliveryVerificationReader:
    def __init__(
        self, reports: SavedReportService, records: RecordRepository, objects: EvidenceObjectStore,
    ) -> None:
        self.reports, self.records, self.objects = reports, records, objects

    def read(
        self, project: str, report_id: str, revision: int, source_identity: str,
        acceptance_ids: tuple[str, ...],
    ) -> tuple[dict[str, object], ApprovalMaterialRef]:
        record = self.records.read(
            aggregate_kind="report", record_id=report_id, revision=revision,
        )
        raw = getattr(record, "payload", None)
        if not isinstance(raw, Mapping):
            raise ValueError("delivery report payload is unreadable")
        report = report_from_payload(raw)
        if (report.context.project_id != project or report.report_id != report_id
                or getattr(record, "revision", None) != revision
                or getattr(record, "record_id", None) != report_id
                or getattr(record, "aggregate_kind", None) != "report"):
            raise ValueError("delivery verification report identity differs")
        reference = SnapshotContentRef.model_validate(raw.get("execution_snapshot_ref"))
        facts = SavedRuntimeRevisionReader(self.records).read_snapshot(
            project_id=project, run_id=report.context.run_id, reference=reference,
        )
        prepared = self.reports._prepared(project, facts.run.intent_id)
        if prepared.snapshot.content_identity != source_identity:
            raise ValueError("delivery verification report has different source content")
        # Runtime changes are read by their exact saved chain. Retain original links
        # too: removing a mapping cannot erase an acceptance obligation.
        cases = list(prepared.frozen_cases)
        if facts.runtime_revision_refs:
            frozen = FrozenRunPlanReader(self.records).read(prepared)
            cases.extend(SavedRuntimeRevisionReader(self.records).read_effective_cases(
                facts=facts, plan=frozen.plan, initial_cases=frozen.cases,
            ))
        if (report.decision.snapshot_commit_id != facts.snapshot_commit_id
                or report.decision.snapshot_cursor != facts.snapshot_cursor):
            raise ValueError("delivery verification report snapshot differs")
        # Re-read permanent bytes; a report hash alone cannot prove available material.
        for evidence in facts.evidence_refs:
            self.objects.read_bytes(StoredObjectRef(
                project, evidence.object_digest, evidence.object_size,
                evidence.media_type or "application/octet-stream",
                f"objects/{project}/{evidence.object_digest.removeprefix('sha256:')}",
            ))
        verified = report.decision.coverage.verified & report.decision.coverage.executed
        by_acceptance = {
            item: {case.case_id for case in cases
                   if item in case.links.acceptance_item_ids}
            for item in acceptance_ids
        }
        done = sorted(item for item, cases in by_acceptance.items() if cases and cases <= verified)
        projection: dict[str, object] = {
            "schema_version": "aitest.delivery-verification/1",
            "report_id": report_id, "report_record_revision": revision,
            "report_digest": _digest(raw), "run_id": facts.run_id,
            "execution_snapshot_ref": reference.model_dump(mode="json"),
            "verified_in_scope": done,
            "unverified_scope": sorted(set(acceptance_ids) - set(done)),
            "business_outcome": report.decision.business_outcome.value,
            "verification_state": (
                "verification_valid" if done else "required_verification_incomplete"
            ),
        }
        return projection, ApprovalMaterialRef("report", report_id, revision, _digest(raw))


def delivery_projection(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    value = payload.get("verification_projection")
    if value is None:
        return None
    if (not isinstance(value, Mapping)
            or value.get("schema_version") != "aitest.delivery-verification/1"):
        raise ValueError("delivery verification projection is unreadable")
    if set(value) != {
        "schema_version", "report_id", "report_record_revision", "report_digest", "run_id",
        "execution_snapshot_ref", "verified_in_scope", "unverified_scope", "business_outcome",
        "verification_state",
    }:
        raise ValueError("delivery verification projection has unknown fields")
    verified, unverified, scope = (
        value["verified_in_scope"], value["unverified_scope"], payload.get("acceptance_item_ids"),
    )
    for items in (verified, unverified, scope):
        if (not isinstance(items, list) or any(not isinstance(x, str) or not x for x in items)
                or len(set(items)) != len(items)):
            raise ValueError("delivery verification scope is unreadable")
    assert isinstance(verified, list) and isinstance(unverified, list) and isinstance(scope, list)
    if (set(verified) & set(unverified) or set(verified) | set(unverified) != set(scope)
            or value["verification_state"] != (
                "verification_valid" if verified else "required_verification_incomplete")
            or value["business_outcome"] not in {
                "passed", "failed", "incomplete", "not_applicable"}):
        raise ValueError("delivery verification scope/state contradicts its frozen materials")
    return value
