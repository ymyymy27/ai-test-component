"""纯只读本地 Git 仓库分析引擎。

仅调用只读 Git 查询命令采集分支、HEAD、远端脱敏地址、Tag、领先/落后提交、
工作区与暂存区改动、相对基线/上次运行 diff 统计、变更行号区间（hunks）及提交热点；
在非 Git 目录或未安装 Git 的环境中平滑降级而不抛异常。
"""

from __future__ import annotations

import re
import subprocess
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DIFF_FILE_RE = re.compile(r"^diff --git a/.+ b/(.+)$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/@^~-]{1,128}$")


def _run_git(root: Path, *args: str) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        return completed.returncode, completed.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def sanitize_remote_url(url: str) -> str:
    """剥离 Git 远端地址中内嵌的用户名或令牌口令，防止凭据进入状态与报告。"""
    cleaned = url.strip()
    if not cleaned:
        return ""
    if "://" in cleaned:
        try:
            parsed = urllib.parse.urlsplit(cleaned)
            if parsed.username or parsed.password:
                hostname = parsed.hostname or ""
                if ":" in hostname and not hostname.startswith("["):
                    hostname = f"[{hostname}]"
                netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
                return urllib.parse.urlunsplit(
                    (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
                )
        except ValueError:
            return cleaned
    return cleaned


def _unquote_git_path(raw: str) -> str:
    text = raw.strip()
    if " -> " in text:
        text = text.split(" -> ")[-1].strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return text.replace("\\", "/").lstrip("./")


def _ref_exists(root: Path, ref: str) -> bool:
    if not ref or not _SAFE_REF_RE.fullmatch(ref):
        return False
    code, _ = _run_git(root, "rev-parse", "--verify", "--quiet", ref)
    return code == 0


def _detect_base_branch(root: Path, current_branch: str) -> str:
    for candidate in (
        "origin/main",
        "origin/master",
        "main",
        "master",
        "develop",
        "origin/develop",
    ):
        if candidate == current_branch:
            continue
        if _ref_exists(root, candidate):
            return candidate
    return ""


def _parse_numstat(output: str, *, status_hint: str = "M") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        add_raw, del_raw, path_raw = parts
        additions = int(add_raw) if add_raw.isdigit() else 0
        deletions = int(del_raw) if del_raw.isdigit() else 0
        path = _unquote_git_path(path_raw)
        if path:
            rows.append(
                {
                    "path": path,
                    "additions": additions,
                    "deletions": deletions,
                    "status": status_hint,
                }
            )
    return rows


def _parse_hunks(output: str, *, limit_files: int = 60) -> list[dict[str, Any]]:
    by_file: dict[str, list[list[int]]] = {}
    current_path = ""
    for line in output.splitlines():
        file_match = _DIFF_FILE_RE.match(line)
        if file_match:
            current_path = _unquote_git_path(file_match.group(1))
            if current_path and current_path not in by_file:
                if len(by_file) >= limit_files:
                    current_path = ""
                    continue
                by_file[current_path] = []
            continue
        if not current_path:
            continue
        hunk_match = _HUNK_RE.match(line)
        if hunk_match:
            start = int(hunk_match.group(1))
            count = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
            end = start if count <= 1 else start + count - 1
            by_file[current_path].append([start, end])
    return [{"path": path, "ranges": ranges} for path, ranges in by_file.items() if ranges]


def inspect_local_git(
    root: Path,
    *,
    since_commit: str = "",
    max_commits: int = 10,
) -> dict[str, Any]:
    """采集被测目录的完整只读本地 Git 事实，非 Git 目录优雅降级返回空结构。"""
    empty: dict[str, Any] = {
        "is_git": False,
        "available": False,
        "reason": "目录不是 Git 仓库或系统未安装 git",
        "branch": "",
        "detached": False,
        "head": "",
        "short_head": "",
        "head_author": "",
        "head_time": "",
        "head_subject": "",
        "base_branch": "",
        "remote_url": "",
        "latest_tag": "",
        "ahead": 0,
        "behind": 0,
        "dirty": False,
        "staged_files": [],
        "unstaged_files": [],
        "untracked_files": [],
        "changed_files": [],
        "diff_stat": [],
        "changed_hunks": [],
        "recent_commits": [],
        "hotspots": [],
    }
    if not root.is_dir():
        empty["reason"] = "项目目录不存在"
        return empty
    code, inside = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0 or inside.strip().lower() != "true":
        return empty

    code_sym, branch_out = _run_git(root, "symbolic-ref", "--short", "HEAD")
    detached = code_sym != 0
    branch = branch_out.strip() if not detached else "HEAD (detached)"

    head_code, head_out = _run_git(root, "rev-parse", "HEAD")
    has_head = head_code == 0 and bool(head_out.strip())
    head = head_out.strip() if has_head else ""
    short_head = head[:8] if head else ""

    head_author = ""
    head_time = ""
    head_subject = ""
    if has_head:
        short_code, short_out = _run_git(root, "rev-parse", "--short", "HEAD")
        if short_code == 0 and short_out.strip():
            short_head = short_out.strip()
        log1_code, log1_out = _run_git(root, "log", "-1", "--format=%an%x1f%aI%x1f%s", "HEAD")
        if log1_code == 0 and "\x1f" in log1_out:
            parts = log1_out.strip().split("\x1f", 2)
            head_author = parts[0]
            head_time = parts[1] if len(parts) > 1 else ""
            head_subject = parts[2] if len(parts) > 2 else ""

    base_branch = _detect_base_branch(root, "" if detached else branch) if has_head else ""

    rem_code, rem_out = _run_git(root, "remote", "get-url", "origin")
    remote_url = sanitize_remote_url(rem_out) if rem_code == 0 else ""

    tag_code, tag_out = _run_git(root, "describe", "--tags", "--abbrev=0")
    latest_tag = tag_out.strip() if tag_code == 0 else ""

    ahead = 0
    behind = 0
    if has_head:
        ab_code, ab_out = _run_git(root, "rev-list", "--left-right", "--count", "HEAD...@{u}")
        if ab_code != 0 and base_branch:
            ab_code, ab_out = _run_git(
                root, "rev-list", "--left-right", "--count", f"HEAD...{base_branch}"
            )
        if ab_code == 0:
            nums = ab_out.strip().split()
            if len(nums) == 2 and nums[0].isdigit() and nums[1].isdigit():
                ahead, behind = int(nums[0]), int(nums[1])

    staged_files: list[str] = []
    unstaged_files: list[str] = []
    untracked_files: list[str] = []
    _, status_out = _run_git(root, "status", "--porcelain=v1", "-uall")
    for line in status_out.splitlines():
        if len(line) < 4:
            continue
        x_flag, y_flag = line[0], line[1]
        rel = _unquote_git_path(line[3:])
        if not rel:
            continue
        if x_flag == "?" and y_flag == "?":
            untracked_files.append(rel)
            continue
        if x_flag not in {" ", "?"}:
            staged_files.append(rel)
        if y_flag not in {" ", "?"}:
            unstaged_files.append(rel)

    diff_map: dict[str, dict[str, Any]] = {}
    if has_head and since_commit and _ref_exists(root, since_commit):
        _, since_num = _run_git(root, "diff", "--numstat", since_commit, "HEAD")
        for row in _parse_numstat(since_num, status_hint="C"):
            diff_map[row["path"]] = row
    if has_head:
        _, work_num = _run_git(root, "diff", "--numstat", "HEAD")
        for row in _parse_numstat(work_num, status_hint="M"):
            existing = diff_map.get(row["path"])
            if existing is None:
                diff_map[row["path"]] = row
            else:
                existing["additions"] += row["additions"]
                existing["deletions"] += row["deletions"]
    else:
        _, cached_num = _run_git(root, "diff", "--cached", "--numstat")
        for row in _parse_numstat(cached_num, status_hint="A"):
            diff_map[row["path"]] = row
    if not diff_map and has_head and base_branch and not staged_files and not unstaged_files:
        _, base_num = _run_git(root, "diff", "--numstat", f"{base_branch}...HEAD")
        for row in _parse_numstat(base_num, status_hint="B"):
            diff_map[row["path"]] = row

    changed_hunks: list[dict[str, Any]] = []
    if has_head:
        if since_commit and _ref_exists(root, since_commit):
            _, hunk_out = _run_git(root, "diff", "-U0", since_commit)
        else:
            _, hunk_out = _run_git(root, "diff", "-U0", "HEAD")
        changed_hunks = _parse_hunks(hunk_out)
    else:
        _, cached_hunk = _run_git(root, "diff", "--cached", "-U0")
        changed_hunks = _parse_hunks(cached_hunk)

    for rel in untracked_files:
        if rel not in diff_map:
            diff_map[rel] = {"path": rel, "additions": 0, "deletions": 0, "status": "?"}

    changed_files = sorted(
        {
            *staged_files,
            *unstaged_files,
            *untracked_files,
            *diff_map.keys(),
        }
    )

    recent_commits: list[dict[str, str]] = []
    hotspots: list[dict[str, Any]] = []
    if has_head:
        _, log_out = _run_git(
            root,
            "log",
            f"-n{max(1, min(max_commits, 30))}",
            "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s",
        )
        for line in log_out.splitlines():
            parts = line.split("\x1f", 4)
            if len(parts) < 5:
                continue
            recent_commits.append(
                {
                    "sha": parts[0],
                    "short_sha": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                    "subject": parts[4],
                }
            )
        _, name_out = _run_git(root, "log", "-n30", "--name-only", "--pretty=format:")
        counts = Counter(
            _unquote_git_path(line) for line in name_out.splitlines() if line.strip()
        )
        hotspots = [{"path": path, "commits": cnt} for path, cnt in counts.most_common(10)]

    return {
        "is_git": True,
        "available": True,
        "reason": "",
        "branch": branch,
        "detached": detached,
        "head": head,
        "short_head": short_head,
        "head_author": head_author,
        "head_time": head_time,
        "head_subject": head_subject,
        "base_branch": base_branch,
        "remote_url": remote_url,
        "latest_tag": latest_tag,
        "ahead": ahead,
        "behind": behind,
        "dirty": bool(staged_files or unstaged_files or untracked_files),
        "staged_files": sorted(set(staged_files)),
        "unstaged_files": sorted(set(unstaged_files)),
        "untracked_files": sorted(set(untracked_files)),
        "changed_files": changed_files,
        "diff_stat": [diff_map[k] for k in sorted(diff_map)],
        "changed_hunks": changed_hunks,
        "recent_commits": recent_commits,
        "hotspots": hotspots,
    }


def compute_changed_files_since(root: Path, since_commit: str = "") -> list[str]:
    """返回指定提交以来（及当前未提交工作区）的所有变动文件相对路径。"""
    info = inspect_local_git(root, since_commit=since_commit, max_commits=1)
    return list(info.get("changed_files") or [])
