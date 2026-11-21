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
    """Explicit manual cases and non-automatable L3 cases wait for a human."""
    if case.verify_method.strip().lower().startswith("manual"):
        return True
    return case.layer == "L3" and not is_automatable(case)


def aggregate_manual(
    steps: list[ManualStep], required_steps: list[str] | None = None
) -> CaseResult:
    """No record means pending; unfinished steps stay incomplete; failures win."""
    if not steps:
        return "manual_pending"
    results = {step.result for step in steps}
    if "failed" in results:
        return "failed"
    if "blocked" in results:
        return "blocked"
    if required_steps:
        recorded = {step.step.strip() for step in steps}
        if any(step.strip() not in recorded for step in required_steps if step.strip()):
            return "manual_pending"
    return "passed"
