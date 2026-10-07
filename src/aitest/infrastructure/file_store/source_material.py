"""Read-only checks of immutable source metadata and its actual pinned bytes."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from aitest.infrastructure.security import (
    KnownSecretRegistry,
    UnsafeMaterialError,
    copy_unchanged_safe_bytes,
    guard_value,
)

MAX_METADATA_BYTES = 16 * 1024 * 1024
_SNAPSHOT_ID = re.compile(r"snap-[0-9a-f]{16}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class SourceMaterialError(ValueError):
    """No byte-identity or safety claim can be made for this material."""

    code = "COMMIT_MATERIAL_UNVERIFIED"


class _PendingSourceRows:
    """A read-only source view; staged additions are never inserted into authority."""

    def __init__(self, original: Any, project_id: str) -> None:
        self.original = original
        self.added: list[dict[str, Any]] = []
        self.metadata = getattr(original, "metadata", {"project_id": project_id})

    def __len__(self) -> int:
        return len(self.original) + len(self.added)

    def __getitem__(self, index: int) -> dict[str, Any]:
        if index < len(self.original):
            value: dict[str, Any] = self.original[index]
            return value
        return self.added[index - len(self.original)]


class _PendingSourceLookup:
    """Delegate exact .get reads, including lazy stores whose dict cache is empty."""

    def __init__(self, original: Any, added: Mapping[str, Any]) -> None:
        self.original, self.added = original, added

    def get(self, key: str, default: Any = None) -> Any:
        return self.added[key] if key in self.added else self.original.get(key, default)


def prospective_source_authority(
    authority: Mapping[str, Any],
    pending: Sequence[tuple[str, str, int | None, Mapping[str, object]]],
    project_id: str,
) -> dict[str, Any]:
    """Overlay only this batch's snapshot/pointer rows, without scanning unrelated history."""
    additions: dict[str, dict[str, _PendingSourceRows]] = {}
    records = authority["records"]
    for kind, identity, expected, payload in pending:
        if kind not in {"source_snapshot", "source_binding_current"}:
            continue
        changed = additions.setdefault(kind, {})
        if identity not in changed:
            changed[identity] = _PendingSourceRows(
                records.get(kind, {}).get(identity, []), project_id
            )
        rows = changed[identity]
        if type(expected) is not int or expected != len(rows):
            raise SourceMaterialError("pending source warehouse revision conflicts")
        if payload.get("project_id") != project_id:
            raise SourceMaterialError("pending source belongs to another project")
        rows.added.append(dict(payload))
    overlay = {
        kind: _PendingSourceLookup(records.get(kind, {}), changed)
        for kind, changed in additions.items()
    }
    return {**authority, "records": _PendingSourceLookup(records, overlay)}


