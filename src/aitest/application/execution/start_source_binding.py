"""C 侧 start 来源绑定解析：把 A 的物化映射变成 `source_binding_digest`。

依据现行接口合同：

- `docs/接口对接/进行中/AB-001-端口与保存/contract.md` 第 3.4 节（1.34）：`materialize`
  成功时返回 `paths`（期望来源路径→workdir 实际路径，含 `sha256`/`size`）与 `content_digest`；
- `docs/接口对接/进行中/BC-001-PreparedRun/contract.md` 第 6 节：
  `source_binding_digest` 由 **C 在 start** 计算，是"在固定 workdir 中解析出的
  期望→实际路径映射"摘要，**不可**与 B 在 prepare 冻结的 `resolved_input_digest` 合并或互替。

本组件只解析并**独立**核对映射，不启动执行、不写业务记录、不声称来源通过：
入口/解释器/实际加载来源是否与冻结绑定相符，仍由既有来源核对决定。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from aitest.application.ports import SourceSnapshotPort

_DIGEST_PREFIX = "sha256:"
_HASH_BLOCK = 1024 * 1024


class SourceBindingUnverified(ValueError):
    code = "SOURCE_BINDING_UNVERIFIED"


class StartSourceBindingResolver:
    """在 start 时把物化结果解析为可核对的来源绑定事实。"""

    def __init__(self, snapshots: SourceSnapshotPort) -> None:
        self._snapshots = snapshots

    def resolve(
        self,
        *,
        snapshot_id: str,
        destination: str,
        expected_relative_paths: Sequence[str] | None = None,
    ) -> Mapping[str, object]:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise SourceBindingUnverified("start source binding requires a saved snapshot identity")
        if not isinstance(destination, str) or not destination.strip():
            raise SourceBindingUnverified("start source binding requires a fixed workdir")
        result = self._snapshots.materialize(snapshot_id, destination)
        if not isinstance(result, Mapping):
            raise SourceBindingUnverified("materialization did not return a verifiable result")
        if result.get("state") != "materialized" or result.get("verified") is not True:
            refused = result.get("refused")
            detail = (
                ",".join(sorted(str(item) for item in refused))
                if isinstance(refused, (list, tuple))
                else ""
            )
            raise SourceBindingUnverified(f"materialization is not verified: {detail}")
        returned_snapshot = result.get("snapshot_id")
        if returned_snapshot != snapshot_id:
            raise SourceBindingUnverified("materialization belongs to another snapshot")
        workdir = str(result.get("destination") or "")
        if not workdir:
            raise SourceBindingUnverified("materialization lacks its actual workdir")
        entries = _entries(result.get("paths"))
        recorded = result.get("content_digest")
        if not isinstance(recorded, str) or not _is_digest(recorded):
            raise SourceBindingUnverified("materialization lacks an exact mapping digest")
        recomputed = _mapping_digest(entries)
        if recomputed != recorded:
            raise SourceBindingUnverified("mapping digest differs from the materialization claim")
        root = Path(workdir)
        if root != Path(destination).resolve():
            raise SourceBindingUnverified(
                "materialization workdir differs from the requested fixed workdir"
            )
        for entry in entries:
            actual = Path(str(entry["actual_path"]))
            digest, size = entry["sha256"], entry["size"]
            if not isinstance(digest, str) or type(size) is not int:
                raise SourceBindingUnverified("mapping entry digest or size is not exact")
            if not actual.is_absolute() or not actual.is_relative_to(root):
                raise SourceBindingUnverified("mapped actual path escapes the fixed workdir")
            _verify_file(actual, digest, size)
        if expected_relative_paths is not None:
            _require_exact_coverage(entries, expected_relative_paths)
        return {
            "snapshot_id": snapshot_id,
            "workdir": root.as_posix(),
            "paths": entries,
            "source_binding_digest": recomputed,
        }


def _require_exact_coverage(
    entries: list[dict[str, object]], expected_relative_paths: Sequence[str]
) -> None:
    """调用方给出冻结期望时，映射必须**恰好**覆盖它：不缺项、不多项、不重复。"""
    expected: list[str] = []
    for item in expected_relative_paths:
        if not isinstance(item, str) or not item.strip():
            raise SourceBindingUnverified("expected relative paths must be nonempty text")
        expected.append(item)
    if len(set(expected)) != len(expected):
        raise SourceBindingUnverified("expected relative paths must be unique")
    actual = [str(entry["relative_path"]) for entry in entries]
    if sorted(actual) != sorted(expected):
        raise SourceBindingUnverified(
            "resolved mapping does not cover the frozen expected source paths exactly"
        )


def _entries(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SourceBindingUnverified("materialization lacks its expected-to-actual path mapping")
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    actuals: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise SourceBindingUnverified("mapping entries must be mappings")
        relative, actual = item.get("relative_path"), item.get("actual_path")
        digest, size = item.get("sha256"), item.get("size")
        if (
            not isinstance(relative, str)
            or not relative.strip()
            or relative.startswith(("/", "\\"))
            or ".." in Path(relative).parts
            or not isinstance(actual, str)
            or not actual.strip()
            or not isinstance(digest, str)
            or not _is_digest(digest)
            or type(size) is not int
            or size < 0
        ):
            raise SourceBindingUnverified(
                "mapping entry identity, type or digest cannot be verified"
            )
        if relative in seen:
            raise SourceBindingUnverified("mapping entries must be unique per expected path")
        seen.add(relative)
        actual_key = _actual_key(actual)
        if actual_key in actuals:
            raise SourceBindingUnverified(
                "two expected source paths must not resolve to the same actual file"
            )
        actuals.add(actual_key)
        entries.append(
            {"relative_path": relative, "actual_path": actual, "sha256": digest, "size": size}
        )
    return entries


def _actual_key(actual: str) -> str:
    """实际路径的归属键；Windows 大小写不敏感，避免同一文件被当成两个来源。"""
    text = Path(actual).as_posix()
    return text.casefold() if os.name == "nt" else text


def _verify_file(path: Path, digest: str, size: int) -> None:
    """有界核对：读取至多 ``size + 1`` 字节，长度与摘要都必须精确相符。"""
    try:
        if not path.is_file() or path.is_symlink():
            raise SourceBindingUnverified("mapped actual path is not a regular file")
        if os.stat(path).st_nlink != 1:
            raise SourceBindingUnverified(
                "mapped actual path shares its bytes with material outside the workdir"
            )
        hasher = hashlib.sha256()
        read = 0
        with path.open("rb") as handle:
            while block := handle.read(min(_HASH_BLOCK, size + 1 - read)):
                hasher.update(block)
                read += len(block)
                if read > size:
                    break
    except OSError as error:
        raise SourceBindingUnverified("mapped actual path cannot be read") from error
    if read != size or _DIGEST_PREFIX + hasher.hexdigest() != digest:
        raise SourceBindingUnverified("mapped actual bytes differ from their exact reference")


def _mapping_digest(entries: list[dict[str, object]]) -> str:
    encoded = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return _DIGEST_PREFIX + hashlib.sha256(encoded).hexdigest()


def _is_digest(value: str) -> bool:
    if not value.startswith(_DIGEST_PREFIX):
        return False
    hexdigest = value[len(_DIGEST_PREFIX) :]
    return len(hexdigest) == 64 and all(char in "0123456789abcdef" for char in hexdigest)


__all__ = [
    "SourceBindingUnverified",
    "StartSourceBindingResolver",
]
