"""Durable command execution handles for cross-core reattachment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from aitest.domain.execution.runs import (
    AdapterKind,
    ExecutionCollectionResult,
    ExecutionHandle,
    ExecutionInspectionState,
    StopRequestResult,
)

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
        if path.exists():
            if self.load(record.handle.handle_id) != record:
                raise ValueError("execution handle identity is immutable")
            return path
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

    def save_collection(self, handle: ExecutionHandle, result: ExecutionCollectionResult) -> None:
        result = _strict_result(
            TypeAdapter(ExecutionCollectionResult),
            json.loads(json.dumps(asdict(result), default=_json_scalar, allow_nan=False)),
        )
        original = self.load(handle.handle_id)
        if original.handle != handle or result.attempt_id != original.attempt_id:
            raise ValueError("execution collection identity mismatch")
        if result.complete is not True or result.exit_fact_ref is None:
            raise ValueError("only a confirmed exit collection may be frozen")
        if (
            result.exit_fact_ref.startup_token != original.startup_token
            or result.exit_fact_ref.process_start_identity != handle.process_start_identity
        ):
            raise ValueError("execution exit identity mismatch")
        previous = self.load_collection(handle)
        if previous is not None:
            if previous != result:
                raise ValueError("confirmed execution collection is immutable")
            return
        atomic.write_json(
            self._result_path(handle.handle_id),
            {
                "schema_version": "aitest.execution-collection/1.0",
                "startup_token": original.startup_token,
                "result": TypeAdapter(ExecutionCollectionResult).dump_python(result, mode="json"),
            },
        )

    def load_collection(self, handle: ExecutionHandle) -> ExecutionCollectionResult | None:
        path = self._result_path(handle.handle_id)
        if not path.exists():
            return None
        record = self.load(handle.handle_id)
        raw = _read_json(path)
        if (
            set(raw) != {"schema_version", "startup_token", "result"}
            or raw.get("schema_version") != "aitest.execution-collection/1.0"
            or raw.get("startup_token") != record.startup_token
            or record.handle != handle
        ):
            raise ValueError("confirmed execution collection identity mismatch")
        result = _strict_result(TypeAdapter(ExecutionCollectionResult), raw["result"])
        if (
            result.attempt_id != record.attempt_id
            or result.complete is not True
            or result.exit_fact_ref is None
            or result.exit_fact_ref.startup_token != record.startup_token
            or result.exit_fact_ref.process_start_identity != handle.process_start_identity
        ):
            raise ValueError("confirmed execution exit identity mismatch")
        return result

    def _result_path(self, handle_id: str) -> Path:
        path = self._path(handle_id)
        return path.with_name(path.stem + "-result.json")

    def save_stop(self, handle: ExecutionHandle, result: StopRequestResult) -> None:
        result = _strict_result(
            TypeAdapter(StopRequestResult),
            json.loads(json.dumps(asdict(result), default=_json_scalar, allow_nan=False)),
        )
        record = self.load(handle.handle_id)
        if (
            record.handle != handle
            or result.handle_id != handle.handle_id
            or result.stop_confirmed is not True
            or result.observed_state is not ExecutionInspectionState.STOPPED
        ):
            raise ValueError("only an owned confirmed group stop can be frozen")
        previous = self.load_stop(handle)
        if previous is not None:
            if previous != result:
                raise ValueError("confirmed stop fact is immutable")
            return
        atomic.write_json(
            self._stop_path(handle.handle_id),
            {
                "schema_version": "aitest.execution-stop/1.0",
                "startup_token": record.startup_token,
                "result": TypeAdapter(StopRequestResult).dump_python(result, mode="json"),
            },
        )

    def load_stop(self, handle: ExecutionHandle) -> StopRequestResult | None:
        path = self._stop_path(handle.handle_id)
        if not path.exists():
            return None
        record = self.load(handle.handle_id)
        raw = _read_json(path)
        if (
            set(raw) != {"schema_version", "startup_token", "result"}
            or record.handle != handle
            or raw.get("schema_version") != "aitest.execution-stop/1.0"
            or raw.get("startup_token") != record.startup_token
        ):
            raise ValueError("confirmed stop identity mismatch")
        result = _strict_result(TypeAdapter(StopRequestResult), raw["result"])
        if (
            result.handle_id != handle.handle_id
            or result.stop_confirmed is not True
            or result.observed_state is not ExecutionInspectionState.STOPPED
        ):
            raise ValueError("confirmed stop fact is invalid")
        return result

    def _stop_path(self, handle_id: str) -> Path:
        path = self._path(handle_id)
        return path.with_name(path.stem + "-stop.json")

    def load(self, handle_id: str) -> PersistedExecutionHandle:
        payload: object = _read_json(self._path(handle_id))
        if not isinstance(payload, dict):
            raise ValueError("execution handle payload must be an object")
        if set(payload) != {"schema_version", "attempt_id", "startup_token", "handle"} or (
            payload.get("schema_version") != _SCHEMA_VERSION
        ):
            raise ValueError("unsupported execution handle schema")
        raw_handle = payload.get("handle")
        if not isinstance(raw_handle, dict):
            raise ValueError("execution handle payload requires handle")
        if set(raw_handle) != {
            "handle_id",
            "adapter_kind",
            "adapter_version",
            "real_execution_id",
            "process_start_identity",
            "workdir_ref",
        }:
            raise ValueError("execution handle has unknown or missing fields")
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
        if handle.handle_id != handle_id:
            raise ValueError("execution handle file identity mismatch")
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
            if path.stem.endswith(("-result", "-stop")):
                continue
            payload: object = _read_json(path)
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


def _strict_result[T](adapter: TypeAdapter[T], raw: object) -> T:
    result = adapter.validate_json(json.dumps(raw, allow_nan=False), strict=True)
    if adapter.dump_python(result, mode="json") != raw:
        raise ValueError("execution receipt has unknown or noncanonical fields")
    return result


def _json_scalar(value: object) -> object:
    if isinstance(value, datetime):
        return TypeAdapter(datetime).dump_python(value, mode="json")
    if isinstance(value, bytes):
        return TypeAdapter(bytes).dump_python(value, mode="json")
    raise TypeError("execution receipt contains a non-JSON value")


def _read_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("execution receipt has duplicate fields")
            value[key] = item
        return value

    def nonfinite(value: str) -> object:
        raise ValueError("execution receipt has a nonfinite value")

    raw = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=nonfinite
    )
    if not isinstance(raw, dict):
        raise ValueError("execution receipt must be an object")
    return raw


__all__ = ["FileExecutionHandleStore", "PersistedExecutionHandle"]
