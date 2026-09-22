"""Reject inconsistent core/panel/extension versions and mismatched release tags."""

import ast
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected: str = project["project"]["version"]
    for directory in ("src/aitest/resources/panel", "integrations/trae"):
        for filename in ("package.json", "package-lock.json"):
            package = json.loads((ROOT / directory / filename).read_text(encoding="utf-8"))
            if package["version"] != expected:
                raise ValueError(f"Version mismatch: {directory}/{filename}")
    tree = ast.parse((ROOT / "src/aitest/__init__.py").read_text(encoding="utf-8"))
    values = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    if values != [expected]:
        raise ValueError("Core __version__ differs from pyproject.toml")
    return expected


if __name__ == "__main__":
    current = version()
    if len(sys.argv) > 1 and sys.argv[1] != f"v{current}":
        raise SystemExit(f"Release tag must be v{current}")
    print(current)
