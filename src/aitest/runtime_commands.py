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
from typing import Any, Literal, cast

from aitest.commands import (
    MUTATING,
    REGISTRY,
    Payload,
    _project,
    _require,
    _task,
    _text,
)
from aitest.config import github_token, model_key
from aitest.core.analysis import analyze_run
from aitest.core.evidence import data_prefix, evidence_bytes, save_evidence
from aitest.core.expectations import align_generated_sources, annotate_cases
from aitest.core.issues import add_issue
from aitest.core.manual import aggregate_manual, is_manual_case
from aitest.core.reports import build_report
from aitest.core.scope import applicable_items, uncovered_items
from aitest.core.verdict import verdict
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import build_client, load_project_config
from aitest.models import (
    Attempt,
    Case,
    Confirmation,
    Issue,
    ManualLog,
    ManualStep,
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
from aitest.security import redact, scan_script
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now


def _plan_path(ws: Workspace, project_id: str, plan_id: str) -> Path:
    return ws.project_dir(project_id) / "plans" / f"{plan_id}.json"


def _load_plan(ws: Workspace, project_id: str, plan_id: str) -> Plan:
    path = _plan_path(ws, project_id, plan_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"计划不存在：{plan_id}", "先生成计划")
    return read_model(path, Plan)


def _task_by_id(ws: Workspace, project_id: str, task_id: str) -> Task:
    path = ws.project_dir(project_id) / "tasks" / f"{task_id}.json"
    if not path.is_file():
        raise AiTestError("TASK_NOT_FOUND", f"任务不存在：{task_id}", "先创建任务")
    return read_model(path, Task)


def _selected_items(task: Any, scope: Scope, project: Project) -> tuple[list[str], int]:
    """Return selected item ids and the applicable count after module filtering."""
    module_ids = set(scope.module_ids)
    if module_ids:
        registered = {module.module_id for module in project.modules}
        unknown_modules = sorted(
            module_id for module_id in module_ids if module_id not in registered
        )
        if unknown_modules:
            raise invalid_request("模块未登记：" + "、".join(unknown_modules))
    active_items = applicable_items(task, list(module_ids))
    active = [item.item_id for item in active_items]
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
    for prefix in (
        "command:",
        "script:",
        "http:",
        "playwright:",
        "manual:",
        "sql:",
        "junit:",
        "pytest:",
    ):
        if method.startswith(prefix):
            return method
    return f"command:{method}"


def cmd_plan_generate(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    project = ws.load_project(task.project_id)
    scope_payload = payload.get("scope")
    if not isinstance(scope_payload, dict):
        raise invalid_request("scope 必须是对象")
    scope = Scope.model_validate(scope_payload)
    selected, applicable = _selected_items(task, scope, project)
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
        align_generated_sources(raw_cases, task.acceptance_items)
    cases = [
        Case.model_validate(item)
        for item in raw_cases
        if not isinstance(item, dict)
        or item.get("acceptance_item_id") in selected
        or item.get("acceptance_item_id") is None
    ]
    covered = {case.acceptance_item_id for case in cases if case.acceptance_item_id}
    missing = [item for item in selected if item not in covered]
    if missing:
        raise invalid_request("以下验收项没有对应用例：" + "、".join(missing))
    for case in cases:
        if not case.expected.strip():
            raise invalid_request(f"用例缺少预期：{case.case_id}")
    warnings = annotate_cases(cases, task.acceptance_items)
    plan = Plan(
        plan_id=new_id("plan"),
        task_id=task.task_id,
        scope=scope,
        applicable_count=applicable,
        rules_version_id=_text(payload, "rules_version_id") or None,
        cases=cases,
        warnings=warnings,
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
        "warnings": warnings,
        "unverified_cases": [case.case_id for case in cases if not case.expectation_verified],
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
    task = _task_by_id(ws, project.project_id, plan.task_id)
    plan.warnings = annotate_cases(plan.cases, task.acceptance_items)
    plan.revision += 1
    plan.status = "published"
    write_model(_plan_path(ws, project.project_id, plan.plan_id), plan)
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "status": plan.status,
        "warnings": plan.warnings,
        "unverified_cases": [
            case.case_id for case in plan.cases if not case.expectation_verified
        ],
    }


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
    task = _task_by_id(ws, project.project_id, plan.task_id)
    plan.warnings = annotate_cases(plan.cases, task.acceptance_items)
    plan.status = "published"
    plan.revision += 1
    write_model(_plan_path(ws, project.project_id, plan.plan_id), plan)
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "warnings": plan.warnings,
    }


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


def _refresh_and_save(path: Path, run: Run) -> Run:
    """Persist a stale-run transition only; plain reads never rewrite run.json."""
    before = run.status
    _refresh_stale(run)
    if run.status != before:
        write_model(path, run)
    return run


def _active_run(ws: Workspace, project_id: str) -> Run | None:
    directory = _runs_dir(ws, project_id)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*/run.json")):
        existing = read_model(path, Run)
        if existing.status == "running" and not _heartbeat_stale(existing):
            return existing
    return None


def _confirmation_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "confirmations"


def _environment_summary(ws: Workspace, project_id: str) -> dict[str, Any]:
    config = load_project_config(ws, project_id)
    services = config.get("services")
    summary: dict[str, dict[str, str]] = {}
    if isinstance(services, dict):
        for name, value in services.items():
            if isinstance(name, str) and isinstance(value, dict):
                summary[name] = {
                    "health": str(value.get("health") or ""),
                    "start": str(value.get("start") or ""),
                }
    return {"services": summary}


def _credential_summary() -> dict[str, str]:
    """Summarise credential configuration without storing any credential body."""
    model_value, model_source = model_key()
    github_value, github_source = github_token()
    return {
        "model_source": model_source,
        "model_digest": hashlib.sha256(model_value.encode("utf-8")).hexdigest()[:16]
        if model_value
        else "",
        "github_source": github_source,
        "github_digest": hashlib.sha256(github_value.encode("utf-8")).hexdigest()[:16]
        if github_value
        else "",
    }


def _confirmation_fingerprint(
    selected: list[str], environment: dict[str, Any], credentials: dict[str, str]
) -> str:
    payload = {
        "scope": sorted(selected),
        "environment": environment,
        "credentials": credentials,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _latest_run(ws: Workspace, project_id: str) -> Run | None:
    directory = _runs_dir(ws, project_id)
    if not directory.is_dir():
        return None
    latest: Run | None = None
    for path in sorted(directory.glob("*/run.json")):
        run = read_model(path, Run)
        if latest is None or run.started_at > latest.started_at:
            latest = run
    return latest


def _reusable_confirmation(
    ws: Workspace, project_id: str, fingerprint: str, selected: list[str]
) -> Confirmation | None:
    """Reuse confirmation only when the latest run had the same scope and environment."""
    latest = _latest_run(ws, project_id)
    if latest is None or not latest.confirmation_id:
        return None
    path = _confirmation_dir(ws, project_id) / f"{latest.confirmation_id}.json"
    if not path.is_file():
        return None
    try:
        record = read_model(path, Confirmation)
    except AiTestError:
        return None
    if record.fingerprint != fingerprint:
        return None
    if sorted(record.acceptance_item_ids) != sorted(selected):
        return None
    return record


def _save_evidence_file(run_dir: Path, name: str, data: bytes) -> str:
    ref = save_evidence(run_dir, name, data)
    assert ref is not None
    return ref


def cmd_run_start(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    project = _project(ws, payload)
    plan = _load_plan(ws, project.project_id, _text({"id": _require(payload, "plan_id")}, "id"))
    if plan.status != "published":
        raise AiTestError("PLAN_NOT_PUBLISHED", "计划尚未发布", "先发布计划")
    confirmed_by = _text(payload, "confirmed_by")
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
    environment = _environment_summary(ws, project.project_id)
    credentials = _credential_summary()
    fingerprint = _confirmation_fingerprint(selected, environment, credentials)
    run_id = new_id("run")
    run_dir = _runs_dir(ws, project.project_id) / run_id
    confirmation_info: dict[str, Any] = {}
    try:
        with ws.lock():
            active = _active_run(ws, project.project_id)
            if active is not None:
                raise AiTestError(
                    "RUN_ACTIVE",
                    f"项目已有运行：{active.run_id}",
                    "等待或取消现有运行后重试",
                )
            explicit = confirm or bool(confirmed_by)
            reused_record = _reusable_confirmation(
                ws, project.project_id, fingerprint, selected
            )
            if not explicit and reused_record is None:
                raise AiTestError(
                    "CONFIRMATION_REQUIRED", "缺少执行前确认", "在面板确认或加 --confirm"
                )
            new_confirmation: Confirmation | None = None
            if reused_record is not None and not explicit:
                confirmed_by_value = reused_record.confirmed_by
                confirmed_at = reused_record.confirmed_at
                confirmation_id = reused_record.confirmation_id
                confirmation_reused = True
            else:
                new_confirmation = Confirmation(
                    confirmation_id=new_id("conf"),
                    project_id=project.project_id,
                    plan_id=plan.plan_id,
                    acceptance_item_ids=list(selected),
                    environment=environment,
                    credentials=credentials,
                    fingerprint=fingerprint,
                    confirmed_by=confirmed_by or "cli",
                    confirmed_at=iso_now(),
                    run_id=run_id,
                )
                confirmed_by_value = new_confirmation.confirmed_by
                confirmed_at = new_confirmation.confirmed_at
                confirmation_id = new_confirmation.confirmation_id
                confirmation_reused = False
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
            if new_confirmation is not None:
                write_model(
                    _confirmation_dir(ws, project.project_id)
                    / f"{new_confirmation.confirmation_id}.json",
                    new_confirmation,
                )
            write_model(run_dir / "plan.json", snapshot)
            run = Run(
                run_id=run_id,
                plan_id=plan.plan_id,
                project_id=project.project_id,
                scope=snapshot_scope.model_copy(update={"frozen_at": iso_now()}),
                confirmed_by=confirmed_by_value,
                confirmed_at=confirmed_at,
                confirmation_id=confirmation_id,
                confirmation_fingerprint=fingerprint,
                confirmation_reused=confirmation_reused,
                status="running",
                started_at=iso_now(),
                heartbeat_at=iso_now(),
                data_prefix=data_prefix(run_id),
                cases=[
                    RunCase(
                        case_id=case.case_id,
                        result="manual_pending" if is_manual_case(case) else "not_run",
                    )
                    for case in filtered
                ],
            )
            write_model(run_dir / "run.json", run)
            confirmation_info = {
                "confirmation_id": confirmation_id,
                "confirmed_by": confirmed_by_value,
                "confirmed_at": confirmed_at,
                "reused": confirmation_reused,
                "fingerprint": fingerprint,
            }
    except AiTestError as exc:
        if exc.info.code == "WORKSPACE_LOCKED":
            active = _active_run(ws, project.project_id)
            if active is not None:
                raise AiTestError(
                    "RUN_ACTIVE",
                    f"项目已有运行：{active.run_id}",
                    "等待或取消现有运行后重试",
                ) from exc
        raise
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
    run_path = run_dir / "run.json"
    started_run = read_model(run_path, Run)
    started_run.pid = process.pid
    write_model(run_path, started_run)
    return {
        "run_id": run_id,
        "status": "running",
        "scope": {"selected": len(selected), "applicable": plan.applicable_count},
        "confirmation": confirmation_info,
    }


def _run(ws: Workspace, payload: Payload) -> tuple[Path, Run]:
    project = _project(ws, payload)
    run_id = _text({"id": _require(payload, "run_id")}, "id")
    path = _runs_dir(ws, project.project_id) / run_id / "run.json"
    if not path.is_file():
        raise AiTestError("RUN_NOT_FOUND", f"运行不存在：{run_id}", "检查运行 ID")
    return path.parent, _refresh_and_save(path, read_model(path, Run))


def cmd_run_status(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    plan_path = run_dir / "plan.json"
    outcome = None
    uncovered: list[str] = []
    if plan_path.is_file():
        plan = read_model(plan_path, Plan)
        outcome = verdict(plan, run)
        task_path = ws.project_dir(run.project_id) / "tasks" / f"{plan.task_id}.json"
        try:
            task = read_model(task_path, Task) if task_path.is_file() else None
        except AiTestError:
            task = None
        uncovered = uncovered_items(task, plan.scope)
    return {
        "run": run.model_dump(mode="json"),
        "outcome": outcome,
        "uncovered_items": uncovered,
    }


def cmd_run_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    runs: list[dict[str, Any]] = []
    directory = _runs_dir(ws, project.project_id)
    if directory.is_dir():
        for path in sorted(directory.glob("*/run.json")):
            run = _refresh_and_save(path, read_model(path, Run))
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


def _manual_path(run_dir: Path, case_id: str) -> Path:
    safe = "".join(ch for ch in case_id if ch.isalnum() or ch in "-_")
    if not safe:
        raise invalid_request(f"用例 ID 不合法：{case_id}")
    return run_dir / "manual" / f"{safe}.json"


def _manual_evidence_ref(run_dir: Path, value: str) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        relative = value.replace("\\", "/")
        target = (run_dir / relative).resolve()
        root = run_dir.resolve()
        if (target == root or root in target.parents) and target.is_file():
            return relative
    source = candidate.expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"证据文件不存在：{source}")
    name = f"manual-{secrets.token_hex(4)}-{source.name}"
    return _save_evidence_file(run_dir, name, source.read_bytes())


def cmd_run_manual(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    case_id = _text({"id": _require(payload, "case_id")}, "id")
    step = _text({"step": _require(payload, "step")}, "step")
    step_result = _text({"result": _require(payload, "result")}, "result")
    if step_result not in {"passed", "failed", "blocked"}:
        raise invalid_request("result 只能是 passed、failed 或 blocked")
    note = _text(payload, "note")
    raw_evidence = payload.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise invalid_request("evidence 必须是数组")
    with ws.lock():
        run_dir, run = _run(ws, payload)
        run_case = next((item for item in run.cases if item.case_id == case_id), None)
        if run_case is None:
            raise invalid_request(f"运行中没有该用例：{case_id}")
        path = _manual_path(run_dir, case_id)
        if path.is_file():
            log = read_model(path, ManualLog)
        else:
            log = ManualLog(run_id=run.run_id, case_id=case_id)
        refs: list[str] = []
        for item in raw_evidence:
            if not isinstance(item, str) or not item.strip():
                raise invalid_request("evidence 必须只包含文件路径")
            refs.append(_manual_evidence_ref(run_dir, item))
        entry = ManualStep(
            step=step,
            result=cast(Literal["passed", "failed", "blocked"], step_result),
            note=note,
            evidence_refs=refs,
            recorded_at=iso_now(),
        )
        position = next(
            (index for index, item in enumerate(log.steps) if item.step == step), None
        )
        if position is None:
            log.steps.append(entry)
        else:
            log.steps[position] = entry
        write_model(path, log)
        manual_result = aggregate_manual(log.steps)
        run_case.result = manual_result
        run_case.attempts.append(
            Attempt(
                attempt=len(run_case.attempts) + 1,
                result=manual_result,
                started_at=entry.recorded_at,
                finished_at=iso_now(),
                evidence_refs=refs,
                message=f"人工记录：{step}",
            )
        )
        for ref in refs:
            if ref not in run_case.evidence_refs:
                run_case.evidence_refs.append(ref)
            if ref not in run.evidence_refs:
                run.evidence_refs.append(ref)
        write_model(run_dir / "run.json", run)
    return {
        "case_id": case_id,
        "result": manual_result,
        "steps": [item.model_dump(mode="json") for item in log.steps],
    }


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
    run.media_bytes = evidence_bytes(run_dir)
    write_model(run_dir / "run.json", run)
    return {"evidence_ref": ref}


def cmd_evidence_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    items = []
    for ref in run.evidence_refs:
        path = run_dir / ref
        exists = path.is_file()
        items.append(
            {
                "ref": ref,
                "name": Path(ref).name,
                "path": str(path) if exists else "",
                "bytes": path.stat().st_size if exists else 0,
                "exists": exists,
                "missing_reason": "" if exists else "证据文件不在运行目录中",
            }
        )
    return {"evidence": items, "run_id": run.run_id}


# -- report -----------------------------------------------------------------


def cmd_report_generate(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_dir, run = _run(ws, payload)
    report = build_report(ws, project, run_dir)
    return {
        "report_id": report.report_id,
        "run_id": report.run_id,
        "outcome": report.outcome,
        "counts": report.counts,
        "coverage": report.coverage,
        "changed_modules": report.changed_modules,
        "unmapped_files": report.unmapped_files,
        "uncovered_items": report.uncovered_items,
        "layers": report.layers,
    }


def _report_path(ws: Workspace, project_id: str, report_id: str) -> Path:
    return ws.project_dir(project_id) / "reports" / f"{report_id}.json"


def _case_row(detail: dict[str, Any]) -> str:
    refs = "、".join(str(item) for item in detail.get("evidence_refs") or []) or "-"
    return (
        f"| {detail.get('case_id', '')} | {detail.get('acceptance_item_id') or '-'} "
        f"| {detail.get('layer', '')} | {detail.get('result', '')} | {refs} |"
    )


def _analysis_lines(analysis: dict[str, Any]) -> list[str]:
    status = str(analysis.get("status") or "not_generated")
    lines = [f"### 分析状态：{status}"]
    if status != "generated":
        lines.append("本轮未生成 AI 分析。")
        return lines
    lines.append(f"**观察**：{analysis.get('observed') or '未记录'}")
    evidence = analysis.get("evidence_refs") or []
    lines.append(
        "**证据引用**：" + ("、".join(str(item) for item in evidence) if evidence else "无")
    )
    lines.append("**候选原因**：")
    causes = analysis.get("candidate_causes") or []
    if not causes:
        lines.append("- 无")
    else:
        for cause in causes:
            if isinstance(cause, dict):
                cause_text = cause.get("cause") or ""
                confidence = cause.get("confidence") or "unknown"
                lines.append(f"- {cause_text}（把握程度：{confidence}）")
            else:
                lines.append(f"- {cause}")
    lines.append("**尚未验证**：")
    unverified = analysis.get("unverified") or []
    if unverified:
        lines.extend(f"- {item}" for item in unverified)
    else:
        lines.append("- 无")
    lines.append("**下一步**：")
    next_steps = analysis.get("next_steps") or []
    if next_steps:
        lines.extend(f"- {item}" for item in next_steps)
    else:
        lines.append("- 无")
    return lines


def _export_lines(report: Report) -> list[str]:
    lines = [
        f"# 测试报告 {report.report_id}",
        "",
        f"- 结论：{report.outcome}",
        f"- 运行：{report.run_id}",
        f"- 范围：{report.counts.get('selected', 0)}/{report.coverage.get('applicable', 0)}",
        f"- 源码提交：{report.source_commit or '未记录'}",
        f"- 规则版本：{report.rules_version_id or '未记录'}",
    ]
    python_version = report.environment.get("python")
    lines.append(f"- 环境 Python：{python_version or '未记录'}")
    services = report.environment.get("services")
    if isinstance(services, list) and services:
        names = [
            str(item.get("name") or "")
            for item in services
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        lines.append("- 服务：" + ("、".join(names) if names else "未配置"))
    else:
        lines.append("- 服务：未配置")
    if isinstance(report.github, dict):
        repo = report.github.get("repo") or "未记录"
        commit = report.github.get("commit") or "未记录"
        branch = report.github.get("branch") or "未记录"
        lines.append(f"- GitHub：{repo}@{commit}（{branch}）")
    lines.append("")
    if report.summary:
        if report.summary_source == "fallback":
            lines.append("AI 摘要未采用，以下为事实摘要")
        lines.append(f"摘要：{report.summary}")
        lines.append("")
    lines.append("## 范围与覆盖")
    lines.append(f"- 已选验收项：{report.counts.get('selected', 0)}")
    lines.append(f"- 适用验收项：{report.coverage.get('applicable', 0)}")
    lines.append(f"- 覆盖验收项：{report.counts.get('covered', 0)}")
    lines.append(f"- 通过：{report.counts.get('passed', 0)}")
    lines.append(f"- 失败：{report.counts.get('failed', 0)}")
    lines.append(f"- 阻塞或未完成：{report.counts.get('incomplete', 0)}")
    lines.append("")
    lines.append("## 未覆盖清单")
    if report.uncovered_items:
        lines.extend(f"- {item}" for item in report.uncovered_items)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 分层计数")
    for layer in ("L1", "L2", "L3"):
        lines.append(f"- {layer}：{report.layers.get(layer, 0)} 个用例")
    lines.append("")
    lines.append("## 用例明细")
    lines.append("| 用例 | 验收项 | 分层 | 结果 | 证据 |")
    lines.append("| --- | --- | --- | --- | --- |")
    if report.case_details:
        lines.extend(_case_row(detail) for detail in report.case_details)
    else:
        lines.append("| 无 | - | - | - | - |")
    lines.append("")
    lines.append("## 失败证据")
    failed = [item for item in report.case_details if item.get("result") == "failed"]
    if not failed:
        lines.append("- 无失败用例")
    else:
        for detail in failed:
            refs = detail.get("evidence_refs") or []
            rendered = "、".join(str(item) for item in refs) or "未采集证据"
            lines.append(f"- {detail.get('case_id', '')}：{rendered}")
    lines.append("")
    lines.append("## AI 分析")
    if not report.analysis:
        lines.append("- 未生成")
    else:
        for analysis in report.analysis:
            lines.extend(_analysis_lines(analysis))
    lines.append("")
    lines.append("## 问题")
    if report.issues:
        for issue in report.issues:
            lines.append(
                f"- {issue.get('issue_id', '')} [{issue.get('severity', '')}"
                f"/{issue.get('status', '')}] {issue.get('title', '')}"
                f"（发生 {issue.get('occurrences', 0)} 次）"
            )
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## Mock 判定")
    if report.mock_judgement:
        for item in report.mock_judgement:
            lines.append(
                f"- {item.get('name', '')}：{item.get('kind', 'unknown')}"
                f"（{item.get('reason', '')}）"
            )
    else:
        lines.append("- 未判定")
    lines.append("")
    lines.append("## 验收意见")
    if report.opinion is None:
        lines.append("未填写验收意见")
    else:
        lines.append(f"验收意见（{report.opinion.by}，{report.opinion.at}）：{report.opinion.text}")
    lines.append("")
    lines.append("## 证据索引")
    lines.append("| 引用 | 存在 | 字节 |")
    lines.append("| --- | --- | --- |")
    if report.evidence_index:
        for item in report.evidence_index:
            exists = "是" if item.get("exists") else "否"
            lines.append(f"| {item.get('ref', '')} | {exists} | {item.get('bytes', 0)} |")
    else:
        lines.append("| 无 | - | 0 |")
    lines.append("")
    return lines


def cmd_report_export(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _text({"id": _require(payload, "report_id")}, "id")
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"报告不存在：{report_id}", "先生成报告")
    report = read_model(path, Report)
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(_export_lines(report))
    secrets = [value for value in (model_key()[0], github_token()[0]) if value]
    target.write_text(redact(text, secrets), encoding="utf-8")
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
    return add_issue(
        ws,
        project.project_id,
        _text({"run_id": _require(payload, "run_id")}, "run_id"),
        _text({"case_id": _require(payload, "case_id")}, "case_id"),
        _text({"title": _require(payload, "title")}, "title"),
        severity=payload.get("severity", "P2"),
        error_type=_text(payload, "error_type", "unknown"),
    )


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


def _invalid_backup(message: str) -> AiTestError:
    return AiTestError("BACKUP_INVALID", message, "换一个备份或重新备份")


def _check_zip_member(name: str) -> None:
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if not name or normalized.startswith("/") or ".." in parts or ":" in parts[0]:
        raise _invalid_backup(f"备份包含非法路径：{name}")


def _zip_manifest(archive: zipfile.ZipFile) -> tuple[list[str], dict[str, str]]:
    try:
        raw = archive.read("manifest.json")
    except KeyError as exc:
        raise _invalid_backup("备份缺少 manifest.json") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid_backup("备份 manifest 不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise _invalid_backup("备份 manifest 不是对象")
    files = data.get("files")
    hashes = data.get("sha256")
    count = data.get("file_count")
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise _invalid_backup("备份 manifest 缺少文件清单")
    if not isinstance(hashes, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in hashes.items()
    ):
        raise _invalid_backup("备份 manifest 缺少 sha256 摘要")
    if not isinstance(count, int) or isinstance(count, bool):
        raise _invalid_backup("备份 manifest 缺少文件总数")
    if count != len(files) or set(files) != set(hashes):
        raise _invalid_backup("备份 manifest 文件总数或摘要与实际文件不一致")
    return files, hashes


def _evidence_refs(value: object) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_refs" and isinstance(item, list):
                refs.extend(entry for entry in item if isinstance(entry, str))
            elif key == "evidence_index" and isinstance(item, list):
                for entry in item:
                    if isinstance(entry, dict) and isinstance(entry.get("ref"), str):
                        refs.append(entry["ref"])
            elif isinstance(item, (dict, list)):
                refs.extend(_evidence_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_evidence_refs(item))
    return refs


def _is_local_ref(ref: str) -> bool:
    """A reference that should resolve inside the restored workspace."""
    text = ref.strip()
    if not text or "://" in text:
        return False
    normalized = text.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return not (":" in parts[0] or Path(text).is_absolute())


def _record_base(restored: Path, path: Path, data: dict[str, Any]) -> Path:
    """Resolve relative evidence references for one restored business record."""
    if path.name == "run.json":
        return path.parent
    run_id = data.get("run_id")
    if isinstance(run_id, str) and run_id:
        parts = path.relative_to(restored).parts
        if len(parts) >= 2 and parts[0] == "projects":
            candidate = restored / "projects" / parts[1] / "runs" / run_id
            if candidate.is_dir():
                return candidate
    return path.parent


def _missing_evidence(restored: Path) -> list[str]:
    missing: list[str] = []
    for path in sorted(restored.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        base = _record_base(restored, path, data)
        for ref in _evidence_refs(data):
            if not _is_local_ref(ref) or ref in missing:
                continue
            if not (base / ref).is_file():
                missing.append(ref)
    return sorted(missing)


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
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": iso_now(),
        "includes_evidence": include_evidence,
        "file_count": len(files),
        "total_files": len(files),
        "files": sorted(str(item).replace("\\", "/") for item in files),
        "sha256": {},
    }
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(files):
            key = str(relative).replace("\\", "/")
            data = (ws.root / relative).read_bytes()
            manifest["sha256"][key] = hashlib.sha256(data).hexdigest()
            archive.writestr(key, data)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "path": str(target),
        "sha256": digest,
        "bytes": target.stat().st_size,
        "includes_evidence": include_evidence,
        "files": len(files),
        "file_count": len(files),
    }


def cmd_backup_restore(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del ws, confirm
    source = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    target = Path(_text({"target": _require(payload, "target")}, "target")).expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"备份不存在：{source}")
    if target.exists():
        if not target.is_dir():
            raise invalid_path(f"目标不是目录：{target}", "换一个空目录")
        if any(target.iterdir()):
            raise invalid_path(f"目标目录非空：{target}", "换一个空目录")
    with zipfile.ZipFile(source) as archive:
        files, hashes = _zip_manifest(archive)
        members: dict[str, zipfile.ZipInfo] = {}
        for member in archive.infolist():
            _check_zip_member(member.filename)
            if member.is_dir():
                continue
            if member.filename in members:
                raise _invalid_backup(f"备份包含重复条目：{member.filename}")
            if member.external_attr >> 16 & 0o170000 == 0o120000:
                raise _invalid_backup(f"备份包含符号链接：{member.filename}")
            members[member.filename] = member
        if set(members) - {"manifest.json"} != set(files):
            raise _invalid_backup("备份 manifest 与 zip 内容不一致")
        for name in files:
            digest = hashlib.sha256(archive.read(name)).hexdigest()
            if digest != hashes[name]:
                raise _invalid_backup(f"备份文件摘要不匹配：{name}")
        target.mkdir(parents=True, exist_ok=True)
        archive.extractall(target)
    missing = _missing_evidence(target)
    notes = (
        f"证据未包含：{len(missing)} 个引用在恢复目录中不存在" if missing else ""
    )
    return {
        "target": str(target),
        "files": len(files),
        "verified": True,
        "missing_evidence": missing,
        "notes": notes,
    }


def _trim_output(text: str | None, limit: int = 4000) -> str:
    if not text:
        return ""
    return text[-limit:]


def _action_log(ws: Workspace, entry: dict[str, Any]) -> str:
    month = datetime.now().astimezone().strftime("%Y-%m")
    path = ws.root / "actions" / f"{month}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return str(path.relative_to(ws.root)).replace("\\", "/")


def cmd_data_cleanup(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    project = _project(ws, payload)
    config = load_project_config(ws, project.project_id)
    settings = config.get("data")
    dry_run = bool(payload.get("dry_run", True))
    if not isinstance(settings, dict):
        raise invalid_request(
            "未配置数据清理命令",
            '在 project.config.json 增加 "data": {"cleanup_dry_run": "...", "cleanup": "..."}',
        )
    key = "cleanup_dry_run" if dry_run else "cleanup"
    command_value = settings.get(key)
    if not isinstance(command_value, str) or not command_value.strip():
        raise invalid_request(
            f"未配置 data.{key}",
            f'在 project.config.json 的 data 段补充 "{key}" 命令',
        )
    if not dry_run and not confirm:
        raise AiTestError("CONFIRMATION_REQUIRED", "清理数据需要确认", "加 --confirm 或先 dry-run")
    run_id = _text(payload, "run_id")
    if "{run_id}" in command_value:
        if not run_id:
            raise invalid_request("清理命令包含 {run_id} 占位符，需要提供 run_id")
        if not all(char.isalnum() or char in "-_." for char in run_id):
            raise invalid_request("run_id 只允许字母、数字、-、_ 和 .")
    command = command_value.replace("{run_id}", run_id)
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=Path(project.root),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AiTestError(
            "CLEANUP_FAILED", f"清理命令无法执行：{exc}", "检查命令与项目根目录"
        ) from exc
    entry = {
        "at": iso_now(),
        "run_id": run_id,
        "command": command,
        "dry_run": dry_run,
        "exit_code": completed.returncode,
        "stdout": _trim_output(completed.stdout),
        "stderr": _trim_output(completed.stderr),
    }
    log_path = _action_log(ws, entry)
    if completed.returncode != 0:
        raise AiTestError(
            "CLEANUP_FAILED",
            f"清理命令退出码 {completed.returncode}",
            f"检查 {log_path} 中的输出",
        )
    return {
        "dry_run": dry_run,
        "executed": not dry_run,
        "run_id": run_id,
        "command": command,
        "exit_code": completed.returncode,
        "stdout": _trim_output(completed.stdout),
        "stderr": _trim_output(completed.stderr),
        "log_path": log_path,
    }


def cmd_analysis_run(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_dir, _current = _run(ws, payload)
    return analyze_run(ws, project.project_id, run_dir)


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
        "run.manual": cmd_run_manual,
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
        "run.manual",
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
