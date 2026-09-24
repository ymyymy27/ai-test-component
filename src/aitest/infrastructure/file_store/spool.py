"""Persist sealed per-attempt output blocks in the workspace spool."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from aitest.domain.execution.runs import (
    CapturedOutputBlock,
    OutputBlockRef,
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


class FileSpoolStore:
    """File-backed spool store for one workspace."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root.resolve()

    def persist_blocks(self, blocks: Sequence[CapturedOutputBlock]) -> SpoolManifest:
        batch = tuple(blocks)
        if not batch:
            raise ValueError("at least one sealed block is required")

        first = batch[0]
        attempt_id = _safe_component(first.attempt_id, "attempt_id")
        run_id = _safe_component(first.run_id, "run_id")
        step_id = _safe_component(first.step_id, "step_id")
        for block in batch:
            if not block.complete:
                raise ValueError("only sealed blocks may be persisted")
            if (block.attempt_id, block.run_id, block.step_id) != (attempt_id, run_id, step_id):
                raise ValueError("all spool blocks must share run_id, step_id and attempt_id")

        existing = self._read_manifest_if_exists(attempt_id)
        if existing is not None and (existing.run_id, existing.step_id) != (run_id, step_id):
            raise ValueError("spool manifest identity does not match incoming blocks")

        by_key = {
            (block.stream_name, block.block_index): block
            for block in (existing.blocks if existing is not None else ())
        }
        for block in batch:
            key = (block.stream_name, block.block_index)
            path = self._block_path(attempt_id, block.stream_name, block.block_index)
            self._write_immutable(path, block.content)
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
            previous = by_key.get(key)
            if previous is not None and previous != ref:
                raise ValueError("spool block conflicts with existing metadata")
            by_key[key] = ref

        ordered_keys = sorted(by_key, key=lambda item: (item[0].value, item[1]))
        manifest = SpoolManifest(
            attempt_id=attempt_id,
            run_id=run_id,
            step_id=step_id,
            blocks=tuple(by_key[key] for key in ordered_keys),
        )
        atomic.write_json(self._manifest_path(attempt_id), self._manifest_to_json(manifest))
        return manifest

    def read_manifest(self, attempt_id: str) -> SpoolManifest:
        safe_attempt = _safe_component(attempt_id, "attempt_id")
        path = self._manifest_path(safe_attempt)
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        return self._manifest_from_json(raw)

    def read_block(self, ref: OutputBlockRef) -> bytes:
        safe_attempt = _safe_component(ref.attempt_id, "attempt_id")
        content = self._block_path(safe_attempt, ref.stream_name, ref.block_index).read_bytes()
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if len(content) != ref.length or digest != ref.digest:
            raise ValueError("spool block content failed verification")
        return content

    def _read_manifest_if_exists(self, attempt_id: str) -> SpoolManifest | None:
        path = self._manifest_path(attempt_id)
        if not path.exists():
            return None
        return self.read_manifest(attempt_id)

    def _manifest_to_json(self, manifest: SpoolManifest) -> dict[str, object]:
        return {
            "schema_version": manifest.schema_version,
            "attempt_id": manifest.attempt_id,
            "run_id": manifest.run_id,
            "step_id": manifest.step_id,
            "blocks": [self._block_to_json(block) for block in manifest.blocks],
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
        if not isinstance(blocks_raw, list):
            raise ValueError("spool blocks must be a list")
        blocks = tuple(self._block_from_json(entry, attempt_id) for entry in blocks_raw)
        return SpoolManifest(
            schema_version=schema_version,
            attempt_id=attempt_id,
            run_id=run_id,
            step_id=step_id,
            blocks=blocks,
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

    def _manifest_path(self, attempt_id: str) -> Path:
        return self._attempt_dir(attempt_id) / "manifest.json"

    def _block_path(
        self,
        attempt_id: str,
        stream_name: OutputStreamName,
        block_index: int,
    ) -> Path:
        return self._attempt_dir(attempt_id) / f"{stream_name.value}-{block_index}.bin"

    def _attempt_dir(self, attempt_id: str) -> Path:
        candidate = (self._root / "spool" / attempt_id).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError("spool path escapes workspace root")
        return candidate

    @staticmethod
    def _write_immutable(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError(
                    f"immutable spool block already exists with different bytes: {path}"
                )
            return
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            atomic.replace_with_retry(temporary, path)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise
