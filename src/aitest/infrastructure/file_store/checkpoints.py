"""File-backed RecoveryCheckpoint records."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from aitest.domain.execution.runs import Attempt, RecoveryCheckpoint, RecoveryRecord

from . import atomic

_SCHEMA_VERSION = "aitest.recovery-checkpoint/1.0"
_ATTEMPT_ADAPTER = TypeAdapter(Attempt)
_CHECKPOINT_ADAPTER = TypeAdapter(RecoveryCheckpoint)


def _safe_component(value: str, name: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"{name} must be a safe path component")
    if any(char in value for char in '\\/:*?"<>|'):
        raise ValueError(f"{name} must be a safe path component")
    return value


class FileCheckpointStore:
    """Persist one current checkpoint record per Attempt."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()

    def persist(self, record: RecoveryRecord) -> Path:
        attempt_id = _safe_component(record.attempt.attempt_id, "attempt_id")
        if record.checkpoint.attempt_id != attempt_id:
            raise ValueError("checkpoint and attempt identity must match")
        path = self._root / "checkpoints" / f"{attempt_id}.json"
        payload: object = self.to_payload(record)
        atomic.write_json(path, payload)  # type: ignore[arg-type]
        return path

    @staticmethod
    def to_payload(record: RecoveryRecord) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "checkpoint": _CHECKPOINT_ADAPTER.dump_python(
                record.checkpoint,
                mode="json",
            ),
            "attempt": _ATTEMPT_ADAPTER.dump_python(record.attempt, mode="json"),
        }

    def load(self, attempt_id: str) -> RecoveryRecord:
        safe_attempt = _safe_component(attempt_id, "attempt_id")
        payload: object = json.loads(
            (self._root / "checkpoints" / f"{safe_attempt}.json").read_text(encoding="utf-8")
        )
        return self._from_payload(payload)

    def scan(self) -> tuple[RecoveryRecord, ...]:
        directory = self._root / "checkpoints"
        if not directory.exists():
            return ()
        records: list[RecoveryRecord] = []
        for path in sorted(directory.glob("*.json")):
            payload: object = json.loads(path.read_text(encoding="utf-8"))
            records.append(self._from_payload(payload))
        return tuple(records)

    @staticmethod
    def _from_payload(payload: object) -> RecoveryRecord:
        if not isinstance(payload, dict):
            raise ValueError("checkpoint payload must be an object")
        if payload.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("unsupported checkpoint schema")
        checkpoint = _CHECKPOINT_ADAPTER.validate_python(payload.get("checkpoint"))
        attempt = _ATTEMPT_ADAPTER.validate_python(payload.get("attempt"))
        return RecoveryRecord(checkpoint=checkpoint, attempt=attempt)

    @classmethod
    def from_payload(cls, payload: object) -> RecoveryRecord:
        return cls._from_payload(payload)


__all__ = ["FileCheckpointStore"]
