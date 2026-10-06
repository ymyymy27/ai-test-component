"""Persist verified command output streams in the workspace spool."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path

import portalocker

from aitest.domain.evidence.evidence import RedactionSummary
from aitest.domain.execution.runs import (
    CapturedOutputBlock,
    OutputBlockRef,
    OutputCursor,
    OutputStreamName,
    SpoolManifest,
)

from ..security import (
    KnownSecretRegistry,
    StreamSecretFilter,
    UnsafeMaterialError,
    guard_bytes,
    known_secrets,
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


def _require_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
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
        registry: KnownSecretRegistry,
    ) -> None:
        if type(block_size) is not int or block_size < 1:
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
        self._capture_lock = store._capture_lock(attempt_id, stream_name)
        self._capture_lock.acquire()
        opened = None
        try:
            manifest = store.read_manifest(attempt_id)
            self._offset = store._stream_boundary(attempt_id, stream_name, manifest)
            self._block_start = self._offset
            self._block_length = 0
            self._hasher = hashlib.sha256()
            self._block_index = store._next_block_index(attempt_id, stream_name)
            self._filter = StreamSecretFilter(registry)
            store._capture_state(attempt_id, stream_name, "active")
            opened = self._path.open("ab")
            self._handle = opened
        except BaseException:
            if opened is not None:
                opened.close()
            self._capture_lock.release()
            raise

    def append(self, content: bytes) -> tuple[OutputBlockRef, ...]:
        if not content:
            return ()
        with self._lock:
            if self._closed:
                raise RuntimeError("spool stream writer is closed")
            safe = self._filter.feed(content)
            if not safe:
                # 整块（或尾部）仍在未决窗口：尚未确认安全，不落盘。
                return ()
            self._handle.write(safe)
            self._handle.flush()
            self._hasher.update(safe)
            self._block_length += len(safe)
            if self._block_length < self._block_size:
                return ()
            os.fsync(self._handle.fileno())
            return (self._seal(complete=True),)

    def close(self, *, complete: bool = True) -> tuple[OutputBlockRef, ...]:
        with self._lock:
            if self._closed:
                return ()
            self._closed = True
            refs: tuple[OutputBlockRef, ...] = ()
            try:
                tail = self._filter.flush()
                if tail:
                    self._handle.write(tail)
                self._handle.flush()
                os.fsync(self._handle.fileno())
                if tail:
                    self._hasher.update(tail)
                    self._block_length += len(tail)
                if self._block_length:
                    refs = (self._seal(complete=complete),)
                self._store._capture_state(self._attempt_id, self._stream_name, "sealed")
            finally:
                self._handle.close()
                self._capture_lock.release()
                self._store._unregister_writer(self._attempt_id, self._stream_name)
            return refs

    def abort(self) -> None:
        """Close stream bytes without sealing metadata to simulate a crash."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # 未确认安全的尾部随异常终止丢弃，绝不转储（架构02第13节）。
            self._filter.abort()
            try:
                self._handle.flush()
                os.fsync(self._handle.fileno())
            finally:
                self._handle.close()
                self._capture_lock.release()
                self._store._unregister_writer(self._attempt_id, self._stream_name)

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

    def __init__(
        self,
        workspace_root: Path,
        *,
        registry: KnownSecretRegistry | None = None,
    ) -> None:
        self._root = workspace_root.resolve()
        self._registry = registry if registry is not None else known_secrets()
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
        if type(block_size) is not int or block_size < 1:
            raise ValueError("block_size must be a positive integer")
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
                registry=self._registry,
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
        # 先对全部密封块做落盘前检查，任何一块不洁则整批拒绝（无孤儿字节）。
        for block in batch:
            if block.complete is not True:
                raise ValueError("only sealed blocks may be persisted")
            if type(block.offset) is not int or type(block.block_index) is not int:
                raise ValueError("spool block offsets and indexes must be exact integers")
            if not isinstance(block.content, bytes):
                raise ValueError("sealed spool content must be immutable bytes")
            if (block.attempt_id, block.run_id, block.step_id) != (attempt_id, run_id, step_id):
                raise ValueError("all spool blocks must share run_id, step_id and attempt_id")
            _guarded, dirty = guard_bytes(block.content, self._registry)
            if dirty:
                # A-09：改写会使 C 包装器既有 digest/offset 失效，故拒绝落盘，
                # 由调用方登记采集缺口并重供安全材料。
                raise UnsafeMaterialError(
                    "sealed spool block still contains a known credential; "
                    "register a capture gap instead of persisting raw bytes"
                )
        with ExitStack() as leases:
            for stream in sorted({block.stream_name for block in batch}):
                leases.enter_context(self._capture_lock(attempt_id, stream))  # type: ignore[arg-type]
            self._ensure_manifest(attempt_id, run_id, step_id)
            self._preflight_blocks(batch, self.read_manifest(attempt_id))
            for block in batch:
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

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            fields: dict[str, object] = {}
            for key, value in pairs:
                if key in fields:
                    raise ValueError("duplicate field in spool manifest: " + key)
                fields[key] = value
            return fields

        with self._manifest_lock:
            path = self._manifest_path(safe_attempt)
            raw: object = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
            manifest = self._manifest_from_json(raw)
            if manifest.attempt_id != safe_attempt:
                raise ValueError("spool manifest identity differs from the requested attempt")
            return manifest

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

    def persist_redaction_summary(
        self,
        attempt_id: str,
        stream_name: OutputStreamName,
        summary: RedactionSummary,
    ) -> str:
        safe_attempt = _safe_component(attempt_id, "attempt_id")
        summary_id = f"redaction:{safe_attempt}:{stream_name.value}"
        payload = {
            "schema_version": "aitest.redaction-summary/1.0",
            "summary_id": summary_id,
            "stream_name": stream_name.value,
            "policy_version": summary.policy_version,
            "applied_rule_categories": list(summary.applied_rule_categories),
            "filtered_streams": list(summary.filtered_streams),
            "filtered_ranges": list(summary.filtered_ranges),
            "replacement_count": summary.replacement_count,
            "completeness": summary.completeness,
            "gap_reasons": list(summary.gap_reasons),
        }
        atomic.write_json(
            self._attempt_dir(safe_attempt) / f"redaction-{stream_name.value}.json",
            payload,
        )
        return summary_id

    def read_redaction_summary(
        self,
        attempt_id: str,
        stream_name: OutputStreamName,
    ) -> RedactionSummary:
        safe_attempt = _safe_component(attempt_id, "attempt_id")
        raw: object = json.loads(
            (self._attempt_dir(safe_attempt) / f"redaction-{stream_name.value}.json").read_text(
                encoding="utf-8"
            )
        )
        if not isinstance(raw, dict):
            raise ValueError("redaction summary must be an object")
        if raw.get("schema_version") != "aitest.redaction-summary/1.0":
            raise ValueError("unsupported redaction summary schema")
        return RedactionSummary(
            policy_version=_require_str(raw.get("policy_version"), "policy_version"),
            applied_rule_categories=tuple(
                _require_str(value, "applied_rule_category")
                for value in _require_list(
                    raw.get("applied_rule_categories"),
                    "applied_rule_categories",
                )
            ),
            filtered_streams=tuple(
                _require_str(value, "filtered_stream")
                for value in _require_list(raw.get("filtered_streams"), "filtered_streams")
            ),
            filtered_ranges=tuple(
                _require_str(value, "filtered_range")
                for value in _require_list(raw.get("filtered_ranges"), "filtered_ranges")
            ),
            replacement_count=_require_int(raw.get("replacement_count"), "replacement_count"),
            completeness=_require_str(raw.get("completeness"), "completeness"),
            gap_reasons=tuple(
                _require_str(value, "gap_reason")
                for value in _require_list(raw.get("gap_reasons"), "gap_reasons")
            ),
        )

    def salvage_streams(self, attempt_id: str) -> SpoolManifest:
        safe_attempt = _safe_component(attempt_id, "attempt_id")
        # OS 锁覆盖全部抢救操作，另一实例/进程的写流存在时绝不抢认字节。
        with ExitStack() as locks:
            for stream in OutputStreamName:
                lock = self._capture_lock(safe_attempt, stream)
                lock.acquire()
                locks.callback(lock.release)
            result = self._salvage_locked(safe_attempt)
            for stream in OutputStreamName:
                if self._capture_path(safe_attempt, stream).exists():
                    self._capture_state(safe_attempt, stream, "recovered")
            return result

    def _capture_path(self, attempt_id: str, stream: OutputStreamName) -> Path:
        return self._attempt_dir(attempt_id) / f"capture-{stream.value}.json"

    def _capture_lock(self, attempt_id: str, stream: OutputStreamName) -> portalocker.Lock:
        directory = self._attempt_dir(attempt_id)
        directory.mkdir(parents=True, exist_ok=True)
        return portalocker.Lock(
            directory / f".capture-{stream.value}.lock",
            mode="a",
            timeout=0,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )

    def _capture_state(self, attempt_id: str, stream: OutputStreamName, state: str) -> None:
        atomic.write_json(
            self._capture_path(attempt_id, stream),
            {
                "attempt_id": attempt_id,
                "stream": stream.value,
                "state": state,
            },
        )

    def _salvage_locked(self, safe_attempt: str) -> SpoolManifest:
        manifest = self.read_manifest(safe_attempt)
        recovered: list[OutputBlockRef] = []
        for stream_name in (OutputStreamName.STDOUT, OutputStreamName.STDERR):
            path = self._stream_path(safe_attempt, stream_name)
            if not path.exists():
                continue
            size = path.stat().st_size
            stream_blocks = [block for block in manifest.blocks if block.stream_name is stream_name]
            covered_end = max(
                (block.offset + block.length for block in stream_blocks),
                default=0,
            )
            cursor = next(
                (current for current in manifest.cursors if current.stream_name is stream_name),
                None,
            )
            start = max(covered_end, cursor.offset if cursor is not None else 0)
            if size <= start:
                continue
            with path.open("rb") as handle:
                handle.seek(start)
                content = handle.read(size - start)
            if not content:
                continue
            # A-09：抢救路径同样不得让凭据进入认领事实。尾区尚未被任何块
            # 认领（文件末尾），过滤后就地重写该尾区，摘要只描述安全字节；
            # 过滤前字节不保留、不复制。
            content, dirty = guard_bytes(content, self._registry)
            if dirty:
                with path.open("r+b") as handle:
                    handle.truncate(start)
                    handle.seek(start)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            block_index = (
                max(
                    (block.block_index for block in stream_blocks),
                    default=-1,
                )
                + 1
            )
            recovered.append(
                OutputBlockRef(
                    block_id=f"{safe_attempt}:{stream_name.value}:{block_index}",
                    attempt_id=safe_attempt,
                    stream_name=stream_name,
                    block_index=block_index,
                    offset=start,
                    length=len(content),
                    digest="sha256:" + hashlib.sha256(content).hexdigest(),
                    complete=False,
                    capture_source="recovery",
                )
            )
        if recovered:
            self._merge_manifest(
                attempt_id=safe_attempt,
                run_id=manifest.run_id,
                step_id=manifest.step_id,
                new_blocks=tuple(recovered),
                new_cursors=(),
            )
        return self.read_manifest(safe_attempt)

    def _stream_boundary(
        self, attempt_id: str, stream: OutputStreamName, manifest: SpoolManifest
    ) -> int:
        blocks = sorted(
            (item for item in manifest.blocks if item.stream_name is stream),
            key=lambda item: item.block_index,
        )
        end = 0
        for index, block in enumerate(blocks):
            if block.block_index != index or block.offset != end:
                raise ValueError("spool stream metadata has no contiguous boundary")
            end += block.length
        cursor = next((item for item in manifest.cursors if item.stream_name is stream), None)
        if cursor is not None:
            cursor_block = next(
                (item for item in blocks if item.block_index == cursor.last_block_index), None
            )
            if cursor_block is None or (cursor.offset, cursor.last_committed_digest) != (
                cursor_block.offset + cursor_block.length,
                cursor_block.digest,
            ):
                raise ValueError("spool cursor does not match the saved stream boundary")
        path = self._stream_path(attempt_id, stream)
        size = path.stat().st_size if path.exists() else 0
        if size != end:
            raise ValueError("spool stream boundary requires recovery before capture")
        return size

    def _preflight_blocks(
        self, batch: tuple[CapturedOutputBlock, ...], manifest: SpoolManifest
    ) -> None:
        """Validate the whole append before the first byte, under all stream leases."""
        streams = {item.stream_name for item in batch}
        ends = {
            stream: self._stream_boundary(manifest.attempt_id, stream, manifest)
            for stream in streams
        }
        existing = {(item.stream_name, item.block_index): item for item in manifest.blocks}
        planned: dict[tuple[OutputStreamName, int], CapturedOutputBlock] = {}
        indexes = {
            stream: sum(item.stream_name is stream for item in manifest.blocks)
            for stream in streams
        }
        for block in batch:
            key = (block.stream_name, block.block_index)
            prior = existing.get(key)
            if prior is not None:
                if (
                    prior.offset != block.offset
                    or prior.length != block.length
                    or prior.digest != block.digest
                    or prior.complete is not block.complete
                    or prior.capture_source != block.capture_source
                    or prior.redaction_summary_id != block.redaction_summary_id
                    or self.read_block(prior) != block.content
                ):
                    raise ValueError("spool block conflicts with existing metadata or bytes")
                continue
            duplicate = planned.get(key)
            if duplicate is not None:
                if duplicate != block:
                    raise ValueError("spool batch contains conflicting blocks")
                continue
            if (
                block.offset != ends[block.stream_name]
                or block.block_index != indexes[block.stream_name]
            ):
                raise ValueError("spool block offsets and indexes must be contiguous")
            planned[key] = block
            ends[block.stream_name] += block.length
            indexes[block.stream_name] += 1

    def _write_block(self, ref: OutputBlockRef, content: bytes) -> None:
        path = self._stream_path(ref.attempt_id, ref.stream_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_size = path.stat().st_size if path.exists() else 0
        if path.exists() and ref.offset < existing_size:
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
                existing = self.read_manifest(attempt_id)
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
                manifest = self.read_manifest(attempt_id)
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
                previous_cursor = cursors.get(cursor.stream_name)
                if previous_cursor is not None and (
                    cursor.offset < previous_cursor.offset
                    or cursor.last_block_index < previous_cursor.last_block_index
                ):
                    # Replaying an older sealed block is idempotent; it cannot rewind
                    # the already committed capture cursor.
                    continue
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
