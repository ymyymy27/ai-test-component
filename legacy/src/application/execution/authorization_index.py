"""Exact current unused authority, read by run identity rather than history scans."""

from __future__ import annotations

from collections.abc import Mapping

from aitest.application.approval_service import _digest
from aitest.application.ports import RecordRepository, StageableWorkspaceUnitOfWork


def authorization_index_id(workspace: str, project: str, run: str) -> str:
    return "authorization-index-" + _digest([workspace, project, run])[7:]


def index_payload(
    workspace: str, project: str, run: str, identities: tuple[str, ...]
) -> dict[str, object]:
    if any(not isinstance(value, str) or not value.strip() for value in identities):
        raise ValueError("unused authorization identities cannot be verified")
    if tuple(sorted(set(identities))) != identities:
        raise ValueError("unused authorization identities must be sorted and unique")
    return {
        "schema_version": "aitest.unused-authorizations/1.0",
        "workspace_id": workspace,
        "project_id": project,
        "run_id": run,
        "authorization_ids": list(identities),
    }


def read_authorization_index(
    records: RecordRepository, workspace: str, project: str, run: str, *, required: bool = False
) -> tuple[int, tuple[str, ...]]:
    identity = authorization_index_id(workspace, project, run)
    revision = records.current_revision(
        aggregate_kind="execution_authorization", record_id=identity
    )
    if type(revision) is not int or revision < 0:
        raise ValueError("unused authorization index revision cannot be verified")
    if revision == 0:
        if required:
            raise ValueError("unused authorization index is unavailable; resolve new authority")
        return 0, ()
    saved = records.read(
        aggregate_kind="execution_authorization", record_id=identity, revision=revision
    )
    if type(getattr(saved, "revision", None)) is not int or (
        getattr(saved, "aggregate_kind", None),
        getattr(saved, "record_id", None),
        getattr(saved, "revision", None),
    ) != ("execution_authorization", identity, revision):
        raise ValueError("unused authorization index envelope cannot be verified")
    raw = getattr(saved, "payload", None)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("authorization_ids"), list):
        raise ValueError("unused authorization index cannot be verified")
    values = tuple(raw["authorization_ids"])
    if dict(raw) != index_payload(workspace, project, run, values):
        raise ValueError("unused authorization index fields or ownership differ")
    return revision, values


def stage_authorization_index(
    unit: StageableWorkspaceUnitOfWork,
    workspace: str,
    project: str,
    run: str,
    revision: int,
    identities: tuple[str, ...],
) -> None:
    unit.stage_record(
        aggregate_kind="execution_authorization",
        record_id=authorization_index_id(workspace, project, run),
        expected_revision=revision,
        payload=index_payload(workspace, project, run, identities),
    )
