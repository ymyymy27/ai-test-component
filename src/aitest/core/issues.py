"""Issue creation and automatic drafting, with one signature-based dedupe rule."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from aitest.errors import AiTestError
from aitest.models import Issue, IssueOccurrence, Plan, Run, new_id
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

Severity = Literal["P0", "P1", "P2", "P3"]


def issues_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "issues"


def add_issue(
    ws: Workspace,
    project_id: str,
    run_id: str,
    case_id: str,
    title: str,
    *,
    severity: Severity = "P2",
    error_type: str = "unknown",
) -> dict[str, Any]:
    signature = f"{case_id}:{error_type}"
    directory = issues_dir(ws, project_id)
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            if issue.signature != signature:
                continue
            issue.occurrences.append(IssueOccurrence(run_id=run_id, case_id=case_id, at=iso_now()))
            issue.revision += 1
            write_model(path, issue)
            return {"issue_id": issue.issue_id, "existing": True, "revision": issue.revision}
    issue = Issue(
        issue_id=new_id("issue"),
        project_id=project_id,
        signature=signature,
        title=title,
        severity=severity,
        occurrences=[IssueOccurrence(run_id=run_id, case_id=case_id, at=iso_now())],
    )
    write_model(directory / f"{issue.issue_id}.json", issue)
    return {"issue_id": issue.issue_id, "existing": False, "revision": issue.revision}


def draft_failed_issues(ws: Workspace, project_id: str, run_dir: Path) -> list[str]:
    """Create draft issues for failed cases using the same signature dedupe."""
    run = read_model(run_dir / "run.json", Run)
    plan = read_model(run_dir / "plan.json", Plan)
    cases = {case.case_id: case for case in plan.cases}
    issue_ids: list[str] = []
    for run_case in run.cases:
        if run_case.result != "failed":
            continue
        case = cases.get(run_case.case_id)
        if case is None:
            title = f"用例失败：{run_case.case_id}"
        else:
            title = case.title or f"用例失败：{case.expected or case.case_id}"
        result = add_issue(ws, project_id, run.run_id, run_case.case_id, title)
        issue_ids.append(str(result["issue_id"]))
    return issue_ids
