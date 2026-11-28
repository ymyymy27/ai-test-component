"""Collect read-only project facts for AI module suggestions.

Only names, the README, one manifest and one entry file are read. Nothing is
written: the command returns suggestions and the user decides which ones to
register through ``module.add``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aitest.core.repo_index import DEFAULT_SKIP_DIRS
from aitest.models import Project

_MAX_EXCERPT = 3000
_MAX_ENTRIES = 500
_MAX_DEPTH = 3

_SKIP_DIRS = DEFAULT_SKIP_DIRS

_README_NAMES = ("README.md", "readme.md", "README.rst", "readme.rst", "README.txt")
_MANIFEST_NAMES = ("pyproject.toml", "package.json", "setup.py", "go.mod", "Cargo.toml")
_ENTRY_NAMES = (
    "main.py",
    "app.py",
    "__main__.py",
    "manage.py",
    "server.py",
    "index.js",
    "src/main.py",
    "src/app.py",
    "src/index.ts",
    "src/main.ts",
)


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


def _tree(root: Path) -> list[str]:
    entries: list[str] = []
    if not root.is_dir():
        return entries

    def walk(directory: Path, depth: int, prefix: str) -> None:
        if len(entries) >= _MAX_ENTRIES:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            return
        for child in children:
            if len(entries) >= _MAX_ENTRIES:
                return
            if child.name in _SKIP_DIRS:
                continue
            relative = f"{prefix}{child.name}"
            try:
                if child.is_dir():
                    entries.append(f"{relative}/")
                    if depth < _MAX_DEPTH:
                        walk(child, depth + 1, f"{relative}/")
                else:
                    entries.append(relative)
            except OSError:
                continue

    walk(root, 0, "")
    return entries


def build_context(project: Project) -> dict[str, Any]:
    """Return the untrusted context handed to ``suggest_modules``."""
    root = Path(project.root)
    readme_name, readme = _first_file(root, _README_NAMES) if root.is_dir() else ("", "")
    manifest_name, manifest = _first_file(root, _MANIFEST_NAMES) if root.is_dir() else ("", "")
    entry_name, entry = _first_file(root, _ENTRY_NAMES) if root.is_dir() else ("", "")
    return {
        "project": {
            "name": project.name,
            "goal": project.goal,
            "registered_modules": [
                {"name": module.name, "responsibility": module.responsibility}
                for module in project.modules
            ],
        },
        "files": {
            "tree": _tree(root),
            "readme": {"path": readme_name, "content": readme},
            "manifest": {"path": manifest_name, "content": manifest},
            "entry": {"path": entry_name, "content": entry},
        },
    }


def invalid_dependencies(
    suggestions: list[dict[str, Any]], registered: set[str]
) -> list[str]:
    """Return dependency targets that are neither suggested nor registered."""
    names = {str(item.get("name", "")).strip() for item in suggestions}
    allowed = names | registered
    missing: list[str] = []
    for item in suggestions:
        module_name = str(item.get("name", "")).strip()
        for dependency in item.get("deps", []):
            if not isinstance(dependency, dict):
                continue
            target = str(dependency.get("to", "")).strip()
            if target and target not in allowed:
                missing.append(f"{module_name} -> {target}")
    return missing
