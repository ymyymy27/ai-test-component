"""Immutable commit material and one compare-and-publish pointer.

This storage primitive does not commit business records on its own. The record,
index and event writers must prepare their material before invoking publication.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aitest.infrastructure.security import guard_bytes, guard_value

from .canonical_manifest import FIELDS as COMPLETE_FIELDS
from .canonical_manifest import OPTIONAL_FIELDS as COMPLETE_OPTIONAL_FIELDS
from .canonical_manifest import SCHEMA as COMPLETE_SCHEMA
from .canonical_manifest import validate_complete, verify_complete
from .ordered_index import OrderedIndexTree
from .publication_backend import FilePublicationBackend
from .references import verify_record_objects
from .sharded_records import SCHEMA as RECORD_SCHEMA
from .sharded_records import open_authority
from .source_material import source_record_files

MANIFEST_SCHEMA = "aitest.commit-manifest/1"
CURRENT_SCHEMA = "aitest.current-commit/2"
LEGACY_CURRENT_SCHEMA = "aitest.current-commit/1"
MAX_MANIFEST_BYTES = 1024 * 1024
_MANIFEST_FIELDS = {
    "schema",
    "workspace_id",
    "generation_id",
    "writer_epoch",
    "commit_sequence",
    "parent_manifest",
    "operation",
    "request_id",
    "intent_id",
    "project_id",
    "record_header",
    "index_root",
    "event_root",
    "created",
    "migration_source",
}


class CommitMaterialError(ValueError):
    code = "COMMIT_MATERIAL_UNVERIFIED"


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _hex(value: object, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(c in "0123456789abcdef" for c in value)
    )


def _text(value: object, *, optional: bool = False, maximum: int = 256) -> bool:
    return (
        optional
        and value is None
        or (
            isinstance(value, str)
            and 1 <= len(value) <= maximum
            and not any(ord(c) < 32 for c in value)
        )
    )


def _integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _compare(left: tuple[Any, ...], right: tuple[Any, ...]) -> int:
    return (left > right) - (left < right)


class FileCommitStore:
    def __init__(self, root: Path) -> None:
        self.reject_links(root)
        self.root = root.resolve()
        self.current_path = self.root / "current.json"
        self.directory = self.root / "manifests"

    @staticmethod
    def reject_links(path: Path) -> None:
        if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
            raise CommitMaterialError("commit material path contains a filesystem link")

    @staticmethod
    def _decode(raw: bytes) -> dict[str, Any]:
        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise CommitMaterialError("duplicate commit material field")
                value[key] = item
            return value

        try:
            value = json.loads(raw, object_pairs_hook=unique)
        except RecursionError as error:
            raise CommitMaterialError("commit material JSON exceeds parsing depth") from error
        if not isinstance(value, dict):
            raise CommitMaterialError("commit material must be an object")
        return value

    def _read_bytes(self, path: Path, maximum: int) -> bytes:
        self.reject_links(path)
        if path.stat().st_size > maximum:
            raise CommitMaterialError("commit material exceeds its read budget")
        with path.open("rb") as handle:
            raw = handle.read(maximum + 1)
        if len(raw) > maximum:
            raise CommitMaterialError("commit material exceeds its read budget")
        return raw

    def _workspace_id(self) -> str:
        workspace = self._decode(self._read_bytes(self.root / "workspace.json", 16384))
        identity = workspace.get("workspace_id")
        if not _text(identity, maximum=128):
            raise CommitMaterialError("workspace identity is unknown")
        return str(identity)

    def read_current(self, *, verify_material: bool = False) -> dict[str, Any] | None:
        """An absent pointer permits legacy handling; an unreadable pointer blocks."""
        self.reject_links(self.current_path)
        try:
            backend = FilePublicationBackend(self.root)
            if backend.inspect_publication() == "unknown":
                raise CommitMaterialError("pointer publication result cannot be verified")
            if not self.current_path.exists():
                return None
            pointer = self._decode(self._read_bytes(self.current_path, 16384))
            legacy = (
                set(pointer) == {"schema", "manifest_digest", "commit_sequence", "workspace_id"}
                and pointer.get("schema") == LEGACY_CURRENT_SCHEMA
            )
            canonical = (
                set(pointer)
                == {
                    "schema_version",
                    "workspace_id",
                    "generation",
                    "commit_id",
                    "manifest_digest",
                    "index_root",
                    "event_cursor",
                }
                and pointer.get("schema_version") == CURRENT_SCHEMA
            )
            if not (legacy or canonical) or not _hex(pointer.get("manifest_digest")):
                raise CommitMaterialError("current commit pointer is invalid or unsupported")
            manifest = self.read_manifest(str(pointer["manifest_digest"]))
            if legacy:
                valid = (
                    _integer(pointer["commit_sequence"])
                    and manifest["commit_sequence"] == pointer["commit_sequence"]
                    and manifest["workspace_id"] == pointer["workspace_id"]
                )
            else:
                # read_manifest already verified this exact immutable material.
                # Strict canonical bytes reject bool/float aliases without a second
                # walk through every business directory of a modified manifest copy.
                expected = self.current_pointer(manifest, pointer["manifest_digest"])
                valid = _integer(pointer["commit_id"]) and canonical_bytes(
                    pointer
                ) == canonical_bytes(expected)
            if not valid or manifest["workspace_id"] != self._workspace_id():
                raise CommitMaterialError("current commit identity does not match its material")
            if verify_material:
                self.verify_material(manifest)
            return dict(pointer=pointer, manifest=manifest)
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise CommitMaterialError("current commit cannot be verified") from error

    @staticmethod
    def current_pointer(value: dict[str, Any], digest: str) -> dict[str, Any]:
        from .ordered_events import OrderedEventStore

        return dict(
            schema_version=CURRENT_SCHEMA,
            workspace_id=value["workspace_id"],
            generation=value["generation_id"],
            commit_id=value["commit_sequence"],
            manifest_digest=digest,
            index_root=value["index_root"],
            event_cursor=OrderedEventStore._cursor(value, value["event_root"]["last_sequence"]),
        )

    def upgrade_current_pointer(self) -> str:
        """Called by a backed-up, activity-guarded format migration only."""
        from .locking import writer_lock

        with writer_lock(self.root / "writer.lock", reentrant=True):
            current = self.read_current(verify_material=True)
            if current is None:
                raise CommitMaterialError("pointer upgrade requires a verified complete commit")
            pointer = self.current_pointer(
                current["manifest"], current["pointer"]["manifest_digest"]
            )
            if pointer == current["pointer"]:
                FilePublicationBackend(self.root).confirm_current()
                return "canonical current pointer already present"
            previous = self._read_bytes(self.current_path, 16384)
            FilePublicationBackend(self.root).replace_current(
                canonical_bytes(pointer), previous=previous
            )
            self.read_current(verify_material=True)
            return "published canonical current pointer; original pointer retained"

    def read_manifest(self, digest: str) -> dict[str, Any]:
        if not _hex(digest):
            raise CommitMaterialError("invalid immutable manifest reference")
        raw = self._read_bytes(self.directory / f"{digest}.json", MAX_MANIFEST_BYTES)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise CommitMaterialError("immutable manifest digest mismatch")
        value = self._decode(raw)
        self.validate(value)
        return value

    def validate(self, value: dict[str, Any]) -> None:
        if (
            not (
                value.get("schema") == MANIFEST_SCHEMA
                and set(value) == _MANIFEST_FIELDS
                or value.get("schema") == COMPLETE_SCHEMA
                and set(value) - COMPLETE_OPTIONAL_FIELDS == _MANIFEST_FIELDS | COMPLETE_FIELDS
            )
            or not _text(value["workspace_id"], maximum=128)
            or not _text(value["generation_id"], maximum=128)
            or not _integer(value["writer_epoch"], 1)
            or not _integer(value["commit_sequence"])
            or value["parent_manifest"] is not None
            and not _hex(value["parent_manifest"])
            or not isinstance(value["operation"], str)
            or value["operation"] not in {"business", "migration", "maintenance"}
            or any(
                not _text(value[k], optional=True, maximum=128)
                for k in ("request_id", "intent_id", "project_id")
            )
        ):
            raise CommitMaterialError("invalid commit manifest identity")
        sequence = value["commit_sequence"]
        header = value["record_header"]
        if (
            not isinstance(header, dict)
            or set(header) != {"schema", "root", "commit"}
            or header["schema"] != RECORD_SCHEMA
            or not _hex(header["root"])
            or not _integer(header["commit"])
            or header["commit"] != sequence
        ):
            raise CommitMaterialError("record root does not belong to this commit")
        index = value["index_root"]
        if (
            not isinstance(index, dict)
            or set(index)
            != {
                "schema",
                "version",
                "generation",
                "last_commit_sequence",
                "snapshot_root",
                "snapshot_sha256",
            }
            or index["schema"] != "aitest.index-root/3-tree"
            or type(index["version"]) is not int
            or index["version"] != 3
            or not _integer(index["generation"], 1)
            or not _integer(index["last_commit_sequence"])
            or index["last_commit_sequence"] != sequence
            or not _hex(index["snapshot_root"], 32)
            or not _hex(index["snapshot_sha256"])
        ):
            raise CommitMaterialError("index root does not belong to this commit")
        event = value["event_root"]
        if (
            not isinstance(event, dict)
            or set(event)
            != {
                "schema",
                "generation",
                "last_sequence",
                "last_commit_sequence",
                "root",
            }
            or event["schema"] != "aitest.ordered-events/1"
            or not _integer(event["generation"], 1)
            or not _integer(event["last_sequence"])
            or not _integer(event["last_commit_sequence"])
            or event["last_commit_sequence"] != sequence
            or event["last_sequence"] != sequence
        ):
            raise CommitMaterialError("event root does not belong to this commit")
        tree = OrderedIndexTree(
            self.root / "event-log/pages", _compare, leaf_size=128, root=event["root"]
        )
        if tree.root is None:
            if event["last_sequence"] != 0:
                raise CommitMaterialError("nonempty event range lacks a root")
        elif (
            tree.root["count"] != event["last_sequence"]
            or tree.root["first"] != [1]
            or tree.root["last"] != [event["last_sequence"]]
        ):
            raise CommitMaterialError("event root cannot prove its complete range")
        created = value["created"]
        if not isinstance(created, list):
            raise CommitMaterialError("invalid changed record references")
        keys: set[tuple[str, str, int]] = set()
        for ref in created:
            if (
                not isinstance(ref, dict)
                or set(ref)
                != {
                    "aggregate_kind",
                    "record_id",
                    "revision",
                    "body_sha256",
                }
                or not _text(ref["aggregate_kind"], maximum=128)
                or not _text(ref["record_id"])
                or not _integer(ref["revision"], 1)
                or not _hex(ref["body_sha256"])
            ):
                raise CommitMaterialError("invalid changed record identity")
            key = ref["aggregate_kind"], ref["record_id"], ref["revision"]
            if key in keys:
                raise CommitMaterialError("duplicate changed record identity")
            keys.add(key)
        if value["operation"] == "business" and (
            not created or value["request_id"] is None or value["project_id"] is None
        ):
            raise CommitMaterialError("business commit lacks its frozen input identity")
        source = value["migration_source"]
        if source is not None and (
            value["operation"] == "business"
            or not isinstance(source, dict)
            or set(source) != {"format", "path", "sha256", "emitter_id"}
            or source["format"] not in ("journal-v2", "events-v1-per-record", "events-v1-boundary")
            or source["path"] not in ("events.json", "event-log/journal.jsonl")
            or source["path"]
            != ("event-log/journal.jsonl" if source["format"] == "journal-v2" else "events.json")
            or not _hex(source["sha256"])
            or not _text(source["emitter_id"], maximum=128)
        ):
            raise CommitMaterialError("invalid migration event source")
        if value["schema"] == COMPLETE_SCHEMA:
            validate_complete(self.root, value)

    def verify_material(self, value: dict[str, Any]) -> None:
        """Check prepared roots and changed bodies; no traversal of unrelated history."""
        self.validate(value)
        data = open_authority(self.root, value["record_header"])
        for ref in value["created"]:
            rows = data["records"].get(ref["aggregate_kind"], {}).get(ref["record_id"], [])
            if ref["revision"] > len(rows):
                raise CommitMaterialError("changed record reference is unavailable")
            body = rows[ref["revision"] - 1]
            if hashlib.sha256(canonical_bytes(body)).hexdigest() != ref["body_sha256"]:
                raise CommitMaterialError("changed record bytes do not match the manifest")
            if rows.metadata.get("project_id") != value["project_id"]:
                raise CommitMaterialError("changed record belongs to another project")
            try:
                verify_record_objects(self.root, body, value["project_id"])
                source_record_files(
                    self.root,
                    ref["aggregate_kind"],
                    body,
                    value["project_id"],
                    data,
                    require_verified="source_material_files" in value,
                )
            except (OSError, ValueError) as error:
                raise CommitMaterialError(
                    "changed record permanent object reference or pinned source is unverified"
                ) from error
        index = value["index_root"]
        raw = self._read_bytes(
            self.root / "indexes/roots" / f"{index['snapshot_root']}.json",
            256 * 1024,
        )
        if hashlib.sha256(raw).hexdigest() != index["snapshot_sha256"]:
            raise CommitMaterialError("index snapshot bytes do not match the manifest")
        snapshot = self._decode(raw)
        if (
            snapshot.get("generation") != index["generation"]
            or snapshot.get("commit_id") != value["commit_sequence"]
        ):
            raise CommitMaterialError("index snapshot boundary does not match the manifest")
        # Validate every family header and its bounded root, rather than accepting
        # a readable JSON snapshot that cannot serve the promised query actions.
        from .index import FileQueryIndex

        query = FileQueryIndex(self.root, detached=True, snapshot_meta=index)
        if not query.is_healthy():
            raise CommitMaterialError("prepared query directories cannot be verified")
        event = value["event_root"]
        tree = OrderedIndexTree(
            self.root / "event-log/pages", _compare, leaf_size=128, root=event["root"]
        )
        if tree.root is not None:
            tree.read(tree.root)
        if value["operation"] == "business":
            self._verify_business(value, data, snapshot, tree)
        if value["migration_source"] is not None:
            source = value["migration_source"]
            path = self.root / source["path"]
            self.reject_links(path)
            # Full history reads belong to migration/explicit verification only.
            if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                raise CommitMaterialError("retained migration source bytes changed")
        if value["schema"] == COMPLETE_SCHEMA:
            verify_complete(self.root, value)

    def _verify_business(
        self,
        value: dict[str, Any],
        data: dict[str, Any],
        snapshot: dict[str, Any],
        events: OrderedIndexTree,
    ) -> None:
        from aitest.contracts.events import Event

        from .events import derive_event_id
        from .index import _POINT_FAMILY, FileQueryIndex, build_index_row
        from .sharded_records import find_commit

        parent_digest = value["parent_manifest"]
        if parent_digest is None:
            raise CommitMaterialError("business publication requires an initialized parent")
        parent = self.read_manifest(parent_digest)
        refs = value["created"]
        if (
            value["workspace_id"] != parent["workspace_id"]
            or value["generation_id"] != parent["generation_id"]
            or value["commit_sequence"] != parent["commit_sequence"] + len(refs)
            or value["event_root"]["generation"] != parent["event_root"]["generation"]
            or value["event_root"]["last_sequence"]
            != parent["event_root"]["last_sequence"] + len(refs)
        ):
            raise CommitMaterialError("business record and event ranges do not match")
        entry = find_commit(
            data["_tree"],
            field="request_id",
            project_id=value["project_id"],
            value=value["request_id"],
        )
        expected_refs = [
            {k: r[k] for k in ("aggregate_kind", "record_id", "revision")} for r in refs
        ]
        if (
            not isinstance(entry, dict)
            or entry.get("created") != expected_refs
            or any(
                entry.get(k) != value[k]
                for k in (
                    "request_id",
                    "intent_id",
                    "project_id",
                    "workspace_id",
                    "writer_epoch",
                    "commit_sequence",
                )
            )
            or entry.get("state") != "committed"
            or value["schema"] == COMPLETE_SCHEMA
            and entry.get("instance_id") != value["instance_id"]
        ):
            raise CommitMaterialError("authority intent result does not match the manifest")
        index = FileQueryIndex(self.root, detached=True, snapshot_meta=value["index_root"])
        point = index._directory(_POINT_FAMILY)._tree(snapshot["families"][_POINT_FAMILY])
        for offset, ref in enumerate(refs):
            kind, record_id, revision = ref["aggregate_kind"], ref["record_id"], ref["revision"]
            body = data["records"].get(kind, {}).get(record_id, [])[revision - 1]
            expected_row = build_index_row(
                project_id=value["project_id"],
                aggregate_kind=kind,
                record_id=record_id,
                revision=revision,
                commit_sequence=parent["commit_sequence"] + offset + 1,
                payload=body,
            )
            if point.get((value["project_id"], kind, record_id, revision)) != expected_row:
                raise CommitMaterialError("changed record lacks its exact query summary")
            sequence = parent["event_root"]["last_sequence"] + offset + 1
            raw_event = events.get((sequence,))
            if raw_event is None:
                raise CommitMaterialError("changed record lacks its saved event")
            event = Event.model_validate(raw_event)
            expected = Event(
                event_id=derive_event_id(
                    instance_id=value.get("instance_id", value["workspace_id"]),
                    commit_sequence=value["commit_sequence"],
                    event_type="record_created",
                    project_id=value["project_id"],
                    aggregate_kind=kind,
                    record_id=record_id,
                    revision=revision,
                ),
                instance_id=value.get("instance_id", value["workspace_id"]),
                record_id=record_id,
                revision=revision,
                event_type="record_created",
                event_sequence=sequence,
                **{
                    k: value[k]
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
            if event != expected:
                raise CommitMaterialError("saved event does not prove this changed record")

    def prepare(self, value: dict[str, Any]) -> str:
        self.verify_material(value)
        if value["workspace_id"] != self._workspace_id():
            raise CommitMaterialError("prepared commit belongs to another workspace")
        raw = canonical_bytes(value)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise CommitMaterialError("manifest exceeds its publication budget")
        safe, changed = guard_value(value)
        guarded, bytes_changed = guard_bytes(raw)
        if changed or safe != value or bytes_changed or guarded != raw:
            raise CommitMaterialError("manifest identity cannot be safely preserved")
        digest = hashlib.sha256(raw).hexdigest()
        path = self.directory / f"{digest}.json"
        self.reject_links(path)
        if path.exists():
            if self._read_bytes(path, MAX_MANIFEST_BYTES) != raw:
                raise CommitMaterialError("existing immutable manifest was modified")
        else:
            FilePublicationBackend(self.root).publish_immutable(path, raw)
        return digest

    def publish(self, digest: str) -> None:
        """Serialize the parent check and final switch, including standalone callers."""
        from .locking import writer_lock

        with writer_lock(self.root / "writer.lock", reentrant=True):
            self._publish_locked(digest)

    def _publish_locked(self, digest: str) -> None:
        value = self.read_manifest(digest)
        if value["workspace_id"] != self._workspace_id():
            raise CommitMaterialError("prepared workspace identity has changed")
        prior = self.read_current(verify_material=True)
        parent = prior["pointer"]["manifest_digest"] if prior is not None else None
        if value["parent_manifest"] != parent:
            raise CommitMaterialError("prepared commit was based on a different current root")
        if prior is not None:
            previous = prior["manifest"]
            if previous["schema"] == COMPLETE_SCHEMA and value["schema"] != COMPLETE_SCHEMA:
                raise CommitMaterialError("canonical manifest format cannot be downgraded")
            if value["workspace_id"] != previous["workspace_id"]:
                raise CommitMaterialError("workspace identity cannot be rebound")
            if value["commit_sequence"] < previous["commit_sequence"]:
                raise CommitMaterialError("commit sequence cannot move backwards")
            if value["writer_epoch"] < previous["writer_epoch"]:
                raise CommitMaterialError("writer epoch cannot move backwards")
            if value["commit_sequence"] == previous["commit_sequence"] and (
                value["operation"] == "business"
                or value["record_header"] != previous["record_header"]
                or value["event_root"] != previous["event_root"]
                or value["migration_source"] != previous["migration_source"]
            ):
                raise CommitMaterialError("same-sequence maintenance cannot replace business facts")
            if (
                value["schema"] == previous["schema"] == COMPLETE_SCHEMA
                and value["operation"] != "business"
                and value["business_change_index_root"] != previous["business_change_index_root"]
            ):
                raise CommitMaterialError("ordinary maintenance must retain the business index")
        self.verify_material(value)
        pointer = self.current_pointer(value, digest)
        raw = canonical_bytes(pointer)
        guarded, changed = guard_bytes(raw)
        if changed or guarded != raw:
            raise CommitMaterialError("current pointer cannot preserve a safe identity")
        self.reject_links(self.current_path)
        previous = self._read_bytes(self.current_path, 16384) if prior is not None else None
        FilePublicationBackend(self.root).replace_current(raw, previous=previous)
        published = self.read_current(verify_material=True)
        if published is None or published["pointer"]["manifest_digest"] != digest:
            raise CommitMaterialError("published commit could not be confirmed")