def reject_links(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise SourceMaterialError("pinned source material path contains a filesystem link")


def metadata_path(root: Path, snapshot_id: str) -> Path:
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise SourceMaterialError("pinned source snapshot identity is invalid")
    return root / "snapshots" / f"{snapshot_id}.json"


def _relative(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and not any(ord(c) < 32 for c in name)
        and not any(c in name for c in '\\:*?"<>|')
        and not Path(name).is_absolute()
        and not Path(name).drive
        and all(p not in {"", ".", ".."} for p in name.split("/"))
    )


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceMaterialError("duplicate pinned source metadata field")
        result[key] = value
    return result


def read_metadata(
    root: Path, snapshot_id: str, *, registry: KnownSecretRegistry | None = None
) -> dict[str, Any]:
    path = metadata_path(root, snapshot_id)
    reject_links(path)
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise SourceMaterialError("pinned source metadata exceeds its read budget")
        with path.open("rb") as stream:
            raw = stream.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            raise SourceMaterialError("pinned source metadata exceeds its read budget")
        record = json.loads(raw, object_pairs_hook=_unique)
    except (OSError, RecursionError, UnicodeError, json.JSONDecodeError) as error:
        raise SourceMaterialError("pinned source metadata is missing or unreadable") from error
    safe, changed = guard_value(record, registry)
    if changed or safe != record:
        raise SourceMaterialError("pinned source metadata cannot preserve safe byte identity")
    if (
        not isinstance(record, dict)
        or record.get("schema") != "aitest.source-snapshot/1.0"
        or record.get("snapshot_id") != snapshot_id
        or not isinstance(record.get("purpose"), str)
        or not record["purpose"]
        or not isinstance(record.get("canonical_path"), str)
        or not Path(record["canonical_path"]).is_absolute()
        or any(ord(c) < 32 for c in record["canonical_path"])
        or not isinstance(record.get("selected_paths"), list)
        or any(not _relative(p) for p in record["selected_paths"])
        or len(set(record["selected_paths"])) != len(record["selected_paths"])
        or not isinstance(record.get("exclusion_rules"), list)
        or any(not _relative(p) for p in record["exclusion_rules"])
        or not isinstance(record.get("files"), list)
    ):
        raise SourceMaterialError("pinned source metadata identity or scope is invalid")
    paths: set[str] = set()
    sizes: dict[str, int] = {}
    for item in record["files"]:
        if (
            not isinstance(item, dict)
            or not _relative(item.get("relative_path"))
            or item["relative_path"] in paths
            or not isinstance(item.get("sha256"), str)
            or not _HEX.fullmatch(item["sha256"])
            or type(item.get("size")) is not int
            or item["size"] < 0
            or item["sha256"] in sizes
            and sizes[item["sha256"]] != item["size"]
        ):
            raise SourceMaterialError("pinned source file reference identity/size is invalid")
        paths.add(item["relative_path"])
        sizes[item["sha256"]] = item["size"]
    identity = json.dumps(
        [
            record[k]
            for k in ("canonical_path", "purpose", "selected_paths", "exclusion_rules", "files")
        ],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if "snap-" + hashlib.sha256(identity).hexdigest()[:16] != snapshot_id:
        raise SourceMaterialError("pinned source manifest digest differs from its identity")
    return record


def verify_pinned_material(
    root: Path, snapshot_id: str, *, registry: KnownSecretRegistry | None = None
) -> dict[str, Any]:
    record = read_metadata(root, snapshot_id, registry=registry)
    seen: set[str] = set()
    for item in record["files"]:
        digest, size = item["sha256"], item["size"]
        if digest in seen:
            continue
        seen.add(digest)
        path = root / "snapshots/blobs" / digest
        reject_links(path)
        try:
            if not path.is_file() or path.stat().st_size != size:
                raise SourceMaterialError("pinned source blob is missing or has a different size")
            with path.open("rb") as stream:
                actual, actual_size = copy_unchanged_safe_bytes(stream, registry=registry)
        except (OSError, UnsafeMaterialError) as error:
            raise SourceMaterialError(
                "pinned source blob cannot preserve safe byte identity"
            ) from error
        if actual != digest or actual_size != size:
            raise SourceMaterialError("pinned source blob digest/size mismatch")
    return record


def verify_source_reference(
    root: Path, body: Mapping[str, object], project_id: str, *, require_verified: bool = False
) -> set[str]:
    """Validate an explicit business snapshot; ordinary inline hashes are ignored."""
    if not {"pinned_manifest_digest", "pinned_snapshot_id"}.intersection(body):
        if require_verified:
            raise SourceMaterialError("legacy source has no verified pinned byte reference")
        return set()
    snapshot_id = body.get("pinned_snapshot_id")
    if (
        not isinstance(snapshot_id, str)
        or not isinstance(body.get("pinned_manifest_digest"), str)
        or body.get("project_id") != project_id
    ):
        raise SourceMaterialError("pinned source business reference has unknown owner/identity")
    record = verify_pinned_material(root, snapshot_id)
    canonical = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    expected_files = [
        {
            "relative_path": f["relative_path"],
            "size": f["size"],
            "content_digest": "sha256:" + f["sha256"],
        }
        for f in record["files"]
    ]
    selection, exclusions = body.get("selected_paths"), body.get("exclusion_rules")
    if (
        body["pinned_manifest_digest"] != "sha256:" + hashlib.sha256(canonical).hexdigest()
        or body.get("files") != expected_files
        or body.get("purpose") != record["purpose"]
        or not isinstance(selection, (list, tuple))
        or list(selection) != record["selected_paths"]
        or not isinstance(exclusions, (list, tuple))
        or list(exclusions) != record["exclusion_rules"]
    ):
        raise SourceMaterialError("business source snapshot differs from actual pinned material")
    return {
        metadata_path(root, snapshot_id).relative_to(root).as_posix(),
        *("snapshots/blobs/" + f["sha256"] for f in record["files"]),
    }


def source_record_files(
    root: Path,
    kind: str,
    body: Mapping[str, object],
    project_id: str,
    authority: Mapping[str, Any],
    *,
    require_verified: bool,
) -> set[str]:
    """Follow only explicit frozen source consumers; historical gaps are not new proof."""
    if kind == "source_snapshot":
        # Domain-only legacy snapshots may be stored, but cannot prove a new preparation.
        return verify_source_reference(root, body, project_id)
    if kind == "source_pin_intent" and body.get("schema_version") == "aitest.source-pin-intent/1.0":
        inputs, result = body.get("inputs"), body.get("result")
        if (
            not isinstance(inputs, Mapping)
            or not isinstance(result, Mapping)
            or body.get("project_id") != project_id
            or inputs.get("project_id") != project_id
            or _body_digest(inputs) != body.get("digest")
        ):
            raise SourceMaterialError("source intent frozen input/owner/digest is unverified")
        source = _owned_record(
            authority,
            "source_snapshot",
            result.get("snapshot_id"),
            result.get("record_revision"),
            project_id,
        )
        if (
            any(
                source.get(key) != result.get(key)
                for key in (
                    "snapshot_id",
                    "content_identity",
                    "pinned_snapshot_id",
                    "purpose",
                    "binding_id",
                )
            )
            or type(source.get("binding_revision")) is not int
            or type(result.get("binding_revision")) is not int
            or source.get("binding_revision") != result.get("binding_revision")
            or not isinstance(result.get("binding_id"), str)
            or not result.get("binding_id")
            or inputs.get("binding_id") != result.get("binding_id")
            or type(inputs.get("binding_revision")) is not int
            or inputs.get("binding_revision") != result.get("binding_revision")
            or inputs.get("purpose") != result.get("purpose")
        ):
            raise SourceMaterialError("source intent result differs from exact source")
        reference = result.get("source_current_ref")
        expected = inputs.get("expected_revision")
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"record_id", "record_revision"}
            or type(expected) is not int
            or expected < 0
            or type(reference.get("record_revision")) is not int
            or reference.get("record_revision") != expected + 1
            or reference.get("record_id")
            != "source-current-" + _body_digest([project_id, result["binding_id"]])[7:]
        ):
            raise SourceMaterialError("source intent original pointer is missing")
        pointer = _owned_record(
            authority,
            "source_binding_current",
            reference.get("record_id"),
            reference.get("record_revision"),
            project_id,
        )
        if pointer != {"project_id": project_id, **result}:
            raise SourceMaterialError("source intent original pointer differs from its result")
        return verify_source_reference(root, source, project_id, require_verified=True)
    if kind == "delivery_submission":
        if (
            body.get("schema_version") != "aitest.delivery-submission/1.0"
            or body.get("status") != "submitted"
            or body.get("project_id") != project_id
        ):
            raise SourceMaterialError("formal delivery source owner/schema is unverified")
        source = _owned_record(
            authority,
            "source_snapshot",
            body.get("snapshot_id"),
            body.get("snapshot_record_revision"),
            project_id,
        )
        if (
            source.get("snapshot_id") != body.get("snapshot_id")
            or source.get("content_identity") != body.get("content_identity")
            or source.get("binding_id") != body.get("binding_id")
            or type(body.get("binding_record_revision")) is not int
            or type(source.get("binding_revision")) is not int
            or source.get("binding_revision") != body.get("binding_record_revision")
        ):
            raise SourceMaterialError("formal delivery differs from its exact frozen source")
        return verify_source_reference(root, source, project_id, require_verified=True)
    model_basis = body.get("generation_basis") if kind == "generated_content" else None
    if kind == "model_outbound_request":
        generation = body.get("generation_identity")
        if isinstance(generation, Mapping) and "basis_identity" in generation:
            if body.get("state") == "outcome" and body.get("response_currency") in {
                "source_changed",
                "superseded_by_manual",
            }:
                # Preserve the safe response and original refs without certifying lost source.
                return set()
            model_basis = generation["basis_identity"]
    if model_basis is not None:
        if not isinstance(model_basis, Mapping):
            raise SourceMaterialError("model source basis is unverified")
        ref = model_basis.get("source_ref")
        expected_source_revision = ref.get("record_revision") if isinstance(ref, Mapping) else None
        if kind == "model_outbound_request":
            declared = body.get("source_revision")
            manual = model_basis.get("manual_ref")
            expected_manual = manual.get("record_revision") if isinstance(manual, Mapping) else 0
            if (
                type(declared) is not int
                or declared != (expected_source_revision if ref is not None else 0)
                or type(body.get("base_manual_revision")) is not int
                or body.get("base_manual_revision") != expected_manual
            ):
                raise SourceMaterialError(
                    "model source/manual revision differs from frozen reference"
                )
        else:
            context = body.get("revision_context")
            if (
                not isinstance(context, Mapping)
                or context.get("source_revision") != expected_source_revision
            ):
                raise SourceMaterialError("generated source revision differs from frozen reference")
        return _model_basis_files(root, model_basis, project_id, authority, require_verified)
    if (
        kind == "execution_intent"
        and body.get("schema_version") == "aitest.run-registration-intent/1.0"
    ):
        prepared = _owned_record(
            authority, "prepared_run", body.get("prepared_run_id"), 1, project_id
        )
        digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    prepared,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        )
        if digest != body.get("fingerprint"):
            raise SourceMaterialError("run registration source preparation digest mismatch")
        return source_record_files(
            root, "prepared_run", prepared, project_id, authority, require_verified=require_verified
        )
    if (
        kind != "prepared_run"
        or body.get("schema_version") != "aitest.prepared-run/1.0"
        or body.get("status") != "prepared"
    ):
        return set()
    if body.get("project_id") != project_id:
        raise SourceMaterialError("prepared source reference belongs to another project")
    ref = body.get("snapshot")
    if not isinstance(ref, Mapping):
        raise SourceMaterialError("prepared source snapshot reference is absent")
    try:
        source = _owned_record(
            authority,
            "source_snapshot",
            ref.get("source_snapshot_id"),
            ref.get("record_revision"),
            project_id,
        )
    except SourceMaterialError:
        if require_verified:
            raise
        return set()
    if source.get("content_identity") != ref.get("content_identity") or source.get(
        "purpose"
    ) != ref.get("purpose"):
        raise SourceMaterialError("prepared source snapshot identity/purpose mismatch")
    return verify_source_reference(root, source, project_id, require_verified=require_verified)


