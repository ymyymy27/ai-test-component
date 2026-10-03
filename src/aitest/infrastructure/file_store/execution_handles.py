"""Durable command execution handles for cross-core reattachment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from aitest.domain.execution.runs import AdapterKind, ExecutionHandle

from . import atomic

_SCHEMA_VERSION = "aitest.execution-handle/1.0"


@dataclass(frozen=True, slots=True)
class PersistedExecutionHandle:
    attempt_id: str
    startup_token: str
    handle: ExecutionHandle

    def __post_init__(self) -> None:
        if not self.attempt_id.strip() or not self.startup_token.strip():
            raise ValueError("persisted execution handle requires attempt and startup identity")


class FileExecutionHandleStore:
    """Persist the adapter-owned handle identity, not a second scheduler."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()

    def save(self, record: PersistedExecutionHandle) -> Path:
        path = self._path(record.handle.handle_id)
        payload: object = {
            "schema_version": _SCHEMA_VERSION,
            "attempt_id": record.attempt_id,
            "startup_token": record.startup_token,
            "handle": {
                "handle_id": record.handle.handle_id,
                "adapter_kind": record.handle.adapter_kind.value,
                "adapter_version": record.handle.adapter_version,
                "real_execution_id": record.handle.real_execution_id,
                "process_start_identity": record.handle.process_start_identity,
                "workdir_ref": record.handle.workdir_ref,
            },
        }
        atomic.write_json(path, payload)  # type: ignore[arg-type]
        return path

    def load(self, handle_id: str) -> PersistedExecutionHandle:
        payload: object = json.loads(
            self._path(handle_id).read_text(encoding="utf-8")
        )
        if not isinstance(payload, dict):
            raise ValueError("execution handle payload must be an object")
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported execution handle schema")
        raw_handle = payload.get("handle")
        if not isinstance(raw_handle, dict):
            raise ValueError("execution handle payload requires handle")
        handle = ExecutionHandle(
            handle_id=_text(raw_handle.get("handle_id"), "handle_id"),
            adapter_kind=AdapterKind(_text(raw_handle.get("adapter_kind"), "adapter_kind")),
            adapter_version=_text(raw_handle.get("adapter_version"), "adapter_version"),
            real_execution_id=_text(raw_handle.get("real_execution_id"), "real_execution_id"),
            process_start_identity=_text(
                raw_handle.get("process_start_identity"),
                "process_start_identity",
            ),
            workdir_ref=_text(raw_handle.get("workdir_ref"), "workdir_ref"),
        )
        return PersistedExecutionHandle(
            attempt_id=_text(payload.get("attempt_id"), "attempt_id"),
            startup_token=_text(payload.get("startup_token"), "startup_token"),
            handle=handle,
        )

    def find_by_attempt(self, attempt_id: str) -> PersistedExecutionHandle | None:
        directory = self._root / "execution-handles"
        if not directory.exists():
            return None
        for path in sorted(directory.glob("*.json")):
            payload: object = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            if payload.get("attempt_id") == attempt_id:
                return self.load(_handle_id(payload))
        return None

    def _path(self, handle_id: str) -> Path:
        digest = hashlib.sha256(handle_id.encode("utf-8")).hexdigest()
        return self._root / "execution-handles" / f"{digest}.json"


def _handle_id(payload: dict[str, object]) -> str:
    raw_handle = payload.get("handle")
    if not isinstance(raw_handle, dict):
        raise ValueError("execution handle payload requires handle")
    return _text(raw_handle.get("handle_id"), "handle_id")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


__all__ = ["FileExecutionHandleStore", "PersistedExecutionHandle"]
