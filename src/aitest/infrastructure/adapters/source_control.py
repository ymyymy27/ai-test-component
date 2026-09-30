"""本地 Git 与可选 GitHub 只读信息，plain 不调用。

只通过受控 ``git`` CLI 子进程取事实（不用 shell），并先做能力探测：

- Git 不可用 → :class:`GitUnavailable`，调用方不得静默转 plain；
- 非仓库路径 → 事实中 ``is_repository=False``；
- 远端领先/落后只基于本地已知的 upstream 引用（不做网络 fetch）；
- GitHub HTTPS API 客户端为**可选**只读能力，未认证可用但受速率限制，
  失败只降级远端状态，不阻塞本地。
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class GitUnavailable(RuntimeError):
    """git CLI 不存在或无法执行。"""


def _run_git(*args: str, cwd: Path, executable: str, timeout: float = 10.0) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - 参数列表，无 shell
            [executable, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitUnavailable(str(error)) from error
    if completed.returncode != 0:
        raise GitUnavailable(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


@dataclass(frozen=True, slots=True)
class GitHubRef:
    """从远端 URL 解析出的 owner/repo。"""

    owner: str
    repo: str

    @classmethod
    def parse(cls, remote_url: str) -> GitHubRef | None:
        # HTTPS: https://github.com/owner/repo(.git)
        match = re.match(
            r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", remote_url
        )
        if not match:
            # SSH: git@github.com:owner/repo(.git)
            match = re.match(
                r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", remote_url
            )
        if not match:
            return None
        return cls(owner=match.group(1), repo=match.group(2))


class GitSourceControl:
    """SourceControlPort 的本地 Git 实现。"""

    def __init__(self, *, executable: str = "git") -> None:
        self._executable = executable
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is None:
            try:
                _run_git("--version", cwd=Path.cwd(), executable=self._executable)
                self._available = True
            except GitUnavailable:
                self._available = False
        return self._available

    def is_repository(self, path: Path) -> bool:
        if not self.is_available():
            raise GitUnavailable("git 不可用")
        try:
            result = _run_git(
                "rev-parse", "--is-inside-work-tree",
                cwd=path, executable=self._executable,
            )
        except GitUnavailable:
            return False
        return result.strip() == "true"

    def describe(self, path: Path) -> dict[str, object]:
        """仓库身份、当前分支、HEAD 与 origin URL。"""
        if not self.is_repository(path):
            return {"is_repository": False}
        top_level = _run_git(
            "rev-parse", "--show-toplevel", cwd=path, executable=self._executable
        ).strip()
        branch = _run_git(
            "rev-parse", "--abbrev-ref", "HEAD",
            cwd=path, executable=self._executable,
        ).strip()
        head = _run_git(
            "rev-parse", "HEAD", cwd=path, executable=self._executable
        ).strip()
        remote_url: str | None = None
        try:
            remote_url = _run_git(
                "config", "--get", "remote.origin.url",
                cwd=path, executable=self._executable,
            ).strip()
        except GitUnavailable:
            remote_url = None
        return {
            "is_repository": True,
            "root": top_level,
            "branch": branch,
            "head_commit": head,
            "origin_url": remote_url,
            "github_ref": None
            if remote_url is None
            else (
                {"owner": ref.owner, "repo": ref.repo}
                if (ref := GitHubRef.parse(remote_url))
                else None
            ),
        }

    def changes(self, path: Path) -> dict[str, object]:
        """工作区相对 HEAD 的变更清单（porcelain -z）。"""
        if not self.is_repository(path):
            return {"is_repository": False}
        raw = _run_git(
            "status", "--porcelain=v1", "-z", cwd=path, executable=self._executable
        )
        entries = [item for item in raw.split("\x00") if item]
        added: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        untracked: list[str] = []
        for entry in entries:
            status = entry[:2]
            name = entry[3:]
            if status == "??":
                untracked.append(name)
            elif "D" in status:
                deleted.append(name)
            elif "A" in status:
                added.append(name)
            else:
                modified.append(name)
        return {
            "is_repository": True,
            "added": sorted(added),
            "modified": sorted(modified),
            "deleted": sorted(deleted),
            "untracked": sorted(untracked),
        }

    def upstream_counts(self, path: Path) -> dict[str, object]:
        """基于本地 upstream 引用的 ahead/behind；不做网络 fetch。"""
        if not self.is_repository(path):
            return {"is_repository": False}
        try:
            raw = _run_git(
                "rev-list", "--left-right", "--count", "HEAD...@{u}",
                cwd=path, executable=self._executable,
            ).strip()
        except GitUnavailable:
            return {"is_repository": True, "upstream": None}
        ahead, behind = (int(part) for part in raw.split())
        return {"is_repository": True, "upstream": {"ahead": ahead, "behind": behind}}


class GitHubReadOnlyClient:
    """可选 GitHub HTTPS 只读；失败只降级，不抛给本地闭环。"""

    _API_ROOT = "https://api.github.com"

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self._timeout = timeout_seconds

    def branch_head(self, ref: GitHubRef, branch: str) -> dict[str, object]:
        url = (
            f"{self._API_ROOT}/repos/{ref.owner}/{ref.repo}/"
            f"branches/{urllib.parse.quote(branch)}"
        )
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json", "User-Agent": "aitest"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            return {"state": "unavailable", "detail": str(error)}
        return {"state": "ok", "head_commit": payload["commit"]["sha"]}


__all__ = [
    "GitHubReadOnlyClient",
    "GitHubRef",
    "GitSourceControl",
    "GitUnavailable",
]
