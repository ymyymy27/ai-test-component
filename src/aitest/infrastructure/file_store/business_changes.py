"""Sparse, immutable business changes published only through CommitManifest."""

from __future__ import annotations

import hashlib
import heapq
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from .ordered_index import OrderedIndexTree

SCHEMA = "aitest.business-change-index/1"
# Storage classification is independent of a platform's upload whitelist.
BUSINESS_TYPES = frozenset(
    {
        "project",
        "binding",
        "module",
        "dependency_set",
        "task",
        "delivery",
        "acceptance_item",
        "environment",
        "source_snapshot",
        "source_pin_intent",
        "source_binding_current",
        "template_ref",
        "generated_content",
        "rule_draft",
        "rule_version",
        "case",
        "case_link",
        "plan",
        "acceptance_scope",
        "preparation_record",
        "prepared_run",
        "model_outbound_policy",
        "model_outbound_request",
        "run",
        "step",
        "attempt",
        "execution_authorization",
        "execution_intent",
        "execution_checkpoint",
        "execution_checkpoint_refs",
        "execution_facts",
        "execution_facts_current",
        "evidence",
        "evidence_ref",
        "run_plan_revision",
        "step_revision_ref",
        "report",
        "report_revision",
        "issue",
        "issue_review",
        "review",
        "manual_evidence",
        "reuse_decision",
    }
)
ADMINISTRATIVE_TYPES = frozenset(
    {
        "diagnostic",
        "submission",
        "submission_control",
        "submission_delivery_grant",
        "submission_receipt",
        "upload_candidate",
        "candidate_cursor",
        "upload_receipt",
    }
)


def compare(left: tuple[Any, ...], right: tuple[Any, ...]) -> int:
    return (left > right) - (left < right)


