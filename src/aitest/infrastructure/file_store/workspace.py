"""Workspace identity and admission."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import atomic
from .locking import LifetimeWriterLock, writer_lock


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
            self._advance_epoch()
            atomic.write_json(self.identity_path, self.identity)
            yield self

    def _advance_epoch(self) -> None:
        """Read under the lock: another UOW may have advanced the saved epoch."""
        latest = json.loads(self.identity_path.read_text(encoding="utf-8"))
        if (
            not isinstance(latest, dict)
            or latest.get("workspace_id") != self.workspace_id
            or type(latest.get("writer_epoch")) is not int
            or latest["writer_epoch"] < 0
        ):
            raise ValueError("workspace writer identity cannot be verified")
        self.identity = latest
        self.identity["writer_epoch"] += 1

    def admit_lifetime(self) -> LifetimeWriterLock:
        """取得覆盖核心全生命周期的排他写锁并登记新 epoch（A-02）。

        恢复、服务与退出全程持有；调用方必须在核心关闭时释放。同根二次
        准入（同进程）或他进程已持锁时抛
        :class:`aitest.application.errors.WorkspaceInUse`。
        """
        lock = LifetimeWriterLock(self.root / "writer.lock")
        lock.acquire()
        try:
            self._advance_epoch()
            atomic.write_json(self.identity_path, self.identity)
        except BaseException:
            lock.release()
            raise
        return lock
