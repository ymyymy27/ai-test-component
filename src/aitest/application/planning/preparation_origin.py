"""Read the exact B preparation and original receipt without inventing a new intent."""

import json
from collections.abc import Mapping

from aitest.application.approval_service import _digest
from aitest.application.planning.preparation import (
    payload_hash,
    preparation_identity_digest,
    preparation_intent_id,
    preparation_record_from_payload,
    preparation_record_payload,
    record_id_for_intent_id,
)
from aitest.application.planning.prepare_run import preparation_payload
from aitest.application.planning.substrate import AggregateKind, RecordReader, require_scoped_record
from aitest.contracts.prepared_run import PreparedRun


def _exact_payload(
    reader: RecordReader, kind: AggregateKind, identity: str, project: str
) -> dict[str, object]:
    try:
        saved = reader.read(aggregate_kind=kind, record_id=identity, revision=1)
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise ValueError("exact saved preparation origin is unavailable") from error
    if (
        (saved.aggregate_kind, saved.record_id, saved.revision) != (kind, identity, 1)
        or type(saved.revision) is not int
        or not isinstance(saved.payload, Mapping)
        or saved.payload.get("project_id") != project
    ):
        raise ValueError("saved preparation origin envelope or project differs")
    require_scoped_record(
        saved, project_id=project, aggregate_kind=kind, record_id=identity, revision=1
    )
    return dict(saved.payload)


def load_saved_preparation(
    *, reader: RecordReader, project_id: str, workspace_id: str, prepared_run_id: str
) -> tuple[PreparedRun, str]:
    raw = _exact_payload(reader, "prepared_run", prepared_run_id, project_id)
    prepared = PreparedRun.model_validate_json(json.dumps(raw, allow_nan=False), strict=True)
    if (prepared.project_id, prepared.workspace_id, prepared.prepared_run_id) != (
        project_id,
        workspace_id,
        prepared_run_id,
    ):
        raise ValueError("saved preparation project/workspace/identity cannot be verified")
    return prepared, _digest(raw)


def validate_preparation_origin(prepared: PreparedRun, *, reader: RecordReader) -> None:
    identity = preparation_identity_digest(
        project_id=prepared.project_id,
        client_id=prepared.client_id,
        prepare_request_id=prepared.prepare_request_id,
    )
    expected_intent = preparation_intent_id(
        project_id=prepared.project_id,
        client_id=prepared.client_id,
        prepare_request_id=prepared.prepare_request_id,
    )
    if (prepared.prepared_run_id, prepared.intent_id) != ("prepared-" + identity, expected_intent):
        raise ValueError("saved preparation does not have its original B-derived identity")
    raw = _exact_payload(
        reader, "preparation_record", record_id_for_intent_id(expected_intent), prepared.project_id
    )
    receipt = preparation_record_from_payload(raw)
    request = receipt.request
    if (
        raw != preparation_record_payload(receipt)
        or receipt.cancelled
        or (request.project_id, request.client_id, request.prepare_request_id)
        != (prepared.project_id, prepared.client_id, prepared.prepare_request_id)
        or receipt.intent_id != expected_intent
        or receipt.created_at_commit != prepared.created_at_commit
        or request.payload_hash != prepared.payload_hash
        or payload_hash(preparation_payload(prepared)) != prepared.payload_hash
        or request.observed_case_revisions
        != tuple(sorted((ref.case_id, ref.revision) for ref in prepared.case_revisions))
        or request.observed_resolved_input_digest != prepared.execution_source.resolved_input_digest
        or request.observed_snapshot_content_identity != prepared.snapshot.content_identity
        or request.observed_scope_id != prepared.scope_id
        or prepared.environment.resolution is None
        or request.observed_environment_content_identity
        != prepared.environment.resolution.content_identity
        or any(
            getattr(request.input_revisions, name) != value
            for name, value in (
                ("project_revision", prepared.project_revision),
                ("binding_revision", prepared.binding_revision),
                ("snapshot_revision", prepared.snapshot.record_revision),
                ("environment_revision", prepared.environment.revision),
                ("plan_revision", prepared.plan_revision.revision_no),
                ("scope_revision", prepared.acceptance_scope_revision),
            )
        )
    ):
        raise ValueError("saved preparation and exact original receipt differ or were cancelled")
