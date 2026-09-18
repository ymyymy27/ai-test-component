import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from ai_test.infrastructure.file_store.locks import WriterLease


class ConflictError(RuntimeError):
    """The record changed after the caller last read it."""


class FileRecordRepository:
    STATE_SCHEMA = "aatp.workspace-state/2.0"
    LEGACY_STATE_SCHEMA = "aatp.workspace-state/1.0"
    INDEX_SCHEMA = "aatp.record-index/1.0"
    SHARD_COUNT = 16

    def __init__(self, workspace: Path, *, lock_timeout_seconds: float = 10.0) -> None:
        self.workspace = workspace.resolve()
        self.generations = self.workspace / "generations"
        self.current_file = self.workspace / "current.json"
        self._lease = WriterLease(
            self.workspace / ".locks" / "writer.lock", lock_timeout_seconds
        )

    def initialize(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "objects" / "sha256").mkdir(parents=True, exist_ok=True)
        (self.workspace / ".locks").mkdir(parents=True, exist_ok=True)
        if self.current_file.exists():
            with self._lease.acquire():
                self._migrate_if_needed()
            return
        with self._lease.acquire():
            if self.current_file.exists():
                self._migrate_if_needed()
                return
            generation = "gen-000001"
            self._atomic_json(
                self.generations / generation / "state.json", self._empty_state()
            )
            self._atomic_json(
                self.current_file,
                {"schema_version": "aatp.workspace-pointer/1.0", "generation": generation},
            )

    def check_writable(self) -> tuple[bool, str | None]:
        self.initialize()
        try:
            with self._lease.acquire():
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".write-probe.", dir=self.workspace / ".locks"
                )
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(b"ok")
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    with suppress(FileNotFoundError):
                        os.unlink(temporary)
            return True, None
        except Exception as error:
            return False, str(error)

    def get(self, kind: str, record_id: str) -> dict[str, Any] | None:
        self.initialize()
        state, generation = self._load_state()
        shard = self._shard_key(kind, record_id)
        chunk = self._load_chunk(state, generation, shard)
        if chunk is None:
            return None
        entry = chunk["records"].get(self._key(kind, record_id))
        if entry is None:
            return None
        return self._load_record(entry)

    def list(self, kind: str) -> Iterator[dict[str, Any]]:
        self.initialize()
        state, generation = self._load_state()
        prefix = f"{kind}/"
        for shard in sorted(state["index_chunks"]):
            if not shard.startswith(prefix):
                continue
            chunk = self._load_chunk(state, generation, shard)
            if chunk is None:
                continue
            for key in sorted(chunk["records"]):
                if key.startswith(prefix):
                    yield self._load_record(chunk["records"][key])

    def put(
        self,
        kind: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> int:
        self._validate_segment(kind, "kind")
        self._validate_segment(record_id, "record_id")
        self.initialize()
        with self._lease.acquire():
            state, generation = self._load_state()
            key = self._key(kind, record_id)
            shard = self._shard_key(kind, record_id)
            chunk = self._load_chunk(state, generation, shard) or {
                "schema_version": self.INDEX_SCHEMA,
                "revision": 0,
                "records": {},
            }
            existing = chunk["records"].get(key)
            actual_revision = 0 if existing is None else int(existing["revision"])
            if expected_revision is not None and expected_revision != actual_revision:
                raise ConflictError(
                    f"expected revision {expected_revision}, found {actual_revision}"
                )
            revision = actual_revision + 1
            document = deepcopy(payload)
            document["revision"] = revision
            relative = Path("generations") / generation / "records" / kind / record_id
            relative /= f"r{revision:08d}.json"
            self._atomic_json(self.workspace / relative, document)

            chunk_revision = int(chunk.get("revision", 0)) + 1
            chunk_path = self._chunk_path(generation, shard, chunk_revision)
            chunk["revision"] = chunk_revision
            chunk["records"][key] = {
                "revision": revision,
                "path": relative.as_posix(),
            }
            self._atomic_json(self.workspace / chunk_path, chunk)

            state["revision"] = int(state["revision"]) + 1
            state["index_chunks"][shard] = {
                "revision": chunk_revision,
                "path": chunk_path.as_posix(),
            }
            self._atomic_json(self._state_path(generation), state)
            return revision

    def _load_state(self) -> tuple[dict[str, Any], str]:
        pointer = self._read_json(self.current_file)
        generation = str(pointer["generation"])
        self._validate_segment(generation, "generation")
        state = self._read_json(self._state_path(generation))
        if state.get("schema_version") != self.STATE_SCHEMA:
            raise ValueError("unsupported workspace state schema")
        return state, generation

    def _load_chunk(
        self, state: dict[str, Any], generation: str, shard: str
    ) -> dict[str, Any] | None:
        reference = state["index_chunks"].get(shard)
        if reference is None:
            return None
        path = self.workspace / str(reference["path"])
        self._ensure_inside_workspace(path)
        chunk = self._read_json(path)
        if chunk.get("schema_version") != self.INDEX_SCHEMA:
            raise ValueError("unsupported record index schema")
        return chunk

    def _load_record(self, entry: dict[str, Any]) -> dict[str, Any]:
        path = self.workspace / str(entry["path"])
        self._ensure_inside_workspace(path)
        return self._read_json(path)

    def _migrate_if_needed(self) -> None:
        pointer = self._read_json(self.current_file)
        generation = str(pointer["generation"])
        self._validate_segment(generation, "generation")
        state_path = self._state_path(generation)
        state = self._read_json(state_path)
        schema = state.get("schema_version")
        if schema == self.STATE_SCHEMA:
            return
        if schema != self.LEGACY_STATE_SCHEMA:
            raise ValueError("unsupported workspace state schema")

        chunks: dict[str, dict[str, Any]] = {}
        for key, entry in state.get("records", {}).items():
            kind, record_id = key.split("/", 1)
            shard = self._shard_key(kind, record_id)
            chunk = chunks.setdefault(
                shard,
                {
                    "schema_version": self.INDEX_SCHEMA,
                    "revision": 0,
                    "records": {},
                },
            )
            chunk["records"][key] = entry

        migrated = self._empty_state()
        migrated["revision"] = int(state.get("revision", 0))
        for shard, chunk in chunks.items():
            chunk["revision"] = 1
            chunk_path = self._chunk_path(generation, shard, 1)
            self._atomic_json(self.workspace / chunk_path, chunk)
            migrated["index_chunks"][shard] = {
                "revision": 1,
                "path": chunk_path.as_posix(),
            }
        self._atomic_json(state_path, migrated)

    def _empty_state(self) -> dict[str, Any]:
        return {
            "schema_version": self.STATE_SCHEMA,
            "revision": 0,
            "index_chunks": {},
        }

    def _state_path(self, generation: str) -> Path:
        return self.generations / generation / "state.json"

    def _chunk_path(self, generation: str, shard: str, revision: int) -> Path:
        kind, prefix = shard.split("/", 1)
        return (
            Path("generations")
            / generation
            / "indexes"
            / kind
            / prefix
            / f"r{revision:08d}.json"
        )

    def _shard_key(self, kind: str, record_id: str) -> str:
        digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
        shard = int(digest[:8], 16) % self.SHARD_COUNT
        return f"{kind}/{shard:02x}"

    @staticmethod
    def _validate_segment(value: str, name: str) -> None:
        if not value or value in {".", ".."} or any(char in value for char in "/\\"):
            raise ValueError(f"invalid {name}")

    @staticmethod
    def _key(kind: str, record_id: str) -> str:
        return f"{kind}/{record_id}"

    def _ensure_inside_workspace(self, path: Path) -> None:
        if not path.resolve().is_relative_to(self.workspace):
            raise ValueError("record path escapes workspace")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return cast(dict[str, Any], json.load(handle))

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise