"""Plan, run, evidence, report, issue, backup and cleanup commands."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from aitest.ai_features import judge_mock, summarize_report
from aitest.commands import (
    MUTATING,
    REGISTRY,
    Payload,
    _project,
    _require,
    _task,
    _text,
)
from aitest.core.verdict import coverage, verdict
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import build_client, load_project_config, model_settings
from aitest.models import (
    Analysis,
    Case,
    Delivery,
    Issue,
    IssueOccurrence,
    Opinion,
    Plan,
    Project,
    Report,
    Run,
    RunCase,
    Scope,
    Task,
    new_id,
)
from aitest.security import scan_script
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

MAX_FILE_BYTES = 20 * 1024 * 1024


def _plan_path(ws: Workspace, project_id: str, plan_id: str) -> Path:
    return ws.project_dir(project_id) / "plans" / f"{plan_id}.json"


def _load_plan(ws: Workspace, project_id: str, plan_id: str) -> Plan:
    path = _plan_path(ws, project_id, plan_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"计划不存在：{plan_id}", "先生成计划")
    return read_model(path, Plan)


def _selected_items(task: Any, scope: Scope) -> tuple[list[str], int]:
    active = [item.item_id for item in task.acceptance_items if item.status == "active"]
    if scope.preset == "all_applicable":
        return active, len(active)
    unknown = [item for item in scope.acceptance_item_ids if item not in active]
    if unknown:
        raise invalid_request("验收项不属于当前任务或不可用：" + "、".join(unknown))
    return list(scope.acceptance_item_ids), len(active)


def _project_context(project: Project, ws: Workspace) -> dict[str, Any]:
    root = Path(project.root)
    files: list[str] = []
    for name in ("README.md", "README.rst", "pyproject.toml", "package.json", "requirements.txt"):
        path = root / name
        if path.is_file():
            files.append(name)
    tests = root / "tests"
    if tests.is_dir():
        files.append("tests/")
    excerpt = ""
    for name in ("README.md", "README.rst", "pyproject.toml", "package.json"):
        path = root / name
        if path.is_file():
            try:
                excerpt += (
                    f"\n--- {name} ---\n"
                    + path.read_text(encoding="utf-8", errors="replace")[:2000]
                )
            except OSError:
                continue
    sources: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts or ".venv" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "route(" in text or "Flask(" in text:
            sources.append(f"--- {path.relative_to(root)} ---\n" + text[:1500])
        if len(sources) >= 3:
            break
    config = load_project_config(ws, project.project_id)
    return {
        "root": str(root),
        "routes": sources,
        "files": files,
        "readme": excerpt,
        "services": config.get("services", {}),
        "runners": config.get("runners", {}),
    }


def _normalize_method(method: str, script_name: str | None = None) -> str:
    if method.startswith("playwright:") and script_name and script_name.endswith(".py"):
        return f"script:{script_name}"
    for prefix in ("command:", "script:", "http:", "playwright:"):
        if method.startswith(prefix):
            return method
    return f"command:{method}"


def cmd_plan_generate(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    scope_payload = payload.get("scope")
    if not isinstance(scope_payload, dict):
        raise invalid_request("scope 必须是对象")
    scope = Scope.model_validate(scope_payload)
    selected, applicable = _selected_items(task, scope)
    if not selected:
        raise AiTestError("SCOPE_EMPTY", "本次范围为空", "至少选择一条可用验收项")
    scope.acceptance_item_ids = list(selected)
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        client = build_client(ws, task.project_id)
        if client is None:
            raise AiTestError(
                "MODEL_UNAVAILABLE",
                "未配置模型，无法生成计划",
                "配置模型密钥，或在 payload 提供 cases",
            )
        selected_items = [
            item.model_dump(mode="json")
            for item in task.acceptance_items
            if item.item_id in selected
        ]
        raw_cases = []
        project = ws.load_project(task.project_id)
        context = _project_context(project, ws)
        for item in client.generate_cases(task.model_dump(mode="json"), selected_items, context):
            depends = item.get("depends_on")
            first = depends[0] if isinstance(depends, list) and depends else None
            raw_cases.append(
                {
                    "acceptance_item_id": item["acceptance_item_id"],
                    "layer": item["layer"],
                    "expected": item["expected"],
                    "expectation_source": item["expectation_source"],
                    "verify_method": _normalize_method(
                        str(item["verify_method"]), item.get("script_name")
                    ),
                    "depends_on": first if first in {"build", "service"} else None,
                    "script_name": item.get("script_name"),
                    "script_content": item.get("script_content"),
                }
            )
    cases = [Case.model_validate(item) for item in raw_cases]
    covered = {case.acceptance_item_id for case in cases if case.acceptance_item_id}
    missing = [item for item in selected if item not in covered]
    if missing:
        raise invalid_request("以下验收项没有对应用例：" + "、".join(missing))
    for case in cases:
        if not case.expected.strip():
            raise invalid_request(f"用例缺少预期：{case.case_id}")
    plan = Plan(
        plan_id=new_id("plan"),
        task_id=task.task_id,
        scope=scope,
        applicable_count=applicable,
        rules_version_id=_text(payload, "rules_version_id") or None,
        cases=cases,
        status="published",
    )
    write_model(_plan_path(ws, task.project_id, plan.plan_id), plan)
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "status": plan.status,
        "scope": scope.model_dump(mode="json"),
        "coverage": {"selected": len(selected), "applicable": applicable},
        "cases": len(cases),
    }


def cmd_plan_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    plan = _load_plan(ws, project.project_id, _text({"id": _require(payload, "plan_id")}, "id"))
    expected = payload.get("expected_revision")
    if expected is not None and expected != plan.revision:
        raise AiTestError("RUN_STATE_CONFLICT", "计划修订号不匹配", "刷新后重试")
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        raise invalid_request("patch 必须是对象")
    data = plan.model_dump(mode="json")
    if "cases" in patch:
        data["cases"] = patch["cases"]
    plan = Plan.model_validate(data)
    plan.revision += 1
    plan.status = "published"
    write_model(_plan_path(ws, project.project_id, plan.plan_id), plan)
    return {"plan_id": plan.plan_id, "revision": plan.revision, "status": plan.status}


def cmd_plan_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    plan = _load_plan(ws, project.project_id, _text({"id": _require(payload, "plan_id")}, "id"))
    return {"plan": plan.model_dump(mode="json")}


def cmd_plan_publish(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    plan = _load_plan(ws, project.project_id, _text({"id": _require(payload, "plan_id")}, "id"))
    if plan.status == "published":
        raise AiTestError("RUN_STATE_CONFLICT", "计划已发布", "修改请用 plan.update")
    plan.status = "published"
    plan.revision += 1
    write_model(_plan_path(ws, project.project_id, plan.plan_id), plan)
    return {"plan_id": plan.plan_id, "revision": plan.revision}


# -- run --------------------------------------------------------------------


def _runs_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "runs"


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _heartbeat_stale(run: Run, seconds: float = 120.0) -> bool:
    if not run.heartbeat_at:
        return True
    try:
        then = datetime.fromisoformat(run.heartbeat_at).timestamp()
    except ValueError:
        return True
    return (time.time() - then) > seconds and not _pid_alive(run.pid)


def _refresh_stale(run: Run) -> Run:
    if run.status == "running" and _heartbeat_stale(run):
        run.status = "interrupted"
    return run


def _save_evidence_file(run_dir: Path, name: str, data: bytes) -> str:
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    truncated = len(data) > MAX_FILE_BYTES
    payload = data[:MAX_FILE_BYTES] if truncated else data
    path = evidence_dir / name
    path.write_bytes(payload)
    if truncated:
        (evidence_dir / f"{name}.truncated").write_text(
            f"原始大小 {len(data)} 字节，已截断\n", encoding="utf-8"
        )
    return str(path.relative_to(run_dir)).replace("\\", "/")


def cmd_run_start(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    project = _project(ws, payload)
    plan = _load_plan(ws, project.project_id, _text({"id": _require(payload, "plan_id")}, "id"))
    if plan.status != "published":
        raise AiTestError("PLAN_NOT_PUBLISHED", "计划尚未发布", "先发布计划")
    confirmed_by = _text(payload, "confirmed_by")
    if not confirm and not confirmed_by:
        raise AiTestError("CONFIRMATION_REQUIRED", "缺少执行前确认", "在面板确认或加 --confirm")
    selected = list(plan.scope.acceptance_item_ids)
    narrowing = payload.get("acceptance_item_ids")
    if narrowing is not None:
        if not isinstance(narrowing, list) or not narrowing:
            raise invalid_request("acceptance_item_ids 必须是非空数组")
        outside = [item for item in narrowing if item not in selected]
        if outside:
            raise invalid_request("收窄范围超出计划范围：" + "、".join(outside))
        selected = [str(item) for item in narrowing]
    if not selected:
        raise AiTestError("SCOPE_EMPTY", "本次范围为空", "至少选择一条验收项")
    for path in _runs_dir(ws, project.project_id).glob("*/run.json"):
        existing = read_model(path, Run)
        if existing.status == "running" and not _heartbeat_stale(existing):
            raise AiTestError(
                "RUN_ACTIVE",
                f"项目已有运行：{existing.run_id}",
                "等待或取消现有运行后重试",
            )
    run_id = new_id("run")
    run_dir = _runs_dir(ws, project.project_id) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    filtered = [
        case
        for case in plan.cases
        if case.acceptance_item_id in selected or case.acceptance_item_id is None
    ]
    snapshot_scope = plan.scope.model_copy(update={"acceptance_item_ids": selected})
    snapshot = plan.model_copy(update={"cases": filtered, "scope": snapshot_scope})
    generated = run_dir / "generated"
    for case in filtered:
        if not case.script_name or not case.script_content:
            continue
        matches = scan_script(case.script_content)
        if matches:
            import shutil

            shutil.rmtree(run_dir, ignore_errors=True)
            raise AiTestError(
                "SCRIPT_REJECTED",
                f"生成脚本包含危险操作：{', '.join(matches)}",
                "检查脚本或改用手写验证",
            )
        generated.mkdir(parents=True, exist_ok=True)
        (generated / case.script_name).write_text(case.script_content, encoding="utf-8")
        case.verify_method = f"script:{case.script_name}"
    write_model(run_dir / "plan.json", snapshot)
    run = Run(
        run_id=run_id,
        plan_id=plan.plan_id,
        project_id=project.project_id,
        scope=snapshot_scope.model_copy(update={"frozen_at": iso_now()}),
        confirmed_by=confirmed_by or "cli",
        confirmed_at=iso_now(),
        status="running",
        started_at=iso_now(),
        heartbeat_at=iso_now(),
        cases=[RunCase(case_id=case.case_id) for case in filtered],
    )
    write_model(run_dir / "run.json", run)
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "aitest.worker",
            "--workspace",
            str(ws.root),
            "--project",
            project.project_id,
            "--run",
            run_id,
        ],
        env=env,
        creationflags=flags,
        close_fds=True,
    )
    run.pid = process.pid
    write_model(run_dir / "run.json", run)
    return {
        "run_id": run_id,
        "status": "running",
        "scope": {"selected": len(selected), "applicable": plan.applicable_count},
    }


def _run(ws: Workspace, payload: Payload) -> tuple[Path, Run]:
    project = _project(ws, payload)
    run_id = _text({"id": _require(payload, "run_id")}, "id")
    path = _runs_dir(ws, project.project_id) / run_id / "run.json"
    if not path.is_file():
        raise AiTestError("RUN_NOT_FOUND", f"运行不存在：{run_id}", "检查运行 ID")
    run = _refresh_stale(read_model(path, Run))
    write_model(path, run)
    return path.parent, run


def cmd_run_status(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    plan_path = run_dir / "plan.json"
    outcome = None
    if plan_path.is_file():
        outcome = verdict(read_model(plan_path, Plan), run)
    return {"run": run.model_dump(mode="json"), "outcome": outcome}


def cmd_run_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    runs: list[dict[str, Any]] = []
    directory = _runs_dir(ws, project.project_id)
    if directory.is_dir():
        for path in sorted(directory.glob("*/run.json")):
            run = _refresh_stale(read_model(path, Run))
            write_model(path, run)
            runs.append(
                {
                    "run_id": run.run_id,
                    "status": run.status,
                    "started_at": run.started_at,
                    "heartbeat_at": run.heartbeat_at,
                }
            )
    runs.sort(key=lambda item: item["started_at"], reverse=True)
    return {"runs": runs}


def cmd_run_cancel(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    if run.status != "running":
        raise AiTestError("RUN_STATE_CONFLICT", f"运行状态为 {run.status}", "无需取消")
    (run_dir / "cancel.requested").write_text(iso_now(), encoding="utf-8")
    return {"run_id": run.run_id, "cancel_requested": True}


# -- evidence ---------------------------------------------------------------


def cmd_evidence_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    source = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"证据文件不存在：{source}")
    name = f"manual-{secrets.token_hex(4)}-{source.name}"
    ref = _save_evidence_file(run_dir, name, source.read_bytes())
    run.evidence_refs.append(ref)
    write_model(run_dir / "run.json", run)
    return {"evidence_ref": ref}


def cmd_evidence_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    items = [
        {
            "ref": ref,
            "bytes": (run_dir / ref).stat().st_size if (run_dir / ref).is_file() else 0,
            "exists": (run_dir / ref).is_file(),
        }
        for ref in run.evidence_refs
    ]
    return {"evidence": items}


# -- report -----------------------------------------------------------------


def cmd_report_generate(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    plan = read_model(run_dir / "plan.json", Plan)
    outcome = verdict(plan, run)
    counts = coverage(plan, run)
    mock_judgement: list[dict[str, Any]] = list(run.mock_judgement)
    summary: str | None = None
    ai_summary: str | None = None
    client = build_client(ws, run.project_id)
    if client is not None:
        project = read_model(ws.project_path(run.project_id), Project)
        plan_task = read_model(
            ws.project_dir(run.project_id) / "tasks" / f"{plan.task_id}.json", Task
        )
        delivery_path = ws.project_dir(run.project_id) / "deliveries" / f"{plan_task.task_id}.json"
        delivery = read_model(delivery_path, Delivery) if delivery_path.is_file() else None
        dependencies: list[str] = []
        if delivery is not None:
            dependencies = [*delivery.dependencies, *delivery.mocks]
        if not dependencies:
            dependencies = [module.name for module in project.modules]
        if dependencies:
            mock_judgement = judge_mock(
                Path(project.root),
                dependencies,
                list(run.evidence_refs),
                client=client,
            )
            run.mock_judgement = mock_judgement
            write_model(run_dir / "run.json", run)
        ai_summary = summarize_report(
            {
                "outcome": outcome,
                "coverage": counts,
                "failed_cases": [case.case_id for case in run.cases if case.result == "failed"],
            },
            client=client,
        )
        summary = ai_summary
        if summary is None:
            summary = (
                f"本次范围 {counts['selected']}/{counts['applicable']}，"
                f"通过 {counts['passed']}，失败 {counts['failed']}，"
                f"阻塞或未完成 {counts['incomplete']}。"
            )
    report = Report(
        report_id=new_id("rep"),
        run_id=run.run_id,
        outcome=outcome,
        counts=counts,
        coverage={"selected": counts["selected"], "applicable": counts["applicable"]},
        evidence_refs=list(run.evidence_refs),
        mock_judgement=mock_judgement,
        summary=summary,
        summary_source="ai" if ai_summary else "fallback",
    )
    write_model(ws.project_dir(run.project_id) / "reports" / f"{report.report_id}.json", report)
    return {"report_id": report.report_id, "outcome": outcome, "counts": counts}


def _report_path(ws: Workspace, project_id: str, report_id: str) -> Path:
    return ws.project_dir(project_id) / "reports" / f"{report_id}.json"


def cmd_report_export(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _text({"id": _require(payload, "report_id")}, "id")
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"报告不存在：{report_id}", "先生成报告")
    report = read_model(path, Report)
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    opinion = report.opinion
    lines = [
        f"# 测试报告 {report.report_id}",
        "",
        f"- 结论：{report.outcome}",
        f"- 运行：{report.run_id}",
        f"- 范围：{report.counts.get('selected', 0)}/{report.coverage.get('applicable', 0)}",
        "",
    ]
    if report.summary:
        if report.summary_source == "fallback":
            lines.append("AI 摘要未采用，以下为事实摘要")
        lines.append(f"摘要：{report.summary}")
        lines.append("")
    if opinion is None:
        lines.append("未填写验收意见")
    else:
        lines.append(f"验收意见（{opinion.by}，{opinion.at}）：{opinion.text}")
    lines.append("")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return {"path": str(target), "bytes": target.stat().st_size}


def cmd_report_opinion(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _text({"id": _require(payload, "report_id")}, "id")
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"报告不存在：{report_id}", "先生成报告")
    report = read_model(path, Report)
    report.opinion = Opinion(
        by=_text({"by": _require(payload, "by")}, "by"),
        at=iso_now(),
        text=_text(payload, "text"),
    )
    report.revision += 1
    write_model(path, report)
    return {"opinion": report.opinion.model_dump(mode="json")}


# -- issues -----------------------------------------------------------------


def _issues_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "issues"


def cmd_issue_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_id = _text({"run_id": _require(payload, "run_id")}, "run_id")
    case_id = _text({"case_id": _require(payload, "case_id")}, "case_id")
    signature = f"{case_id}:{_text(payload, 'error_type', 'unknown')}"
    for path in _issues_dir(ws, project.project_id).glob("*.json"):
        issue = read_model(path, Issue)
        if issue.signature == signature:
            issue.occurrences.append(IssueOccurrence(run_id=run_id, case_id=case_id, at=iso_now()))
            issue.revision += 1
            write_model(path, issue)
            return {"issue_id": issue.issue_id, "existing": True, "revision": issue.revision}
    issue = Issue(
        issue_id=new_id("issue"),
        project_id=project.project_id,
        signature=signature,
        title=_text({"title": _require(payload, "title")}, "title"),
        severity=payload.get("severity", "P2"),
        occurrences=[IssueOccurrence(run_id=run_id, case_id=case_id, at=iso_now())],
    )
    write_model(_issues_dir(ws, project.project_id) / f"{issue.issue_id}.json", issue)
    return {"issue_id": issue.issue_id, "existing": False, "revision": issue.revision}


def cmd_issue_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    issues = [
        {
            "issue_id": issue.issue_id,
            "title": issue.title,
            "status": issue.status,
            "severity": issue.severity,
            "occurrences": len(issue.occurrences),
        }
        for issue in (
            read_model(path, Issue) for path in _issues_dir(ws, project.project_id).glob("*.json")
        )
    ]
    return {"issues": issues}


def cmd_issue_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    issue_id = _text({"id": _require(payload, "issue_id")}, "id")
    path = _issues_dir(ws, project.project_id) / f"{issue_id}.json"
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"问题不存在：{issue_id}", "检查问题 ID")
    issue = read_model(path, Issue)
    data = issue.model_dump(mode="json")
    for key in ("status", "disposition", "severity", "fix_version"):
        if key in payload:
            data[key] = payload[key]
    issue = Issue.model_validate(data)
    issue.revision += 1
    write_model(path, issue)
    return {"issue_id": issue.issue_id, "status": issue.status, "revision": issue.revision}


def cmd_issue_close(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    issue_id = _text({"id": _require(payload, "issue_id")}, "id")
    path = _issues_dir(ws, project.project_id) / f"{issue_id}.json"
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"问题不存在：{issue_id}", "检查问题 ID")
    if not _text(payload, "run_id"):
        raise AiTestError("RUN_STATE_CONFLICT", "关闭问题需要新的运行证据", "先执行回归")
    issue = read_model(path, Issue)
    issue.status = "closed"
    issue.revision += 1
    write_model(path, issue)
    return {"issue_id": issue.issue_id, "status": issue.status}


# -- backup / cleanup / analysis -------------------------------------------


def cmd_backup_create(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    include_evidence = bool(payload.get("include_evidence", False))
    target.parent.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for path in ws.root.rglob("*"):
        if not path.is_file() or path == target:
            continue
        relative = path.relative_to(ws.root)
        if path.name == ".workspace.lock" or relative.parts[0] in {"requests", "backups"}:
            continue
        if not include_evidence and ("evidence" in relative.parts or "generated" in relative.parts):
            continue
        files.append(relative)
    manifest = {"files": sorted(str(item).replace("\\", "/") for item in files)}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(ws.root / relative, str(relative).replace("\\", "/"))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "path": str(target),
        "sha256": digest,
        "bytes": target.stat().st_size,
        "includes_evidence": include_evidence,
        "files": len(files),
    }


def cmd_backup_restore(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del ws, confirm
    source = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    target = Path(_text({"target": _require(payload, "target")}, "target")).expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"备份不存在：{source}")
    if target.exists() and any(target.iterdir()):
        raise invalid_path(f"目标目录非空：{target}", "换一个空目录")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            name = member.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise AiTestError("BACKUP_INVALID", f"备份包含非法路径：{name}", "换一个备份")
        archive.extractall(target)
    return {"target": str(target), "files": len(list(target.rglob("*")))}


def cmd_data_cleanup(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    _project(ws, payload)
    dry_run = bool(payload.get("dry_run", True))
    if not dry_run and not confirm:
        raise AiTestError("CONFIRMATION_REQUIRED", "清理数据需要确认", "加 --confirm 或先 dry-run")
    return {"rows": 0, "conditions": "run marker", "executed": not dry_run}


def cmd_analysis_run(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    client = build_client(ws, run.project_id)
    if client is None:
        run.analysis = [Analysis(status="not_generated")]
        write_model(run_dir / "run.json", run)
        return {"status": "not_generated", "analysis": None}
    failed = [case for case in run.cases if case.result in {"failed", "blocked", "error"}]
    if not failed:
        run.analysis = [Analysis(status="not_generated")]
        write_model(run_dir / "run.json", run)
        return {"status": "not_generated", "analysis": None}
    evidence = [ref for case in failed for ref in case.evidence_refs]
    try:
        data = client.analyze_failure(run.model_dump(mode="json"), evidence)
    except AiTestError as exc:
        status: Literal["invalid", "not_generated"] = (
            "invalid" if exc.info.code == "MODEL_OUTPUT_INVALID" else "not_generated"
        )
        run.analysis = [Analysis(status=status)]
        write_model(run_dir / "run.json", run)
        return {"status": status, "analysis": None}
    analysis = Analysis(
        status="generated",
        model=str(model_settings(ws, run.project_id)["name"]),
        generated_at=iso_now(),
        observed=data["observed"],
        evidence_refs=data["evidence_refs"],
        candidate_causes=[{"cause": item} for item in data["candidate_causes"]],
        unverified=data["unverified"],
        next_steps=data["next_steps"],
    )
    run.analysis = [analysis]
    write_model(run_dir / "run.json", run)
    return {"status": "generated", "analysis": analysis.model_dump(mode="json")}


REGISTRY.update(
    {
        "plan.generate": cmd_plan_generate,
        "plan.update": cmd_plan_update,
        "plan.show": cmd_plan_show,
        "plan.publish": cmd_plan_publish,
        "run.start": cmd_run_start,
        "run.status": cmd_run_status,
        "run.list": cmd_run_list,
        "run.cancel": cmd_run_cancel,
        "evidence.add": cmd_evidence_add,
        "evidence.list": cmd_evidence_list,
        "issue.add": cmd_issue_add,
        "issue.list": cmd_issue_list,
        "issue.update": cmd_issue_update,
        "issue.close": cmd_issue_close,
        "report.generate": cmd_report_generate,
        "report.export": cmd_report_export,
        "report.opinion": cmd_report_opinion,
        "backup.create": cmd_backup_create,
        "backup.restore": cmd_backup_restore,
        "data.cleanup": cmd_data_cleanup,
        "analysis.run": cmd_analysis_run,
    }
)
MUTATING.update(
    {
        "plan.generate",
        "plan.update",
        "plan.publish",
        "run.start",
        "run.cancel",
        "evidence.add",
        "issue.add",
        "issue.update",
        "issue.close",
        "report.generate",
        "report.export",
        "report.opinion",
        "backup.create",
        "backup.restore",
        "data.cleanup",
        "analysis.run",
    }
)
