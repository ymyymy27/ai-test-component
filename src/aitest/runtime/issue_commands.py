"""Runtime 命令模块：issue_commands。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from aitest.commands import (
    Payload,
    _project,
    _require,
    _safe_id,
    _text,
)
from aitest.config import known_secret_values
from aitest.core.issues import add_issue
from aitest.core.verdict import case_results, item_outcomes
from aitest.errors import AiTestError, invalid_request
from aitest.model_config import load_project_config
from aitest.models import (
    Issue,
    Plan,
    Run,
    Task,
    case_item_ids,
)
from aitest.runtime._common import _run_id, _runs_dir
from aitest.security import redact
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace


def _issues_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "issues"



def cmd_issue_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_id = _run_id(payload)
    run_dir = _runs_dir(ws, project.project_id) / run_id
    if not (run_dir / "run.json").is_file():
        raise AiTestError("RUN_NOT_FOUND", f"运行不存在：{run_id}", "检查运行 ID")
    case_id = _text({"case_id": _require(payload, "case_id")}, "case_id")
    run = read_model(run_dir / "run.json", Run)
    run_case = next((item for item in run.cases if item.case_id == case_id), None)
    if run_case is None:
        raise invalid_request(f"运行中没有该用例：{case_id}")
    evidence = payload.get("evidence_refs")
    refs = (
        [str(item) for item in evidence if isinstance(item, str)]
        if isinstance(evidence, list)
        else list(run_case.evidence_refs)
    )
    item_id = ""
    item_no = ""
    item_text = ""
    plan_path = run_dir / "plan.json"
    if plan_path.is_file():
        plan_snapshot = read_model(plan_path, Plan)
        plan_case = next(
            (item for item in plan_snapshot.cases if item.case_id == case_id), None
        )
        if plan_case is not None:
            ids = case_item_ids(plan_case)
            item_id = ids[0] if ids else ""
            item_text = plan_snapshot.item_texts.get(item_id, "")
            task_path = run_dir / "task.json"
            if task_path.is_file():
                try:
                    task_snapshot = read_model(task_path, Task)
                except AiTestError:
                    task_snapshot = None
                if task_snapshot is not None:
                    for index, entry in enumerate(task_snapshot.acceptance_items, start=1):
                        if entry.item_id != item_id:
                            continue
                        item_no = f"{index:02d}"
                        item_text = item_text or entry.text
                        break
    raw_steps = payload.get("steps")
    steps = (
        [str(item) for item in raw_steps if isinstance(item, str)]
        if isinstance(raw_steps, list)
        else []
    )
    return add_issue(
        ws,
        project.project_id,
        run_id,
        case_id,
        _text({"title": _require(payload, "title")}, "title"),
        severity=payload.get("severity", "P2"),
        error_type=_text(payload, "error_type", "unknown"),
        stack_key=_text(payload, "stack_key") or _text(payload, "stack"),
        evidence_refs=refs,
        run_dir=run_dir,
        steps=steps,
        expected=_text(payload, "expected"),
        actual=_text(payload, "actual"),
        item_no=item_no,
        item_text=item_text,
        target_key=item_id,
    )



def cmd_issue_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    issues = []
    skipped: list[dict[str, str]] = []
    for path in sorted(_issues_dir(ws, project.project_id).glob("*.json")):
        try:
            issue = read_model(path, Issue)
        except AiTestError as exc:
            skipped.append(
                {
                    "issue_id": path.stem,
                    "code": exc.info.code,
                    "message": exc.info.message,
                }
            )
            continue
        last = max(issue.occurrences, key=lambda item: item.at) if issue.occurrences else None
        issues.append(
            {
                "issue_id": issue.issue_id,
                "title": issue.title,
                "status": issue.status,
                "severity": issue.severity,
                "occurrences": len(issue.occurrences),
                "last_at": last.at if last is not None else "",
                "last_result": issue.last_result,
                "unstable": issue.unstable,
                "disposition": issue.disposition,
                "reason": issue.reason,
                "verification_run_id": issue.verification_run_id,
                "last_case_id": last.case_id if last is not None else "",
                "last_run_id": last.run_id if last is not None else "",
            }
        )
    return {"issues": issues, "skipped": skipped}



def cmd_issue_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    issue_id = _safe_id(
        _text({"id": _require(payload, "issue_id")}, "id"), "问题 ID"
    )
    path = _issues_dir(ws, project.project_id) / f"{issue_id}.json"
    if not path.is_file():
        raise invalid_request(f"问题不存在：{issue_id}")
    issue = read_model(path, Issue)
    return {"issue": issue.model_dump(mode="json")}



_ISSUE_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress"},
    "in_progress": {"retest", "pending"},
    "retest": {"in_progress", "pending"},
    "closed": {"in_progress"},
}



def cmd_issue_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    issue_id = _safe_id(
        _text({"id": _require(payload, "issue_id")}, "id"), "问题 ID"
    )
    path = _issues_dir(ws, project.project_id) / f"{issue_id}.json"
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"问题不存在：{issue_id}", "检查问题 ID")
    issue = read_model(path, Issue)
    data = issue.model_dump(mode="json")
    target_status = payload.get("status")
    if target_status == "closed":
        raise AiTestError(
            "INVALID_REQUEST",
            "关闭问题必须走 issue.close 并绑定新运行证据",
            "先安排回归，再用 issue.close 关闭",
        )
    if target_status is not None:
        if not isinstance(target_status, str) or target_status not in _ISSUE_TRANSITIONS:
            raise invalid_request("status 只能是 pending、in_progress、retest 或 closed")
        if target_status != issue.status and target_status not in _ISSUE_TRANSITIONS[issue.status]:
            raise AiTestError(
                "RUN_STATE_CONFLICT",
                f"问题状态不能从 {issue.status} 变为 {target_status}",
                "按待确认→处理中→待复测→已关闭的流转更新",
            )
        if issue.status == "closed" and target_status == "in_progress":
            if not _text(payload, "reason"):
                raise invalid_request("重开问题需要写原因")
            data["disposition"] = None
            data["verification_run_id"] = ""
        data["status"] = target_status
    secrets = known_secret_values([load_project_config(ws, project.project_id)])
    if "disposition" in payload:
        disposition = payload.get("disposition")
        if disposition is None:
            data["disposition"] = None
            if "reason" in payload:
                data["reason"] = redact(_text(payload, "reason"), secrets)
        else:
            if disposition not in {"fixed", "non_defect", "duplicate", "deferred"}:
                raise invalid_request("disposition 只能是 fixed、non_defect、duplicate、deferred")
            reason = _text(payload, "reason") or issue.reason
            if disposition in {"non_defect", "duplicate", "deferred"} and not reason.strip():
                raise invalid_request("非缺陷、重复、延期必须写理由")
            data["disposition"] = disposition
            data["reason"] = redact(reason, secrets)
    elif "reason" in payload:
        data["reason"] = redact(_text(payload, "reason"), secrets)
    if "severity" in payload:
        data["severity"] = payload["severity"]
    if "fix_version" in payload:
        raw_fix = payload["fix_version"]
        data["fix_version"] = redact(raw_fix, secrets) if isinstance(raw_fix, str) else raw_fix
    updated = Issue.model_validate(data)
    updated.revision += 1
    write_model(path, updated)
    return {
        "issue_id": updated.issue_id,
        "status": updated.status,
        "disposition": updated.disposition,
        "revision": updated.revision,
    }



def cmd_issue_close(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    issue_id = _safe_id(
        _text({"id": _require(payload, "issue_id")}, "id"), "问题 ID"
    )
    path = _issues_dir(ws, project.project_id) / f"{issue_id}.json"
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"问题不存在：{issue_id}", "检查问题 ID")
    issue = read_model(path, Issue)
    if issue.status != "retest":
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            f"问题当前状态为 {issue.status}，不能直接关闭",
            "先复测并把状态更新为 retest",
        )
    run_id = _run_id(payload)
    run_dir = _runs_dir(ws, project.project_id) / run_id
    if not (run_dir / "run.json").is_file():
        raise AiTestError("RUN_NOT_FOUND", f"运行不存在：{run_id}", "先执行回归")
    run = read_model(run_dir / "run.json", Run)
    if run.status != "completed":
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            f"运行状态为 {run.status}，不能作为关闭依据",
            "等回归运行完成且结果为通过",
        )
    occurred_runs = {item.run_id for item in issue.occurrences}
    if run_id in occurred_runs:
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            "关闭问题需要新运行，不能复用问题发生时的运行",
            "重新执行包含该用例的回归运行",
        )
    last_at = max((item.at for item in issue.occurrences if item.at), default="")
    if last_at and run.started_at:
        try:
            if datetime.fromisoformat(run.started_at) <= datetime.fromisoformat(last_at):
                raise AiTestError(
                    "RUN_STATE_CONFLICT",
                    "关闭问题需要晚于最后一次发生的回归运行",
                    "问题最后一次发生后再执行一次回归",
                )
        except ValueError:
            pass
    case_id = issue.occurrences[-1].case_id if issue.occurrences else ""
    last_run_id = issue.occurrences[-1].run_id if issue.occurrences else ""
    plan = read_model(run_dir / "plan.json", Plan)
    run_case = next((item for item in run.cases if item.case_id == case_id), None)
    if run_case is not None:
        effective = case_results(plan, run).get(case_id, "not_run")
        if effective != "passed":
            raise AiTestError(
                "RUN_STATE_CONFLICT",
                f"回归运行结果不是通过（{effective}）",
                "修复后重跑，或保持问题未关闭",
            )
        if not run_case.evidence_refs:
            raise AiTestError(
                "RUN_STATE_CONFLICT",
                "回归运行没有证据，不能关闭问题",
                "重跑并确保证据已保存",
            )
    else:
        matched_item_ids: list[str] = []
        if last_run_id:
            prev_plan_path = _runs_dir(ws, project.project_id) / last_run_id / "plan.json"
            if prev_plan_path.is_file():
                try:
                    prev_plan = read_model(prev_plan_path, Plan)
                    prev_case = next(
                        (item for item in prev_plan.cases if item.case_id == case_id), None
                    )
                    if prev_case is not None:
                        matched_item_ids = [
                            item_id
                            for item_id in case_item_ids(prev_case)
                            if item_id in plan.scope.acceptance_item_ids
                        ]
                except AiTestError:
                    matched_item_ids = []
        if not matched_item_ids and issue.item_text:
            matched_item_ids = [
                item_id
                for item_id, text in plan.item_texts.items()
                if text == issue.item_text and item_id in plan.scope.acceptance_item_ids
            ]
        if not matched_item_ids:
            raise AiTestError(
                "RUN_STATE_CONFLICT",
                f"回归运行中没有用例 {case_id}",
                "在回归范围里包含该用例或对应验收项",
            )
        outcomes = item_outcomes(plan, run)
        for item_id in matched_item_ids:
            effective = outcomes.get(item_id, "not_run")
            if effective != "passed":
                raise AiTestError(
                    "RUN_STATE_CONFLICT",
                    f"回归运行结果不是通过（{effective}）",
                    "修复后重跑，或保持问题未关闭",
                )
        related_cases = [
            rc
            for pc in plan.cases
            if set(case_item_ids(pc)) & set(matched_item_ids)
            for rc in run.cases
            if rc.case_id == pc.case_id
        ]
        if not related_cases or any(not rc.evidence_refs for rc in related_cases):
            raise AiTestError(
                "RUN_STATE_CONFLICT",
                "回归运行没有证据，不能关闭问题",
                "重跑并确保证据已保存",
            )
    reason = _text(payload, "reason").strip()
    if not reason:
        raise invalid_request("关闭问题需要写理由", "填写关闭依据后再提交")
    secrets = known_secret_values([load_project_config(ws, project.project_id)])
    issue.status = "closed"
    issue.verification_run_id = run_id
    issue.last_result = "passed"
    issue.reason = redact(reason, secrets)
    issue.revision += 1
    write_model(path, issue)
    return {
        "issue_id": issue.issue_id,
        "status": issue.status,
        "verification_run_id": run_id,
    }



