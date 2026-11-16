"""Expectation-source and precondition checks for published plans.

A case whose ``expectation_source`` cannot be tied back to the selected
acceptance item is marked ``expectation_verified = False`` instead of being
silently accepted. Missing preconditions only add a warning; neither check
blocks publishing, so a plan with unverified expectations stays usable.
"""

from __future__ import annotations

from aitest.models import AcceptanceItem, Case

_UNVERIFIED = "预期未证实：expectation_source 不是验收项原文的子串或等价引用"
_MISSING_PRECONDITION = "缺少前置条件，执行前需人工确认"


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
    return item.item_id in source


def annotate_cases(cases: list[Case], items: list[AcceptanceItem]) -> list[str]:
    """Set per-case validation flags and return plan-level warnings."""
    by_id = {item.item_id: item for item in items}
    warnings: list[str] = []
    for case in cases:
        case.warnings = []
        item = by_id.get(case.acceptance_item_id or "")
        if item is None:
            case.expectation_verified = False
            case.warnings.append("未绑定当前任务的验收项，预期未证实")
        else:
            case.expectation_verified = expectation_matches(case.expectation_source, item)
            if not case.expectation_verified:
                case.warnings.append(_UNVERIFIED)
        if not case.preconditions:
            case.warnings.append(_MISSING_PRECONDITION)
        warnings.extend(f"{case.case_id}：{warning}" for warning in case.warnings)
    return warnings
