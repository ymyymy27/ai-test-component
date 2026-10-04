"""Version 2 manifest material, preserving version 1 historical bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .business_changes import BusinessChangeIndex
from .sharded_records import find_commit, open_authority

SCHEMA = "aitest.commit-manifest/2"
FIELDS = {
    "commit_id",
    "generation",
    "parent_commit",
    "changed_records",
    "event_range",
    "instance_id",
    "idempotency_result_ref",
    "file_digests",
    "business_change_index_root",
}
OPTIONAL_FIELDS = {"source_material_files"}


def validate_complete(root: Path, value: dict[str, Any]) -> None:
    from .commit_manifest import CommitMaterialError, _text

    if (
        value["commit_id"] != value["commit_sequence"]
        or type(value["commit_id"]) is not int
        or value["generation"] != value["generation_id"]
        or value["parent_commit"] != value["parent_manifest"]
        or value["changed_records"] != value["created"]
        or not _text(value["instance_id"], maximum=128)
        or not isinstance(value["event_range"], dict)
        or set(value["event_range"]) != {"first", "last"}
    ):
        raise CommitMaterialError("canonical manifest identity aliases disagree")
    interval = value["event_range"]
    last = value["event_root"]["last_sequence"]
    expected = {
        "first": last - len(value["created"]) + 1 if value["created"] else None,
        "last": last if value["created"] else None,
    }
    if interval != expected or any(type(v) is not int for v in interval.values() if v is not None):
        raise CommitMaterialError("canonical event range disagrees with changed records")
    BusinessChangeIndex(root, value["business_change_index_root"])
    refs = value["file_digests"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 4096:
        raise CommitMaterialError("invalid canonical file digest set")
    paths = []
    for ref in refs:
        _validate_ref(ref)
        paths.append(ref["path"])
    if paths != sorted(set(paths)):
        raise CommitMaterialError("canonical file digest references are unordered or duplicate")
    source_files = value.get("source_material_files")
    if "source_material_files" in value and (
        not isinstance(source_files, list)
        or any(not isinstance(p, str) for p in source_files)
        or source_files != sorted(set(source_files))
        or len(source_files) > 4096
    ):
        raise CommitMaterialError("invalid pinned source material file set")
    result = value["idempotency_result_ref"]
    if value["operation"] == "business":
        _validate_ref(result)
        if not result["path"].startswith("transactions/results/") or result not in refs:
            raise CommitMaterialError("canonical commit lacks its exact idempotency result")
    elif result is not None:
        raise CommitMaterialError("maintenance cannot create a business idempotency result")


def _validate_ref(ref: Any) -> None:
    from .commit_manifest import CommitMaterialError, _hex, _integer

    if (
        not isinstance(ref, dict)
        or set(ref) != {"path", "sha256", "size"}
        or not isinstance(ref["path"], str)
        or "\\" in ref["path"]
        or Path(ref["path"]).is_absolute()
        or Path(ref["path"]).drive
        or any(part in ("", ".", "..") for part in ref["path"].split("/"))
        or not ref["path"].startswith(
            ("record-store/", "indexes/", "event-log/pages/", "transactions/results/", "snapshots/")
        )
        or not _hex(ref["sha256"])
        or not _integer(ref["size"])
        or ref["size"] > 16 * 1024 * 1024
        and not ref["path"].startswith("snapshots/blobs/")
    ):
        raise CommitMaterialError("invalid canonical file reference")
    if ref["path"].startswith("snapshots/"):
        name = ref["path"]
        if name.startswith("snapshots/blobs/"):
            if (
                not _hex(name.removeprefix("snapshots/blobs/"))
                or ref["sha256"] != name.rsplit("/", 1)[-1]
            ):
                raise CommitMaterialError("invalid source blob file reference")
        else:
            from .source_material import metadata_path

            if (
                not name.endswith(".json")
                or metadata_path(
                    Path("."), name.removeprefix("snapshots/").removesuffix(".json")
                ).as_posix()
                != name
            ):
                raise CommitMaterialError("invalid source metadata file reference")


def source_files(root: Path, value: dict[str, Any]) -> set[str]:
    from .source_material import source_record_files

    data = open_authority(root, value["record_header"])
    paths: set[str] = set()
    for ref in value["created"]:
        rows = data["records"].get(ref["aggregate_kind"], {}).get(ref["record_id"], [])
        body = rows[ref["revision"] - 1]
        paths.update(
            source_record_files(
                root, ref["aggregate_kind"], body, value["project_id"], data, require_verified=True
            )
        )
    return paths


def required_files(root: Path, value: dict[str, Any]) -> set[str]:
    """Only roots and paths needed to prove this batch; never enumerate history."""
    from .commit_manifest import FileCommitStore, _compare
    from .index import _POINT_FAMILY, FileQueryIndex
    from .ordered_index import OrderedIndexTree

    paths = {f"record-store/{value['record_header']['root']}.json"}
    data = open_authority(root, value["record_header"])
    for ref in value["created"]:
        rows = data["records"].get(ref["aggregate_kind"], {}).get(ref["record_id"], [])
        rows[ref["revision"] - 1]
    if value["operation"] == "business":
        find_commit(
            data["_tree"],
            field="request_id",
            project_id=value["project_id"],
            value=value["request_id"],
        )
    paths.update(f"record-store/{digest}.json" for digest in data["_tree"]._cache)
    index = value["index_root"]
    name = f"indexes/roots/{index['snapshot_root']}.json"
    paths.add(name)
    store = FileCommitStore(root)
    snapshot = store._decode(store._read_bytes(root / name, 256 * 1024))
    query = FileQueryIndex(root, detached=True, snapshot_meta=index)
    for kind, meta in [*snapshot["families"].items(), ("latest-keys", snapshot["latest_keys"])]:
        tree = query._directory(kind)._tree(meta)
        if tree.root is not None:
            tree.read(tree.root)
        if kind == _POINT_FAMILY:
            for ref in value["created"]:
                tree.get(
                    (value["project_id"], ref["aggregate_kind"], ref["record_id"], ref["revision"])
                )
        paths.update((tree.directory / file).relative_to(root).as_posix() for file in tree._cache)
    events = OrderedIndexTree(
        root / "event-log/pages", _compare, leaf_size=128, root=value["event_root"]["root"]
    )
    if events.root is not None:
        events.read(events.root)
    if value["created"]:
        first = value["event_root"]["last_sequence"] - len(value["created"]) + 1
        for number in range(first, value["event_root"]["last_sequence"] + 1):
            events.get((number,))
    paths.update(f"event-log/pages/{file}" for file in events._cache)
    business = BusinessChangeIndex(root, value["business_change_index_root"])
    for kind in business.header["types"]:
        tree = business.tree(kind)
        if tree.root is not None:
            tree.read(tree.root)
        for ref in value["created"]:
            if ref["aggregate_kind"] == kind:
                tree.get(
                    (
                        value["commit_sequence"],
                        value["workspace_id"],
                        value["project_id"],
                        ref["record_id"],
                        ref["revision"],
                    )
                )
        paths.update((tree.directory / file).relative_to(root).as_posix() for file in tree._cache)
    if value["idempotency_result_ref"] is not None:
        paths.add(value["idempotency_result_ref"]["path"])
    if "source_material_files" in value:
        declared = set(value["source_material_files"])
        if declared != source_files(root, value):
            raise ValueError("pinned source material list differs from changed record references")
        paths.update(declared)
    return paths


def file_reference(root: Path, path: str) -> dict[str, Any]:
    from .commit_manifest import FileCommitStore

    if path.startswith("snapshots/blobs/"):
        FileCommitStore.reject_links(root / path)
        digest = hashlib.sha256()
        size = 0
        with (root / path).open("rb") as stream:
            while block := stream.read(65536):
                digest.update(block)
                size += len(block)
        return {"path": path, "sha256": digest.hexdigest(), "size": size}
    raw = FileCommitStore(root)._read_bytes(root / path, 16 * 1024 * 1024)
    return {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def complete_manifest(
    root: Path,
    value: dict[str, Any],
    *,
    instance_id: str,
    business_root: dict[str, Any],
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .commit_manifest import canonical_bytes
    from .publication_backend import FilePublicationBackend

    result_ref = None
    if result is not None:
        from aitest.infrastructure.security import guard_bytes, guard_value

        raw = canonical_bytes(result)
        safe, changed = guard_value(result)
        filtered, bytes_changed = guard_bytes(raw)
        if changed or safe != result or bytes_changed or filtered != raw:
            raise ValueError("idempotency result identity cannot be safely preserved")
        digest = hashlib.sha256(raw).hexdigest()
        path = f"transactions/results/{digest}.json"
        FilePublicationBackend(root).publish_immutable(root / path, raw)
        result_ref = {"path": path, "sha256": digest, "size": len(raw)}
    last = value["event_root"]["last_sequence"]
    value = {
        **value,
        "schema": SCHEMA,
        "commit_id": value["commit_sequence"],
        "generation": value["generation_id"],
        "parent_commit": value["parent_manifest"],
        "changed_records": value["created"],
        "instance_id": instance_id,
        "event_range": {
            "first": last - len(value["created"]) + 1 if value["created"] else None,
            "last": last if value["created"] else None,
        },
        "idempotency_result_ref": result_ref,
        "business_change_index_root": business_root,
    }
    value["source_material_files"] = sorted(source_files(root, value))
    value["file_digests"] = [
        file_reference(root, name) for name in sorted(required_files(root, value))
    ]
    return value


def verify_complete(root: Path, value: dict[str, Any]) -> None:
    from .commit_manifest import CommitMaterialError, FileCommitStore

    validate_complete(root, value)
    actual = set()
    for ref in value["file_digests"]:
        if file_reference(root, ref["path"]) != ref:
            raise CommitMaterialError("canonical commit file bytes changed")
        actual.add(ref["path"])
    if not required_files(root, value) <= actual:
        raise CommitMaterialError("canonical file digests omit required commit material")
    index = BusinessChangeIndex(root, value["business_change_index_root"])
    index.verify(high_water=value["commit_sequence"])
    if value["operation"] == "business":
        parent = FileCommitStore(root).read_manifest(value["parent_manifest"])
        if parent["schema"] == SCHEMA:
            index.verify_extension(
                BusinessChangeIndex(root, parent["business_change_index_root"]), value["created"]
            )
        index.verify_changes(
            value["created"],
            commit_sequence=value["commit_sequence"],
            workspace_id=value["workspace_id"],
            project_id=value["project_id"],
        )
        result = FileCommitStore(root)._decode(
            FileCommitStore(root)._read_bytes(
                root / value["idempotency_result_ref"]["path"], 1024 * 1024
            )
        )
        expected = {
            "schema": "aitest.idempotency-result/1",
            "state": "committed",
            **{
                k: value[k]
                for k in (
                    "request_id",
                    "intent_id",
                    "workspace_id",
                    "project_id",
                    "writer_epoch",
                    "instance_id",
                    "commit_sequence",
                )
            },
            "created": [
                [r["aggregate_kind"], r["record_id"], r["revision"]] for r in value["created"]
            ],
        }
        if result != expected:
            raise CommitMaterialError("canonical idempotency result does not prove this commit")
