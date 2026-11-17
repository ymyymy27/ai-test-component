"""Build a factual report for one run.

``report.generate`` and the run worker both call :func:`build_report`, so the
CLI response, the stored report and the automatic post-run report cannot drift.
Missing data is stored as an empty list or ``None``; nothing is guessed.
"""

from __future__ import annotations

import hashlib
import mimetypes
import platform
from pathlib import Path
from typing import Any, Literal

from aitest.adapters.github import read_commit_files
from aitest.adapters.model import ModelClient
from aitest.ai_features import collect_dependencies, judge_mock, summarize_report
from aitest.config import known_secret_values
from aitest.core.scope import uncovered_items as scope_uncovered
from aitest.core.verdict import coverage, item_outcomes, verdict
from aitest.errors import AiTestError
from aitest.model_config import build_client, load_project_config
from aitest.models import Delivery, Issue, Module, Plan, Project, Report, Run, Task, new_id
from aitest.security import redact_tree
from aitest.storage.files import read_json, read_model, write_model
from aitest.storage.workspace import Workspace

_OUTCOME_LABEL = {
    "passed": "本范围通过",
    "failed": "本范围存在失败",
    "incomplete": "本次运行未完成",
    "not_applicable": "本次范围不适用",
}


def _issues_summary(
    ws: Workspace, project_id: str, run: Run
) -> tuple[list[dict[str, Any]], int, int]:
    """Return project issue summaries plus run-related counts."""
    directory = ws.project_dir(project_id) / "issues"
    summary: list[dict[str, Any]] = []
    related: set[str] = set()
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            if any(item.run_id == run.run_id for item in issue.occurrences):
                related.add(issue.issue_id)
            summary.append(
                {
                    "issue_id": issue.issue_id,
                    "title": issue.title,
                    "status": issue.status,
                    "severity": issue.severity,
                    "occurrences": len(issue.occurrences),
                    "run_occurrences": sum(
                        1 for item in issue.occurrences if item.run_id == run.run_id
                    ),
                    "steps": list(issue.steps),
                    "expected": issue.expected,
                    "actual": issue.actual,
                    "unstable": issue.unstable,
                    "disposition": issue.disposition,
                    "reason": issue.reason,
                }
            )
    failures = sum(1 for case in run.cases if case.result == "failed")
    return summary, len(related), failures


def _github_record(
    ws: Workspace, project_id: str, source_commit: str | None
) -> tuple[dict[str, Any] | None, list[str]]:
    """Read the GitHub record for this run, never substituting a different commit."""
    directory = ws.project_dir(project_id) / "github"
    if not directory.is_dir() or not source_commit:
        return None, []
    path = directory / f"{source_commit}.json"
    if not path.is_file():
        return None, []
    try:
        record = read_json(path)
    except AiTestError:
        return None, []
    raw_files = record.get("files")
    files = [str(item) for item in raw_files] if isinstance(raw_files, list) else []
    if not files:
        repo = record.get("repo")
        commit = record.get("commit")
        if isinstance(repo, str) and repo and isinstance(commit, str) and commit:
            files = read_commit_files(repo, commit)
    return record, files


def _match_module(value: str, modules: list[Module]) -> str | None:
    text = value.strip().replace("\\", "/").lower()
    if not text:
        return None
    segments = [segment for segment in text.split("/") if segment]
    for module in modules:
        name = module.name.strip()
        if not name:
            continue
        lowered = name.lower()
        if text == lowered or lowered in segments:
            return module.name
        if lowered in text and (len(lowered) >= 3 or not lowered.isascii()):
            return module.name
    return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _module_impact(
    delivery: Delivery | None, modules: list[Module], github_files: list[str]
) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    unmapped: list[str] = []
    for entry in delivery.changed_modules if delivery is not None else []:
        matched = _match_module(entry, modules)
        if matched is None:
            unmapped.append(entry)
        else:
            changed.append(matched)
    for file_path in github_files:
        matched = _match_module(file_path, modules)
        if matched is None:
            unmapped.append(file_path)
        else:
            changed.append(matched)
    return _dedupe(changed), _dedupe(unmapped)


def _environment(ws: Workspace, project_id: str) -> dict[str, Any]:
    config = load_project_config(ws, project_id)
    services: list[dict[str, Any]] = []
    raw_services = config.get("services")
    if isinstance(raw_services, dict):
        for name in sorted(raw_services):
            value = raw_services[name]
            if not isinstance(value, dict):
                continue
            services.append(
                {
                    "name": name,
                    "health_configured": bool(str(value.get("health") or "").strip()),
                    "start_configured": bool(str(value.get("start") or "").strip()),
                }
            )
    return {"python": platform.python_version(), "services": services}


def _load_delivery(ws: Workspace, project_id: str, task_id: str) -> Delivery | None:
    path = ws.project_dir(project_id) / "deliveries" / f"{task_id}.json"
    if not path.is_file():
        return None
    return read_model(path, Delivery)


def _load_task(ws: Workspace, project_id: str, task_id: str) -> Task | None:
    path = ws.project_dir(project_id) / "tasks" / f"{task_id}.json"
    if not path.is_file():
        return None
    return read_model(path, Task)


