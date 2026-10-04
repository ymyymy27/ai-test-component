"""Verified, backed-up transition to a shared record/index/event boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from aitest.contracts.events import Event

from .commit_manifest import CommitMaterialError, FileCommitStore, canonical_bytes
from .events import FileEventJournal, derive_event_id
from .index import FileQueryIndex
from .ordered_events import OrderedEventStore
from .records import FileRecordRepository
from .sharded_records import SCHEMA, open_authority


def migrate_commit_closure(root: Path) -> str:
    """Called by FileMigrationManager only after its verified backup/activity guard."""
    store = FileCommitStore(root)
    if store.read_current(verify_material=True) is not None:
        return "shared commit root already verified"
    header = json.loads((root / "records.json").read_text(encoding="utf-8"))
    if header.get("schema") != SCHEMA:
        raise CommitMaterialError("record format must be migrated first")
    workspace = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    data = open_authority(root, header)
    ledger = list(data["commits"])
    journal = FileEventJournal(root, instance_id=workspace["workspace_id"])
    events = journal._parse_journal()
    migration_source = None
    emitter = "migration-" + uuid4().hex
    if events:
        migration_source = dict(
            format="journal-v2",
            path="event-log/journal.jsonl",
            emitter_id=emitter,
            sha256=hashlib.sha256(journal._journal.read_bytes()).hexdigest(),
        )
    elif (root / "events.json").exists():
        raw = (root / "events.json").read_bytes()
        legacy = store._decode(raw)
        if set(legacy) != {"schema", "events"} or legacy["schema"] != "aitest.events/1.0":
            raise CommitMaterialError("unsupported legacy event projection")
        per_record: list[dict[str, Any]] = []
        per_boundary = []
        prepared = []
        for entry in ledger:
            for ref in entry["created"]:
                number = len(per_record) + 1
                expected = dict(
                    event_type="record_created",
                    aggregate_kind=ref["aggregate_kind"],
                    record_id=ref["record_id"],
                    revision=ref["revision"],
                    commit_sequence=number,
                    **{
                        k: entry[k]
                        for k in (
                            "project_id",
                            "request_id",
                            "intent_id",
                            "workspace_id",
                            "writer_epoch",
                        )
                    },
                )
                per_record.append(expected)
                per_boundary.append({**expected, "commit_sequence": entry["commit_sequence"]})
                prepared.append(
                    Event(
                        event_id=derive_event_id(
                            instance_id=emitter,
                            commit_sequence=entry["commit_sequence"],
                            event_type="record_created",
                            project_id=entry["project_id"],
                            aggregate_kind=ref["aggregate_kind"],
                            record_id=ref["record_id"],
                            revision=ref["revision"],
                        ),
                        instance_id=emitter,
                        event_sequence=number,
                        event_type="record_created",
                        record_id=ref["record_id"],
                        revision=ref["revision"],
                        **{
                            k: entry[k]
                            for k in (
                                "request_id",
                                "intent_id",
                                "workspace_id",
                                "writer_epoch",
                                "commit_sequence",
                                "project_id",
                            )
                        },
                    )
                )
        frozen = canonical_bytes(legacy["events"])
        if frozen == canonical_bytes(per_record):
            source_format = "events-v1-per-record"
        elif frozen == canonical_bytes(per_boundary):
            source_format = "events-v1-boundary"
        else:
            raise CommitMaterialError("legacy events do not exactly match the authority ledger")
        migration_source = dict(
            format=source_format,
            path="events.json",
            emitter_id=emitter,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        events = tuple(prepared)
    covered: set[tuple[str, str, int]] = set()
    offset = 0
    for entry in ledger:
        refs = entry.get("created")
        if (
            not isinstance(refs, list)
            or not refs
            or type(entry.get("commit_sequence")) is not int
            or entry["commit_sequence"] != offset + len(refs)
            or entry.get("workspace_id") != workspace["workspace_id"]
            or entry.get("state") != "committed"
        ):
            raise CommitMaterialError("legacy commit range cannot be proved")
        for ref in refs:
            key = ref["aggregate_kind"], ref["record_id"], ref["revision"]
            rows = data["records"].get(key[0], {}).get(key[1], [])
            if (
                key in covered
                or not 1 <= key[2] <= len(rows)
                or rows.metadata.get("project_id") != entry.get("project_id")
            ):
                raise CommitMaterialError("legacy changed record identity cannot be proved")
            covered.add(key)
            if offset >= len(events):
                raise CommitMaterialError("legacy event evidence is missing; migration is blocked")
            event = Event.model_validate(events[offset].model_dump())
            if (
                event.event_sequence != offset + 1
                or event.record_id != key[1]
                or event.revision != key[2]
                or event.event_type != "record_created"
                or any(
                    getattr(event, name) != entry.get(name)
                    for name in (
                        "workspace_id",
                        "request_id",
                        "intent_id",
                        "project_id",
                        "writer_epoch",
                        "commit_sequence",
                    )
                )
                or event.event_id
                != derive_event_id(
                    instance_id=event.instance_id,
                    commit_sequence=event.commit_sequence,
                    event_type=event.event_type,
                    project_id=event.project_id,
                    aggregate_kind=key[0],
                    record_id=key[1],
                    revision=key[2],
                )
            ):
                raise CommitMaterialError("legacy event identity cannot prove the record boundary")
            offset += 1
    actual = {
        (kind, record_id, revision)
        for kind, identities in data["records"].items()
        for record_id, rows in identities.items()
        for revision in range(1, len(rows) + 1)
    }
    if actual != covered or offset != header["commit"] or offset != len(events):
        raise CommitMaterialError("legacy material contains unproved or extra business facts")
    query = FileQueryIndex(root, detached=True)
    previous_index = query._read_raw()
    generation = previous_index["generation"] + 1 if previous_index is not None else 1
    rows = FileRecordRepository(root)._summary_rows_from_authority(data, ledger)
    index_root = query._build_all(rows, generation=generation, commit_sequence=header["commit"])
    event_store = OrderedEventStore(root)
    event_root = event_store.prepare(
        event_store.empty_root(),
        events,
        commit_sequence=header["commit"],
        workspace_id=workspace["workspace_id"],
    )
    manifest = dict(
        schema="aitest.commit-manifest/1",
        workspace_id=workspace["workspace_id"],
        generation_id=uuid4().hex,
        writer_epoch=max(1, workspace["writer_epoch"]),
        commit_sequence=header["commit"],
        parent_manifest=None,
        operation="migration",
        request_id=None,
        intent_id=None,
        project_id=None,
        record_header=header,
        index_root=index_root,
        event_root=event_root,
        migration_source=migration_source,
        created=[],
    )
    store.publish(store.prepare(manifest))
    return "published verified shared record/index/event boundary; legacy materials retained"
