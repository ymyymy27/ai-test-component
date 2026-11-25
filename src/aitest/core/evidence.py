"""Evidence persistence: per-file cap, run media budget and data markers.

Evidence files live under ``runs/<run_id>/evidence``. Every file is capped at
the configured single-file limit (20 MiB by default) and counted towards the
run media budget. Known credential bodies are redacted before bytes reach the
disk. Failed cases always keep their evidence; once the budget is exhausted,
additional evidence of a passing case is skipped and a ``.skipped`` marker
records why.
"""

from __future__ import annotations

import codecs
import errno
import hashlib
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from aitest.errors import AiTestError
from aitest.security import _BINARY_PREFIXES, ensure_within, redact, redact_bytes

DEFAULT_FILE_CAP_BYTES = 20 * 1024 * 1024
DEFAULT_MEDIA_BUDGET_MB = 500
_STREAM_CHUNK_BYTES = 64 * 1024

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg"})
MEDIA_SUFFIXES = VIDEO_SUFFIXES | frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".svg",
        ".mp3",
        ".wav",
        ".m4a",
        ".har",
        ".zip",
    }
)


def data_prefix(run_id: str) -> str:
    """Return the stable test-data marker injected into executed commands."""
    return f"aitest_{run_id}_"


def evidence_path(run_dir: Path, ref: str) -> Path | None:
    """Resolve an evidence reference only when it stays inside the run directory."""
    if not ref:
        return None
    try:
        return ensure_within(run_dir, run_dir / ref)
    except ValueError:
        return None


def is_video(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_SUFFIXES


def media_type(name: str) -> str:
    guessed, _encoding = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def evidence_bytes(run_dir: Path) -> int:
    """Total bytes held by persisted evidence files, ignoring markers."""
    directory = run_dir / "evidence"
    if not directory.is_dir():
        return 0
    total = 0
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith(".skipped") or path.name.endswith(".truncated"):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def budget_bytes(media_budget_mb: float) -> int:
    return max(0, int(media_budget_mb * 1024 * 1024))


def settings_from_config(config: dict[str, Any]) -> tuple[int, int, bool]:
    """Return ``(media_budget, file_cap, allow_video)`` from project config."""
    value = config.get("evidence")
    evidence = value if isinstance(value, dict) else {}
    budget_mb = _as_float(evidence.get("media_budget_mb"), float(DEFAULT_MEDIA_BUDGET_MB))
    cap_mb = _as_float(
        evidence.get("file_cap_mb"), DEFAULT_FILE_CAP_BYTES / (1024 * 1024)
    )
    allow_video = evidence.get("video", False)
    if isinstance(allow_video, str):
        allow_video = allow_video.strip().lower() in {"1", "true", "yes", "on"}
    return budget_bytes(budget_mb), budget_bytes(cap_mb), bool(allow_video)


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def sha256_file(path: Path) -> str:
    """分块流式计算文件 SHA-256 摘要，避免将大文件整体读入内存。"""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_STREAM_CHUNK_BYTES):
            hasher.update(chunk)
    return hasher.hexdigest()


def _replace_temp_atomic(tmp: Path, path: Path) -> None:
    """带 Windows 文件句柄短占用重试的原子替换，并统一映射磁盘满错误。"""
    try:
        last_error: PermissionError | None = None
        for _ in range(20):
            try:
                os.replace(tmp, path)
                last_error = None
                break
            except PermissionError as exc:
                winerror = getattr(exc, "winerror", None)
                if os.name != "nt" or winerror not in {None, 5, 32}:
                    raise
                last_error = exc
                time.sleep(0.05)
        if last_error is not None:
            raise last_error
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise AiTestError(
                "DISK_FULL",
                "磁盘空间不足，已停止写入证据",
                "释放磁盘空间后重跑；已有证据不会被删除",
            ) from exc
        raise AiTestError(
            "EVIDENCE_WRITE_FAILED", f"证据写入失败：{path}（{exc}）", "检查目录权限与磁盘"
        ) from exc


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise AiTestError(
                "DISK_FULL",
                "磁盘空间不足，已停止写入证据",
                "释放磁盘空间后重跑；已有证据不会被删除",
            ) from exc
        raise AiTestError(
            "EVIDENCE_WRITE_FAILED", f"证据写入失败：{path}（{exc}）", "检查目录权限与磁盘"
        ) from exc
    _replace_temp_atomic(tmp, path)