def _layers(plan: Plan) -> dict[str, int]:
    counts = {"L1": 0, "L2": 0, "L3": 0}
    for case in plan.cases:
        counts[case.layer] += 1
    return counts


def _case_details(plan: Plan, run: Run) -> list[dict[str, Any]]:
    by_case = {case.case_id: case for case in run.cases}
    details: list[dict[str, Any]] = []
    for case in plan.cases:
        run_case = by_case.get(case.case_id)
        details.append(
            {
                "case_id": case.case_id,
                "acceptance_item_id": case.acceptance_item_id,
                "layer": case.layer,
                "title": case.title,
                "expected": case.expected,
                "expectation_verified": case.expectation_verified,
                "warnings": list(case.warnings),
                "result": run_case.result if run_case is not None else "not_run",
                "evidence_refs": list(run_case.evidence_refs) if run_case is not None else [],
                "attempts": [
                    {
                        "attempt": attempt.attempt,
                        "result": attempt.result,
                        "message": attempt.message,
                        "evidence_refs": list(attempt.evidence_refs),
                    }
                    for attempt in run_case.attempts
                ]
                if run_case is not None
                else [],
            }
        )
    return details


def _item_details(plan: Plan, run: Run, task: Task | None) -> list[dict[str, Any]]:
    """Acceptance-item main table: worst result and the cases behind it."""
    outcomes = item_outcomes(plan, run)
    by_item: dict[str, list[str]] = {}
    for case in plan.cases:
        if case.acceptance_item_id is not None:
            by_item.setdefault(case.acceptance_item_id, []).append(case.case_id)
    items = {item.item_id: item for item in task.acceptance_items} if task else {}
    details: list[dict[str, Any]] = []
    for item_id in plan.scope.acceptance_item_ids:
        item = items.get(item_id)
        details.append(
            {
                "item_id": item_id,
                "text": item.text if item is not None else "",
                "module_id": item.module_id if item is not None else None,
                "selected": True,
                "result": outcomes.get(item_id, "not_run"),
                "case_ids": by_item.get(item_id, []),
            }
        )
    selected = set(plan.scope.acceptance_item_ids)
    for item in items.values():
        if item.item_id in selected or item.status != "active":
            continue
        details.append(
            {
                "item_id": item.item_id,
                "text": item.text,
                "module_id": item.module_id,
                "selected": False,
                "result": "not_selected",
                "case_ids": [],
            }
        )
    return details


def _l1_details(plan: Plan, run: Run) -> dict[str, Any] | None:
    """Project-level checks, kept out of the acceptance-item counts."""
    cases = {
        case["case_id"]: case
        for case in _case_details(plan, run)
        if case["acceptance_item_id"] is None
    }
    if run.l1 is None and not cases:
        return None
    blocking: list[str] = []
    for detail in cases.values():
        if detail["result"] in {"blocked", "error"}:
            for attempt in detail["attempts"]:
                message = str(attempt.get("message") or "")
                if message:
                    blocking.append(f"{detail['case_id']}：{message}")
    return {
        "build": (run.l1 or {}).get("build"),
        "entries": (run.l1 or {}).get("entries", []),
        "cases": list(cases.values()),
        "blocking_reasons": _dedupe(blocking),
    }


def _evidence_index(run_dir: Path, run: Run) -> list[dict[str, Any]]:
    refs = list(run.evidence_refs)
    for case in run.cases:
        refs.extend(case.evidence_refs)
    index: list[dict[str, Any]] = []
    for ref in _dedupe(refs):
        path = run_dir / ref
        exists = path.is_file()
        digest = ""
        if exists:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest = ""
        guessed, _encoding = mimetypes.guess_type(ref)
        index.append(
            {
                "ref": ref,
                "exists": exists,
                "bytes": path.stat().st_size if exists else 0,
                "sha256": digest,
                "media_type": guessed or "application/octet-stream",
            }
        )
    return index


