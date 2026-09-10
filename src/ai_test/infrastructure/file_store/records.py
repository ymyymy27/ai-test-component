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
    STATE_SCHEMA = "aatp.workspace-state/1.0"

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
            return
        with self._lease.acquire():
            if self.current_file.exists():
                return
            generation = "gen-000001"
            state = {"schema_version": self.STATE_SCHEMA, "revision": 0, "records": {}}
            self._atomic_json(self.generations / generation / "state.json", state)
            self._atomic_json(
                self.current_file,
                {"schema_version": "aatp.workspace-pointer/1.0", "generation": generation},
            )

    def get(self, kind: str, record_id: str) -> dict[str, Any] | None:
        state, generation = self._load_state()
        entry = state["records"].get(self._key(kind, record_id))
        if entry is None:
            return None
        path = self.workspace / entry["path"]
        self._ensure_inside_workspace(path)
        with path.open("r", encoding="utf-8") as handle:
            return cast(dict[str, Any], json.load(handle))

    def list(self, kind: str) -> Iterator[dict[str, Any]]:
        state, _ = self._load_state()
        prefix = f"{kind}/"
        for key in sorted(state["records"]):
            if key.startswith(prefix):
                record_id = key[len(prefix) :]
                item = self.get(kind, record_id)
                if item is not None:
                    yield item

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
            existing = state["records"].get(key)
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
            state["revision"] = int(state["revision"]) + 1
            state["records"][key] = {
                "revision": revision,
                "path": relative.as_posix(),
            }
            self._atomic_json(self.generations / generation / "state.json", state)
            return revision

    def _load_state(self) -> tuple[dict[str, Any], str]:
        self.initialize()
        with self.current_file.open("r", encoding="utf-8") as handle:
            pointer = json.load(handle)
        generation = str(pointer["generation"])
        self._validate_segment(generation, "generation")
        with (self.generations / generation / "state.json").open(
            "r", encoding="utf-8"
        ) as handle:
            state = json.load(handle)
        if state.get("schema_version") != self.STATE_SCHEMA:
            raise ValueError("unsupported workspace state schema")
        return state, generation

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
