"""Read-only integrity inspection for the committed workspace boundary.

显式完整检查（非日常启动轻量路径）核对四件事：

1. **目录白名单**：工作空间根下只允许合同登记的文件/目录存在；符号链接
   出现在任何位置都视为越界（解析后仍可能逃出工作空间）。
2. **结构化材料可解析**：所有永久 ``*.json`` 必须是合法 JSON；永久
   JSONL 段（事件日志、各类审计/台账）逐行可解析，撕裂尾行显式报错。
3. **对象内容摘要**：``objects/<project>/<sha256>`` 字节摘要必须与名一致；
   ``snapshots/blobs/<sha256>`` 固定字节同样核对，且每个快照清单
   ``snapshots/<snapshot_id>.json`` 的 ``files[].sha256`` 必须在 blob 区可达。
4. **永久引用闭包**：记录、提交台账、事件、诊断、导出、报告、历史世代
   及附件链接中，**只有对象引用键**（``object_digest``、
   ``output_object_digest``、``artifact_digest``）携带的 ``sha256:<hex>``
   才构成对 ``objects/`` 的闭包引用；内联指纹（``content_digest``、
   ``projection_digest``、规则/计划 ``digest`` 等）只是对自身字节的校验
   值，不要求存在同名对象。spool 清单引用按输出流切片逐块核对摘要与游标
   边界。

活动暂存（``event-log/staging``）的撕裂字节属于崩溃后正常材料，由事件
日志恢复编排在对账阶段处理，不在本检查中判损坏；隐藏临时遗留（``.``
开头）由维护回收处理，也不在此判错。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Final

_DIGEST_RE: Final = re.compile(r"^sha256:([0-9a-f]{64})$")
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")

#: 指向 ``objects/<project>/<sha256>`` 内容寻址对象的引用键白名单。
#: 只有这些键携带的 ``sha256:<hex>`` 参与对象闭包核对；其余键名下的
#: 摘要均为内联指纹（内容/投影/规则/计划等），不要求存在同名对象（A-06）。
_OBJECT_REF_KEYS: Final = frozenset(
    {
        "object_digest",
        "output_object_digest",
        "artifact_digest",
    }
)

#: 根目录允许的永久/运行期文件。
_ALLOWED_TOP_FILES: Final = frozenset(
    {
        "workspace.json",
        "records.json",
        "indexes.json",
        "current.json",
        "commit.json",
        "events.json",
        "transactions.json",
        "writer.lock",
    }
)
#: 根目录允许的永久/运行期目录。
_ALLOWED_TOP_DIRS: Final = frozenset(
    {
        "objects",
        "spool",
        "checkpoints",
        "transactions",
        "events",
        "manifests",
        "event-log",
        "snapshots",
        "diagnostics",
        "exports",
        "migrations",
        "reports",
        "maintenance",
        "core",
        "workdirs",
        "generations",
        "outbox",
        "backup",
        "backups",
        "indexes",
    }
)

#: 活动暂存：崩溃后允许存在非完整行，由事件日志恢复对账处理。
_STAGING_PREFIX: Final = ("event-log", "staging")


def _iter_digest_refs(value: Any, *, key: str | None = None) -> list[str]:
    """递归收集对象引用键携带的 ``sha256:<hex>`` 永久对象引用。

    仅当字符串挂在白名单对象引用键下才计入闭包；内联摘要键（甚至任意
    其他键名）携带的同形字符串不构成对 ``objects/`` 的引用（A-06）。
    数组元素继承其所在字段的键名。
    """
    found: list[str] = []
    if isinstance(value, str):
        if key in _OBJECT_REF_KEYS:
            match = _DIGEST_RE.match(value)
            if match is not None:
                found.append(match.group(1))
    elif isinstance(value, dict):
        for child_key, nested in value.items():
            found.extend(_iter_digest_refs(nested, key=str(child_key)))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_iter_digest_refs(nested, key=key))
    return found


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _check_whitelist(root: Path, errors: list[str]) -> None:
    for child in root.iterdir():
        name = child.name
        # 隐藏文件一律是原子发布/锁遗留候选，由维护回收判定，不属越界。
        if name.startswith("."):
            continue
        if child.is_symlink():
            errors.append(f"unexpected symlink: {name}")
            continue
        allowed = (
            _ALLOWED_TOP_DIRS if child.is_dir() else _ALLOWED_TOP_FILES
        )
        if name not in allowed:
            kind = "directory" if child.is_dir() else "file"
            errors.append(f"unexpected {kind} outside whitelist: {name}")


def _verify_spool_manifest(
    path: Path,
    root: Path,
    errors: list[str],
) -> None:
    """核对 spool 清单：块摘要按流切片验证，游标不超过已持久化字节。"""
    location = _rel(path, root)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{location}: {exc}")
        return
    if not isinstance(manifest, dict):
        errors.append(f"{location}: spool manifest must be an object")
        return
    attempt_id = manifest.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id:
        errors.append(f"{location}: invalid attempt_id")
        return
    attempt_dir = root / "spool" / attempt_id
    stream_sizes: dict[str, int] = {}
    block_digests: dict[str, set[str]] = {}

    def _stream_size(stream_name: str) -> int:
        if stream_name not in stream_sizes:
            stream_path = attempt_dir / f"{stream_name}.log"
            stream_sizes[stream_name] = (
                stream_path.stat().st_size if stream_path.exists() else -1
            )
        return stream_sizes[stream_name]

    blocks = manifest.get("blocks", [])
    if not isinstance(blocks, list):
        errors.append(f"{location}: blocks must be a list")
        return
    for raw in blocks:
        if not isinstance(raw, dict):
            errors.append(f"{location}: block must be an object")
            continue
        stream_name = raw.get("stream_name")
        offset = raw.get("offset")
        length = raw.get("length")
        digest = raw.get("digest")
        if (
            not isinstance(stream_name, str)
            or not isinstance(offset, int)
            or not isinstance(length, int)
            or not isinstance(digest, str)
        ):
            errors.append(f"{location}: malformed block entry")
            continue
        match = _DIGEST_RE.match(digest)
        size = _stream_size(stream_name)
        if size < 0:
            errors.append(f"{location}: missing stream bytes for {stream_name}")
            continue
        if offset < 0 or length < 0 or offset + length > size:
            errors.append(
                f"{location}: block {stream_name}[{offset}:{offset + length}] "
                "exceeds durable stream bytes"
            )
            continue
        stream_path = attempt_dir / f"{stream_name}.log"
        with stream_path.open("rb") as handle:
            handle.seek(offset)
            actual = hashlib.sha256(handle.read(length)).hexdigest()
        if match is None or actual != match.group(1):
            errors.append(
                f"{location}: spool block digest mismatch for "
                f"{stream_name}@{offset}"
            )
        block_digests.setdefault(stream_name, set()).add(digest)

    cursors = manifest.get("cursors", [])
    if not isinstance(cursors, list):
        errors.append(f"{location}: cursors must be a list")
        return
    for raw in cursors:
        if not isinstance(raw, dict):
            errors.append(f"{location}: cursor must be an object")
            continue
        stream_name = raw.get("stream_name")
        last_digest = raw.get("last_committed_digest")
        offset = raw.get("offset")
        if not isinstance(stream_name, str) or not isinstance(offset, int):
            errors.append(f"{location}: malformed cursor entry")
            continue
        if not isinstance(last_digest, str) or last_digest not in (
            block_digests.get(stream_name, set())
        ):
            errors.append(
                f"{location}: cursor for {stream_name} does not match a "
                "sealed block digest"
            )
        size = _stream_size(stream_name)
        if size >= 0 and offset > size:
            errors.append(
                f"{location}: cursor for {stream_name} beyond durable bytes"
            )


def _check_permanent_record(
    path: Path,
    root: Path,
    errors: list[str],
    referenced: set[str],
) -> None:
    location = _rel(path, root)
    suffix = path.suffix.lower()
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        errors.append(f"{location}: {exc}")
        return
    if suffix == ".json":
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{location}: {exc}")
            return
        referenced.update(_iter_digest_refs(data))
    elif suffix == ".jsonl":
        for line_number, line in enumerate(
            raw_bytes.decode("utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{location}:{line_number}: {exc}")
                continue
            referenced.update(_iter_digest_refs(data))


def _verify_snapshot_manifests(
    root: Path,
    manifests: list[Path],
    blob_digests: set[str],
    errors: list[str],
) -> None:
    """每个快照清单的 files[].sha256 必须在 snapshots/blobs 可达（A-BACKUP-02）。"""
    for path in manifests:
        location = _rel(path, root)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{location}: {exc}")
            continue
        if not isinstance(record, dict):
            errors.append(f"{location}: snapshot manifest must be an object")
            continue
        files = record.get("files", [])
        if not isinstance(files, list):
            errors.append(f"{location}: snapshot files must be a list")
            continue
        for raw in files:
            if not isinstance(raw, dict):
                errors.append(f"{location}: snapshot file entry must be an object")
                continue
            relative = raw.get("relative_path")
            digest = raw.get("sha256")
            if not isinstance(relative, str) or not isinstance(digest, str):
                errors.append(f"{location}: malformed snapshot file entry")
                continue
            if not _HEX64_RE.match(digest):
                errors.append(f"{location}: invalid blob digest for {relative}")
            elif digest not in blob_digests:
                errors.append(
                    f"{location}: unreachable snapshot blob sha256:{digest} "
                    f"for {relative}"
                )


def check_workspace(root: Path) -> dict[str, object]:
    """对已提交工作空间边界执行显式完整检查（只读）。"""
    root = root.resolve()
    errors: list[str] = []
    referenced: set[str] = set()

    if root.exists():
        _check_whitelist(root, errors)

    objects = 0
    object_digests: set[str] = set()
    snapshot_blob_digests: set[str] = set()
    snapshot_manifests: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            errors.append(f"unexpected symlink: {_rel(path, root)}")
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        name = path.name

        # 隐藏临时遗留（.records.json.XXXX 等）归维护回收，不做结构核对。
        if name.startswith("."):
            continue

        if parts and parts[0] == "objects":
            # 仅接受 objects/<project>/<sha256> 形态。
            if len(parts) == 3 and _HEX64_RE.match(name):
                objects += 1
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                object_digests.add(name)
                if actual != name:
                    errors.append(f"digest mismatch: {_rel(path, root)}")
            else:
                errors.append(
                    f"object outside objects/<project>/<sha256>: {_rel(path, root)}"
                )
            continue

        # 快照固定字节：仅接受 snapshots/blobs/<sha256>，内容摘要必须与名一致。
        if parts and parts[0] == "snapshots":
            if len(parts) == 3 and parts[1] == "blobs":
                if _HEX64_RE.match(name):
                    actual = hashlib.sha256(path.read_bytes()).hexdigest()
                    snapshot_blob_digests.add(name)
                    if actual != name:
                        errors.append(f"digest mismatch: {_rel(path, root)}")
                else:
                    errors.append(
                        "snapshot blob outside snapshots/blobs/<sha256>: "
                        f"{_rel(path, root)}"
                    )
                continue
            if len(parts) == 2 and path.suffix.lower() == ".json":
                # 快照清单走专用 blob 闭包核对（files[].sha256 为裸 hex，
                # 不属于 objects/ 引用），不再进入通用永久记录扫描。
                snapshot_manifests.append(path)
                continue

        # 活动暂存允许撕裂，由事件日志恢复编排处理。
        if parts[:2] == _STAGING_PREFIX:
            continue

        if (
            parts[:1] == ("spool",)
            and len(parts) >= 2
            and name == "manifest.json"
        ):
            _verify_spool_manifest(path, root, errors)
            continue

        if path.suffix.lower() in {".json", ".jsonl"}:
            _check_permanent_record(path, root, errors, referenced)

    # 快照清单闭包：每个 files[].sha256 必须有可达且摘要一致的 blob。
    _verify_snapshot_manifests(
        root, snapshot_manifests, snapshot_blob_digests, errors
    )
    # 永久引用闭包：每条对象引用都必须有可达且摘要一致的对象。
    for digest in sorted(referenced - object_digests):
        errors.append(f"unreachable object reference: sha256:{digest}")
    return {"ok": not errors, "objects": objects, "errors": errors}


__all__ = ["check_workspace"]
