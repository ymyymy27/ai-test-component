"""Immutable, content addressed authority with bounded identity lookup.

The header is published through current.json in migrated workspaces; records.json
is the legacy publication path. New nodes remain uncommitted until that switch.
Full traversal is reserved for migration, integrity and explicit index rebuilding.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.security import guard_bytes, guard_value

from . import atomic
from .material_json import decode_material, read_material_bytes

SCHEMA = "aitest.records/2.0"
_LEAF_LIMIT = 32
_MISSING = object()
_MAX_NODE_BYTES = 16 * 1024 * 1024


def _key(*parts: object) -> str:
    return json.dumps(parts, separators=(",", ":"), ensure_ascii=True)


def exact_counter(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("authority counter must be a nonnegative integer")
    return value


class AuthorityTree:
    def __init__(self, root: Path, pointer: str | None = None) -> None:
        self.directory = root / "record-store"
        self.pointer = pointer
        self._cache: dict[str, dict[str, Any]] = {}

    def _read(self, pointer: str) -> dict[str, Any]:
        if len(pointer) != 64 or any(c not in "0123456789abcdef" for c in pointer):
            raise ValueError("invalid authority node reference")
        if pointer not in self._cache:
            path = self.directory / f"{pointer}.json"
            self._reject_links(path)
            raw = read_material_bytes(path, _MAX_NODE_BYTES)
            if hashlib.sha256(raw).hexdigest() != pointer:
                raise ValueError("authority node digest mismatch")
            value = decode_material(raw)
            if not isinstance(value, dict):
                raise ValueError("invalid authority node")
            if set(value) not in ({"value"}, {"entries"}, {"children"}):
                raise ValueError("invalid authority node shape")
            for field, maximum in (("entries", _LEAF_LIMIT), ("children", 16)):
                if field not in value:
                    continue
                references = value[field]
                if not isinstance(references, dict) or len(references) > maximum:
                    raise ValueError("invalid authority node references")
                if field == "children" and any(
                    len(key) != 1 or key not in "0123456789abcdef" for key in references
                ):
                    raise ValueError("invalid authority hash path")
                if any(
                    not isinstance(ref, str)
                    or len(ref) != 64
                    or any(c not in "0123456789abcdef" for c in ref)
                    for ref in references.values()
                ):
                    raise ValueError("invalid authority node reference")
            self._cache[pointer] = value
        return deepcopy(self._cache[pointer])

    def _write(self, node: dict[str, Any]) -> str:
        raw = (
            json.dumps(node, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode("utf-8")
        if len(raw) > _MAX_NODE_BYTES:
            raise ValueError("immutable material exceeds its byte limit")
        material = decode_material(raw)
        safe, changed = guard_value(material)
        guarded, raw_changed = guard_bytes(raw)
        if changed or safe != material or raw_changed or guarded != raw:
            raise ValueError("unsafe authority material cannot preserve its identity")
        pointer = hashlib.sha256(raw).hexdigest()
        path = self.directory / f"{pointer}.json"
        self._reject_links(path)
        if not path.exists():
            atomic.write_json(path, material)
        elif read_material_bytes(path, _MAX_NODE_BYTES) != raw:
            raise ValueError("immutable authority node was modified")
        self._cache[pointer] = material
        return pointer

    @staticmethod
    def _reject_links(path: Path) -> None:
        if any(part.is_symlink() or compat.is_junction(part) for part in (path, *path.parents)):
            raise ValueError("authority material path contains a filesystem link")

    def get(self, key: str, default: Any = None) -> Any:
        pointer = self.pointer
        digest = hashlib.sha256(key.encode()).hexdigest()
        depth = 0
        while pointer is not None:
            node = self._read(pointer)
            if "entries" in node:
                value = node["entries"].get(key)
                if value is None:
                    return default
                material = self._read(value)
                if set(material) != {"value"}:
                    raise ValueError("authority value reference points to a directory")
                return material["value"]
            if depth >= len(digest):
                raise ValueError("authority tree exceeded maximum depth")
            if "children" not in node:
                raise ValueError("authority path does not reference a tree directory")
            pointer = node["children"].get(digest[depth])
            depth += 1
        return default

    def put(self, key: str, value: object) -> None:
        digest = hashlib.sha256(key.encode()).hexdigest()
        value_pointer = self._write({"value": value})

        def update(pointer: str | None, depth: int) -> str:
            node = self._read(pointer) if pointer is not None else {"entries": {}}
            if "entries" in node:
                entries = {**node["entries"], key: value_pointer}
                if len(entries) <= _LEAF_LIMIT:
                    return self._write({"entries": entries})
                return split(entries, depth)
            if depth >= len(digest):
                raise ValueError("authority hash collision cannot be split")
            if "children" not in node:
                raise ValueError("authority path does not reference a tree directory")
            children = dict(node["children"])
            children[digest[depth]] = update(children.get(digest[depth]), depth + 1)
            return self._write({"children": children})

        def split(entries: dict[str, Any], depth: int) -> str:
            if depth >= len(digest):
                raise ValueError("authority hash collision cannot be split")
            groups: dict[str, dict[str, Any]] = {}
            for entry, item in entries.items():
                digit = hashlib.sha256(entry.encode()).hexdigest()[depth]
                groups.setdefault(digit, {})[entry] = item
            children = {
                digit: (
                    self._write({"entries": group})
                    if len(group) <= _LEAF_LIMIT
                    else split(group, depth + 1)
                )
                for digit, group in groups.items()
            }
            return self._write({"children": children})

        self.pointer = update(self.pointer, 0)

    def items(self) -> Iterator[tuple[str, Any]]:
        def walk(pointer: str, depth: int = 0) -> Iterator[tuple[str, Any]]:
            if depth > 64:
                raise ValueError("authority tree exceeded maximum depth")
            node = self._read(pointer)
            if "entries" in node:
                for key, value in node["entries"].items():
                    material = self._read(value)
                    if set(material) != {"value"}:
                        raise ValueError("authority value reference points to a directory")
                    yield key, material["value"]
            else:
                if "children" not in node:
                    raise ValueError("authority path does not reference a tree directory")
                for child in node["children"].values():
                    yield from walk(child, depth + 1)

        if self.pointer is not None:
            yield from walk(self.pointer)


class ShardedRows(list[Any]):
    def __init__(self, tree: AuthorityTree, kind: str, record_id: str) -> None:
        self.tree, self.kind, self.record_id = tree, kind, record_id

    @property
    def metadata(self) -> dict[str, Any]:
        value = self.tree.get(_key("identity", self.kind, self.record_id), _MISSING)
        if value is _MISSING:
            return {}
        if not isinstance(value, dict) or "revision" not in value:
            raise ValueError("authority identity metadata is invalid")
        exact_counter(value["revision"])
        return dict(value)

    def __len__(self) -> int:
        return exact_counter(self.metadata.get("revision", 0))

    def __getitem__(self, index: Any) -> Any:
        if type(index) is not int:
            raise TypeError("authority revisions require an exact integer index")
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError("unknown authority revision")
        value = self.tree.get(_key("record", self.kind, self.record_id, index + 1), _MISSING)
        if value is _MISSING:
            raise IndexError("unknown authority revision")
        if not isinstance(value, dict):
            raise ValueError("authority record is not an object")
        return dict(value)

    def __iter__(self) -> Iterator[Any]:
        for index in range(len(self)):
            yield self[index]

    def set_owner(self, owner: str | None) -> None:
        self.tree.put(
            _key("identity", self.kind, self.record_id),
            {**self.metadata, "revision": len(self), "project_id": owner},
        )

    def append(self, payload: Any) -> None:
        revision = len(self) + 1
        self.tree.put(_key("record", self.kind, self.record_id, revision), dict(payload))
        self.tree.put(
            _key("identity", self.kind, self.record_id), {**self.metadata, "revision": revision}
        )


class ShardedIdentities(dict[str, Any]):
    def __init__(self, tree: AuthorityTree, kind: str) -> None:
        self.tree, self.kind = tree, kind

    def get(self, record_id: str, default: Any = None) -> Any:
        rows = ShardedRows(self.tree, self.kind, record_id)
        return rows if len(rows) else default

    def setdefault(self, record_id: str, default: Any = None) -> ShardedRows:
        return ShardedRows(self.tree, self.kind, record_id)

    def items(self) -> Any:
        for key, _value in self.tree.items():
            parts = json.loads(key)
            if parts[:2] == ["identity", self.kind]:
                yield parts[2], ShardedRows(self.tree, self.kind, parts[2])


class ShardedKinds(dict[str, Any]):
    def __init__(self, tree: AuthorityTree) -> None:
        self.tree = tree

    def get(self, kind: str, default: Any = None) -> ShardedIdentities:
        return ShardedIdentities(self.tree, kind)

    def setdefault(self, kind: str, default: Any = None) -> ShardedIdentities:
        return ShardedIdentities(self.tree, kind)

    def items(self) -> Any:
        kinds = set()
        for key, _value in self.tree.items():
            parts = json.loads(key)
            if parts[0] == "identity":
                kinds.add(parts[1])
        for kind in sorted(kinds):
            yield kind, ShardedIdentities(self.tree, kind)


class ShardedIntents(dict[str, Any]):
    def __init__(self, tree: AuthorityTree) -> None:
        self.tree = tree

    def __contains__(self, intent: object) -> bool:
        return self.tree.get(_key("intent", intent)) is not None

    def __getitem__(self, intent: str) -> Any:
        value = self.get(intent)
        if value is None:
            raise KeyError(intent)
        return value

    def __setitem__(self, intent: str, value: Any) -> None:
        self.tree.put(_key("intent", intent), value)

    def get(self, intent: str, default: Any = None) -> Any:
        return self.tree.get(_key("intent", intent), default)

    def items(self) -> Any:
        for key, value in self.tree.items():
            parts = json.loads(key)
            if parts[0] == "intent":
                yield parts[1], value


class ShardedCommits(list[Any]):
    def __init__(self, tree: AuthorityTree) -> None:
        self.tree = tree

    def __len__(self) -> int:
        return exact_counter(self.tree.get(_key("ledger_count"), 0))

    def __iter__(self) -> Iterator[Any]:
        for index in range(len(self)):
            yield self[index]

    def __getitem__(self, index: Any) -> Any:
        if type(index) is not int:
            raise TypeError("commit lookup requires an integer index")
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError("unknown authority commit")
        value = self.tree.get(_key("ledger", index))
        if not isinstance(value, dict):
            raise ValueError("missing authority commit")
        return value

    def append(self, value: Any) -> None:
        index = len(self)
        self.tree.put(_key("ledger", index), value)
        self.tree.put(_key("ledger_count"), index + 1)
        for field in ("request_id", "intent_id"):
            if value.get(field) is not None:
                self.tree.put(_key("lookup", field, value.get("project_id"), value[field]), value)


def open_authority(root: Path, header: dict[str, Any]) -> dict[str, Any]:
    pointer = header.get("root")
    sequence = header.get("commit")
    if (
        header.get("schema") != SCHEMA
        or not isinstance(pointer, str)
        or len(pointer) != 64
        or any(c not in "0123456789abcdef" for c in pointer)
        or type(sequence) is not int
        or sequence < 0
    ):
        raise ValueError("invalid authority root header")
    tree = AuthorityTree(root, pointer)
    node = tree._read(pointer)
    if "entries" not in node and "children" not in node:
        raise ValueError("authority root must reference a tree node")
    stored_sequence = tree.get(_key("authority_commit"))
    if type(stored_sequence) is not int or stored_sequence < 0 or stored_sequence != sequence:
        raise ValueError("authority root commit watermark mismatch")
    return {
        "records": ShardedKinds(tree),
        "intents": ShardedIntents(tree),
        "commits": ShardedCommits(tree),
        "commit": sequence,
        "_tree": tree,
    }


def authority_header(data: dict[str, Any]) -> dict[str, Any]:
    data["_tree"].put(_key("authority_commit"), data["commit"])
    return {"schema": SCHEMA, "commit": data["commit"], "root": data["_tree"].pointer}


def migrate_to_shards(root: Path) -> None:
    path = root / "records.json"
    legacy: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"records": {}, "commit": 0}
    )
    if not isinstance(legacy, dict):
        raise ValueError("legacy authority must be an object")
    if legacy.get("schema") == SCHEMA:
        return
    tree = AuthorityTree(root)
    owners: dict[tuple[str, str], set[str]] = {}
    for commit in legacy.get("commits", []):
        owner = commit.get("project_id")
        if owner is not None:
            for item in commit.get("created", []):
                owners.setdefault((item["aggregate_kind"], item["record_id"]), set()).add(owner)
    for kind, identities in legacy.get("records", {}).items():
        for record_id, payloads in identities.items():
            rows = ShardedRows(tree, kind, record_id)
            observed = owners.get((kind, record_id), set()) | {
                payload["project_id"] for payload in payloads if payload.get("project_id")
            }
            if len(observed) > 1:
                raise ValueError("legacy identity has conflicting project ownership")
            rows.set_owner(next(iter(observed), None))
            for payload in payloads:
                rows.append(payload)
    intents = ShardedIntents(tree)
    for intent, payload in legacy.get("intents", {}).items():
        intents[intent] = payload
    commits = ShardedCommits(tree)
    for commit in legacy.get("commits", []):
        commits.append(commit)
    tree.put(
        _key("legacy_extras"),
        {
            key: value
            for key, value in legacy.items()
            if key not in {"records", "intents", "commits", "commit"}
        },
    )
    tree.put(_key("legacy_shape"), {"exists": path.exists(), "keys": sorted(legacy)})
    tree.put(_key("authority_commit"), legacy.get("commit", 0))
    atomic.write_json(
        path, {"schema": SCHEMA, "commit": legacy.get("commit", 0), "root": tree.pointer}
    )


def materialize_legacy(root: Path, header: dict[str, Any]) -> dict[str, Any]:
    data = open_authority(root, header)
    return {
        **data["_tree"].get(_key("legacy_extras"), {}),
        "records": {
            kind: {record_id: list(rows) for record_id, rows in identities.items()}
            for kind, identities in data["records"].items()
        },
        "intents": dict(data["intents"].items()),
        "commits": list(data["commits"]),
        "commit": data["commit"],
    }


def rollback_shards(root: Path) -> None:
    path = root / "records.json"
    if not path.exists():
        return
    header = json.loads(path.read_text(encoding="utf-8"))
    if header.get("schema") != SCHEMA:
        return
    tree = AuthorityTree(root, header["root"])
    shape = tree.get(_key("legacy_shape"))
    if not isinstance(shape, dict) or not isinstance(shape.get("keys"), list):
        raise ValueError("missing migration before-state")
    data = materialize_legacy(root, header)
    if not shape["exists"]:
        if header["commit"] != 0:
            raise ValueError("cannot remove newly committed authority")
        path.unlink()
    else:
        atomic.write_json(path, {key: data[key] for key in shape["keys"]})


def find_commit(tree: AuthorityTree, *, field: str, project_id: str, value: str) -> Any:
    return tree.get(_key("lookup", field, project_id, value))
