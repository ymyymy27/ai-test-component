"""Verdict and coverage derivation. One place only; panels and adapters read these results.

A selected acceptance item without any executed case is not a pass: it stays
``not_run`` and makes the verdict ``incomplete``. A case whose expectation could
not be tied to the acceptance-item text may execute but can never count as
``passed``; its item is reported as ``incomplete`` instead.
"""

from __future__ import annotations

from typing import Literal

from aitest.models import CaseResult, Plan, Run, Verdict, case_item_ids, new_id

_FAILURE = "failed"
_PENDING = {"blocked", "error", "not_run", "manual_pending"}
_UNATTEMPTED = {"not_run", "manual_pending"}

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


def case_results(plan: Plan, run: Run) -> dict[str, CaseResult]:
    """Effective per-case results: an unverified expectation cannot pass."""
    by_case = {case.case_id: case for case in plan.cases}
    results: dict[str, CaseResult] = {}
    for run_case in run.cases:
        case = by_case.get(run_case.case_id)
        result = run_case.result
        if (
            case is not None
            and case_item_ids(case)
            and not case.expectation_verified
            and result == "passed"
        ):
            # The case ran, but its expectation was never tied to the item text.
            result = "blocked"
        if (
            case is not None
            and case_item_ids(case)
            and case.coverage_kind in {"file", "shared", "unverified"}
            and result == "passed"
        ):
            # Whole-file or shared evidence cannot prove this specific item.
            result = "blocked"
        results[run_case.case_id] = result
    return results


def _item_case_results(plan: Plan, run: Run) -> dict[str, list[CaseResult]]:
    results = case_results(plan, run)
    by_item: dict[str, list[CaseResult]] = {
        item_id: [] for item_id in plan.scope.acceptance_item_ids
    }
    for case in plan.cases:
        ids = case_item_ids(case)
        if not ids:
            continue
        result = results.get(case.case_id, "not_run")
        for item_id in ids:
            if item_id in by_item:
                by_item[item_id].append(result)
    return by_item


def item_outcomes(plan: Plan, run: Run) -> dict[str, CaseResult]:
    """Worst effective result per selected acceptance item.

    Every selected item appears. Items without any plan case get ``not_run`` so
    a scope gap can never disappear from the verdict.
    """
    return {
        item_id: worst_result(results)
        for item_id, results in _item_case_results(plan, run).items()
    }


def _project_results(plan: Plan, run: Run) -> list[CaseResult]:
    """Results of project-level checks (L1 cases without an acceptance item)."""
    results = case_results(plan, run)
    return [
        results.get(case.case_id, "not_run")
        for case in plan.cases
        if not case_item_ids(case)
    ]


def _l1_outcome(run: Run) -> Literal["failed", "incomplete"] | None:
    """Project-level checks outside plan cases still affect the run verdict.

    ``run.l1`` is written by the worker before case execution. A failed build or
    entry import cannot be hidden just because the plan has no L1 case for it.
    """
    l1 = run.l1
    if not isinstance(l1, dict):
        return None
    checks: list[bool] = []
    build = l1.get("build")
    if isinstance(build, dict) and "ok" in build:
        checks.append(bool(build.get("ok")))
    entries = l1.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and "ok" in entry:
                checks.append(bool(entry.get("ok")))
    if checks and not all(checks):
        return "failed"
    if l1.get("cancelled") is True:
        return "incomplete"
    return None


def _l1_executed(l1: dict[str, object]) -> bool:
    """Whether any project-level check actually ran.

    ``l1`` can be an empty object (for example the panel saves an empty
    ``l1`` section). An empty object must not be reported as a pass.
    """
    build = l1.get("build")
    if isinstance(build, dict) and build.get("exit_code") is not None:
        return True
    entries = l1.get("entries")
    return isinstance(entries, list) and len(entries) > 0


def verdict(plan: Plan, run: Run) -> Verdict:
    if run.kind == "project_check":
        l1_result = _l1_outcome(run)
        if l1_result is not None:
            return l1_result
        if run.l1 is None or not _l1_executed(run.l1):
            return "not_applicable"
        return "passed"
    outcomes = item_outcomes(plan, run)
    project_results = _project_results(plan, run)
    results = [*outcomes.values(), *project_results]
    if any(result == _FAILURE for result in results):
        return "failed"
    l1_result = _l1_outcome(run)
    if l1_result == "failed":
        return "failed"
    if not results:
        return "incomplete" if plan.applicable_count > 0 else "not_applicable"
    if l1_result == "incomplete" or any(result in _PENDING for result in results):
        return "incomplete"
    return "passed"


def coverage(plan: Plan, run: Run) -> dict[str, int]:
    by_item = _item_case_results(plan, run)
    outcomes = {item_id: worst_result(results) for item_id, results in by_item.items()}
    selected = plan.scope.acceptance_item_ids
    return {
        "selected": len(selected),
        "applicable": coverage_applicable(plan, selected),
        "covered": sum(
            1
            for item_id in selected
            if any(result not in _UNATTEMPTED for result in by_item.get(item_id, []))
        ),
        "passed": sum(1 for value in outcomes.values() if value == "passed"),
        "failed": sum(1 for value in outcomes.values() if value == "failed"),
        "incomplete": sum(1 for value in outcomes.values() if value in _PENDING),
    }


def coverage_applicable(plan: Plan, selected: list[str]) -> int:
    """M is the frozen applicable set; it can never be smaller than S."""
    return max(len(selected), plan.applicable_count)


def build_report_id() -> str:
    return new_id("rep")
