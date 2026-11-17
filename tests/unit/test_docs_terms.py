"""Reject old system-level concepts that are only allowed in "not doing" prose.

Current documents must not describe ``常驻核心``, ``命名管道``, ``提交根``,
``证据等级 A``, ``计划许可``, ``逐动作授权`` or ``MCP`` as capabilities. A hit is
tolerated when the same line makes the negation explicit (不做/禁止/已废止/
归档/移除/没有), which is how the requirements and architecture docs phrase the
out-of-scope list. 历史材料已删除，只扫描现行项目文档与仓库入口。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TERMS = (
    "常驻核心",
    "命名管道",
    "提交根",
    "证据等级",
    "A 级",
    "A—D",
    "计划许可",
    "许可账本",
    "逐动作授权",
    "MCP",
    "Grant",
    "QuerySpec",
    "不可变提交",
    "覆盖契约",
)
ALLOWED_MARKERS = (
    "不做",
    "禁止",
    "废止",
    "归档",
    "移除",
    "没有",
    "不再",
    "不进入",
    "不实现",
    "不支持",
    "不属于",
    "不计入",
    "不计算",
    "不自动",
    "已删除",
)

DOCS = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
)
DOCS += tuple(sorted((ROOT / "docs/项目文档").rglob("*.md")))


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


def test_scanner_flags_an_old_grade_sentence_without_negation() -> None:
    line = "只有 full 计算证据等级；A 级不等于业务通过。"
    assert any(term in line for term in TERMS)
    assert not any(marker in line for marker in ALLOWED_MARKERS)
