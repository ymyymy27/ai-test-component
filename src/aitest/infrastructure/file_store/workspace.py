"""Workspace identity and admission."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import atomic
from .locking import writer_lock


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity_path = self.root / "workspace.json"
        if self.identity_path.exists():
            self.identity: dict[str, Any] = json.loads(
                self.identity_path.read_text(encoding="utf-8")
            )
        else:
            self.identity = {
                "workspace_id": str(uuid.uuid4()),
                "schema_version": "1.0",
                "writer_epoch": 0,
            }
            atomic.write_json(self.identity_path, self.identity)

    @property
    def workspace_id(self) -> str:
        return str(self.identity["workspace_id"])

    def validate(self, workspace_id: str) -> None:
        if workspace_id != self.workspace_id:
            raise ValueError("workspace identity mismatch")

    def path(self, *parts: str) -> Path:
        candidate = (self.root.joinpath(*parts)).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("path escapes workspace root")
        return candidate

    @contextmanager
    def acquire(self) -> Iterator[Workspace]:
        with writer_lock(self.root / "writer.lock"):
            self.identity["writer_epoch"] += 1
            atomic.write_json(self.identity_path, self.identity)
            yield self
