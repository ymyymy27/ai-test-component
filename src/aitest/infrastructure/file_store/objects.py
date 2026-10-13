"""Content-addressed project object storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.infrastructure import path_compat as compat
from aitest.infrastructure.file_store.object_chunks import (
    DEFAULT_CHUNK_SIZE,
    ChunkManifest,
    build_chunk_manifest,
    manifest_from_payload,
    manifest_to_payload,
)

from ..security import KnownSecretRegistry, guard_bytes, known_secrets


def _safe_component(value: str, name: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"{name} must be a safe path component")
    if any(char in value for char in '\\/:*?"<>|'):
        raise ValueError(f"{name} must be a safe path component")
    return value


def _reject_links(path: Path) -> None:
    if any(item.is_symlink() or compat.is_junction(item) for item in (path, *path.parents)):
        raise ValueError("object path cannot traverse a link")


class FileObjectStore:
    """Store immutable bytes under objects/<project>/<sha256>.

    落盘前安全底线（A-09）：内容先经过不可关闭的凭据过滤，摘要与对象
    一律基于**过滤后字节**；过滤前字节从不进入临时文件或内容寻址区。
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        registry: KnownSecretRegistry | None = None,
    ) -> None:
        _reject_links(workspace_root)
        self._root = workspace_root.resolve()
        self._registry = registry if registry is not None else known_secrets()

    def publish_bytes(
        self,
        project_id: str,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
    ) -> StoredObjectRef:
        # 必须在任何临时文件创建之前完成过滤：调用方拿到的 digest/大小
        # 只描述可安全保存的字节（架构02第13节）。
        content, _changed = guard_bytes(content, self._registry)
        safe_project = _safe_component(project_id, "project_id")
        digest_hex = hashlib.sha256(content).hexdigest()
        digest = "sha256:" + digest_hex
        relative = Path("objects") / safe_project / digest_hex
        _reject_links(self._root / relative)
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("object path escapes workspace root")
        self._write_immutable(path, content)
        return StoredObjectRef(
            project_id=safe_project,
            digest=digest,
            size=len(content),
            media_type=media_type,
            relative_path=relative.as_posix(),
        )

    def read_bytes(self, ref: StoredObjectRef, *, max_bytes: int | None = None) -> bytes:
        path = self._object_path(ref)
        if max_bytes is not None and (max_bytes < 0 or ref.size > max_bytes):
            raise ValueError("object exceeds its explicit read budget")
        with path.open("rb") as handle:
            content = handle.read(ref.size + 1)
        if "sha256:" + hashlib.sha256(content).hexdigest() != ref.digest:
            raise ValueError("object digest mismatch")
        if len(content) != ref.size:
            raise ValueError("object size mismatch")
        return content

    def read_range(
        self,
        ref: StoredObjectRef,
        *,
        offset: int,
        length: int,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> bytes:
        """按需范围读取（架构 04 §12）：**只读取并验证触及块**。

        顺序：

        1. 校验归属（项目/摘要/大小/路径）——与 `read_bytes` 同一套底线；
        2. 取受信块清单：清单必须与 `ref` 的完整摘要一致；**没有受信清单的旧对象先完整校验
           并建立可复用结果**（写回清单），不能每个分页重复哈希整个对象；
        3. 只读覆盖该范围的块并逐块验证摘要；任一不符即**立即失效**（删除清单）并拒绝。
        """

        path = self._object_path(ref)
        manifest_path = path.with_name(path.name + ".chunks.json")
        if not path.is_file():
            raise ValueError("object bytes are unavailable")
        manifest = self._trusted_manifest(
            ref, path=path, manifest_path=manifest_path, chunk_size=chunk_size
        )
        touched = manifest.chunks_for(offset=offset, length=length)
        if not touched:
            return b""
        result = bytearray()
        with path.open("rb") as handle:
            for chunk in touched:
                handle.seek(chunk.offset)
                block = handle.read(chunk.length)
                if (
                    len(block) != chunk.length
                    or "sha256:" + hashlib.sha256(block).hexdigest() != chunk.digest
                ):
                    # 仅 mtime/大小相同不能证明内容不变：块摘要不符即失效清单。
                    with suppress(OSError):
                        manifest_path.unlink()
                    raise ValueError("object chunk digest mismatch")
                result += block
        start = offset - touched[0].offset
        return bytes(result[start : start + length])

    def _object_path(self, ref: StoredObjectRef) -> Path:
        safe_project = _safe_component(ref.project_id, "project_id")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", ref.digest):
            raise ValueError("object digest identity is invalid")
        if type(ref.size) is not int or ref.size < 0:
            raise ValueError("object size must be a nonnegative integer")
        relative = Path("objects") / safe_project / ref.digest.removeprefix("sha256:")
        if ref.relative_path != relative.as_posix():
            raise ValueError("object reference does not match its project/digest path")
        _reject_links(self._root / relative)
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("object path escapes workspace root")
        return path

    def _trusted_manifest(
        self,
        ref: StoredObjectRef,
        *,
        path: Path,
        manifest_path: Path,
        chunk_size: int,
    ) -> ChunkManifest:
        """受信清单：绑定实际内容摘要、块清单与受控对象身份；不可信即重建。"""

        _reject_links(manifest_path)
        if manifest_path.exists():
            with manifest_path.open("r", encoding="utf-8") as handle:
                try:
                    manifest = manifest_from_payload(json.loads(handle.read()))
                except (ValueError, TypeError):
                    manifest = None
            if (
                manifest is not None
                and manifest.digest == ref.digest
                and manifest.size == ref.size
                and manifest.chunk_size == chunk_size
            ):
                return manifest
            with suppress(OSError):
                manifest_path.unlink()
        # 旧对象/清单不可信：完整校验后建立可复用结果。
        content = self.read_bytes(ref)
        manifest = build_chunk_manifest(content, chunk_size=chunk_size)
        self._write_manifest(manifest_path, manifest)
        return manifest

    @staticmethod
    def _write_manifest(manifest_path: Path, manifest: ChunkManifest) -> None:
        payload = json.dumps(
            manifest_to_payload(manifest), ensure_ascii=True, sort_keys=True
        ).encode("utf-8")
        FileObjectStore._write_immutable(manifest_path, payload)

    @staticmethod
    def _write_immutable(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open("rb") as handle:
                existing = handle.read(len(content) + 1)
            if existing != content:
                raise ValueError("content-addressed object conflicts")
            return
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temporary)
            raise


__all__ = ["FileObjectStore"]
