"""Expectation-source and precondition checks for published plans.

A case whose ``expectation_source`` cannot be tied back to the selected
acceptance item is marked ``expectation_verified = False``. Such a case may be
executed, but it can never count as passed (see ``core.verdict``). Missing
expectation text or preconditions block publishing instead of becoming a quiet
warning.
"""

from __future__ import annotations

import re

from aitest.errors import invalid_request
from aitest.models import AcceptanceItem, Case, case_item_ids

_UNVERIFIED = "预期未证实：expectation_source 不是验收项原文的子串或等价引用"
_MISSING_PRECONDITION = "缺少前置条件，执行前需人工确认"
_MISSING_EXPECTED = "用例缺少预期"


def _normalize(text: str) -> str:
    cleaned = text.strip().strip("\"'“”‘’「」")
    return " ".join(cleaned.split())


def expectation_matches(source: str, item: AcceptanceItem) -> bool:
    """Return True when the source quotes the item text or references its id."""
    normalized_source = _normalize(source)
    if not normalized_source:
        return False
    normalized_text = _normalize(item.text)
    if normalized_text and normalized_source in normalized_text:
        return True
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(item.item_id)}(?![A-Za-z0-9_])",
            source,
        )
    )


def annotate_cases(cases: list[Case], items: list[AcceptanceItem]) -> list[str]:
    """Set per-case validation flags and return plan-level warnings."""
    by_id = {item.item_id: item for item in items}
    warnings: list[str] = []
    for case in cases:
        case.warnings = []
        ids = case_item_ids(case)
        bound = [by_id.get(item_id) for item_id in ids]
        if not ids or any(item is None for item in bound):
            case.expectation_verified = False
            if ids:
                case.warnings.append("未绑定当前任务的验收项，预期未证实")
        else:
            case.expectation_verified = all(
                expectation_matches(case.expectation_source, item)
                for item in bound
                if item is not None
            )
            if not case.expectation_verified:
                case.warnings.append(_UNVERIFIED)
        if not any(text.strip() for text in case.preconditions):
            case.warnings.append(_MISSING_PRECONDITION)
        warnings.extend(f"{case.case_id}：{warning}" for warning in case.warnings)
    return warnings


def publish_problems(cases: list[Case]) -> list[str]:
    """Blocking defects that must be fixed before a plan can be published."""
    problems: list[str] = []
    for case in cases:
        if not case.expected.strip():
            problems.append(f"{case.case_id}：{_MISSING_EXPECTED}")
        if not any(text.strip() for text in case.preconditions):
            problems.append(f"{case.case_id}：{_MISSING_PRECONDITION}")
    return problems


def ensure_publishable(cases: list[Case]) -> None:
    problems = publish_problems(cases)
    if problems:
        raise invalid_request(
            "计划不能发布：存在未完成的用例",
            "补齐预期与前置条件后重试：" + "；".join(problems),
        )
