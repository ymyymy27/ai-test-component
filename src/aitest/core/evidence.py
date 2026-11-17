"""Evidence persistence: per-file cap, run media budget and data markers.

Evidence files live under ``runs/<run_id>/evidence``. Every file is capped at
the configured single-file limit (20 MiB by default) and counted towards the
run media budget. Known credential bodies are redacted before bytes reach the
disk. Failed cases always keep their evidence; once the budget is exhausted,
additional evidence of a passing case is skipped and a ``.skipped`` marker
records why.
"""

from __future__ import annotations

import errno
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any

from aitest.errors import AiTestError
from aitest.security import redact_bytes

DEFAULT_FILE_CAP_BYTES = 20 * 1024 * 1024
DEFAULT_MEDIA_BUDGET_MB = 500

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


def is_media(name: str) -> bool:
    return Path(name).suffix.lower() in MEDIA_SUFFIXES


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


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
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
) -> str | None:
    """Persist one evidence file and return its run-relative reference.

    Returns ``None`` when the file was skipped because the run budget is
    exhausted or video evidence is disabled; a ``<name>.skipped`` marker is
    written in that case. Evidence of a failed case is never skipped.
    """
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"证据名称不合法：{name}")
    if secrets:
        data = redact_bytes(data, secrets)
    evidence_dir = run_dir / "evidence"
    payload = data[:file_cap_bytes]
    truncated = len(data) > file_cap_bytes
    if result != "failed":
        reason = ""
        if is_video(name) and not allow_video:
            reason = "视频证据未启用"
        elif budget_bytes is not None and evidence_bytes(run_dir) + len(payload) > budget_bytes:
            reason = f"运行媒体预算已用完（{budget_bytes} 字节）"
        if reason:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / f"{name}.skipped").write_text(f"{reason}\n", encoding="utf-8")
            return None
    path = evidence_dir / name
    _write_bytes_atomic(path, payload)
    if truncated:
        (evidence_dir / f"{name}.truncated").write_text(
            f"原始大小 {len(data)} 字节，已截断\n", encoding="utf-8"
        )
    return str(path.relative_to(run_dir)).replace("\\", "/")
