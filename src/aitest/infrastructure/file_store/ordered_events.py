"""Bounded event pages prepared before the shared commit pointer is switched."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from itertools import islice
from pathlib import Path
from typing import Any

from aitest.contracts.events import Event

from .commit_manifest import CommitMaterialError, FileCommitStore
from .events import EventReadResult
from .ordered_index import OrderedIndexTree

EVENT_ROOT_SCHEMA = "aitest.ordered-events/1"


def _compare(left: tuple[Any, ...], right: tuple[Any, ...]) -> int:
    return (left > right) - (left < right)


class OrderedEventStore:
    def __init__(self, root: Path) -> None:
        self.store = FileCommitStore(root)
        self.root = self.store.root

    def tree(self, header: dict[str, Any]) -> OrderedIndexTree:
        return OrderedIndexTree(
            self.root / "event-log/pages", _compare, leaf_size=128, root=header["root"]
        )

    @staticmethod
    def empty_root(*, commit_sequence: int = 0, generation: int = 1) -> dict[str, Any]:
        return dict(
            schema=EVENT_ROOT_SCHEMA,
            generation=generation,
            last_sequence=0,
            last_commit_sequence=commit_sequence,
            root=None,
        )

    def prepare(
        self,
        previous: dict[str, Any],
        events: Sequence[Event],
        *,
        commit_sequence: int,
        workspace_id: str,
    ) -> dict[str, Any]:
        """Copy affected paths only; no mutable journal or boundary is published."""
        if type(commit_sequence) is not int or commit_sequence < previous["last_commit_sequence"]:
            raise CommitMaterialError("event publication cannot move backwards")
        tree = self.tree(previous)
        last = previous["last_sequence"]
        if tree.root is not None:
            tree.read(tree.root)
        sequence = last
        previous_commit = previous["last_commit_sequence"]
        entries = []
        for event in events:
            event = Event.model_validate(event.model_dump())
            sequence += 1
            if (
                event.event_sequence != sequence
                or event.workspace_id != workspace_id
                or not previous_commit <= event.commit_sequence <= commit_sequence
            ):
                raise CommitMaterialError("prepared event does not belong to this boundary")
            previous_commit = event.commit_sequence
            entries.append(((sequence,), event.model_dump(mode="json")))
        if events and events[-1].commit_sequence != commit_sequence:
            raise CommitMaterialError("event material does not reach the prepared boundary")
        tree.replace(entries, [])
        return dict(
            schema=EVENT_ROOT_SCHEMA,
            generation=previous["generation"],
            last_sequence=sequence,
            last_commit_sequence=commit_sequence,
            root=tree.root,
        )

    @staticmethod
    def _cursor(manifest: dict[str, Any], sequence: int) -> str:
        body = dict(
            v=1,
            w=manifest["workspace_id"],
            g=manifest["generation_id"],
            e=manifest["event_root"]["generation"],
            s=sequence,
        )
        return base64.urlsafe_b64encode(
            json.dumps(body, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")

    @staticmethod
    def _decode_cursor(token: str, manifest: dict[str, Any]) -> int:
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 1024:
                raise ValueError
            body = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
            if (
                not isinstance(body, dict)
                or set(body) != {"v", "w", "g", "e", "s"}
                or type(body["v"]) is not int
                or body["v"] != 1
                or body["w"] != manifest["workspace_id"]
                or body["g"] != manifest["generation_id"]
                or type(body["e"]) is not int
                or body["e"] != manifest["event_root"]["generation"]
                or type(body["s"]) is not int
                or not 0 <= body["s"] <= manifest["event_root"]["last_sequence"]
            ):
                raise ValueError
            return int(body["s"])
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            raise ValueError("invalid event cursor") from error

    @staticmethod
    def _event(value: dict[str, Any], sequence: int, manifest: dict[str, Any]) -> Event:
        event = Event.model_validate(value)
        if (
            event.event_sequence != sequence
            or event.workspace_id != manifest["workspace_id"]
            or event.commit_sequence > manifest["commit_sequence"]
        ):
            raise CommitMaterialError("saved event identity does not match its root")
        return event

    def read(self, *, cursor: str | None, limit: int) -> EventReadResult:
        try:
            current = self.store.read_current()
            if current is None:
                raise CommitMaterialError("event root is not initialized")
            manifest = current["manifest"]
            try:
                after = 0 if cursor is None else self._decode_cursor(cursor, manifest)
            except ValueError:
                return EventReadResult(status="invalid_cursor")
            header = manifest["event_root"]
            if type(limit) is not int or not 1 <= limit <= 500:
                return EventReadResult(status="invalid_cursor")
            selected: list[Event] = []
            for key, value in islice(
                self.tree(header).scan(
                    lower=(after + 1,),
                    upper=(header["last_sequence"],),
                    after=None,
                    descending=False,
                ),
                limit,
            ):
                expected = after + len(selected) + 1
                if key != (expected,):
                    raise CommitMaterialError("saved event range is incomplete")
                selected.append(self._event(value, expected, manifest))
            if len(selected) != min(limit, header["last_sequence"] - after):
                raise CommitMaterialError("saved event page is incomplete")
            return EventReadResult(
                status="ok",
                events=tuple(selected),
                next_cursor=self._cursor(manifest, selected[-1].event_sequence)
                if selected
                else None,
            )
        except (OSError, ValueError, TypeError, KeyError):
            return EventReadResult(status="maintenance_required")

    def snapshot_cursor(self, *, commit_sequence: int) -> str | None:
        """Seek the last event in an exact boundary; work is bounded by tree height."""
        current = self.store.read_current()
        if current is None or type(commit_sequence) is not int or commit_sequence < 0:
            return None
        manifest = current["manifest"]
        if commit_sequence > manifest["commit_sequence"]:
            return None
        tree = self.tree(manifest["event_root"])
        low, high = 1, manifest["event_root"]["last_sequence"]
        found = None
        while low <= high:
            middle = (low + high) // 2
            value = tree.get((middle,))
            if value is None:
                raise CommitMaterialError("saved event position is unavailable")
            event = self._event(value, middle, manifest)
            if event.commit_sequence <= commit_sequence:
                if event.commit_sequence == commit_sequence:
                    found = middle
                low = middle + 1
            else:
                high = middle - 1
        if found is None:
            return self._cursor(manifest, 0) if commit_sequence == 0 else None
        return self._cursor(manifest, found)