class BusinessChangeIndex:
    def __init__(self, root: Path, header: dict[str, Any] | None) -> None:
        self.directory = root / "indexes/business"
        if header is None:
            raise ValueError("INDEX_REBUILD_REQUIRED: business change index is absent")
        if set(header) != {"schema", "types"} or header["schema"] != SCHEMA:
            raise ValueError("unsupported business change index")
        if not isinstance(header["types"], dict) or len(header["types"]) > 128:
            raise ValueError("invalid business change type directory")
        self.header = header
        for kind, meta in header["types"].items():
            if not isinstance(kind, str) or not 1 <= len(kind) <= 128:
                raise ValueError("invalid business change type")
            if (
                kind in ADMINISTRATIVE_TYPES
                or not isinstance(meta, dict)
                or set(meta) != {"root", "last_business_commit", "classification"}
                or meta["classification"] != self.classification(kind)
                or type(meta["last_business_commit"]) is not int
                or meta["last_business_commit"] < 1
            ):
                raise ValueError("invalid business change type metadata")
            tree = self.tree(kind)
            if tree.root is None or tree.root["last"][0] != meta["last_business_commit"]:
                raise ValueError("business high water does not match its root")
            self._key(tree.root["first"])
            self._key(tree.root["last"])

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"schema": SCHEMA, "types": {}}

    @staticmethod
    def classification(kind: str) -> str:
        # Unknown component records are conservatively tracked, never silently
        # dropped and never thereby admitted to a platform upload whitelist.
        return "business" if kind in BUSINESS_TYPES else "unclassified"

    def tree(self, kind: str) -> OrderedIndexTree:
        directory = self.directory / hashlib.sha256(kind.encode("utf-8")).hexdigest()
        meta = self.header["types"].get(kind)
        return OrderedIndexTree(
            directory, compare, leaf_size=128, root=meta["root"] if meta else None
        )

    @staticmethod
    def _key(key: object) -> tuple[Any, ...]:
        if (
            not isinstance(key, (tuple, list))
            or len(key) != 5
            or type(key[0]) is not int
            or key[0] < 1
            or type(key[4]) is not int
            or key[4] < 1
            or any(not isinstance(part, str) or not part for part in key[1:4])
        ):
            raise ValueError("invalid business change key")
        return tuple(key)

    def prepare(
        self,
        refs: Sequence[dict[str, Any]],
        *,
        commit_sequence: int,
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        changed = {ref["aggregate_kind"] for ref in refs} - ADMINISTRATIVE_TYPES
        if not changed:
            return self.header
        updated = {**self.header["types"]}
        for kind in sorted(changed):
            tree = self.tree(kind)
            rows = []
            for ref in refs:
                if ref["aggregate_kind"] != kind:
                    continue
                key = self._key(
                    (commit_sequence, workspace_id, project_id, ref["record_id"], ref["revision"])
                )
                rows.append(
                    (
                        key,
                        {
                            **ref,
                            "commit_sequence": commit_sequence,
                            "origin_workspace_id": workspace_id,
                            "project_id": project_id,
                        },
                    )
                )
            tree.replace(rows, [])
            updated[kind] = {
                "root": tree.root,
                "last_business_commit": commit_sequence,
                "classification": self.classification(kind),
            }
        header = {"schema": SCHEMA, "types": updated}
        BusinessChangeIndex(self.directory.parent.parent, header)
        return header

    def verify(self, *, high_water: int) -> None:
        for kind, meta in self.header["types"].items():
            if meta["last_business_commit"] > high_water:
                raise ValueError("business change index extends beyond committed high water")
            tree = self.tree(kind)
            assert tree.root is not None
            tree.read(tree.root)

    def verify_changes(
        self,
        refs: Sequence[dict[str, Any]],
        *,
        commit_sequence: int,
        workspace_id: str,
        project_id: str,
    ) -> None:
        for ref in refs:
            kind = ref["aggregate_kind"]
            if kind in ADMINISTRATIVE_TYPES:
                continue
            key = (commit_sequence, workspace_id, project_id, ref["record_id"], ref["revision"])
            expected = {
                **ref,
                "commit_sequence": commit_sequence,
                "origin_workspace_id": workspace_id,
                "project_id": project_id,
            }
            if self.tree(kind).get(key) != expected:
                raise ValueError("changed business record lacks its exact sparse index entry")

    def verify_extension(
        self,
        previous: BusinessChangeIndex,
        refs: Sequence[dict[str, Any]],
    ) -> None:
        changed = {r["aggregate_kind"] for r in refs} - ADMINISTRATIVE_TYPES
        old_types, new_types = previous.header["types"], self.header["types"]
        if set(new_types) != set(old_types) | changed:
            raise ValueError("business index cannot remove or invent tracked types")
        for kind in new_types:
            if kind not in changed:
                if new_types[kind] != old_types[kind]:
                    raise ValueError("unrelated business index root cannot change")
                continue
            old, new = previous.tree(kind), self.tree(kind)
            old_count = old.root["count"] if old.root else 0
            count = sum(r["aggregate_kind"] == kind for r in refs)
            if new.root is None or new.root["count"] != old_count + count:
                raise ValueError("business index cannot omit a batch or its history")
            if old.root is not None:
                self._verify_preserved_prefix(old, new, old.root)

    @staticmethod
    def _verify_preserved_prefix(
        old: OrderedIndexTree,
        new: OrderedIndexTree,
        ref: dict[str, Any],
    ) -> None:
        # Identical immutable subtrees prove whole historical ranges at once.
        # Only the changed boundary path/leaf needs individual comparison.
        candidate = new.root
        while candidate is not None:
            if candidate == ref:
                return
            node = new.read(candidate)
            if "entries" in node:
                break
            candidate = next(
                (
                    child
                    for child in node["children"]
                    if compare(tuple(child["first"]), tuple(ref["first"])) <= 0
                    and compare(tuple(child["last"]), tuple(ref["last"])) >= 0
                ),
                None,
            )
        original = old.read(ref)
        if "entries" in original:
            for entry in original["entries"]:
                if new.get(tuple(entry["k"])) != entry["v"]:
                    raise ValueError("business index changed an immutable historical entry")
        else:
            for child in original["children"]:
                BusinessChangeIndex._verify_preserved_prefix(old, new, child)

    def read(
        self,
        *,
        types: Sequence[str],
        high_water: int,
        limit: int = 100,
        after: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Merge selected frozen trees; unrelated types and manifest tails are unread."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 500
            or not 1 <= len(types) <= 16
            or len(set(types)) != len(types)
            or type(high_water) is not int
            or high_water < 0
        ):
            raise ValueError("invalid sparse business query")
        if after is not None:
            if len(after) != 6 or not isinstance(after[-1], str):
                raise ValueError("invalid business query continuation")
            self._key(after[:5])
        streams: list[Iterator[tuple[tuple[Any, ...], dict[str, Any]]]] = []
        for kind in types:
            if kind not in BUSINESS_TYPES:
                raise ValueError("unsupported or unclassified business query type")
            meta = self.header["types"].get(kind)
            if meta is None or meta["last_business_commit"] <= (after[0] - 1 if after else 0):
                continue
            stream = self.tree(kind).scan(
                lower=(1, "", "", "", 1),
                upper=(high_water, "\U0010ffff", "\U0010ffff", "\U0010ffff", 2**63 - 1),
                after=None if after is None else after[:5],
                descending=False,
            )
            # Seek is strictly after a five-part key. Include that key if the
            # continuation's type sorts before this type (cross-type tie).
            if after is not None and kind > after[-1]:
                equal = self.tree(kind).get(after[:5])
                if equal is not None and after[0] <= high_water:
                    from itertools import chain

                    stream = chain([(after[:5], equal)], stream)
            streams.append(self._with_type(stream, kind))
        rows = []
        for key, row in heapq.merge(*streams, key=lambda item: item[0]):
            expected = (
                row.get("commit_sequence"),
                row.get("origin_workspace_id"),
                row.get("project_id"),
                row.get("record_id"),
                row.get("revision"),
                row.get("aggregate_kind"),
            )
            if key != expected or (after is not None and key <= after):
                raise ValueError("sparse business entry does not match its key")
            rows.append(row)
            if len(rows) == limit:
                break
        return rows

    @staticmethod
    def _with_type(
        stream: Iterator[tuple[tuple[Any, ...], dict[str, Any]]],
        kind: str,
    ) -> Iterator[tuple[tuple[Any, ...], dict[str, Any]]]:
        for key, row in stream:
            yield key + (kind,), row
