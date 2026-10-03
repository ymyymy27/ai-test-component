"""A-09：落盘前凭据过滤底线（架构02第13节 采集安全与原始证据语义）。

覆盖：
- KnownSecretRegistry 精确值登记（短值忽略、长→短排序）；
- guard_bytes/guard_value 对精确值、明列模式（sk-/ghp_/Bearer）与
  敏感键名的过滤，二进制不做猜测性改写；
- StreamSecretFilter：跨块凭据不漏出（token 跨块、已知值跨块）、
  UTF-8 多字节字符不被切坏、abort 丢弃未决尾部、flush 封口过滤；
- FileObjectStore.publish_bytes：过滤前字节不入临时文件/对象区，
  digest 基于过滤后字节；
- FileSpoolStore 流式 append/persist_blocks：磁盘日志无凭据原文，
  不洁密封块整批拒绝（无孤儿字节）；
- FileUnitOfWork.stage_record：records.json 落盘无凭据原文；
- SecretManager 解析即登记。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from aitest.domain.execution.runs import CapturedOutputBlock, OutputStreamName
from aitest.infrastructure.credentials import (
    EnvironmentSecretProvider,
    SecretManager,
)
from aitest.infrastructure.file_store.objects import FileObjectStore
from aitest.infrastructure.file_store.spool import FileSpoolStore
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
from aitest.infrastructure.security import (
    KnownSecretRegistry,
    StreamSecretFilter,
    UnsafeMaterialError,
    guard_bytes,
    guard_value,
)

_SECRET = "zz-secret-token-abcdef1234567890"
_SHORT = "abc"
_SK_TOKEN = "sk-" + "K" * 28
_GHP_TOKEN = "ghp_" + "G" * 32


# ------------------------------------------------------------- KnownSecretRegistry


def test_registry_ignores_short_values_and_orders_longest_first() -> None:
    registry = KnownSecretRegistry()
    registry.register(_SHORT)
    registry.register("1234567")
    registry.register("1234567890")
    values = registry.text_values()
    assert values == ("1234567890", "1234567")
    assert registry.longest_length() == 10
    assert bool(registry)


def test_registry_empty() -> None:
    assert not KnownSecretRegistry()


# --------------------------------------------------------------------- guard_bytes


def test_guard_bytes_replaces_exact_value() -> None:
    registry = KnownSecretRegistry()
    registry.register(_SECRET)
    safe, changed = guard_bytes(f"token={_SECRET}".encode(), registry)
    assert changed is True
    assert _SECRET.encode() not in safe
    assert b"[REDACTED]" in safe


def test_guard_bytes_replaces_pattern() -> None:
    safe, changed = guard_bytes(f"Authorization: Bearer {_SK_TOKEN}".encode())
    assert changed
    assert _SK_TOKEN.encode() not in safe


def test_guard_bytes_binary_passes_through_but_exact_value_still_scrubbed() -> None:
    binary = bytes(range(256)) * 4
    safe, changed = guard_bytes(binary)
    assert not changed
    assert safe == binary
    # 即使夹在二进制中，已登记精确值仍做字节级替换。
    registry = KnownSecretRegistry()
    registry.register(_SECRET)
    mixed = b"\x00\x01" + _SECRET.encode() + b"\xff\xfe"
    safe, changed = guard_bytes(mixed, registry)
    assert changed
    assert _SECRET.encode() not in safe


def test_guard_bytes_github_token_pattern() -> None:
    safe, changed = guard_bytes(_GHP_TOKEN.encode())
    assert changed
    assert _GHP_TOKEN.encode() not in safe


# -------------------------------------------------------------------- guard_value


def test_guard_value_replaces_sensitive_keys_and_nested_values() -> None:
    registry = KnownSecretRegistry()
    registry.register(_SECRET)
    payload = {
        "Authorization": f"Bearer {_SK_TOKEN}",
        "nested": {"access_token": "ignored", "text": f"x={_SECRET}"},
        "items": [f"Bearer {_SK_TOKEN}", 3, True, None],
        "api_key": "abc",
    }
    safe, changed = guard_value(payload, registry)
    assert changed
    serialized = json.dumps(safe)
    assert _SECRET not in serialized
    assert _SK_TOKEN not in serialized
    assert safe["Authorization"] == "[REDACTED]"
    assert safe["api_key"] == "[REDACTED]"
    assert safe["nested"]["access_token"] == "[REDACTED]"
    assert isinstance(safe["items"], list)


def test_guard_value_clean_payload_unchanged() -> None:
    payload = {"project_id": "p1", "count": 2, "ok": True, "none": None}
    safe, changed = guard_value(payload)
    assert not changed
    assert safe == payload


# ------------------------------------------------------------- StreamSecretFilter


def test_stream_filter_single_chunk() -> None:
    stream = StreamSecretFilter()
    # 无任何疑似密钥形态的普通文本立即放行（逐字节切点检查通过）。
    assert stream.feed(b"hello world\n") == b"hello world\n"
    assert stream.flush() == b""


def test_stream_filter_secret_split_across_chunks_never_leaks() -> None:
    stream = StreamSecretFilter()
    token = _SK_TOKEN.encode()
    written = b""
    split = 5
    written += stream.feed(b"output " + token[:split])
    written += stream.feed(token[split:] + b" done\n")
    written += stream.flush()
    assert token not in written
    assert b"sk-" not in written or b"[REDACTED]" in written
    assert b"done" in written


def test_stream_filter_known_value_split_across_chunks() -> None:
    registry = KnownSecretRegistry()
    registry.register(_SECRET)
    stream = StreamSecretFilter(registry)
    mid = len(_SECRET) // 2
    written = b""
    written += stream.feed(b"prefix " + _SECRET.encode()[:mid])
    written += stream.feed(_SECRET.encode()[mid:] + b" suffix")
    written += stream.flush()
    assert _SECRET.encode() not in written
    assert b"[REDACTED]" in written


def test_stream_filter_large_output_eventually_drains_without_secret_leak() -> None:
    stream = StreamSecretFilter()
    token = _SK_TOKEN.encode()
    written = b""
    filler = b"A" * 2000
    written += stream.feed(filler + b" " + token + b" tail")
    written += stream.feed(b" more output " + b"B" * 2000)
    written += stream.flush()
    assert token not in written
    assert len(written) > 4000  # 正常输出没有被未决窗口无限扣留


def test_stream_filter_multibyte_utf8_not_split() -> None:
    stream = StreamSecretFilter()
    text = "输出" * 1000  # 3 字节字符
    out1 = stream.feed(text[:1500].encode())
    out2 = stream.feed(text[1500:].encode())
    tail = stream.flush()
    combined = out1 + out2 + tail
    assert combined.decode("utf-8") == text


def test_stream_filter_abort_drops_pending_tail() -> None:
    stream = StreamSecretFilter()
    stream.feed(b"first chunk without newline")
    stream.abort()
    assert stream.flush() == b""


# ----------------------------------------------------------------- FileObjectStore


def test_object_store_prefilters_before_disk(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path)
    content = f"model reply with {_SK_TOKEN} embedded".encode()
    ref = store.publish_bytes("project-a09", content)
    on_disk = (tmp_path / ref.relative_path).read_bytes()
    assert _SK_TOKEN.encode() not in on_disk
    # digest/大小描述的是过滤后字节
    assert ref.size == len(on_disk)
    assert store.read_bytes(ref) == on_disk


def test_object_store_exact_value_scrubbed(tmp_path: Path) -> None:
    registry = KnownSecretRegistry()
    registry.register(_SECRET)
    store = FileObjectStore(tmp_path, registry=registry)
    ref = store.publish_bytes("project-a09", f"v={_SECRET}".encode())
    on_disk = (tmp_path / ref.relative_path).read_bytes()
    assert _SECRET.encode() not in on_disk


def test_object_store_clean_content_unchanged(tmp_path: Path) -> None:
    store = FileObjectStore(tmp_path)
    ref = store.publish_bytes("project-a09", b"plain content")
    assert store.read_bytes(ref) == b"plain content"


# ---------------------------------------------------------------------- spool


def _block(
    content: bytes,
    *,
    attempt_id: str = "att-1",
    block_index: int = 0,
    offset: int = 0,
) -> CapturedOutputBlock:
    return CapturedOutputBlock(
        run_id="run-1",
        step_id="step-1",
        attempt_id=attempt_id,
        stream_name=OutputStreamName.STDOUT,
        block_index=block_index,
        offset=offset,
        content=content,
    )


def test_spool_stream_writer_filters_on_disk(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="att-1",
        stream_name=OutputStreamName.STDOUT,
        block_size=1 << 20,
    )
    try:
        mid = len(_SK_TOKEN) // 2
        writer.append(b"start " + _SK_TOKEN.encode()[:mid])
        writer.append(_SK_TOKEN.encode()[mid:] + b" end\n")
    finally:
        writer.close()
    log = (tmp_path / "spool" / "att-1" / "stdout.log").read_bytes()
    assert _SK_TOKEN.encode() not in log
    assert b"[REDACTED]" in log


def test_spool_stream_writer_abort_never_writes_pending_secret(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    writer = store.open_stream(
        run_id="run-1",
        step_id="step-1",
        attempt_id="att-2",
        stream_name=OutputStreamName.STDERR,
        block_size=1 << 20,
    )
    # 纯 sk- token 整块停留在未决尾部（切点一路回退到 0）。
    writer.append(_SK_TOKEN.encode())
    writer.abort()
    log = tmp_path / "spool" / "att-2" / "stderr.log"
    if log.exists():
        assert _SK_TOKEN.encode() not in log.read_bytes()


def test_persist_blocks_rejects_dirty_batch_entirely(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    clean = _block(b"safe block", block_index=0, offset=0)
    dirty = _block(f"Bearer {_SK_TOKEN}".encode(), block_index=1, offset=10)
    with pytest.raises(UnsafeMaterialError):
        store.persist_blocks([clean, dirty])
    spool_dir = tmp_path / "spool"
    # 整批拒绝：连"干净"的第一块也不得先落盘（无孤儿字节）。
    if spool_dir.exists():
        for path in spool_dir.rglob("*.log"):
            assert b"safe block" not in path.read_bytes()


def test_persist_blocks_accepts_clean(tmp_path: Path) -> None:
    store = FileSpoolStore(tmp_path)
    manifest = store.persist_blocks([_block(b"safe block")])
    assert len(manifest.blocks) == 1
    assert store.read_block(manifest.blocks[0]) == b"safe block"


# ----------------------------------------------------------------------- UOW


def test_uow_stage_record_prefilters_payload(tmp_path: Path) -> None:
    registry = KnownSecretRegistry()
    registry.register(_SECRET)
    unit = FileUnitOfWork(tmp_path, registry=registry)
    unit.begin("req-a09", "project-a09")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={
            "project_id": "project-a09",
            "generated_content_payload": f"reply {_SECRET}",
            "headers": {"Authorization": f"Bearer {_SK_TOKEN}"},
        },
    )
    unit.commit("req-a09")
    raw = (tmp_path / "records.json").read_text(encoding="utf-8")
    assert _SECRET not in raw
    assert _SK_TOKEN not in raw
    assert "Bearer" not in raw


# ----------------------------------------------------------- SecretManager wiring


def test_secret_manager_registers_value_on_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_value = "env-secret-zzz-123456"
    monkeypatch.setenv("A09_SECRET_ENV", secret_value)
    manager = SecretManager(
        (EnvironmentSecretProvider({("model", "ref-1"): "A09_SECRET_ENV"}),)
    )
    resolved = manager.resolve("ref-1", purpose="model")
    assert resolved.reveal() == secret_value
    safe, changed = guard_bytes(f"x={secret_value}".encode())
    assert changed
    assert secret_value.encode() not in safe


# --------------------------------------------------------- diagnostics & backup


def test_connection_facts_prefilter_error_detail(tmp_path: Path) -> None:
    from aitest.infrastructure.connections import (
        ConnectionFactStore,
        TransportFact,
    )

    store = ConnectionFactStore(tmp_path)
    store.append(
        endpoint_address="tcp://example.invalid:443",
        fact=TransportFact(
            reachable=False,
            elapsed_ms=5,
            error_kind="refused",
            detail=f"Authorization: Bearer {_SK_TOKEN}",
        ),
        source_session="s-1",
        observed_at=1.0,
    )
    raw = store.path.read_text(encoding="utf-8")
    assert _SK_TOKEN not in raw
    assert "[REDACTED]" in raw


def test_backup_contains_only_filtered_bytes(tmp_path: Path) -> None:
    from aitest.infrastructure.file_store.backup import FileBackupStore

    registry = KnownSecretRegistry()
    registry.register(_SECRET)
    objects = FileObjectStore(tmp_path, registry=registry)
    objects.publish_bytes(
        "project-a09", f"model said {_SECRET} and {_SK_TOKEN}".encode()
    )
    unit = FileUnitOfWork(tmp_path, registry=registry)
    unit.begin("req-bak", "project-a09")
    unit.stage_record(
        aggregate_kind="case",
        record_id="case-1",
        expected_revision=None,
        payload={"project_id": "project-a09", "body": f"token {_SECRET}"},
    )
    unit.commit("req-bak")

    destination = tmp_path.parent / "backup-a09"
    FileBackupStore(tmp_path).create(destination)
    for path in destination.rglob("*"):
        if path.is_file():
            assert _SECRET.encode() not in path.read_bytes(), path
            assert _SK_TOKEN.encode() not in path.read_bytes(), path
