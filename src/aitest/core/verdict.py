"""Verdict and coverage derivation. One place only; panels and adapters read these results."""

from __future__ import annotations

from aitest.models import CaseResult, Plan, Run, Verdict, new_id

_FAILURE = "failed"
_PENDING = {"blocked", "error", "not_run", "manual_pending"}

_RANK: dict[CaseResult, int] = {
    "failed": 4,
    "blocked": 3,
    "error": 3,
    "not_run": 3,
    "manual_pending": 3,
    "passed": 2,
}


def worst_result(results: list[CaseResult]) -> CaseResult:
    if not results:
        return "not_run"
    return max(results, key=lambda item: _RANK[item])


def item_outcomes(plan: Plan, run: Run) -> dict[str, CaseResult]:
    by_case: dict[str, CaseResult] = {case.case_id: case.result for case in run.cases}
    outcomes: dict[str, CaseResult] = {}
    for case in plan.cases:
        if case.acceptance_item_id is None:
            continue
        result = by_case.get(case.case_id, "not_run")
        current = outcomes.get(case.acceptance_item_id)
        if current is None or _RANK[result] > _RANK[current]:
            outcomes[case.acceptance_item_id] = result
    return outcomes


def verdict(plan: Plan, run: Run) -> Verdict:
    outcomes = item_outcomes(plan, run)
    if not outcomes:
        if not run.cases:
            return "not_applicable"
        results = [case.result for case in run.cases]
        if any(result == "failed" for result in results):
            return "failed"
        if any(result in _PENDING for result in results):
            return "incomplete"
        return "passed" if all(result == "passed" for result in results) else "incomplete"
    if any(result == _FAILURE for result in outcomes.values()):
        return "failed"
    if any(result in _PENDING for result in outcomes.values()):
        return "incomplete"
    return "passed"


def coverage(plan: Plan, run: Run) -> dict[str, int]:
    selected = plan.scope.acceptance_item_ids
    applicable = coverage_applicable(plan, selected)
    outcomes = item_outcomes(plan, run)
    return {
        "selected": len(selected),
        "applicable": applicable,
        "covered": len(outcomes),
        "passed": sum(1 for value in outcomes.values() if value == "passed"),
        "failed": sum(1 for value in outcomes.values() if value == "failed"),
        "incomplete": sum(1 for value in outcomes.values() if value in _PENDING),
    }


def coverage_applicable(plan: Plan, selected: list[str]) -> int:
    return max(len(selected), plan.applicable_count)


def build_report_id() -> str:
    return new_id("rep")
