"""Reject old system-level concepts that are only allowed in "not doing" prose.

Current documents must not describe ``常驻核心``, ``命名管道``, ``提交根``,
``证据等级 A``, ``计划许可``, ``逐动作授权`` or ``MCP`` as capabilities. A hit is
tolerated when the same line makes the negation explicit (不做/禁止/已废止/
归档/移除/没有), which is how the requirements and architecture docs phrase the
out-of-scope list. Historical records under ``docs/归档/`` are not scanned.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TERMS = ("常驻核心", "命名管道", "提交根", "证据等级 A", "计划许可", "逐动作授权", "MCP")
ALLOWED_MARKERS = ("不", "禁止", "废止", "归档", "移除", "没有")

DOCS = (
    ROOT / "docs/README.md",
    ROOT / "AGENTS.md",
    ROOT / "docs/现状.md",
)
DOCS += tuple(sorted((ROOT / "docs/项目文档/一期").rglob("*.md")))


def scan_lines() -> list[str]:
    hits: list[str] = []
    for path in DOCS:
        relative = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            matched = [term for term in TERMS if term in line]
            if not matched:
                continue
            if any(marker in line for marker in ALLOWED_MARKERS):
                continue
            hits.append(f"{relative}:{number}: {matched}: {line.strip()}")
    return hits


def test_current_docs_do_not_describe_removed_components() -> None:
    hits = scan_lines()
    assert hits == [], "old concepts used as capability descriptions:\n" + "\n".join(hits)


def test_scanner_tolerates_explicit_negations_and_archives() -> None:
    line = "- 不做常驻核心、命名管道、MCP；旧实现已归档。"
    assert any(term in line for term in TERMS)
    assert any(marker in line for marker in ALLOWED_MARKERS)


def test_scanner_flags_a_plain_capability_claim() -> None:
    line = "| CLI/MCP | 同一套 dispatch |"
    assert any(term in line for term in TERMS)
    assert not any(marker in line for marker in ALLOWED_MARKERS)
