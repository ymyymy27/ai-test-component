"""Check that every phase-one FR is traced to real code, commands and tests.

The matrix lives in ``docs/validation/fr-traceability.md`` and is hand-written,
so this test guards it against rot: all FR01—FR18 must appear exactly once,
every referenced path must exist, every command must be dispatchable, and the
coverage label must stay within the two honest values.
"""

from __future__ import annotations

import re
from pathlib import Path

import aitest.cli  # noqa: F401 - importing registers runtime/integration commands
from aitest.commands import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/validation/fr-traceability.md"
REQUIREMENTS = ROOT / "docs/项目文档/一期/02-需求/02-需求规格.md"

EXPECTED_FRS = [f"FR{number:02d}" for number in range(1, 19)]
COLUMNS = 7


def _rows() -> list[list[str]]:
    rows: list[list[str]] = []
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        if re.match(r"\|\s*FR\d\d\s*\|", line) is None:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == COLUMNS, f"unexpected column count in row: {line}"
        rows.append(cells)
    return rows


def _tokens(cell: str) -> list[str]:
    return [token.strip() for token in re.findall(r"`([^`]+)`", cell)]


def test_matrix_lists_every_fr_once() -> None:
    rows = _rows()
    ids = [row[0] for row in rows]
    assert ids == EXPECTED_FRS, f"matrix FR order/coverage mismatch: {ids}"


def test_matrix_frs_match_the_requirement_headings() -> None:
    headings = re.findall(r"^### (FR\d\d) ", REQUIREMENTS.read_text(encoding="utf-8"), re.M)
    assert headings == EXPECTED_FRS
    assert [row[0] for row in _rows()] == headings


def test_every_referenced_file_exists() -> None:
    missing: list[str] = []
    for row in _rows():
        for cell in (row[2], row[4]):
            for token in _tokens(cell):
                if not (ROOT / token).exists():
                    missing.append(f"{row[0]}: {token}")
    assert missing == [], "missing referenced files:\n" + "\n".join(missing)


def test_every_referenced_command_is_in_the_registry() -> None:
    unknown: list[str] = []
    for row in _rows():
        commands = _tokens(row[3])
        assert commands, f"{row[0]} has no CLI command"
        unknown.extend(f"{row[0]}: {command}" for command in commands if command not in REGISTRY)
    assert unknown == [], "unknown commands in the matrix:\n" + "\n".join(unknown)


def test_coverage_labels_are_honest_values() -> None:
    labels = {row[5] for row in _rows()}
    assert labels <= {"完整", "部分"}, f"unexpected coverage labels: {sorted(labels)}"


def test_partial_rows_explain_the_gap() -> None:
    unexplained = [row[0] for row in _rows() if row[5] == "部分" and not row[6]]
    assert unexplained == [], f"partial rows without a reason: {unexplained}"
