"""Workspace identity and admission."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import atomic
from .commit_manifest import FileCommitStore
from .locking import LifetimeWriterLock, admitted_writer_epoch, writer_lock


class Workspace:
    def __init__(self, root: Path) -> None:
        FileCommitStore.reject_links(root)
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity_path = self.root / "workspace.json"
        FileCommitStore.reject_links(self.identity_path)
        if self.identity_path.exists():
            self.identity: dict[str, Any] = self._load_identity()
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
    def acquire(self, *, expected_epoch: int | None = None) -> Iterator[Workspace]:
        with writer_lock(self.root / "writer.lock"):
            admitted = admitted_writer_epoch(self.root / "writer.lock")
            if expected_epoch is not None:
                if admitted != expected_epoch:
                    raise ValueError("writer epoch no longer owns core admission")
                self.validate_writer_epoch(expected_epoch)
            elif admitted is not None:
                self.validate_writer_epoch(admitted)
            else:
                self._advance_epoch()
                atomic.write_json(self.identity_path, self.identity)
            yield self

    def _read_identity(self) -> dict[str, Any]:
        latest = self._load_identity()
        if latest["workspace_id"] != self.workspace_id:
            raise ValueError("workspace writer identity cannot be verified")
        return latest

    def _load_identity(self) -> dict[str, Any]:
        """Read bounded, unique fields before admission can rewrite any bytes."""
        store = FileCommitStore(self.root)
        latest = store._decode(store._read_bytes(self.identity_path, 16384))
        identity, schema, epoch = (
            latest.get("workspace_id"), latest.get("schema_version"),
            latest.get("writer_epoch"),
        )
        if (
            not isinstance(identity, str) or not identity.strip() or len(identity) > 128
            or any(ord(char) < 32 for char in identity)
            or not isinstance(schema, str) or not schema.strip() or len(schema) > 128
            or type(epoch) is not int or epoch < 0
        ):
            raise ValueError("workspace writer identity cannot be verified")
        return latest

    def validate_writer_epoch(self, expected_epoch: int) -> None:
        """Recheck the durable identity while the caller holds its writer lock."""
        latest = self._read_identity()
        if type(expected_epoch) is not int or latest["writer_epoch"] != expected_epoch:
            raise ValueError("workspace writer epoch changed")
        admitted = admitted_writer_epoch(self.root / "writer.lock")
        if admitted is not None and admitted != expected_epoch:
            raise ValueError("writer epoch no longer owns core admission")
        self.identity = latest

    def _advance_epoch(self) -> None:
        """Only a new writer admission advances the durable epoch."""
        self.identity = self._read_identity()
        self.identity["writer_epoch"] += 1

    def admit_lifetime(self) -> LifetimeWriterLock:
        """取得覆盖核心全生命周期的排他写锁并登记新 epoch（A-02）。

        恢复、服务与退出全程持有；调用方必须在核心关闭时释放。同根二次
        准入（同进程）或他进程已持锁时抛
        :class:`aitest.application.errors.WorkspaceInUse`。
        """
        lock = LifetimeWriterLock(self.root / "writer.lock", identity_admission=True)
        lock.acquire()
        try:
            self._advance_epoch()
            atomic.write_json(self.identity_path, self.identity)
            lock.bind_epoch(self.identity["writer_epoch"])
        except BaseException:
            lock.release()
            raise
        return lock