def _template_entries(
    ws: Workspace, project_id: str, task: Task | None, plan: Plan
) -> list[dict[str, Any]]:
    entries = list(plan.templates)
    seen = {
        str(entry.get("template_id"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("template_id")
    }
    if task is None:
        return entries
    directory = ws.project_dir(project_id) / "tasks" / task.task_id / "templates"
    if not directory.is_dir():
        return entries
    for path in sorted(directory.glob("*.json")):
        try:
            data = read_json(path)
        except AiTestError:
            continue
        entry = {key: value for key, value in data.items() if key != "schema_version"}
        template_id = str(entry.get("template_id") or "")
        if not template_id or template_id in seen:
            continue
        seen.add(template_id)
        entries.append(entry)
    return entries


def _mock_judgement(
    project: Project,
    run: Run,
    run_dir: Path,
    delivery: Delivery | None,
    client: ModelClient | None,
    workspace: Workspace,
) -> list[dict[str, Any]]:
    judgement = list(run.mock_judgement)
    if client is None:
        return judgement
    dependencies: list[str] = []
    if delivery is not None:
        dependencies = [*delivery.dependencies, *delivery.mocks]
    dependencies = [
        *dependencies,
        *collect_dependencies(Path(project.root)),
        *(module.name for module in project.modules),
    ]
    dependencies = _dedupe([item.strip() for item in dependencies if item.strip()])
    if not dependencies:
        return judgement
    config = load_project_config(workspace, project.project_id)
    evidence_value = config.get("evidence")
    evidence = evidence_value if isinstance(evidence_value, dict) else {}
    mock_value = evidence.get("mock")
    mock = mock_value if isinstance(mock_value, dict) else {}
    max_files = mock.get("max_files", 40)
    max_depth = mock.get("max_depth", 3)
    judgement = judge_mock(
        Path(project.root),
        dependencies,
        list(run.evidence_refs),
        client=client,
        max_files=max_files if isinstance(max_files, int) else 40,
        max_depth=max_depth if isinstance(max_depth, int) else 3,
        workspace=workspace.root,
        project_revision=project.revision,
    )
    run.mock_judgement = judgement
    write_model(run_dir / "run.json", run)
    return judgement


def _summary(
    outcome: str,
    counts: dict[str, int],
    uncovered: list[str],
    client: ModelClient | None,
) -> tuple[str, Literal["ai", "fallback"]]:
    ai_summary: str | None = None
    if client is not None:
        ai_summary = summarize_report(
            {
                "outcome": outcome,
                "coverage": counts,
                "uncovered_items": uncovered,
                "failed_cases": counts.get("failed", 0),
            },
            client=client,
        )
    if ai_summary is not None:
        return ai_summary, "ai"
    return (
        f"本次范围 {counts['selected']}/{counts['applicable']}，"
        f"通过 {counts['passed']}，失败 {counts['failed']}，"
        f"阻塞或未完成 {counts['incomplete']}，未覆盖验收项 {len(uncovered)}。",
        "fallback",
    )


def _conclusion(outcome: str, counts: dict[str, int], uncovered: list[str]) -> str:
    return (
        f"{_OUTCOME_LABEL.get(outcome, outcome)}："
        f"已选 {counts.get('selected', 0)}/{counts.get('applicable', 0)}，"
        f"未覆盖 {len(uncovered)}，失败 {counts.get('failed', 0)}，"
        f"阻塞或未完成 {counts.get('incomplete', 0)}。"
    )


def build_report(
    ws: Workspace,
    project: Project,
    run_dir: Path,
    *,
    use_model: bool = True,
) -> Report:
    """Build, store and return the report for the run contained in ``run_dir``."""
    run = read_model(run_dir / "run.json", Run)
    plan = read_model(run_dir / "plan.json", Plan)
    outcome = verdict(plan, run)
    counts = coverage(plan, run)
    task = _load_task(ws, project.project_id, plan.task_id)
    uncovered = scope_uncovered(task, plan.scope)
    delivery = (
        _load_delivery(ws, project.project_id, task.task_id) if task is not None else None
    )
    source_commit = delivery.commit if delivery is not None and delivery.commit else None
    github, github_files = _github_record(ws, project.project_id, source_commit)
    changed_modules, unmapped_files = _module_impact(delivery, project.modules, github_files)
    client = build_client(ws, project.project_id) if use_model else None
    mock_judgement = _mock_judgement(project, run, run_dir, delivery, client, ws)
    real_chain = bool(mock_judgement) and all(
        item.get("kind") == "real" for item in mock_judgement
    )
    summary, summary_source = _summary(outcome, counts, uncovered, client)
    issues, issue_count, failure_occurrences = _issues_summary(ws, project.project_id, run)
    report = Report(
        report_id=new_id("rep"),
        run_id=run.run_id,
        outcome=outcome,
        conclusion=_conclusion(outcome, counts, uncovered),
        counts=counts,
        coverage={"selected": counts["selected"], "applicable": counts["applicable"]},
        changed_modules=changed_modules,
        unmapped_files=unmapped_files,
        evidence_refs=list(run.evidence_refs),
        mock_judgement=mock_judgement,
        real_chain=real_chain,
        summary=summary,
        summary_source="ai" if summary_source == "ai" else "fallback",
        issues=issues,
        issue_count=issue_count,
        failure_occurrences=failure_occurrences,
        analysis=[analysis.model_dump(mode="json") for analysis in run.analysis],
        layers=_layers(plan),
        rules_version_id=plan.rules_version_id or project.rules_version_id,
        source_commit=source_commit,
        templates=_template_entries(ws, project.project_id, task, plan),
        environment=_environment(ws, project.project_id),
        github=github,
        uncovered_items=uncovered,
        item_details=_item_details(plan, run, task),
        case_details=_case_details(plan, run),
        evidence_index=_evidence_index(run_dir, run),
        l1=_l1_details(plan, run),
    )
    secrets = known_secret_values([load_project_config(ws, project.project_id)])
    safe = Report.model_validate(redact_tree(report.model_dump(mode="json"), secrets))
    write_model(ws.project_dir(project.project_id) / "reports" / f"{safe.report_id}.json", safe)
    return safe
