"""Safe immutable model receipt material; never a replacement business authority."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aitest.domain.evidence.evidence import StoredObjectRef

from ..security import guard_value
from . import atomic
from .commit_manifest import FileCommitStore
from .objects import FileObjectStore
from .workspace import Workspace

MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate model receipt field")
        result[key] = value
    return result


class FileModelResponseStore:
    def __init__(self, root: Path, *, writer_epoch: int) -> None:
        FileCommitStore.reject_links(root)
        self.root, self.epoch = root.resolve(), writer_epoch
        self.objects = FileObjectStore(self.root)

    def validate(self, *, project_id: str, request_id: str, identity: Mapping[str, object]) -> None:
        if (
            not project_id
            or project_id in {".", ".."}
            or Path(project_id).name != project_id
            or any(c in project_id for c in '\\/:*?"<>|')
            or any(ord(c) < 32 for c in project_id)
            or project_id.rstrip(" .") != project_id
            or project_id.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                "COM1",
                "COM2",
                "COM3",
                "COM4",
                "COM5",
                "COM6",
                "COM7",
                "COM8",
                "COM9",
                "LPT1",
                "LPT2",
                "LPT3",
                "LPT4",
                "LPT5",
                "LPT6",
                "LPT7",
                "LPT8",
                "LPT9",
            }
            or not request_id.strip()
        ):
            raise ValueError("model receipt namespace is invalid")
        Workspace(self.root).validate_writer_epoch(self.epoch)
        self._pointer(project_id, request_id)
        safe, _ = guard_value(dict(identity))
        if safe != identity or len(_canonical(identity)) > MAX_RECEIPT_BYTES:
            raise ValueError("model receipt identity is unsafe or too large")

    def _pointer(self, project_id: str, request_id: str) -> Path:
        key = hashlib.sha256(_canonical([project_id, request_id])).hexdigest()
        path = self.root / "model-responses" / key / "receipt.json"
        FileCommitStore.reject_links(path)
        return path

    def save(
        self,
        *,
        project_id: str,
        request_id: str,
        identity: Mapping[str, object],
        response: Mapping[str, object],
    ) -> StoredObjectRef:
        self.validate(project_id=project_id, request_id=request_id, identity=identity)
        pointer = self._pointer(project_id, request_id)
        body = {
            "schema_version": "aitest.model-response-receipt/1.0",
            "project_id": project_id,
            "request_id": request_id,
            "identity_digest": hashlib.sha256(_canonical(identity)).hexdigest(),
            "response": dict(response),
        }
        safe, _ = guard_value(body)
        if safe != body:
            raise ValueError("model response was not completely filtered before saving")
        content = _canonical(body)
        if len(content) > MAX_RECEIPT_BYTES:
            raise ValueError("safe model receipt exceeds its explicit size budget")
        existing = self.find(project_id=project_id, request_id=request_id, identity=identity)
        if existing is not None:
            if _canonical(existing[1]) != content:
                raise ValueError("an immutable model response already exists with different bytes")
            return existing[0]
        ref = self.objects.publish_bytes(project_id, content, media_type="application/json")
        Workspace(self.root).validate_writer_epoch(self.epoch)
        atomic.write_json(
            pointer,
            {"schema_version": "aitest.model-response-pointer/1.0", "object_ref": asdict(ref)},
        )
        verified = self.find(project_id=project_id, request_id=request_id, identity=identity)
        if verified is None or verified[0] != ref:
            raise ValueError("saved model response could not be verified")
        return ref

    def find(
        self, *, project_id: str, request_id: str, identity: Mapping[str, object]
    ) -> tuple[StoredObjectRef, Mapping[str, object]] | None:
        pointer = self._pointer(project_id, request_id)
        if not pointer.exists():
            return None
        if pointer.stat().st_size > 16 * 1024:
            raise ValueError("model receipt pointer exceeds its explicit size budget")
        with pointer.open("rb") as handle:
            pointer_bytes = handle.read(16 * 1024 + 1)
        if len(pointer_bytes) > 16 * 1024:
            raise ValueError("model receipt pointer exceeds its explicit size budget")
        record = json.loads(pointer_bytes, object_pairs_hook=_unique)
        if (
            not isinstance(record, dict)
            or set(record) != {"schema_version", "object_ref"}
            or record.get("schema_version") != "aitest.model-response-pointer/1.0"
        ):
            raise ValueError("unknown model receipt pointer")
        raw = record.get("object_ref")
        if (
            not isinstance(raw, dict)
            or set(raw) != {"project_id", "digest", "size", "media_type", "relative_path"}
            or raw.get("project_id") != project_id
            or any(
                not isinstance(raw.get(key), str)
                for key in ("digest", "media_type", "relative_path")
            )
            or raw.get("media_type") != "application/json"
            or type(raw.get("size")) is not int
            or not 0 <= raw["size"] <= MAX_RECEIPT_BYTES
        ):
            raise ValueError("model receipt object ownership/size is unverified")
        ref = StoredObjectRef(**raw)
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", ref.digest):
            raise ValueError("model receipt object digest is invalid")
        expected = "objects/" + project_id + "/" + ref.digest.removeprefix("sha256:")
        if ref.relative_path != expected:
            raise ValueError("model receipt object path is unverified")
        path = self.root / expected
        FileCommitStore.reject_links(path)
        if path.stat().st_size != ref.size:
            raise ValueError("model receipt object size differs")
        body = json.loads(
            self.objects.read_bytes(ref, max_bytes=MAX_RECEIPT_BYTES), object_pairs_hook=_unique
        )
        safe, _ = guard_value(body)
        if (
            not isinstance(body, dict)
            or safe != body
            or body.get("schema_version") != "aitest.model-response-receipt/1.0"
            or body.get("project_id") != project_id
            or body.get("request_id") != request_id
            or body.get("identity_digest") != hashlib.sha256(_canonical(identity)).hexdigest()
            or not isinstance(body.get("response"), dict)
        ):
            raise ValueError("model receipt identity/bytes cannot be verified")
        return ref, body
