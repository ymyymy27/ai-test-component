"""Persist verified command output streams in the workspace spool."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Sequence
from pathlib import Path

from aitest.domain.execution.runs import (
    CapturedOutputBlock,
    OutputBlockRef,
    OutputCursor,
    OutputStreamName,
    SpoolManifest,
)

from . import atomic

_INVALID_COMPONENT_CHARS = frozenset('\\/:*?"<>|')


def _safe_component(value: str, name: str) -> str:
    if not value or value.strip() != value or value in {".", ".."}:
        raise ValueError(f"{name} is not a safe path component")
    if Path(value).name != value or any(char in _INVALID_COMPONENT_CHARS for char in value):
        raise ValueError(f"{name} is not a safe path component")
    return value


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _require_optional_str(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, name)


class _FileSpoolStreamWriter:
    def __init__(
        self,
        store: FileSpoolStore,
        *,
        run_id: str,
        step_id: str,
        attempt_id: str,
        stream_name: OutputStreamName,
        capture_source: str,
        block_size: int,
        redaction_summary_id: str | None,
    ) -> None:
        if block_size < 1:
            raise ValueError("block_size must be positive")
        self._store = store
        self._run_id = run_id
        self._step_id = step_id
        self._attempt_id = attempt_id
        self._stream_name = stream_name
        self._capture_source = capture_source
        self._block_size = block_size
        self._redaction_summary_id = redaction_summary_id
        self._lock = threading.Lock()
        self._closed = False
        self._path = store._stream_path(attempt_id, stream_name)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("ab")
        self._offset = self._path.stat().st_size
        self._block_start = self._offset
        self._block_length = 0
        self._hasher = hashlib.sha256()
        self._block_index = store._next_block_index(attempt_id, stream_name)

    def append(self, content: bytes) -> tuple[OutputBlockRef, ...]:
        if not content:
            return ()
        with self._lock:
            if self._closed:
                raise RuntimeError("spool stream writer is closed")
            self._handle.write(content)
            self._handle.flush()
            self._hasher.update(content)
            self._block_length += len(content)
            if self._block_length < self._block_size:
                return ()
            return (self._seal(complete=True),)

    def close(self, *, complete: bool = True) -> tuple[OutputBlockRef, ...]:
        with self._lock:
            if self._closed:
                return ()
            self._closed = True
            refs: tuple[OutputBlockRef, ...] = ()
            try:
                self._handle.flush()
                os.fsync(self._handle.fileno())
                if self._block_length:
                    refs = (self._seal(complete=complete),)
            finally:
                self._handle.close()
                self._store._unregister_writer(self._attempt_id, self._stream_name)
            return refs

    def _seal(self, *, complete: bool) -> OutputBlockRef:
        digest = "sha256:" + self._hasher.hexdigest()
        ref = OutputBlockRef(
            block_id=f"{self._attempt_id}:{self._stream_name.value}:{self._block_index}",
            attempt_id=self._attempt_id,
            stream_name=self._stream_name,
            block_index=self._block_index,
            offset=self._block_start,
            length=self._block_length,
            digest=digest,
            complete=complete,
            capture_source=self._capture_source,
            redaction_summary_id=self._redaction_summary_id,
        )
        cursor = OutputCursor(
            attempt_id=self._attempt_id,
            stream_name=self._stream_name,
            offset=self._block_start + self._block_length,
            last_block_index=self._block_index,
            last_committed_digest=digest,
            durable=True,
        )
        self._store._merge_manifest(
            attempt_id=self._attempt_id,
            run_id=self._run_id,
            step_id=self._step_id,
            new_blocks=(ref,),
            new_cursors=(cursor,),
        )
        self._offset += self._block_length
        self._block_start = self._offset
        self._block_length = 0
        self._hasher = hashlib.sha256()
        self._block_index += 1
        return ref


class FileSpoolStore:
    """File-backed streaming spool store for one workspace."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()
        self._manifest_lock = threading.RLock()
        self._writer_lock = threading.Lock()
        self._open_writers: set[tuple[str, OutputStreamName]] = set()

    def open_stream(
        self,
        *,
        run_id: str,
        step_id: str,
        attempt_id: str,
        stream_name: OutputStreamName,
        capture_source: str = "command",
        block_size: int = 64 * 1024,
        redaction_summary_id: str | None = None,
    ) -> _FileSpoolStreamWriter:
        safe_run = _safe_component(run_id, "run_id")
        safe_step = _safe_component(step_id, "step_id")
        safe_attempt = _safe_component(attempt_id, "attempt_id")
        key = (safe_attempt, stream_name)
        with self._writer_lock:
            if key in self._open_writers:
                raise ValueError("spool stream already open")
            self._ensure_manifest(safe_attempt, safe_run, safe_step)
            writer = _FileSpoolStreamWriter(
                self,
                run_id=safe_run,
                step_id=safe_step,
                attempt_id=safe_attempt,
                stream_name=stream_name,
                capture_source=capture_source,
                block_size=block_size,
                redaction_summary_id=redaction_summary_id,
            )
            self._open_writers.add(key)
            return writer

    def persist_blocks(self, blocks: Sequence[CapturedOutputBlock]) -> SpoolManifest:
        batch = tuple(blocks)
        if not batch:
            raise ValueError("at least one block is required")
        first = batch[0]
        attempt_id = _safe_component(first.attempt_id, "attempt_id")
        run_id = _safe_component(first.run_id, "run_id")
        step_id = _safe_component(first.step_id, "step_id")
        refs: list[OutputBlockRef] = []
        cursors: list[OutputCursor] = []
        for block in batch:
            if not block.complete:
                raise ValueError("only sealed blocks may be persisted")
            if (block.attempt_id, block.run_id, block.step_id) != (attempt_id, run_id, step_id):
                raise ValueError("all spool blocks must share run_id, step_id and attempt_id")
            ref = OutputBlockRef(
                block_id=f"{attempt_id}:{block.stream_name.value}:{block.block_index}",
                attempt_id=attempt_id,
                stream_name=block.stream_name,
                block_index=block.block_index,
                offset=block.offset,
                length=block.length,
                digest=block.digest,
                complete=block.complete,
                capture_source=block.capture_source,
                redaction_summary_id=block.redaction_summary_id,
            )
            self._write_block(ref, block.content)
            refs.append(ref)
            cursors.append(
                OutputCursor(
                    attempt_id=attempt_id,
                    stream_name=block.stream_name,
                    offset=block.offset + block.length,
                    last_block_index=block.block_index,
                    last_committed_digest=block.digest,
                    durable=True,
                )
            )
        self._merge_manifest(
            attempt_id=attempt_id,
            run_id=run_id,
            step_id=step_id,
            new_blocks=tuple(refs),
            new_cursors=tuple(cursors),
        )
        return self.read_manifest(attempt_id)

    def read_manifest(self, attempt_id: str) -> SpoolManifest:
        safe_attempt = _safe_component(attempt_id, "attempt_id")
        with self._manifest_lock:
            path = self._manifest_path(safe_attempt)
            raw: object = json.loads(path.read_text(encoding="utf-8"))
            return self._manifest_from_json(raw)

    def read_block(self, ref: OutputBlockRef) -> bytes:
        safe_attempt = _safe_component(ref.attempt_id, "attempt_id")
        path = self._stream_path(safe_attempt, ref.stream_name)
        with path.open("rb") as handle:
            handle.seek(ref.offset)
            content = handle.read(ref.length)
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if len(content) != ref.length or digest != ref.digest:
            raise ValueError("spool block content failed verification")
        return content

    def _write_block(self, ref: OutputBlockRef, content: bytes) -> None:
        path = self._stream_path(ref.attempt_id, ref.stream_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing_size = path.stat().st_size
            if ref.offset < existing_size:
                with path.open("rb") as handle:
                    handle.seek(ref.offset)
                    existing = handle.read(ref.length)
                if len(existing) == ref.length and existing == content:
                    return
                raise ValueError("stream block conflicts with existing bytes")
            if ref.offset != existing_size:
                raise ValueError("stream block offset must be contiguous")
        with path.open("ab") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    def _ensure_manifest(self, attempt_id: str, run_id: str, step_id: str) -> None:
        with self._manifest_lock:
            path = self._manifest_path(attempt_id)
            if path.exists():
                existing = self._manifest_from_json(json.loads(path.read_text(encoding="utf-8")))
                if (existing.run_id, existing.step_id) != (run_id, step_id):
                    raise ValueError("spool manifest identity does not match")
                return
            manifest = SpoolManifest(attempt_id=attempt_id, run_id=run_id, step_id=step_id)
            atomic.write_json(path, self._manifest_to_json(manifest))

    def _merge_manifest(
        self,
        *,
        attempt_id: str,
        run_id: str,
        step_id: str,
        new_blocks: tuple[OutputBlockRef, ...],
        new_cursors: tuple[OutputCursor, ...],
    ) -> None:
        with self._manifest_lock:
            path = self._manifest_path(attempt_id)
            if path.exists():
                manifest = self._manifest_from_json(json.loads(path.read_text(encoding="utf-8")))
                if (manifest.run_id, manifest.step_id) != (run_id, step_id):
                    raise ValueError("spool manifest identity does not match")
            else:
                manifest = SpoolManifest(attempt_id=attempt_id, run_id=run_id, step_id=step_id)
            blocks = {(block.stream_name, block.block_index): block for block in manifest.blocks}
            for block in new_blocks:
                previous = blocks.get((block.stream_name, block.block_index))
                if previous is not None and previous != block:
                    raise ValueError("spool block conflicts with existing metadata")
                blocks[(block.stream_name, block.block_index)] = block
            cursors = {cursor.stream_name: cursor for cursor in manifest.cursors}
            for cursor in new_cursors:
                cursors[cursor.stream_name] = cursor
            ordered_blocks = tuple(
                blocks[key] for key in sorted(blocks, key=lambda item: (item[0].value, item[1]))
            )
            ordered_cursors = tuple(
                cursors[key] for key in sorted(cursors, key=lambda stream: stream.value)
            )
            updated = SpoolManifest(
                attempt_id=attempt_id,
                run_id=run_id,
                step_id=step_id,
                blocks=ordered_blocks,
                cursors=ordered_cursors,
            )
            atomic.write_json(path, self._manifest_to_json(updated))

    def _next_block_index(self, attempt_id: str, stream_name: OutputStreamName) -> int:
        try:
            manifest = self.read_manifest(attempt_id)
        except FileNotFoundError:
            return 0
        indexes = [
            block.block_index for block in manifest.blocks if block.stream_name is stream_name
        ]
        return max(indexes, default=-1) + 1

    def _unregister_writer(self, attempt_id: str, stream_name: OutputStreamName) -> None:
        with self._writer_lock:
            self._open_writers.discard((attempt_id, stream_name))

    def _manifest_to_json(self, manifest: SpoolManifest) -> dict[str, object]:
        return {
            "schema_version": manifest.schema_version,
            "attempt_id": manifest.attempt_id,
            "run_id": manifest.run_id,
            "step_id": manifest.step_id,
            "blocks": [self._block_to_json(block) for block in manifest.blocks],
            "cursors": [self._cursor_to_json(cursor) for cursor in manifest.cursors],
        }

    def _manifest_from_json(self, raw: object) -> SpoolManifest:
        if not isinstance(raw, dict):
            raise ValueError("spool manifest must be a JSON object")
        schema_version = _require_str(raw.get("schema_version"), "schema_version")
        if schema_version != "aitest.spool/1.0":
            raise ValueError(f"unsupported spool schema: {schema_version}")
        attempt_id = _safe_component(
            _require_str(raw.get("attempt_id"), "attempt_id"),
            "attempt_id",
        )
        run_id = _safe_component(_require_str(raw.get("run_id"), "run_id"), "run_id")
        step_id = _safe_component(_require_str(raw.get("step_id"), "step_id"), "step_id")
        blocks_raw = raw.get("blocks")
        cursors_raw = raw.get("cursors", [])
        if not isinstance(blocks_raw, list) or not isinstance(cursors_raw, list):
            raise ValueError("spool blocks and cursors must be lists")
        blocks = tuple(self._block_from_json(entry, attempt_id) for entry in blocks_raw)
        cursors = tuple(self._cursor_from_json(entry, attempt_id) for entry in cursors_raw)
        return SpoolManifest(
            schema_version=schema_version,
            attempt_id=attempt_id,
            run_id=run_id,
            step_id=step_id,
            blocks=blocks,
            cursors=cursors,
        )

    @staticmethod
    def _block_to_json(block: OutputBlockRef) -> dict[str, object]:
        return {
            "block_id": block.block_id,
            "attempt_id": block.attempt_id,
            "stream_name": block.stream_name.value,
            "block_index": block.block_index,
            "offset": block.offset,
            "length": block.length,
            "digest": block.digest,
            "complete": block.complete,
            "capture_source": block.capture_source,
            "redaction_summary_id": block.redaction_summary_id,
        }

    @staticmethod
    def _cursor_to_json(cursor: OutputCursor) -> dict[str, object]:
        return {
            "attempt_id": cursor.attempt_id,
            "stream_name": cursor.stream_name.value,
            "offset": cursor.offset,
            "last_block_index": cursor.last_block_index,
            "last_committed_digest": cursor.last_committed_digest,
            "durable": cursor.durable,
        }

    @staticmethod
    def _block_from_json(raw: object, manifest_attempt_id: str) -> OutputBlockRef:
        if not isinstance(raw, dict):
            raise ValueError("spool block must be a JSON object")
        attempt_id = _safe_component(
            _require_str(raw.get("attempt_id"), "attempt_id"),
            "attempt_id",
        )
        if attempt_id != manifest_attempt_id:
            raise ValueError("spool block attempt_id must match manifest")
        return OutputBlockRef(
            block_id=_require_str(raw.get("block_id"), "block_id"),
            attempt_id=attempt_id,
            stream_name=OutputStreamName(_require_str(raw.get("stream_name"), "stream_name")),
            block_index=_require_int(raw.get("block_index"), "block_index"),
            offset=_require_int(raw.get("offset"), "offset"),
            length=_require_int(raw.get("length"), "length"),
            digest=_require_str(raw.get("digest"), "digest"),
            complete=_require_bool(raw.get("complete"), "complete"),
            capture_source=_require_str(raw.get("capture_source"), "capture_source"),
            redaction_summary_id=_require_optional_str(
                raw.get("redaction_summary_id"),
                "redaction_summary_id",
            ),
        )

    @staticmethod
    def _cursor_from_json(raw: object, manifest_attempt_id: str) -> OutputCursor:
        if not isinstance(raw, dict):
            raise ValueError("spool cursor must be a JSON object")
        attempt_id = _safe_component(
            _require_str(raw.get("attempt_id"), "attempt_id"),
            "attempt_id",
        )
        if attempt_id != manifest_attempt_id:
            raise ValueError("spool cursor attempt_id must match manifest")
        return OutputCursor(
            attempt_id=attempt_id,
            stream_name=OutputStreamName(_require_str(raw.get("stream_name"), "stream_name")),
            offset=_require_int(raw.get("offset"), "offset"),
            last_block_index=_require_int(raw.get("last_block_index"), "last_block_index"),
            last_committed_digest=_require_str(
                raw.get("last_committed_digest"),
                "last_committed_digest",
            ),
            durable=_require_bool(raw.get("durable"), "durable"),
        )

    def _manifest_path(self, attempt_id: str) -> Path:
        return self._attempt_dir(attempt_id) / "manifest.json"

    def _stream_path(self, attempt_id: str, stream_name: OutputStreamName) -> Path:
        return self._attempt_dir(attempt_id) / f"{stream_name.value}.log"

    def _attempt_dir(self, attempt_id: str) -> Path:
        candidate = (self._root / "spool" / attempt_id).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError("spool path escapes workspace root")
        return candidate


__all__ = ["FileSpoolStore"]
