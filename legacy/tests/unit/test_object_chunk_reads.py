"""对象分块清单与按需范围读取（架构 04 §12）。

分册原文：「对象发布时流式计算完整摘要；**分块清单记录总大小、固定顺序块ID/长度/摘要和
完整对象摘要**，块清单纳入已提交引用。**按需范围读取先校验归属及块清单，再读取并验证
触及块**；没有受信分块清单的旧对象须先**完整校验并建立可复用结果**，不能每个分页重复
哈希整个对象。仅mtime/大小相同不能证明内容不变；**校验缓存绑定实际内容摘要、已提交
块清单及受控对象身份，发现对象变化或异常立即失效**。」以及「详情只读取所选对象块」。

本文件锁定：①清单形状与严格解析；②范围读取只验证**触及块**（未触及块损坏不影响、
触及块损坏即拒绝并失效清单）；③旧对象首次范围读取会完整校验并建立可复用清单；
④归属/摘要/大小不符一律拒绝。
"""

from __future__ import annotations

import json

import pytest

from aitest.domain.evidence.evidence import StoredObjectRef
from aitest.infrastructure.file_store.object_chunks import (
    ChunkManifest,
    ChunkRef,
    build_chunk_manifest,
    manifest_from_payload,
    manifest_to_payload,
)
from aitest.infrastructure.file_store.objects import FileObjectStore

PAYLOAD = bytes(range(256)) * 8  # 2048 字节，便于跨块断言


def _store(tmp_path) -> FileObjectStore:
    return FileObjectStore(tmp_path)


def _object_path(store: FileObjectStore, ref: StoredObjectRef):
    return store._root / ref.relative_path  # noqa: SLF001 - 直接核对落盘布局


def _manifest_path(store: FileObjectStore, ref: StoredObjectRef):
    path = _object_path(store, ref)
    return path.with_name(path.name + ".chunks.json")


def test_manifest_shape_is_frozen_and_strict() -> None:
    manifest = build_chunk_manifest(PAYLOAD, chunk_size=512)
    assert manifest.digest.startswith("sha256:")
    assert manifest.size == len(PAYLOAD)
    assert [chunk.index for chunk in manifest.chunks] == list(range(4))
    assert [chunk.offset for chunk in manifest.chunks] == [0, 512, 1024, 1536]
    assert all(chunk.length == 512 for chunk in manifest.chunks)
    assert sum(chunk.length for chunk in manifest.chunks) == manifest.size

    payload = manifest_to_payload(manifest)
    assert payload["schema_version"] == "aitest.object-chunk-manifest/1.0"
    assert manifest_from_payload(json.loads(json.dumps(payload))) == manifest

    # 不可信/异常的清单一律拒绝，而不是"看起来像清单就信"。
    with pytest.raises(ValueError):
        manifest_from_payload({**payload, "schema_version": "other"})
    with pytest.raises(ValueError):
        manifest_from_payload({**payload, "extra": 1})
    with pytest.raises(ValueError):
        manifest_from_payload({**payload, "chunks": [{**payload["chunks"][0], "extra": 1}]})
    with pytest.raises(ValueError):
        ChunkManifest(digest=manifest.digest, size=10, chunk_size=4, chunks=())
    with pytest.raises(ValueError):
        ChunkManifest(
            digest=manifest.digest,
            size=3,
            chunk_size=4,
            chunks=(ChunkRef(1, 0, 3, manifest.chunks[0].digest),),
        )


def test_empty_object_has_no_chunks() -> None:
    manifest = build_chunk_manifest(b"")
    assert manifest.size == 0 and manifest.chunks == ()
    assert manifest.chunks_for(offset=0, length=0) == ()


def test_range_read_verifies_only_the_touched_chunks(tmp_path) -> None:
    store = _store(tmp_path)
    ref = store.publish_bytes("project-1", PAYLOAD, media_type="application/octet-stream")
    assert store.read_range(ref, offset=600, length=100, chunk_size=512) == PAYLOAD[600:700]
    assert _manifest_path(store, ref).exists()

    # 未触及的块损坏：范围读取仍然成功（证明**没有**整对象重算）。
    untouched = _object_path(store, ref)
    broken = bytearray(untouched.read_bytes())
    broken[0:4] = b"XXXX"
    untouched.write_bytes(bytes(broken))
    assert store.read_range(ref, offset=600, length=100, chunk_size=512) == PAYLOAD[600:700]

    # 触及的块损坏：立即拒绝并删除清单（对象变化立即失效）。
    broken = bytearray(untouched.read_bytes())
    broken[600:604] = b"YYYY"
    untouched.write_bytes(bytes(broken))
    with pytest.raises(ValueError, match="chunk digest mismatch"):
        store.read_range(ref, offset=600, length=100, chunk_size=512)
    assert not _manifest_path(store, ref).exists()
    # 清单失效后重新完整校验仍能重建（对象已损坏，故完整校验会拒绝）。
    with pytest.raises(ValueError, match="digest mismatch"):
        store.read_range(ref, offset=600, length=100, chunk_size=512)


def test_legacy_object_is_fully_verified_once_then_reused(tmp_path) -> None:
    """没有受信清单的旧对象：首次范围读取完整校验并建立可复用清单。"""

    store = _store(tmp_path)
    ref = store.publish_bytes("project-1", PAYLOAD)
    manifest_path = _manifest_path(store, ref)
    # 发布不预写清单（清单是读取侧建立的可复用校验结果），这里断言初始不存在。
    assert not manifest_path.exists()

    assert store.read_range(ref, offset=0, length=8, chunk_size=256) == PAYLOAD[:8]
    assert manifest_path.exists()
    # 第二次读取直接用受信清单（内容不变）。
    assert store.read_range(ref, offset=2000, length=48, chunk_size=256) == PAYLOAD[2000:]


def test_untrusted_manifest_is_rebuilt_from_full_verification(tmp_path) -> None:
    store = _store(tmp_path)
    ref = store.publish_bytes("project-1", PAYLOAD)
    manifest_path = _manifest_path(store, ref)
    # 清单与实际内容不一致（改块大小 / 改摘要）：不得使用，须重建。
    manifest_path.write_text(
        json.dumps(
            manifest_to_payload(build_chunk_manifest(b"other bytes", chunk_size=512))
        ),
        encoding="utf-8",
    )
    assert store.read_range(ref, offset=10, length=10, chunk_size=512) == PAYLOAD[10:20]
    assert manifest_from_payload(json.loads(manifest_path.read_text(encoding="utf-8"))) == (
        build_chunk_manifest(PAYLOAD, chunk_size=512)
    )


def test_range_read_refuses_ownership_and_bounds(tmp_path) -> None:
    store = _store(tmp_path)
    ref = store.publish_bytes("project-1", PAYLOAD)

    with pytest.raises(ValueError):
        store.read_range(
            StoredObjectRef(
                project_id="other-project",
                digest=ref.digest,
                size=ref.size,
                media_type=ref.media_type,
                relative_path=f"objects/other-project/{ref.digest.removeprefix('sha256:')}",
            ),
            offset=0,
            length=4,
        )
    with pytest.raises(ValueError, match="range exceeds the object size"):
        store.read_range(ref, offset=len(PAYLOAD) - 1, length=4)
    with pytest.raises(ValueError):
        store.read_range(ref, offset=-1, length=4)
    with pytest.raises(ValueError):
        store.read_range(ref, offset=0, length=-1)
    # 零长范围不读任何块。
    assert store.read_range(ref, offset=len(PAYLOAD) - 2, length=0) == b""