def _encode_utf8_capped(text: str, limit: int) -> tuple[bytes, bool]:
    """将文本编码为 UTF-8 并在不截断多字节字符的前提下严格控制在 limit 字节内。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return raw, False
    sliced = raw[:limit].decode("utf-8", errors="ignore").encode("utf-8")
    return sliced, True


def _check_evidence_quota(
    run_dir: Path,
    name: str,
    planned_size: int,
    *,
    result: str,
    budget_bytes: int | None,
    allow_video: bool,
    current_bytes: int | None,
) -> bool:
    """检查非失败用例的媒体预算与视频开关，超限时写 .skipped 标记并返回 False。"""
    if result == "failed":
        return True
    reason = ""
    used = evidence_bytes(run_dir) if current_bytes is None else current_bytes
    if is_video(name) and not allow_video:
        reason = "视频证据未启用"
    elif budget_bytes is not None and used + planned_size > budget_bytes:
        reason = f"运行媒体预算已用完（{budget_bytes} 字节）"
    if reason:
        evidence_dir = run_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / f"{name}.skipped").write_text(f"{reason}\n", encoding="utf-8")
        return False
    return True


def save_evidence_stream(
    run_dir: Path,
    name: str,
    source_path: Path,
    *,
    result: str = "",
    budget_bytes: int | None = None,
    allow_video: bool = True,
    file_cap_bytes: int = DEFAULT_FILE_CAP_BYTES,
    secrets: list[str] | None = None,
    current_bytes: int | None = None,
) -> str | None:
    """流式脱敏并按单文件上限截断保存证据，不在内存中加载整份大文件。"""
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"证据名称不合法：{name}")
    actual_size = source_path.stat().st_size
    cap = max(0, file_cap_bytes)
    if not _check_evidence_quota(
        run_dir,
        name,
        min(actual_size, cap),
        result=result,
        budget_bytes=budget_bytes,
        allow_video=allow_video,
        current_bytes=current_bytes,
    ):
        return None

    secret_list = list(secrets or [])
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    fd, tmp_name = tempfile.mkstemp(prefix=f".{name}.", dir=evidence_dir)
    tmp = Path(tmp_name)
    hit_cap = False
    written = 0
    try:
        with source_path.open("rb") as src, os.fdopen(fd, "wb") as dst:
            head = src.read(_STREAM_CHUNK_BYTES)
            is_binary = not head or head.startswith(_BINARY_PREFIXES) or b"\x00" in head[:4096]
            if is_binary:
                chunk: bytes = head
                while chunk and written < cap:
                    remaining = cap - written
                    part = chunk[:remaining]
                    if len(part) < len(chunk):
                        hit_cap = True
                    dst.write(part)
                    written += len(part)
                    if written >= cap:
                        if src.read(1):
                            hit_cap = True
                        break
                    chunk = src.read(_STREAM_CHUNK_BYTES)
            else:
                overlap = max(256, max((len(s) for s in secret_list if s), default=0) + 128)
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                buf = decoder.decode(head, final=False)
                while True:
                    next_chunk = src.read(_STREAM_CHUNK_BYTES)
                    eof = not next_chunk
                    buf += decoder.decode(next_chunk, final=eof)
                    redacted = redact(buf, secret_list)
                    if eof:
                        encoded, capped = _encode_utf8_capped(redacted, cap - written)
                        dst.write(encoded)
                        written += len(encoded)
                        if capped:
                            hit_cap = True
                        break
                    if len(redacted) > overlap:
                        flush_part = redacted[:-overlap]
                        buf = redacted[-overlap:]
                        encoded, capped = _encode_utf8_capped(flush_part, cap - written)
                        dst.write(encoded)
                        written += len(encoded)
                        if capped or written >= cap:
                            hit_cap = True
                            break
                    else:
                        buf = redacted
            dst.flush()
            os.fsync(dst.fileno())
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise AiTestError(
                "DISK_FULL",
                "磁盘空间不足，已停止写入证据",
                "释放磁盘空间后重跑；已有证据不会被删除",
            ) from exc
        raise AiTestError(
            "EVIDENCE_WRITE_FAILED", f"证据写入失败：{path}（{exc}）", "检查目录权限与磁盘"
        ) from exc
    _replace_temp_atomic(tmp, path)
    if hit_cap or actual_size > cap:
        (evidence_dir / f"{name}.truncated").write_text(
            f"原始大小 {actual_size} 字节，已截断\n", encoding="utf-8"
        )
    return str(path.relative_to(run_dir)).replace("\\", "/")


def save_evidence(
    run_dir: Path,
    name: str,
    data: bytes,
    *,
    result: str = "",
    budget_bytes: int | None = None,
    allow_video: bool = True,
    file_cap_bytes: int = DEFAULT_FILE_CAP_BYTES,
    secrets: list[str] | None = None,
    original_size: int | None = None,
    current_bytes: int | None = None,
) -> str | None:
    """Persist one evidence file and return its run-relative reference.

    Returns ``None`` when the file was skipped because the run budget is
    exhausted or video evidence is disabled; a ``<name>.skipped`` marker is
    written in that case. Evidence of a failed case is never skipped.
    """
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"证据名称不合法：{name}")
    data = redact_bytes(data, secrets or [])
    evidence_dir = run_dir / "evidence"
    payload = data[:file_cap_bytes]
    actual_size = original_size if original_size is not None else len(data)
    truncated = actual_size > file_cap_bytes
    if result != "failed":
        reason = ""
        used = evidence_bytes(run_dir) if current_bytes is None else current_bytes
        if is_video(name) and not allow_video:
            reason = "视频证据未启用"
        elif budget_bytes is not None and used + len(payload) > budget_bytes:
            reason = f"运行媒体预算已用完（{budget_bytes} 字节）"
        if reason:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / f"{name}.skipped").write_text(f"{reason}\n", encoding="utf-8")
            return None
    path = evidence_dir / name
    _write_bytes_atomic(path, payload)
    if truncated:
        (evidence_dir / f"{name}.truncated").write_text(
            f"原始大小 {actual_size} 字节，已截断\n", encoding="utf-8"
        )
    return str(path.relative_to(run_dir)).replace("\\", "/")
