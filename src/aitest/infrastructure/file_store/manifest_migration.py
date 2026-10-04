"""Backed-up canonical manifest migration with a frozen, resumable index build."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from . import atomic
from .business_changes import BusinessChangeIndex
from .canonical_manifest import SCHEMA, complete_manifest
from .commit_manifest import CommitMaterialError, FileCommitStore, canonical_bytes
from .sharded_records import open_authority


def migrate_canonical_manifest(root: Path) -> str:
    """Invoked only by FileMigrationManager after backup and its activity guard."""
    store = FileCommitStore(root)
    current = store.read_current(verify_material=True)
    if current is None:
        raise CommitMaterialError("canonical manifest migration requires a verified current")
    previous = current["manifest"]
    if previous["schema"] == SCHEMA:
        return "canonical manifest and business change index already verified"
    source = current["pointer"]["manifest_digest"]
    high_water = previous["commit_sequence"]
    data = open_authority(root, previous["record_header"])
    checkpoint = root / "migrations/business-index-progress.json"
    offset = 0
    header = BusinessChangeIndex.empty()
    if checkpoint.exists():
        progress = store._decode(store._read_bytes(checkpoint, 1024 * 1024))
        if (
            set(progress) != {"schema", "source_manifest", "high_water", "next_entry", "root"}
            or progress["schema"] != "aitest.business-index-migration/1"
            or progress["source_manifest"] != source
            or progress["high_water"] != high_water
            or type(progress["next_entry"]) is not int
            or not 0 <= progress["next_entry"] <= len(data["commits"])
        ):
            raise CommitMaterialError("business index maintenance checkpoint cannot be verified")
        offset, header = progress["next_entry"], progress["root"]
        BusinessChangeIndex(root, header).verify(high_water=high_water)
    covered = set()
    sequence = 0
    for position, entry in enumerate(data["commits"]):
        if (
            not isinstance(entry, dict)
            or entry.get("state") != "committed"
            or entry.get("workspace_id") != previous["workspace_id"]
            or entry.get("commit_sequence") != sequence + len(entry.get("created", []))
            or not entry.get("project_id")
        ):
            raise CommitMaterialError("historical ledger cannot prove business change coverage")
        sequence = entry["commit_sequence"]
        refs = []
        for ref in entry["created"]:
            identity = ref["aggregate_kind"], ref["record_id"], ref["revision"]
            if identity in covered:
                raise CommitMaterialError("duplicate historical business record reference")
            covered.add(identity)
            rows = data["records"].get(ref["aggregate_kind"], {}).get(ref["record_id"], [])
            if rows.metadata.get("project_id") != entry["project_id"]:
                raise CommitMaterialError("historical business reference has unknown ownership")
            body = rows[ref["revision"] - 1]
            refs.append({**ref, "body_sha256": hashlib.sha256(canonical_bytes(body)).hexdigest()})
        if position < offset:
            BusinessChangeIndex(root, header).verify_changes(
                refs,
                commit_sequence=sequence,
                workspace_id=entry["workspace_id"],
                project_id=entry["project_id"],
            )
        else:
            header = BusinessChangeIndex(root, header).prepare(
                refs,
                commit_sequence=sequence,
                workspace_id=entry["workspace_id"],
                project_id=entry["project_id"],
            )
            atomic.write_json(
                checkpoint,
                {
                    "schema": "aitest.business-index-migration/1",
                    "source_manifest": source,
                    "high_water": high_water,
                    "next_entry": position + 1,
                    "root": header,
                },
            )
    actual = {
        (kind, record_id, revision)
        for kind, identities in data["records"].items()
        for record_id, rows in identities.items()
        for revision in range(1, len(rows) + 1)
    }
    if covered != actual or sequence != high_water:
        raise CommitMaterialError("business index migration cannot prove complete fixed coverage")
    identity = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    value = {
        **previous,
        "operation": "migration",
        "request_id": None,
        "intent_id": None,
        "project_id": None,
        "created": [],
        "parent_manifest": source,
        "writer_epoch": max(previous["writer_epoch"], identity["writer_epoch"]),
    }
    value = complete_manifest(
        root, value, instance_id="manifest-migration-" + uuid4().hex, business_root=header
    )
    # Recheck the frozen source under the same writer admission before publishing.
    latest = store.read_current()
    if latest is None or latest["pointer"]["manifest_digest"] != source:
        raise CommitMaterialError("business index source advanced during maintenance")
    store.publish(store.prepare(value))
    return "published canonical manifest and fixed-high-water business index; originals retained"
