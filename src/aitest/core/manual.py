"""Manual L3 execution helpers shared by the run command and the worker."""

from __future__ import annotations

from aitest.models import Case, CaseResult, ManualStep


def is_automatable(case: Case) -> bool:
    method = case.verify_method.strip()
    if not method or method.lower().startswith("manual"):
        return False
    prefix, _, rest = method.partition(":")
    return bool(rest.strip()) or prefix not in {"command", "script", "http", "playwright"}


def is_manual_case(case: Case) -> bool:
    """L3 cases without an automatable verification method wait for a human."""
    return case.layer == "L3" and not is_automatable(case)


def aggregate_manual(steps: list[ManualStep]) -> CaseResult:
    """No record means pending; failures win over blocks; all passed means passed."""
    if not steps:
        return "manual_pending"
    results = {step.result for step in steps}
    if "failed" in results:
        return "failed"
    if "blocked" in results:
        return "blocked"
    return "passed"