def _body_digest(body: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        ).hexdigest()
    )


def _model_basis_files(
    root: Path, basis: object, project_id: str, authority: Mapping[str, Any], require_verified: bool
) -> set[str]:
    if (
        not isinstance(basis, Mapping)
        or basis.get("schema_version") != "aitest.model-generation-basis/1.0"
    ):
        raise SourceMaterialError("model source basis schema is unverified")
    manual = basis.get("manual_ref")
    if manual is not None:
        if not isinstance(manual, Mapping) or type(manual.get("record_revision")) is not int:
            raise SourceMaterialError("model manual source reference is unverified")
        revision = manual["record_revision"]
        record_id = manual.get("record_id")
        if not isinstance(record_id, str) or not record_id or revision < 0:
            raise SourceMaterialError("model manual source identity is unverified")
        rows = authority["records"].get("generated_content", {}).get(record_id, [])
        if len(rows) != revision:
            raise SourceMaterialError("model manual source current revision differs")
        if revision > 0:
            saved = _owned_record(authority, "generated_content", record_id, revision, project_id)
            text = saved.get("draft_text")
            if (
                not isinstance(text, str)
                or "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
                != manual.get("content_digest")
                or saved.get("content_digest") != manual.get("content_digest")
                or _body_digest(saved) != basis.get("manual_record_digest")
            ):
                raise SourceMaterialError("model manual source content digest differs")
        elif (
            manual.get("content_digest") is not None
            or basis.get("manual_record_digest") is not None
        ):
            raise SourceMaterialError("absent model manual source has an invented digest")
    ref = basis.get("source_ref")
    if ref is None:
        if basis.get("source_record_digest") is not None or basis.get("binding_ref") is not None:
            raise SourceMaterialError("absent model source has an invented reference")
        return set()
    if not isinstance(ref, Mapping):
        raise SourceMaterialError("model source snapshot reference is unverified")
    source = _owned_record(
        authority,
        "source_snapshot",
        ref.get("source_snapshot_id"),
        ref.get("record_revision"),
        project_id,
    )
    if (
        _body_digest(source) != basis.get("source_record_digest")
        or source.get("content_identity") != ref.get("content_identity")
        or source.get("purpose") != ref.get("purpose")
    ):
        raise SourceMaterialError("model source snapshot identity/digest differs")
    bound = basis.get("binding_ref")
    if not isinstance(bound, Mapping):
        raise SourceMaterialError("model source binding reference is unverified")
    binding = _owned_record(
        authority, "binding", bound.get("record_id"), bound.get("record_revision"), project_id
    )
    if (
        _body_digest(binding) != bound.get("body_digest")
        or source.get("binding_id") != bound.get("record_id")
        or source.get("binding_revision") != bound.get("record_revision")
    ):
        raise SourceMaterialError("model source binding digest differs")
    return verify_source_reference(root, source, project_id, require_verified=require_verified)


def _owned_record(
    authority: Mapping[str, Any], kind: str, record_id: object, revision: object, project_id: str
) -> dict[str, Any]:
    if not isinstance(record_id, str) or not record_id or type(revision) is not int or revision < 1:
        raise SourceMaterialError("frozen source record reference identity/revision is invalid")
    rows = authority["records"].get(kind, {}).get(record_id, [])
    if revision > len(rows):
        raise SourceMaterialError("frozen source record reference is unavailable")
    body = rows[revision - 1]
    metadata = getattr(rows, "metadata", {})
    if (
        body.get("project_id") != project_id
        or metadata
        and metadata.get("project_id") != project_id
    ):
        raise SourceMaterialError("frozen source record reference project cannot be verified")
    return dict(body)
