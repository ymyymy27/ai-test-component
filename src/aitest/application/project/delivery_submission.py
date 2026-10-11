"""Normalize only exact saved delivery/task/source references for controlled submission."""

from collections.abc import Mapping
from dataclasses import asdict

from aitest.application.approval_service import _digest
from aitest.application.planning.basis_approval import SavedBasisApprovalResolver
from aitest.application.ports import ApprovalRecords, ControlledWriteProof
from aitest.domain.approvals import ApprovalMaterialRef, ApprovalRequired
from aitest.domain.project.deliveries import DeliverySubmission

from .serialization import delivery_from_payload, delivery_to_payload, task_from_payload
from .source_analysis import SourceAnalysisService


def _reference(raw: object, key: str) -> tuple[str, int]:
    if not isinstance(raw, Mapping) or set(raw) != {key, "record_revision"}:
        raise ApprovalRequired("delivery submission requires its exact declared references")
    identity, revision = raw[key], raw["record_revision"]
    if (
        not isinstance(identity, str)
        or not identity.strip()
        or type(revision) is not int
        or revision < 1
    ):
        raise ApprovalRequired("delivery submission reference is not an exact warehouse identity")
    return identity, revision


def submission_content(
    project: str,
    parameters: Mapping[str, object],
    records: ApprovalRecords | None,
    workspace: str,
    proof: ControlledWriteProof | None,
    sources: SourceAnalysisService | None,
) -> tuple[dict[str, object], tuple[ApprovalMaterialRef, ...]]:
    if (
        set(parameters)
        != {"project_revision", "expected_revision", "submission_id", "delivery_ref", "source_ref"}
        or type(parameters["expected_revision"]) is not int
        or parameters["expected_revision"] != 0
    ):
        raise ApprovalRequired("formal submission is immutable and requires its exact inputs")
    identity = parameters["submission_id"]
    if not isinstance(identity, str) or not identity.strip():
        raise ApprovalRequired("formal submission identity is missing")
    if records is None or proof is None or sources is None:
        raise ApprovalRequired("formal submission's saved source/origin readers are unavailable")
    delivery_id, delivery_revision = _reference(parameters["delivery_ref"], "delivery_id")
    snapshot_id, snapshot_revision = _reference(parameters["source_ref"], "snapshot_id")
    reader = SavedBasisApprovalResolver(records, workspace)
    delivery_raw = reader._read("delivery", delivery_id, delivery_revision, project)
    delivery = delivery_from_payload(delivery_raw)
    task_revision = delivery_raw.get("task_revision")
    if type(task_revision) is not int or task_revision < 1:
        raise ApprovalRequired("legacy delivery needs an exact saved task revision")
    if delivery_raw != delivery_to_payload(delivery, project_id=project) | {
        "task_revision": task_revision
    }:
        raise ApprovalRequired("delivery draft has unknown or inconsistent saved fields")
    if delivery.verified_in_scope:
        raise ApprovalRequired(
            "historical self-declared verification is not formal delivery evidence"
        )
    task_raw = reader._read("task", delivery.task_id, task_revision, project)
    task = task_from_payload(task_raw)
    snapshot_raw = reader._read("source_snapshot", snapshot_id, snapshot_revision, project)
    verified_source = sources.verify_saved(
        project_id=project, snapshot_id=snapshot_id, revision=snapshot_revision
    )
    if dict(verified_source) != snapshot_raw:
        raise ApprovalRequired("delivery source differs from actual fixed material")
    binding_id, binding_revision = (
        snapshot_raw.get("binding_id"),
        snapshot_raw.get("binding_revision"),
    )
    if not isinstance(binding_id, str) or type(binding_revision) is not int:
        raise ApprovalRequired("delivery source has no exact binding")
    binding_raw = reader._read("binding", binding_id, binding_revision, project)
    proof.validate_saved_write(
        project_id=project,
        action="save_binding",
        aggregate_kind="binding",
        record_id=binding_id,
        record_revision=binding_revision,
        payload=binding_raw,
    )
    content_identity = snapshot_raw.get("content_identity")
    if not isinstance(content_identity, str):
        raise ApprovalRequired("delivery source identity cannot be verified")
    submission = DeliverySubmission(
        identity,
        project,
        delivery.version,
        delivery_id,
        delivery_revision,
        delivery.task_id,
        task_revision,
        snapshot_id,
        snapshot_revision,
        binding_id,
        binding_revision,
        content_identity,
        tuple(item.acceptance_item_id for item in task.acceptance_items),
    )
    payload = asdict(submission)
    payload["acceptance_item_ids"] = list(submission.acceptance_item_ids)
    payload.update(schema_version="aitest.delivery-submission/1.0", status="submitted")
    materials = tuple(
        ApprovalMaterialRef(kind, record_id, revision, _digest(raw))
        for kind, record_id, revision, raw in (
            ("delivery", delivery_id, delivery_revision, delivery_raw),
            ("task", delivery.task_id, task_revision, task_raw),
            ("source_snapshot", snapshot_id, snapshot_revision, snapshot_raw),
            ("binding", binding_id, binding_revision, binding_raw),
        )
    )
    return payload, materials
