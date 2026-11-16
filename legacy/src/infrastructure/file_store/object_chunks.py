"""对象分块清单与按需范围读取（架构 04 §12「查询索引与按需读取」）。

冻结要求（原文）：

> 对象发布时流式计算完整摘要；**分块清单记录总大小、固定顺序块ID/长度/摘要和完整对象摘要**，
> 块清单纳入已提交引用。**按需范围读取先校验归属及块清单，再读取并验证触及块**；
> 没有受信分块清单的旧对象须先**完整校验并建立可复用结果**，不能每个分页重复哈希整个对象。
> 仅mtime/大小相同不能证明内容不变；**校验缓存绑定实际内容摘要、已提交块清单及受控对象身份，
> 发现对象变化或异常立即失效**。

> 效率核对以访问范围为准：列表只读取摘要页、**详情只读取所选对象块**……
> 观察实际读写路径和重复计算以发现退化。

本模块把上述语义落成纯数据 + 严格解析：

- `ChunkManifest`：总大小、固定顺序块（块号/偏移/长度/摘要）、完整对象摘要；
- 清单是**内容寻址**的缓存：解析时逐项校验（长度、连续性、总大小、摘要形状），
  任一不符即视为不可信（调用方须重新完整校验），而不是"看起来像清单就信"。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: 默认块大小：64 KiB——足够小到"详情只读取所选对象块"，也足够大到不产生海量小块。
DEFAULT_CHUNK_SIZE = 64 * 1024

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def _require_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a sha256 digest")
    return value


@dataclass(frozen=True, slots=True)
class ChunkRef:
    """固定顺序里的一块：块号、起始偏移、长度与块摘要。"""

    index: int
    offset: int
    length: int
    digest: str

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("chunk index must be a nonnegative integer")
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("chunk offset must be a nonnegative integer")
        if type(self.length) is not int or self.length <= 0:
            raise ValueError("chunk length must be a positive integer")
        _require_digest(self.digest, "chunk digest")

    @property
    def end(self) -> int:
        return self.offset + self.length


@dataclass(frozen=True, slots=True)
class ChunkManifest:
    """对象的分块清单：完整摘要 + 总大小 + 固定顺序块表（架构 04 §12）。"""

    digest: str
    size: int
    chunk_size: int
    chunks: tuple[ChunkRef, ...]

    def __post_init__(self) -> None:
        _require_digest(self.digest, "object digest")
        if type(self.size) is not int or self.size < 0:
            raise ValueError("object size must be a nonnegative integer")
        if type(self.chunk_size) is not int or self.chunk_size <= 0:
            raise ValueError("chunk size must be a positive integer")
        offset = 0
        for expected_index, chunk in enumerate(self.chunks):
            if chunk.index != expected_index:
                raise ValueError("chunk manifest must list chunks in fixed order")
            if chunk.offset != offset:
                raise ValueError("chunk manifest offsets must be contiguous from zero")
            if chunk.length > self.chunk_size:
                raise ValueError("chunk manifest block exceeds the declared chunk size")
            offset = chunk.end
        if offset != self.size:
            raise ValueError("chunk manifest lengths must add up to the object size")
        if self.size == 0 and self.chunks:
            raise ValueError("an empty object has no chunks")

    def chunk_for(self, offset: int) -> ChunkRef | None:
        """返回覆盖该偏移的块；越界返回 `None`（不猜、不外推）。"""

        if type(offset) is not int or offset < 0 or offset >= self.size:
            return None
        for chunk in self.chunks:
            if chunk.offset <= offset < chunk.end:
                return chunk
        return None

    def chunks_for(self, *, offset: int, length: int) -> tuple[ChunkRef, ...]:
        """返回覆盖 `[offset, offset+length)` 的**最少**块序列（详情只读所选块）。"""

        if type(offset) is not int or offset < 0:
            raise ValueError("range offset must be a nonnegative integer")
        if type(length) is not int or length < 0:
            raise ValueError("range length must be a nonnegative integer")
        if length == 0:
            return ()
        if offset + length > self.size:
            raise ValueError("range exceeds the object size")
        return tuple(
            chunk
            for chunk in self.chunks
            if chunk.offset < offset + length and chunk.end > offset
        )


def build_chunk_manifest(
    content: bytes, *, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> ChunkManifest:
    """从**过滤后字节**构建清单：完整摘要 + 固定顺序块摘要。"""

    if not isinstance(content, bytes):
        raise ValueError("chunk manifest requires the exact saved bytes")
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk size must be a positive integer")
    chunks = tuple(
        ChunkRef(
            index=index,
            offset=offset,
            length=len(block),
            digest="sha256:" + hashlib.sha256(block).hexdigest(),
        )
        for index, offset, block in _blocks(content, chunk_size)
    )
    return ChunkManifest(
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
        chunk_size=chunk_size,
        chunks=chunks,
    )


def _blocks(content: bytes, chunk_size: int) -> list[tuple[int, int, bytes]]:
    return [
        (index, offset, content[offset : offset + chunk_size])
        for index, offset in enumerate(range(0, len(content), chunk_size))
    ]


def manifest_to_payload(manifest: ChunkManifest) -> dict[str, Any]:
    return {
        "schema_version": "aitest.object-chunk-manifest/1.0",
        "digest": manifest.digest,
        "size": manifest.size,
        "chunk_size": manifest.chunk_size,
        "chunks": [
            {
                "index": chunk.index,
                "offset": chunk.offset,
                "length": chunk.length,
                "digest": chunk.digest,
            }
            for chunk in manifest.chunks
        ],
    }


def manifest_from_payload(payload: Mapping[str, Any]) -> ChunkManifest:
    """严格解析清单：未知/缺失/形状不符一律拒绝（不可信清单不得用于范围读取）。"""

    if not isinstance(payload, Mapping):
        raise ValueError("object chunk manifest must be a mapping")
    if payload.get("schema_version") != "aitest.object-chunk-manifest/1.0":
        raise ValueError("object chunk manifest schema version is unknown")
    unknown = set(payload) - {"schema_version", "digest", "size", "chunk_size", "chunks"}
    if unknown:
        raise ValueError(f"object chunk manifest has unknown fields: {sorted(unknown)}")
    raw_chunks = payload.get("chunks")
    if not isinstance(raw_chunks, list):
        raise ValueError("object chunk manifest requires a chunk list")
    chunks: list[ChunkRef] = []
    for raw in raw_chunks:
        if not isinstance(raw, Mapping):
            raise ValueError("object chunk manifest entries must be mappings")
        extra = set(raw) - {"index", "offset", "length", "digest"}
        if extra:
            raise ValueError(f"object chunk entry has unknown fields: {sorted(extra)}")
        chunks.append(
            ChunkRef(
                index=raw.get("index"),  # type: ignore[arg-type]
                offset=raw.get("offset"),  # type: ignore[arg-type]
                length=raw.get("length"),  # type: ignore[arg-type]
                digest=_require_digest(raw.get("digest"), "chunk digest"),
            )
        )
    return ChunkManifest(
        digest=_require_digest(payload.get("digest"), "object digest"),
        size=payload.get("size"),  # type: ignore[arg-type]
        chunk_size=payload.get("chunk_size"),  # type: ignore[arg-type]
        chunks=tuple(chunks),
    )


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "ChunkManifest",
    "ChunkRef",
    "build_chunk_manifest",
    "manifest_from_payload",
    "manifest_to_payload",
]
