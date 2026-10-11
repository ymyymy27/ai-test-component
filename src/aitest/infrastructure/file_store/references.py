"""Verify only explicit permanent object references, never inline content hashes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.security import UnsafeMaterialError, copy_unchanged_safe_bytes

OBJECT_REFERENCE_KEYS = frozenset({"object_digest", "output_object_digest", "artifact_digest"})
_OBJECT_FIELDS = {"project_id", "digest", "size", "media_type", "relative_path"}
_DIGEST = re.compile(r"sha256:([0-9a-f]{64})\Z")


def verify_record_objects(root: Path, body: Mapping[str, object], project_id: str) -> None:
    """Check changed-record closure with streaming safety/digest verification.

    Work is bounded by this record's explicit references. Existing unrelated
    history and unattached objects are not visited; duplicate references share
    one verification. Nothing is written, redacted in place, or guessed.
    """
    references: dict[str, int | None] = {}
    pending: list[object] = [body]
    while pending:
        value = pending.pop()
        if isinstance(value, Mapping):
            structured = "relative_path" in value and "digest" in value
            if structured:
                if (
                    not _OBJECT_FIELDS.issubset(value)
                    or value["project_id"] != project_id
                    or type(value["size"]) is not int
                    or value["size"] < 0
                    or not isinstance(value["media_type"], str)
                    or not value["media_type"].strip()
                ):
                    raise ValueError("permanent object reference identity/size/project is invalid")
                _add_reference(references, value["digest"], value["size"])
                match = _DIGEST.fullmatch(str(value["digest"]))
                assert match is not None
                if value["relative_path"] != f"objects/{project_id}/{match[1]}":
                    raise ValueError("permanent object reference path does not match its identity")
            for key, item in value.items():
                if key in OBJECT_REFERENCE_KEYS:
                    if "project_id" in value and value["project_id"] != project_id:
                        raise ValueError("permanent object reference belongs to another project")
                    items = item if isinstance(item, (list, tuple)) else (item,)
                    for reference in items:
                        if reference is not None:
                            _add_reference(references, reference, None)
                elif key == "object_ref" and item is not None:
                    if not isinstance(item, Mapping) or not _OBJECT_FIELDS.issubset(item):
                        raise ValueError("permanent object reference is incomplete")
                pending.append(item)
        elif isinstance(value, (list, tuple)):
            pending.extend(value)

    if references and (
        not project_id
        or Path(project_id).name != project_id
        or project_id in {".", ".."}
        or any(char in project_id for char in '\\/:*?"<>|')
    ):
        raise ValueError("permanent object reference project is not a safe path component")
    for digest, expected_size in references.items():
        path = root / "objects" / project_id / digest.removeprefix("sha256:")
        if any(part.is_symlink() or compat.is_junction(part) for part in (path, *path.parents)):
            raise ValueError("permanent object reference path contains a filesystem link")
        try:
            if expected_size is not None and path.stat().st_size != expected_size:
                raise ValueError("permanent object reference size mismatch")
            with path.open("rb") as stream:
                actual_digest, actual_size = copy_unchanged_safe_bytes(stream)
        except UnsafeMaterialError as error:
            raise ValueError(
                "permanent object reference cannot preserve safe byte identity"
            ) from error
        except OSError as error:
            raise ValueError("permanent object reference is missing or unreadable") from error
        if f"sha256:{actual_digest}" != digest:
            raise ValueError("permanent object reference digest mismatch")
        if expected_size is not None and actual_size != expected_size:
            raise ValueError("permanent object reference size changed during verification")


def _add_reference(references: dict[str, int | None], digest: object, size: int | None) -> None:
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise ValueError("permanent object reference digest is invalid")
    if digest in references:
        old_size = references[digest]
        if old_size is not None and size is not None and old_size != size:
            raise ValueError("conflicting permanent object reference sizes")
        if size is None:
            return
    references[digest] = size
