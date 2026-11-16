"""Collect read-only project facts for an AI delivery-draft request.

The draft itself is produced by the model adapter; this module only reads the
project README, one manifest and ``git show --stat``. Git failures never block
the command: the missing stat is reported as an empty string, so the model
cannot invent commands from a failed probe.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from aitest.models import Project, Task

_MAX_EXCERPT = 4000
_GIT_TIMEOUT_SECONDS = 10

_README_NAMES = ("README.md", "readme.md", "README.rst", "readme.rst", "README.txt")
_MANIFEST_NAMES = ("pyproject.toml", "package.json")


def _read_excerpt(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[:_MAX_EXCERPT]


def _first_file(root: Path, names: tuple[str, ...]) -> tuple[str, str]:
    for name in names:
        path = root / name
        if path.is_file():
            return name, _read_excerpt(path)
    return "", ""


def _safe_revision(commit: str) -> str:
    """Keep user input from turning into a git option or a multi-argument spec."""
    text = commit.strip()
    if not text or text.startswith("-") or any(char.isspace() for char in text):
        return "HEAD"
    return text


def _commit_stat(root: Path, commit: str) -> tuple[str, str]:
    """Return ``(commit, stat)``; an empty stat means git could not answer."""
    revision = _safe_revision(commit)
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "show", "--stat", "--oneline", revision],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return commit, ""
    if completed.returncode != 0:
        return commit, ""
    return commit, completed.stdout[:_MAX_EXCERPT]


def build_context(project: Project, task: Task, commit: str = "") -> dict[str, Any]:
    """Return the untrusted context handed to ``draft_delivery``."""
    root = Path(project.root)
    readme_name = ""
    readme = ""
    manifest_name = ""
    manifest = ""
    if root.is_dir():
        readme_name, readme = _first_file(root, _README_NAMES)
        manifest_name, manifest = _first_file(root, _MANIFEST_NAMES)
    resolved_commit, stat = _commit_stat(root, commit) if root.is_dir() else (commit, "")
    return {
        "project": {
            "name": project.name,
            "goal": project.goal,
            "modules": [module.name for module in project.modules],
        },
        "task": {
            "title": task.title,
            "goal": task.goal,
            "scope": task.scope,
            "outputs": task.outputs,
            "acceptance_items": [
                item.text for item in task.acceptance_items if item.status == "active"
            ],
        },
        "commit": resolved_commit,
        "files": {
            "readme": {"path": readme_name, "content": readme},
            "manifest": {"path": manifest_name, "content": manifest},
            "commit_stat": stat,
        },
    }
