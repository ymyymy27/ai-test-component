"""Immutable ordered pages; ordinary reads/writes visit only relevant tree paths.

Nodes have bounded fanout, checked content identities and explicit height. A root
is only a reference; publication belongs to FileQueryIndex's final root switch.
Full traversal is deliberately confined to explicit bulk building/migration.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from copy import deepcopy
from functools import cmp_to_key
from pathlib import Path
from typing import Any

from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.security import guard_value

from . import atomic
from .material_json import decode_material, read_material_bytes

Key = tuple[Any, ...]
Ref = dict[str, Any]
_FANOUT = 16
_MAX_HEIGHT = 32
_MAX_BYTES = 2 * 1024 * 1024


class OrderedIndexTree:
    def __init__(
        self,
        directory: Path,
        compare: Callable[[Key, Key], int],
        *,
        leaf_size: int,
        root: Ref | None = None,
        leaf_reader: Callable[[Ref], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.directory, self.compare = directory, compare
        self._reject_links(directory)
        self.leaf_size = max(8, leaf_size)
        self.root = self.validate_ref(root) if root is not None else None
        self.leaf_reader = leaf_reader
        self._cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _reject_links(path: Path) -> None:
        if any(part.is_symlink() or compat.is_junction(part) for part in (path, *path.parents)):
            raise ValueError("ordered index path contains a filesystem link")

    @staticmethod
    def _key(value: object) -> Key:
        if not isinstance(value, list) or not 1 <= len(value) <= 16:
            raise ValueError("invalid ordered index key")
        if any(type(part) not in (str, int) for part in value):
            raise ValueError("invalid ordered index key component")
        return tuple(value)

    def validate_ref(self, value: object) -> Ref:
        if not isinstance(value, dict) or set(value) != {"file", "count", "first", "last", "level"}:
            raise ValueError("invalid ordered index reference")
        file = value["file"]
        if (
            not isinstance(file, str)
            or len(file) != 69
            or not file.endswith(".json")
            or any(c not in "0123456789abcdef" for c in file[:-5])
        ):
            raise ValueError("invalid ordered index node identity")
        if type(value["count"]) is not int or value["count"] <= 0:
            raise ValueError("invalid ordered index count")
        if type(value["level"]) is not int or not 0 <= value["level"] <= _MAX_HEIGHT:
            raise ValueError("invalid ordered index height")
        if self.compare(self._key(value["first"]), self._key(value["last"])) > 0:
            raise ValueError("invalid ordered index interval")
        return value

    def read(self, ref: Ref) -> dict[str, Any]:
        self.validate_ref(ref)
        file = ref["file"]
        if file not in self._cache:
            path = self.directory / file
            self._reject_links(path)
            raw = read_material_bytes(path, _MAX_BYTES)
            if hashlib.sha256(raw).hexdigest() != file[:-5]:
                raise ValueError("ordered index node digest mismatch")
            value = decode_material(raw)
            if not isinstance(value, dict) or set(value) not in ({"entries"}, {"children"}):
                raise ValueError("invalid ordered index node shape")
            self._cache[file] = value
        node = self._cache[file]
        if "entries" in node:
            entries = node["entries"]
            if (
                ref["level"] != 0
                or not isinstance(entries, list)
                or not 1 <= len(entries) <= self.leaf_size
            ):
                raise ValueError("invalid ordered index leaf")
            previous = None
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or set(entry) != {"k", "v"}
                    or not isinstance(entry["v"], dict)
                ):
                    raise ValueError("invalid ordered index entry")
                key = self._key(entry["k"])
                if previous is not None and self.compare(previous, key) >= 0:
                    raise ValueError("unordered or duplicate index keys")
                previous = key
            count, first, last = len(entries), entries[0]["k"], entries[-1]["k"]
        else:
            children = node["children"]
            if (
                ref["level"] == 0
                or not isinstance(children, list)
                or not 1 <= len(children) <= _FANOUT
            ):
                raise ValueError("invalid ordered index branch")
            previous_last = None
            for child in children:
                self.validate_ref(child)
                if child["level"] != ref["level"] - 1:
                    raise ValueError("ordered index child height mismatch")
                if (
                    previous_last is not None
                    and self.compare(previous_last, self._key(child["first"])) >= 0
                ):
                    raise ValueError("overlapping ordered index intervals")
                previous_last = self._key(child["last"])
            count = sum(child["count"] for child in children)
            first, last = children[0]["first"], children[-1]["last"]
        if (count, first, last) != (ref["count"], ref["first"], ref["last"]):
            raise ValueError("ordered index reference does not match node")
        return deepcopy(node)

    def _write(self, node: dict[str, Any], level: int) -> Ref:
        if not 0 <= level <= _MAX_HEIGHT:
            raise ValueError("ordered index exceeded maximum height")
        raw = (
            json.dumps(node, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
        if len(raw) > _MAX_BYTES:
            raise ValueError("ordered index node exceeds bounded size")
        material = decode_material(raw)
        safe, changed = guard_value(material)
        if changed or safe != material:
            raise ValueError("unsafe index material cannot preserve its key identity")
        file = hashlib.sha256(raw).hexdigest() + ".json"
        path = self.directory / file
        self._reject_links(path)
        if not path.exists():
            atomic.write_json(path, material)
        elif read_material_bytes(path, _MAX_BYTES) != raw:
            raise ValueError("immutable ordered index node was modified")
        self._cache[file] = material
        if level == 0:
            entries = node["entries"]
            return dict(
                file=file, level=0, count=len(entries), first=entries[0]["k"], last=entries[-1]["k"]
            )
        children = node["children"]
        return dict(
            file=file,
            level=level,
            count=sum(child["count"] for child in children),
            first=children[0]["first"],
            last=children[-1]["last"],
        )

    def _leaves(self, entries: list[dict[str, Any]]) -> list[Ref]:
        return [
            self._write({"entries": entries[i : i + self.leaf_size]}, 0)
            for i in range(0, len(entries), self.leaf_size)
        ]

    def _branches(self, children: list[Ref], level: int) -> list[Ref]:
        return [
            self._write({"children": children[i : i + _FANOUT]}, level)
            for i in range(0, len(children), _FANOUT)
        ]

    def bulk_build(self, rows: list[tuple[Key, dict[str, Any]]]) -> None:
        def compare_rows(
            left: tuple[Key, dict[str, Any]], right: tuple[Key, dict[str, Any]]
        ) -> int:
            return self.compare(left[0], right[0])

        ordered = sorted(rows, key=cmp_to_key(compare_rows))
        entries: list[dict[str, Any]] = []
        for key, value in ordered:
            self._key(list(key))
            if entries and self.compare(tuple(entries[-1]["k"]), key) == 0:
                if entries[-1]["v"] != value:
                    raise ValueError("conflicting duplicate index key")
                continue
            entries.append({"k": list(key), "v": value})
        refs = self._leaves(entries)
        level = 1
        while len(refs) > 1:
            if level > _MAX_HEIGHT:
                raise ValueError("ordered index exceeded maximum height")
            refs = self._branches(refs, level)
            level += 1
        self.root = refs[0] if refs else None

    def get(self, key: Key) -> dict[str, Any] | None:
        ref = self.root
        while ref is not None:
            node = self.read(ref)
            if "entries" in node:
                for entry in node["entries"]:
                    comparison = self.compare(tuple(entry["k"]), key)
                    if comparison == 0:
                        value: dict[str, Any] = entry["v"]
                        return value
                    if comparison > 0:
                        break
                return None
            ref = next(
                (
                    child
                    for child in node["children"]
                    if self.compare(tuple(child["last"]), key) >= 0
                ),
                None,
            )
        return None

    def replace(self, rows: list[tuple[Key, dict[str, Any]]], evictions: list[Key]) -> None:
        def compare_entries(left: dict[str, Any], right: dict[str, Any]) -> int:
            return self.compare(tuple(left["k"]), tuple(right["k"]))

        def update(ref: Ref | None, key: Key, value: dict[str, Any] | None) -> list[Ref]:
            if ref is None:
                return [] if value is None else self._leaves([{"k": list(key), "v": value}])
            node = self.read(ref)
            if "entries" in node:
                entries = [
                    entry for entry in node["entries"] if self.compare(tuple(entry["k"]), key) != 0
                ]
                if value is not None:
                    old = next(
                        (
                            entry["v"]
                            for entry in node["entries"]
                            if self.compare(tuple(entry["k"]), key) == 0
                        ),
                        None,
                    )
                    if old is not None and old != value:
                        raise ValueError("conflicting duplicate index key")
                    entries.append({"k": list(key), "v": value})
                    entries.sort(key=cmp_to_key(compare_entries))
                return [ref] if entries == node["entries"] else self._leaves(entries)
            children = node["children"]
            position = next(
                (
                    i
                    for i, child in enumerate(children)
                    if self.compare(tuple(child["last"]), key) >= 0
                ),
                len(children) - 1,
            )
            changed = update(children[position], key, value)
            if changed == [children[position]]:
                return [ref]
            return self._branches(
                [*children[:position], *changed, *children[position + 1 :]], ref["level"]
            )

        for key, value in [*((key, None) for key in evictions), *rows]:
            self._key(list(key))
            refs = update(self.root, key, value)
            if len(refs) > 1:
                refs = self._branches(refs, refs[0]["level"] + 1)
            self.root = refs[0] if refs else None
            # Empty subtrees disappear; a unary root need not add a seek level.
            while self.root is not None and self.root["level"] > 0:
                children = self.read(self.root)["children"]
                if len(children) != 1:
                    break
                self.root = children[0]

    def scan(
        self,
        *,
        lower: Key,
        upper: Key,
        after: Key | None,
        descending: bool,
    ) -> Iterator[tuple[Key, dict[str, Any]]]:
        def walk(ref: Ref) -> Iterator[tuple[Key, dict[str, Any]]]:
            if (
                self.compare(tuple(ref["first"]), upper) > 0
                or self.compare(tuple(ref["last"]), lower) < 0
            ):
                return
            if after is not None and (
                (descending and self.compare(tuple(ref["first"]), after) >= 0)
                or (not descending and self.compare(tuple(ref["last"]), after) <= 0)
            ):
                return
            if ref["level"] == 0:
                entries = self.leaf_reader(ref) if self.leaf_reader else self.read(ref)["entries"]
                for entry in reversed(entries) if descending else entries:
                    key = tuple(entry["k"])
                    if self.compare(key, lower) < 0 or self.compare(key, upper) > 0:
                        continue
                    if after is not None and (
                        (descending and self.compare(key, after) >= 0)
                        or (not descending and self.compare(key, after) <= 0)
                    ):
                        continue
                    yield key, entry["v"]
            else:
                children = self.read(ref)["children"]
                for child in reversed(children) if descending else children:
                    yield from walk(child)

        if self.root is not None:
            yield from walk(self.root)
