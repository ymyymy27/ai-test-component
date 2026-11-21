"""Issue creation and automatic drafting, with one signature-based dedupe rule."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Literal

from aitest.config import known_secret_values
from aitest.core.evidence import evidence_path
from aitest.errors import AiTestError
from aitest.model_config import build_client, load_project_config
from aitest.models import Issue, IssueOccurrence, Plan, Run, case_item_ids, new_id
from aitest.security import redact
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

Severity = Literal["P0", "P1", "P2", "P3"]

_ERROR_HINT = re.compile(
    r"(?i)(assertionerror|assert\w*|traceback|error|exception|failed|timeout|失败|超时|异常)"
)


def issues_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "issues"


def _signature(case_id: str, error_type: str, stack_key: str) -> str:
    normalized = " ".join(stack_key.split())
    if not normalized:
        return f"{case_id}:{error_type}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{case_id}:{error_type}:{digest}"


def _case_error(run_dir: Path, case_id: str, run: Run) -> tuple[str, str]:
    """Derive (error type, key stack/assertion line) from one failed case."""
    run_case = next((item for item in run.cases if item.case_id == case_id), None)
    if run_case is None:
        return "unknown", ""
    messages = [attempt.message for attempt in run_case.attempts if attempt.message]
    evidence: list[str] = []
    for ref in run_case.evidence_refs[:3]:
        path = evidence_path(run_dir, ref)
        if path is None or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if _ERROR_HINT.search(line):
                evidence.append(line.strip())
                break
    key = next((line for line in evidence if line), "")
    if not key and messages:
        key = messages[-1].strip()
    error_type = "unknown"
    for candidate in [*messages, *evidence]:
        match = _ERROR_HINT.search(candidate or "")
        if match:
            error_type = match.group(1).lower()
            break
    return error_type, key[:400]


def add_issue(
    ws: Workspace,
    project_id: str,
    run_id: str,
    case_id: str,
    title: str,
    *,
    severity: Severity = "P2",
    error_type: str = "unknown",
    stack_key: str = "",
    evidence_refs: list[str] | None = None,
    run_dir: Path | None = None,
    steps: list[str] | None = None,
    expected: str = "",
    actual: str = "",
    item_no: str = "",
    item_text: str = "",
    unstable: bool = False,
) -> dict[str, Any]:
    if run_dir is not None and error_type == "unknown" and not stack_key:
        run = read_model(run_dir / "run.json", Run)
        error_type, stack_key = _case_error(run_dir, case_id, run)
    secrets = known_secret_values([load_project_config(ws, project_id)])
    title = redact(title, secrets)
    item_no = redact(item_no, secrets)
    item_text = redact(item_text, secrets)
    expected = redact(expected, secrets)
    actual = redact(actual, secrets)
    steps = [redact(step, secrets) for step in (steps or [])]
    signature = _signature(case_id, error_type, stack_key)
    refs = list(evidence_refs or [])
    directory = issues_dir(ws, project_id)
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            if issue.signature != signature:
                continue
            issue.occurrences.append(
                IssueOccurrence(
                    run_id=run_id, case_id=case_id, at=iso_now(), evidence_refs=refs
                )
            )
            issue.last_result = "failed"
            if issue.status == "closed":
                # A closed issue that fails again is no longer closed; keep the
                # occurrence and require a fresh retest before closing again.
                issue.status = "retest"
                issue.disposition = None
                issue.reason = "关闭后再次复现，待复测"
            if unstable:
                issue.unstable = True
            issue.revision += 1
            write_model(path, issue)
            return {"issue_id": issue.issue_id, "existing": True, "revision": issue.revision}
    issue = Issue(
        issue_id=new_id("issue"),
        project_id=project_id,
        signature=signature,
        title=title,
        item_no=item_no,
        item_text=item_text,
        steps=list(steps or []),
        expected=expected,
        actual=actual,
        severity=severity,
        occurrences=[
            IssueOccurrence(run_id=run_id, case_id=case_id, at=iso_now(), evidence_refs=refs)
        ],
        last_result="failed",
        unstable=unstable,
    )
    write_model(directory / f"{issue.issue_id}.json", issue)
    return {"issue_id": issue.issue_id, "existing": False, "revision": issue.revision}


def _mark_unstable(ws: Workspace, project_id: str, case_id: str) -> None:
    """A later pass on a previously failed case is suspicious, not a silent fix."""
    directory = issues_dir(ws, project_id)
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        try:
            issue = read_model(path, Issue)
        except AiTestError:
            continue
        if issue.status == "closed" or not issue.signature.startswith(f"{case_id}:"):
            continue
        if issue.last_result != "passed":
            issue.last_result = "passed"
            issue.unstable = True
            issue.revision += 1
            write_model(path, issue)


def _excerpt(run_dir: Path, ref: str, secrets: list[str]) -> str:
    path = run_dir / ref
    if not path.is_file():
        return ""
    try:
        return redact(path.read_text(encoding="utf-8", errors="replace")[:2000], secrets)
    except OSError:
        return ""


def _draft_issue(
    ws: Workspace,
    project_id: str,
    run_dir: Path,
    run: Run,
    plan: Plan,
    run_case: Any,
    secrets: list[str],
) -> dict[str, Any]:
    case = next((item for item in plan.cases if item.case_id == run_case.case_id), None)
    messages = [attempt.message for attempt in run_case.attempts if attempt.message]
    context = {
        "case": (
            {
                "case_id": case.case_id,
                "title": case.title,
                "steps": case.steps,
                "expected": case.expected,
            }
            if case is not None
            else {"case_id": run_case.case_id}
        ),
        "result": run_case.result,
        "messages": messages,
        "evidence": [
            _excerpt(run_dir, ref, secrets) for ref in run_case.evidence_refs[:3]
        ],
    }
    client = build_client(ws, project_id)
    if client is not None:
        try:
            draft = client.draft_issue(context)
            if draft.get("title"):
                return draft
        except AiTestError:
            pass
    title = (
        (case.title if case is not None and case.title else "")
        or (f"用例失败：{case.expected}" if case is not None else f"用例失败：{run_case.case_id}")
    )
    return {
        "title": title,
        "steps": list(case.steps) if case is not None else [],
        "expected": case.expected if case is not None else "",
        "actual": messages[-1] if messages else "",
    }


def _previous_case_result(
    ws: Workspace, project_id: str, current_run_id: str, case_id: str
) -> str | None:
    runs_dir = ws.project_dir(project_id) / "runs"
    if not runs_dir.is_dir():
        return None
    candidates: list[tuple[str, str]] = []
    for path in runs_dir.glob("*/run.json"):
        try:
            data = read_model(path, Run)
        except AiTestError:
            continue
        if data.run_id == current_run_id:
            continue
        result = next(
            (item.result for item in data.cases if item.case_id == case_id), None
        )
        if result is not None:
            candidates.append((data.started_at, result))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def ensure_run_issues(ws: Workspace, project_id: str, run_dir: Path) -> list[str]:
    """Draft issues for a run's failed cases if post-processing has not done it.

    Report generation can race the worker's best-effort post-processing. Without
    this guard a report could truthfully say "failed" while its issue list is
    empty, which reads like no problem was recorded.
    """
    run = read_model(run_dir / "run.json", Run)
    if not any(case.result == "failed" for case in run.cases):
        return []
    directory = issues_dir(ws, project_id)
    covered: set[tuple[str, str]] = set()
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            for item in issue.occurrences:
                if item.run_id == run.run_id:
                    covered.add((item.run_id, item.case_id))
    return draft_failed_issues(ws, project_id, run_dir, covered)


def draft_failed_issues(
    ws: Workspace,
    project_id: str,
    run_dir: Path,
    covered: set[tuple[str, str]] | None = None,
) -> list[str]:
    """Create draft issues for failed cases and flag inconsistent later passes."""
    run = read_model(run_dir / "run.json", Run)
    plan = read_model(run_dir / "plan.json", Plan)
    secrets = known_secret_values([load_project_config(ws, project_id)])
    issue_ids: list[str] = []
    for run_case in run.cases:
        if run_case.result == "passed":
            _mark_unstable(ws, project_id, run_case.case_id)
            continue
        if run_case.result != "failed":
            continue
        if covered and (run.run_id, run_case.case_id) in covered:
            continue
        draft = _draft_issue(ws, project_id, run_dir, run, plan, run_case, secrets)
        previous = _previous_case_result(ws, project_id, run.run_id, run_case.case_id)
        plan_case = next(
            (item for item in plan.cases if item.case_id == run_case.case_id), None
        )
        item_id = case_item_ids(plan_case)[0] if plan_case is not None else ""
        item_text = plan.item_texts.get(item_id, "") if item_id else ""
        result = add_issue(
            ws,
            project_id,
            run.run_id,
            run_case.case_id,
            draft["title"],
            item_text=item_text,
            steps=draft["steps"],
            expected=draft["expected"],
            actual=draft["actual"],
            evidence_refs=list(run_case.evidence_refs),
            run_dir=run_dir,
            unstable=previous == "passed",
        )
        issue_ids.append(str(result["issue_id"]))
    return issue_ids
