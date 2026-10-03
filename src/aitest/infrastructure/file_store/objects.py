"""Content-addressed project object storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from aitest.domain.evidence.evidence import StoredObjectRef

from ..security import KnownSecretRegistry, guard_bytes, known_secrets


def _safe_component(value: str, name: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError(f"{name} must be a safe path component")
    if any(char in value for char in '\\/:*?"<>|'):
        raise ValueError(f"{name} must be a safe path component")
    return value


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
        self._root = workspace_root.resolve()
        self._registry = registry or known_secrets()

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

    def read_bytes(self, ref: StoredObjectRef) -> bytes:
        path = (self._root / ref.relative_path).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("object path escapes workspace root")
        content = path.read_bytes()
        if "sha256:" + hashlib.sha256(content).hexdigest() != ref.digest:
            raise ValueError("object digest mismatch")
        if len(content) != ref.size:
            raise ValueError("object size mismatch")
        return content

    @staticmethod
    def _write_immutable(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
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
