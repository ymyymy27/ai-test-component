"""Guard against reintroducing the removed system-level components.

The lightweight plugin is explicitly not allowed to grow a database, a daemon,
a migration framework, an MCP server or a permission/grant ledger. The check
looks at imports and string literals only; client-side ``urllib``/``subprocess``
and the file lock (``portalocker``) stay allowed.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src/aitest"

FORBIDDEN_ROOTS = {
    "sqlite3",
    "sqlalchemy",
    "asyncpg",
    "pymysql",
    "psycopg",
    "psycopg2",
    "socket",
    "multiprocessing",
}
FORBIDDEN_MODULES = {"http.server"}
FORBIDDEN_STRINGS = ("daemon", "migrat", "mcp", "grant", "evidence_level")


def _module_hit(name: str) -> bool:
    root = name.split(".")[0]
    if root in FORBIDDEN_ROOTS:
        return True
    if any(term in root for term in FORBIDDEN_STRINGS):
        return True
    return any(name == module or name.startswith(f"{module}.") for module in FORBIDDEN_MODULES)


def scan_source(path: Path) -> list[str]:
    """Return ``path:line: reason: text`` for every forbidden import or string."""

    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    hits: list[str] = []

    def add(lineno: int, reason: str) -> None:
        text = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        hits.append(f"{path}:{lineno}: {reason}: {text}")

    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _module_hit(alias.name):
                    add(node.lineno, f"forbidden import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _module_hit(module):
                add(node.lineno, f"forbidden import from {module}")
            elif module == "http" and any(alias.name == "server" for alias in node.names):
                add(node.lineno, "forbidden import from http.server")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for word in FORBIDDEN_STRINGS:
                if word in lowered:
                    add(node.lineno, f"forbidden string {word!r}")
                    break

    return hits


def test_src_has_no_database_daemon_migration_permission_or_mcp() -> None:
    hits: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        hits.extend(scan_source(path))
    assert hits == [], "forbidden system-level components found:\n" + "\n".join(hits)


def test_scanner_detects_imports_strings_and_http_server(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text(REFERENCE_SOURCE, encoding="utf-8")
    hits = scan_source(path)
    reasons = " ".join(hits)
    assert "sqlite3" in reasons
    assert "http.server" in reasons
    assert "import mcp" in reasons
    assert "'daemon'" in reasons
    assert "'evidence_level'" in reasons
    assert all(str(path) in hit for hit in hits)


def test_scanner_allows_client_urllib_subprocess_and_portalocker(tmp_path: Path) -> None:
    path = tmp_path / "allowed.py"
    path.write_text(ALLOWED_SOURCE, encoding="utf-8")
    assert scan_source(path) == []


REFERENCE_SOURCE = """\
import mcp
import sqlite3

from http import server

FLAG = "daemon"
LEVEL = "evidence_level"
"""

ALLOWED_SOURCE = """\
import subprocess
import urllib.request

import portalocker

LOCK = portalocker.LOCK_EX
"""
