"""Bounded active projection published inside the existing authority root."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from aitest.domain.execution.continuation import RunContinuation
from aitest.domain.execution.payload_identity import execution_payload_digest

from .sharded_records import AuthorityTree, ShardedRows, _key

_KEY = _key("active_execution_schedules")
_ABSENT = object()
_MAX_ACTIVE = 64


class ContinuationCapacityExceeded(ValueError):
    code = "BACKGROUND_WORK_CAPACITY"


def active_catalog(
    tree: AuthorityTree, workspace: str, *, required: bool = False
) -> dict[str, dict[str, Any]]:
    raw = tree.get(_KEY, _ABSENT)
    if raw is _ABSENT:
        if required:
            raise ValueError("registered continuation directory is missing")
        return {}
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema", "workspace_id", "entries"}
        or raw["schema"] != "aitest.active-execution-schedules/1"
        or raw["workspace_id"] != workspace
        or not isinstance(raw["entries"], dict)
        or len(raw["entries"]) > _MAX_ACTIVE
    ):
        raise ValueError("active continuation directory cannot be verified")
    entries = raw["entries"]
    for identity, entry in entries.items():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"record_id", "workspace_id", "project_id", "run_id", "revision"}
            or any(
                type(entry[key]) is not str or not 1 <= len(entry[key]) <= 128
                for key in ("record_id", "workspace_id", "project_id", "run_id")
            )
            or entry["record_id"] != identity
            or entry["workspace_id"] != workspace
            or type(entry["revision"]) is not int
            or entry["revision"] < 1
            or identity
            != RunContinuation(
                workspace, entry["project_id"], entry["run_id"], "unused", "unused", "unused", True
            ).record_id
        ):
            raise ValueError("active continuation entry cannot be verified")
    return entries


def update_catalog(
    tree: AuthorityTree,
    *,
    workspace: str,
    project: str,
    identity: str,
    revision: int,
    payload: Mapping[str, object],
    required: bool = False,
) -> None:
    work = RunContinuation.read(payload)
    if (work.workspace_id, work.project_id, work.record_id) != (workspace, project, identity):
        raise ValueError("continuation belongs to a different workspace, project or run")
    previous = ShardedRows(tree, "execution_schedule", identity)
    if len(previous):
        original = RunContinuation.read(previous[-1])
        if replace(original, active=work.active) != work:
            raise ValueError("continuation cannot replace its original admission or snapshot")
    verify_dependencies(tree, work)
    entries = active_catalog(tree, workspace, required=required)
    if work.active:
        if identity not in entries and len(entries) >= _MAX_ACTIVE:
            raise ContinuationCapacityExceeded(
                "active run capacity reached; no admission published"
            )
        entries[identity] = {
            "record_id": identity,
            "workspace_id": workspace,
            "project_id": project,
            "run_id": work.run_id,
            "revision": revision,
        }
    else:
        entries.pop(identity, None)
    tree.put(
        _KEY,
        {
            "schema": "aitest.active-execution-schedules/1",
            "workspace_id": workspace,
            "entries": entries,
        },
    )


def verify_catalog_record(
    tree: AuthorityTree,
    *,
    workspace: str,
    identity: str,
    revision: int,
    payload: Mapping[str, object],
) -> None:
    work = RunContinuation.read(payload)
    verify_dependencies(tree, work)
    entry = active_catalog(tree, workspace, required=True).get(identity)
    expected = {
        "record_id": identity,
        "workspace_id": workspace,
        "project_id": work.project_id,
        "run_id": work.run_id,
        "revision": revision,
    }
    if (
        work.workspace_id != workspace
        or work.record_id != identity
        or (entry != expected if work.active else entry is not None)
    ):
        raise ValueError("active directory differs from its exact continuation record")


def verify_dependencies(tree: AuthorityTree, work: RunContinuation) -> None:
    materials = []
    for kind, identity in (
        ("execution_intent", work.schedule_intent_id),
        ("execution_facts", work.base_snapshot_commit_id),
    ):
        rows = ShardedRows(tree, kind, identity)
        if len(rows) != 1 or rows.metadata.get("project_id") != work.project_id:
            raise ValueError("continuation dependency is missing or has another owner/revision")
        materials.append(rows[0])
    admission, snapshot = materials
    if (
        admission.get("schema_version") != "aitest.run-schedule-intent/1.0"
        or any(
            admission.get(key) != getattr(work, key)
            for key in (
                "workspace_id",
                "project_id",
                "run_id",
                "intent_id",
                "base_snapshot_commit_id",
            )
        )
        or admission.get("snapshot_digest") != execution_payload_digest(snapshot)
        or (snapshot.get("project_id"), snapshot.get("run_id"), snapshot.get("snapshot_commit_id"))
        != (work.project_id, work.run_id, work.base_snapshot_commit_id)
        or type(snapshot.get("snapshot_revision")) is not int
        or snapshot["snapshot_revision"] != 1
    ):
        raise ValueError("continuation dependency scope, frozen snapshot or digest differs")
