"""Evidence persistence: per-file cap, run media budget and data markers.

Evidence files live under ``runs/<run_id>/evidence``. Every file is capped at
the configured single-file limit (20 MiB by default) and counted towards the
run media budget. Failed cases always keep their evidence; once the budget is
exhausted, additional evidence of a passing case is skipped and a ``.skipped``
marker records why.
"""

from __future__ import annotations

from pathlib import Path

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


def save_evidence(
    run_dir: Path,
    name: str,
    data: bytes,
    *,
    result: str = "",
    budget_bytes: int | None = None,
    allow_video: bool = True,
    file_cap_bytes: int = DEFAULT_FILE_CAP_BYTES,
) -> str | None:
    """Persist one evidence file and return its run-relative reference.

    Returns ``None`` when the file was skipped because the run budget is
    exhausted or video evidence is disabled; a ``<name>.skipped`` marker is
    written in that case. Evidence of a failed case is never skipped.
    """
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError(f"证据名称不合法：{name}")
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
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    path.write_bytes(payload)
    if truncated:
        (evidence_dir / f"{name}.truncated").write_text(
            f"原始大小 {len(data)} 字节，已截断\n", encoding="utf-8"
        )
    return str(path.relative_to(run_dir)).replace("\\", "/")
