"""Runtime 命令模块：run_commands。"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from aitest.commands import (
    Payload,
    _project,
    _require,
    _safe_id,
    _text,
)
from aitest.config import github_token, known_secret_values, model_key
from aitest.core.analysis import analyze_run
from aitest.core.evidence import (
    data_prefix,
    evidence_bytes,
    evidence_path,
    sha256_file,
)
from aitest.core.link_location import locate_link
from aitest.core.manual import aggregate_manual, is_manual_case
from aitest.core.scope import uncovered_items
from aitest.core.shared_config import resolve_environment_profile
from aitest.core.verdict import coverage, item_outcomes, verdict
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import load_project_config
from aitest.models import (
    Attempt,
    Confirmation,
    Delivery,
    ManualLog,
    ManualStep,
    Plan,
    Run,
    RunCase,
    Scope,
    Task,
    case_item_ids,
    new_id,
)
from aitest.procutil import pid_alive
from aitest.runners.package_install import install_packages
from aitest.runtime._common import (
    _active_run,
    _as_bool,
    _ensure_settled,
    _evidence_settings,
    _load_plan,
    _plan_dependencies,
    _plan_signature,
    _refresh_and_save,
    _refresh_stale,
    _run,
    _runs_dir,
    _safe_script_name,
    _save_evidence_file,
    _save_evidence_stream,
    _snapshot_excluded_items,
    _task_by_id,
    build_resumed_run_cases,
    copy_resumed_evidence,
    facade_attr,
    load_resumable_cases,
)
from aitest.security import redact, scan_script
from aitest.storage.files import read_json, read_model, write_json, write_model
from aitest.storage.workspace import Workspace, iso_now


def _spawn_worker(
    ws: Workspace, project_id: str, run_dir: Path, run_id: str
) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    flags = (0x08000000 | 0x00000200) if os.name == "nt" else 0
    log_path = run_dir / "worker-stderr.log"
    stderr_handle = log_path.open("ab")
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "aitest.worker",
                "--workspace",
                str(ws.root),
                "--project",
                project_id,
                "--run",
                run_id,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
            creationflags=flags,
            close_fds=True,
        )
    finally:
        stderr_handle.close()



def _elapsed_seconds(start: str, end: str) -> int | None:
    try:
        started = datetime.fromisoformat(start)
        finished = datetime.fromisoformat(end)
    except ValueError:
        return None
    return max(0, int((finished - started).total_seconds()))



def _confirmation_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "confirmations"



def _environment_summary(
    ws: Workspace, project_id: str, requested_env: str = ""
) -> dict[str, Any]:
    config = load_project_config(ws, project_id)
    profile = resolve_environment_profile(config, requested_env)
    if requested_env and not profile["found"]:
        raise invalid_request(
            f"环境档案不存在：{requested_env}",
            "检查 project.config.json 或 .aitest.json 中的 environments 配置后重试",
        )
    raw_services = config.get("services")
    services = dict(raw_services) if isinstance(raw_services, dict) else {}
    if isinstance(profile.get("services"), dict) and profile["services"]:
        services.update(profile["services"])
    summary: dict[str, dict[str, str]] = {}
    for name, value in services.items():
        if isinstance(name, str) and isinstance(value, dict):
            summary[name] = {
                "health": str(value.get("health") or ""),
                "start": str(value.get("start") or ""),
            }
    result: dict[str, Any] = {"services": summary}
    if profile["name"]:
        result["profile"] = profile["name"]
    if profile["base_url"]:
        result["base_url"] = profile["base_url"]
    return result



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
    plan: Plan,
    selected: list[str],
    environment: dict[str, Any],
    credentials: dict[str, str],
    side_effects: list[str],
) -> str:
    payload = {
        "plan_id": plan.plan_id,
        "plan_revision": plan.revision,
        "scope": sorted(selected),
        "environment": environment,
        "credentials": credentials,
        "side_effects": sorted(side_effects),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()



def _side_effects(plan: Plan, selected: list[str]) -> list[str]:
    """Side-effect summary shown in the confirmation record."""
    effects: list[str] = []
    for case in plan.cases:
        bound = set(case_item_ids(case))
        if bound and bound.isdisjoint(selected):
            continue
        method = case.verify_method.strip()
        if method.startswith("http:"):
            try:
                requests = json.loads(method[len("http:") :])
            except json.JSONDecodeError:
                requests = []
            for request in requests if isinstance(requests, list) else []:
                if isinstance(request, dict) and str(request.get("method", "GET")).upper() not in {
                    "GET",
                    "HEAD",
                    "OPTIONS",
                }:
                    effects.append(f"{case.case_id}：{request.get('method')} {request.get('url')}")
        elif method.startswith(("command:", "script:", "pytest:", "sql:", "playwright:", "junit:")):
            effects.append(f"{case.case_id}：执行 {method.split(':', 1)[0]} 验证")
    return sorted(set(effects))



def _latest_run(ws: Workspace, project_id: str) -> Run | None:
    directory = _runs_dir(ws, project_id)
    if not directory.is_dir():
        return None
    latest: Run | None = None
    for path in sorted(directory.glob("*/run.json")):
        try:
            run = read_model(path, Run)
        except AiTestError:
            continue
        if latest is None or run.started_at > latest.started_at:
            latest = run
    return latest



def _reusable_confirmation(
    ws: Workspace, project_id: str, plan: Plan, fingerprint: str, selected: list[str]
) -> Confirmation | None:
    """Reuse confirmation only for the same plan revision, scope and environment."""
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
    if record.plan_id != plan.plan_id or record.plan_revision != plan.revision:
        return None
    if record.fingerprint != fingerprint:
        return None
    if sorted(record.acceptance_item_ids) != sorted(selected):
        return None
    return record



def cmd_run_quick(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Run project-level L1 only; no task, no acceptance items, no plan scope."""
    project = _project(ws, payload)
    plan_id = new_id("plan")
    run_id = new_id("run")
    run_dir = _runs_dir(ws, project.project_id) / run_id
    scope = Scope(preset="selected", acceptance_item_ids=[])
    plan = Plan(
        plan_id=plan_id,
        task_id="",
        plan_no=0,
        revision=1,
        scope=scope,
        applicable_count=0,
        cases=[],
        status="published",
        source_signature="",
    )
    requested_env = _text(payload, "environment")
    environment = _environment_summary(ws, project.project_id, requested_env)
    credentials = _credential_summary()
    side_effects = ["执行项目级检查"]
    fingerprint = _confirmation_fingerprint(plan, [], environment, credentials, side_effects)
    confirmed_by = _text(payload, "confirmed_by") or "cli"
    try:
        with ws.project_lock(project.project_id):
            active = _active_run(ws, project.project_id)
            if active is not None:
                raise AiTestError(
                    "RUN_ACTIVE",
                    f"项目已有运行：{active.run_id}",
                    "等待或取消现有运行后重试",
                )
            if not confirm:
                raise AiTestError(
                    "CONFIRMATION_REQUIRED", "缺少执行前确认", "在面板确认或加 --confirm"
                )
            confirmation = Confirmation(
                confirmation_id=new_id("conf"),
                project_id=project.project_id,
                plan_id=plan_id,
                plan_revision=1,
                acceptance_item_ids=[],
                environment=environment,
                credentials=credentials,
                side_effects=side_effects,
                fingerprint=fingerprint,
                confirmed_by=confirmed_by,
                confirmed_at=iso_now(),
                run_id=run_id,
            )
            run_dir.mkdir(parents=True, exist_ok=False)
            write_model(
                _confirmation_dir(ws, project.project_id)
                / f"{confirmation.confirmation_id}.json",
                confirmation,
            )
            write_model(run_dir / "plan.json", plan)
            run = Run(
                run_id=run_id,
                plan_id=plan_id,
                project_id=project.project_id,
                kind="project_check",
                scope=scope.model_copy(update={"frozen_at": iso_now()}),
                confirmed_by=confirmed_by,
                confirmed_at=confirmation.confirmed_at,
                confirmation_id=confirmation.confirmation_id,
                confirmation_fingerprint=fingerprint,
                confirmation_reused=False,
                status="running",
                started_at=iso_now(),
                heartbeat_at=iso_now(),
                data_prefix=data_prefix(run_id),
            )
            write_model(run_dir / "run.json", run)
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
    try:
        _spawn_worker(ws, project.project_id, run_dir, run_id)
    except OSError as exc:
        with ws.project_lock(project.project_id):
            failed = read_model(run_dir / "run.json", Run)
            failed.status = "execution_error"
            failed.heartbeat_at = iso_now()
            write_model(run_dir / "run.json", failed)
        raise AiTestError(
            "RUN_START_FAILED",
            f"worker 启动失败：{exc}",
            "检查 Python 解释器与运行目录权限后重试",
        ) from exc
    return {
        "run_id": run_id,
        "status": "running",
        "kind": "project_check",
        "scope": {"selected": 0, "applicable": 0},
        "confirmation": {
            "confirmation_id": confirmation.confirmation_id,
            "confirmed_by": confirmed_by,
            "confirmed_at": confirmation.confirmed_at,
            "reused": False,
            "fingerprint": fingerprint,
            "plan_revision": 1,
            "side_effects": side_effects,
        },
    }



def cmd_run_start(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    project = _project(ws, payload)
    plan = _load_plan(ws, project.project_id, _text({"id": _require(payload, "plan_id")}, "id"))
    if plan.status != "published":
        raise AiTestError("PLAN_NOT_PUBLISHED", "计划尚未发布", "先发布计划")
    warnings: list[str] = []
    current_signature = _plan_signature(ws, project, plan.task_id)
    if not plan.source_signature or plan.source_signature != current_signature:
        raise AiTestError(
            "PLAN_STALE",
            "计划缺少来源签名，或生成后项目、交付提交、任务验收项或服务配置已变化",
            "重新生成计划后重试",
        )
    confirmed_by = _text(payload, "confirmed_by")
    selected = list(plan.scope.acceptance_item_ids)
    narrowing = payload.get("acceptance_item_ids")
    if narrowing is not None:
        if (
            not isinstance(narrowing, list)
            or not narrowing
            or any(not isinstance(item, str) for item in narrowing)
        ):
            raise invalid_request("acceptance_item_ids 必须是非空字符串数组")
        deduped = list(dict.fromkeys(item for item in narrowing))
        outside = [item for item in deduped if item not in selected]
        if outside:
            raise invalid_request("收窄范围超出计划范围：" + "、".join(outside))
        selected = deduped
    if not selected and (
        plan.applicable_count > 0
        or not any(not case_item_ids(case) for case in plan.cases)
    ):
        raise AiTestError("SCOPE_EMPTY", "本次范围为空", "至少选择一条验收项")
    requested_env = _text(payload, "environment")
    environment = _environment_summary(ws, project.project_id, requested_env)
    credentials = _credential_summary()
    relevant_cases = [
        case
        for case in plan.cases
        if not case_item_ids(case) or not set(case_item_ids(case)).isdisjoint(selected)
    ]
    dependencies = _plan_dependencies(relevant_cases)
    side_effects = _side_effects(plan, selected)
    fingerprint = _confirmation_fingerprint(
        plan, selected, environment, credentials, side_effects
    )
    resume_from_run_id = _text(payload, "resume_from_run_id")
    resumable_map: dict[str, dict[str, Any]] = {}
    resumable_refs: list[str] = []
    source_run_dir: Path | None = None
    if resume_from_run_id:
        resume_from_run_id = _safe_id(resume_from_run_id, "resume_from_run_id")
        source_run_dir = _runs_dir(ws, project.project_id) / resume_from_run_id
        resumable_map, resumable_refs = load_resumable_cases(
            source_run_dir, plan, relevant_cases
        )
    run_id = new_id("run")
    run_dir = _runs_dir(ws, project.project_id) / run_id
    confirmation_info: dict[str, Any] = {}
    try:
        with ws.project_lock(project.project_id):
            active = _active_run(ws, project.project_id)
            if active is not None:
                raise AiTestError(
                    "RUN_ACTIVE",
                    f"项目已有运行：{active.run_id}",
                    "等待或取消现有运行后重试",
                )
            explicit = confirm
            reused_record = _reusable_confirmation(
                ws, project.project_id, plan, fingerprint, selected
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
                    plan_revision=plan.revision,
                    acceptance_item_ids=list(selected),
                    environment=environment,
                    credentials=credentials,
                    side_effects=side_effects,
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
                if not case_item_ids(case)
                or not set(case_item_ids(case)).isdisjoint(selected)
            ]
            snapshot_scope = plan.scope.model_copy(update={"acceptance_item_ids": selected})
            snapshot = plan.model_copy(update={"cases": filtered, "scope": snapshot_scope})
            generated = run_dir / "generated"
            config = load_project_config(ws, project.project_id)
            secrets_list = known_secret_values([config])
            model_value = config.get("model")
            model_name = (
                str(model_value.get("name") or "")
                if isinstance(model_value, dict)
                else ""
            )
            generated_index: list[dict[str, Any]] = []
            written_scripts: set[str] = set()
            for case in filtered:
                if not case.script_name or not case.script_content:
                    continue
                name = case.script_name.strip()
                if name.lower() in written_scripts:
                    import shutil

                    shutil.rmtree(run_dir, ignore_errors=True)
                    raise AiTestError(
                        "SCRIPT_REJECTED",
                        f"脚本文件名冲突：{name}",
                        "每条脚本用例必须使用唯一 script_name",
                    )
                written_scripts.add(name.lower())
                if not _safe_script_name(name):
                    import shutil

                    shutil.rmtree(run_dir, ignore_errors=True)
                    raise AiTestError(
                        "SCRIPT_REJECTED",
                        f"脚本文件名不合法：{name}",
                        "只允许 generated/ 下的单层文件名（.py/.ts/.js）",
                    )
                matches = scan_script(case.script_content)
                if matches:
                    import shutil

                    shutil.rmtree(run_dir, ignore_errors=True)
                    raise AiTestError(
                        "SCRIPT_REJECTED",
                        f"生成脚本包含危险操作：{', '.join(matches)}",
                        "检查脚本或改用手写验证",
                    )
                content = case.script_content
                redacted = redact(content, secrets_list) != content
                if redacted:
                    content = redact(content, secrets_list)
                marker = "# generated by aitest\n" if name.endswith(".py") else (
                    "// generated by aitest\n"
                )
                if marker.strip() not in content:
                    content = marker + content
                generated.mkdir(parents=True, exist_ok=True)
                target = generated / name
                if target.resolve().parent != generated.resolve():
                    import shutil

                    shutil.rmtree(run_dir, ignore_errors=True)
                    raise AiTestError(
                        "SCRIPT_REJECTED", f"脚本路径越界：{name}", "改用单层文件名"
                    )
                target.write_text(content, encoding="utf-8")
                case.script_content = content
                case.verify_method = f"script:{name}"
                generated_index.append(
                    {
                        "name": name,
                        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "generated_at": iso_now(),
                        "model": model_name or "provided",
                        "secrets_redacted": redacted,
                    }
                )
            if generated_index:
                write_json(
                    generated / "index.json",
                    {"schema_version": 1, "files": generated_index},
                )
            if new_confirmation is not None:
                write_model(
                    _confirmation_dir(ws, project.project_id)
                    / f"{new_confirmation.confirmation_id}.json",
                    new_confirmation,
                )
            task = _task_by_id(ws, project.project_id, plan.task_id)
            write_model(run_dir / "plan.json", snapshot)
            write_model(run_dir / "task.json", task)
            delivery_path = (
                ws.project_dir(project.project_id) / "deliveries" / f"{task.task_id}.json"
            )
            if delivery_path.is_file():
                write_model(run_dir / "delivery.json", read_model(delivery_path, Delivery))
            excluded_snapshot = (
                []
                if not selected and snapshot.applicable_count == 0
                else _snapshot_excluded_items(task, selected)
            )
            resumed_case_refs: list[str] = []
            if source_run_dir is not None and resumable_map:
                copied_refs = copy_resumed_evidence(
                    source_run_dir, run_dir, resumable_refs
                )
                copied_set = set(copied_refs)
                filtered_resumable = {
                    cid: {
                        **entry,
                        "evidence_refs": [
                            ref
                            for ref in (entry.get("evidence_refs") or [])
                            if ref in copied_set
                        ],
                    }
                    for cid, entry in resumable_map.items()
                }
                initial_cases, resumed_case_refs = build_resumed_run_cases(
                    filtered_cases=filtered,
                    resumable=filtered_resumable,
                    source_run_id=resume_from_run_id,
                    is_manual_fn=is_manual_case,
                )
            else:
                initial_cases = [
                    RunCase(
                        case_id=case.case_id,
                        result="manual_pending" if is_manual_case(case) else "not_run",
                    )
                    for case in filtered
                ]
            profile_name = str(environment.get("profile") or "")
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
                environment={"profile": profile_name} if profile_name else {},
                excluded_items=excluded_snapshot,
                resumed_from_run_id=resume_from_run_id,
                evidence_refs=resumed_case_refs,
                media_bytes=evidence_bytes(run_dir) if resumed_case_refs else 0,
                cases=initial_cases,
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
    try:
        process = _spawn_worker(ws, project.project_id, run_dir, run_id)
    except OSError as exc:
        with ws.project_lock(project.project_id):
            failed_run = read_model(run_dir / "run.json", Run)
            failed_run.status = "execution_error"
            failed_run.heartbeat_at = iso_now()
            write_model(run_dir / "run.json", failed_run)
        raise AiTestError(
            "RUN_START_FAILED",
            f"worker 启动失败：{exc}",
            "检查 Python 解释器与运行目录权限后重试",
        ) from exc
    assert process is not None
    result: dict[str, Any] = {
        "run_id": run_id,
        "status": "running",
        "scope": {"selected": len(selected), "applicable": plan.applicable_count},
        "confirmation": {
            **confirmation_info,
            "plan_revision": plan.revision,
            "side_effects": side_effects,
            "dependencies": dependencies,
        },
        "warnings": warnings,
    }
    if resume_from_run_id:
        result["resumed_from_run_id"] = resume_from_run_id
        result["resumed_cases"] = len(resumable_map)
    return result



def cmd_run_status(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    plan_path = run_dir / "plan.json"
    plan_snapshot: Plan | None = None
    task_snapshot: Task | None = None
    task_snapshot_exists = False
    outcome = None
    uncovered: list[str] = []
    counts: dict[str, int] = {}
    item_rows: list[dict[str, Any]] = []
    pending_items = 0
    if plan_path.is_file():
        plan_snapshot = read_model(plan_path, Plan)
        outcome = verdict(plan_snapshot, run)
        counts = coverage(plan_snapshot, run)
        item_rows = [
            {
                "item_id": item_id,
                "result": result,
                "case_ids": [
                    case.case_id
                    for case in plan_snapshot.cases
                    if item_id in case_item_ids(case)
                ],
            }
            for item_id, result in item_outcomes(plan_snapshot, run).items()
        ]
        pending_items = max(0, counts.get("applicable", 0) - counts.get("covered", 0))
        snapshot_path = run_dir / "task.json"
        snapshot_present = snapshot_path.is_file()
        if snapshot_present:
            try:
                task_snapshot = read_model(snapshot_path, Task)
                task_snapshot_exists = True
            except AiTestError:
                task_snapshot = None
        if task_snapshot is None and not snapshot_present:
            task_path = ws.project_dir(run.project_id) / "tasks" / f"{plan_snapshot.task_id}.json"
            try:
                task_snapshot = read_model(task_path, Task) if task_path.is_file() else None
            except AiTestError:
                task_snapshot = None
        uncovered = uncovered_items(
            task_snapshot, plan_snapshot.scope, plan_snapshot.applicable_count
        )
    link: dict[str, Any] | None = None
    link_path = run_dir / "link.json"
    if link_path.is_file():
        imported = read_json(link_path)
        stored = imported.get("location")
        if isinstance(stored, dict):
            location = stored
        else:
            nodes = imported.get("nodes")
            assertions = imported.get("assertions")
            location = locate_link(
                nodes if isinstance(nodes, list) else [],
                assertions if isinstance(assertions, list) else [],
            )
        link = {
            **(location if isinstance(location, dict) else {}),
            "assertions": imported.get("assertions")
            if isinstance(imported.get("assertions"), list)
            else [],
        }
    include_tail = _as_bool(payload.get("include_tail"), False)
    result_payload: dict[str, Any] = {
        "run": run.model_dump(mode="json"),
        "plan": plan_snapshot.model_dump(mode="json") if plan_snapshot is not None else None,
        "task": task_snapshot.model_dump(mode="json") if task_snapshot is not None else None,
        "task_snapshot": task_snapshot_exists,
        "outcome": outcome,
        "counts": counts,
        "item_outcomes": item_rows,
        "pending_items": pending_items,
        "manual_cases": sum(1 for case in run.cases if case.result == "manual_pending"),
        "l1": run.l1,
        "uncovered_items": uncovered,
        "link": link,
        "excluded_items": (
            run.excluded_items
            if run.excluded_items is not None
            else (plan_snapshot.excluded_items if plan_snapshot is not None else None)
        ),
    }
    if include_tail:
        result_payload["log_tail"] = _collect_run_log_tail(
            run_dir,
            known_secret_values([load_project_config(ws, run.project_id)]),
        )
    return result_payload


def _read_tail_text(path: Path, secrets_list: list[str], max_bytes: int = 4096) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size <= 0:
                return ""
            handle.seek(max(0, size - max_bytes), os.SEEK_SET)
            raw = handle.read(max_bytes)
    except OSError:
        return ""
    if b"\x00" in raw:
        return ""
    return redact(raw.decode("utf-8", errors="replace"), secrets_list)


def _collect_run_log_tail(run_dir: Path, secrets_list: list[str]) -> list[dict[str, str]]:
    """收集运行目录末尾日志片段（worker stderr、最新证据日志与服务日志）。"""
    candidates: list[tuple[str, Path]] = []
    stderr_log = run_dir / "worker-stderr.log"
    if stderr_log.is_file():
        candidates.append(("worker-stderr.log", stderr_log))
    for folder, limit in (("evidence", 2), ("services", 1)):
        sub = run_dir / folder
        if not sub.is_dir():
            continue
        logs = [p for p in sub.glob("*.log") if p.is_file()]
        logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for path in logs[:limit]:
            candidates.append((f"{folder}/{path.name}", path))
    tails: list[dict[str, str]] = []
    for label, path in candidates:
        text = _read_tail_text(path, secrets_list)
        if text.strip():
            tails.append({"name": label, "text": text})
    return tails



_REPAIR_SPEC = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
    r"(?:\[[A-Za-z0-9,._-]+\])?"
    r"(?:[<>=!~][A-Za-z0-9.*+!<>=~-]{1,40})?$"
)



def _project_python(
    root: Path,
    config: dict[str, Any] | None = None,
) -> Path:
    """统一与 Worker 一致的解释器解析：runners.python -> 项目 .venv -> sys.executable。"""
    from aitest.worker import _venv_python
    from aitest.worker_impl.env import _configured_python

    configured = _configured_python(config, root) if isinstance(config, dict) else None
    if configured is not None:
        return configured
    found = _venv_python(root)
    return found if found is not None else Path(sys.executable)



def _installed_versions(
    python: Path, packages: list[str], env: dict[str, str]
) -> dict[str, str]:
    probe = (
        "import importlib.metadata as m, re, sys\n"
        "for spec in sys.argv[1:]:\n"
        "    name = re.split(r'[<>=!~\\[]', spec)[0]\n"
        "    try:\n"
        "        print(name + '==' + m.version(name))\n"
        "    except Exception:\n"
        "        print(name + '==?')\n"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", probe, *packages],
            capture_output=True,
            timeout=60,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return {spec: "?" for spec in packages}
    versions: dict[str, str] = {}
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        name, _, version = line.partition("==")
        if name:
            versions[name] = version or "?"
    return versions



def cmd_run_repair(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Install reviewed test dependencies for a completed run and record the change."""

    if not confirm:
        raise AiTestError(
            "CONFIRMATION_REQUIRED",
            "安装依赖需要显式确认",
            "查看包名与来源后带 --confirm 重试",
        )
    project = _project(ws, payload)
    confirmed_by = _text(payload, "confirmed_by").strip()
    if not confirmed_by:
        raise AiTestError(
            "CONFIRMATION_REQUIRED",
            "缺少字段：confirmed_by",
            "安装依赖必须记录确认人，带 confirmed_by 与 --confirm 重试",
        )
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        raise invalid_request("packages 必须是非空数组", "从本次运行的依赖建议中选择")
    packages: list[str] = []
    for item in raw_packages:
        spec = str(item).strip()
        if not _REPAIR_SPEC.fullmatch(spec):
            raise invalid_request(f"依赖格式不合法：{spec}", "只接受包名与版本范围")
        packages.append(spec)

    # 使用项目级安装锁串联并发修复，同时在耗时最长 600s 的 pip 安装阶段释放工作空间与项目写锁。
    with ws.repair_lock(project.project_id, timeout=10.0):
        with ws.project_lock(project.project_id, timeout=30.0):
            run_dir, run = _run(ws, payload)
            _ensure_settled(run)
            suggested = {
                str(entry.get("package") or "")
                for entry in run.dependency_suggestions
                if isinstance(entry, dict) and str(entry.get("installed") or "") != "true"
            }
            already = [spec for spec in packages if spec not in suggested]
            if already and all(
                any(
                    isinstance(entry, dict)
                    and entry.get("package") == spec
                    and str(entry.get("installed") or "") == "true"
                    for entry in run.dependency_suggestions
                )
                for spec in already
            ):
                raise AiTestError(
                    "DEPENDENCY_ALREADY_INSTALLED",
                    "这些依赖已经安装并记录",
                    "直接重跑未完成项；不要重复安装",
                )
            unknown = [spec for spec in packages if spec not in suggested]
            if unknown:
                raise invalid_request(
                    "只能安装本次运行检测到的依赖：" + "、".join(unknown),
                    "重新查看运行结果中的依赖修复建议",
                )
            run_id = run.run_id

        cfg_repair = load_project_config(ws, project.project_id)
        python = _project_python(Path(project.root), cfg_repair)
        from aitest.worker import _environment_fingerprint, _run_env

        env = _run_env(Path(project.root), python, run_id)
        installed = facade_attr("install_packages", install_packages)(
            python,
            packages,
            Path(project.root),
            env,
            timeout_seconds=600,
        )
        output = installed.output
        ok = installed.ok
        exit_code = installed.exit_code
        versions_fn = facade_attr("_installed_versions", _installed_versions)
        versions = versions_fn(python, packages, env) if ok else {}

        with ws.project_lock(project.project_id, timeout=30.0):
            run = _refresh_and_save(run_dir / "run.json", read_model(run_dir / "run.json", Run))
            config = load_project_config(ws, project.project_id)
            budget, cap, allow_video = _evidence_settings(config)
            ref = _save_evidence_file(
                run_dir,
                "repair-deps.log",
                output,
                budget,
                cap,
                allow_video,
                known_secret_values([config]),
                result="passed" if ok else "failed",
            )
            repair = {
                "at": iso_now(),
                "confirmed_by": confirmed_by,
                "packages": versions if versions else {spec: "?" for spec in packages},
                "exit_code": exit_code,
                "ok": ok,
                "installer": installed.installer,
                "attempts": installed.attempts,
                "evidence_ref": ref,
            }
            run.dependency_repairs.append(repair)
            if not ok:
                write_model(run_dir / "run.json", run)
                next_step = "查看 repair-deps.log；修正环境后重试"
                if installed.error == "ensurepip failed":
                    next_step = (
                        "项目解释器没有 pip，且 uv/ensurepip 均不可用；"
                        "安装 uv 或重建带 pip 的 venv 后重试"
                    )
                raise AiTestError(
                    "DEPENDENCY_INSTALL_FAILED",
                    "依赖安装失败",
                    next_step,
                )
            before = dict(run.environment)
            after = _environment_fingerprint(python, Path(project.root))
            run.environment_history.append(
                {
                    "at": repair["at"],
                    "confirmed_by": confirmed_by,
                    "packages": repair["packages"],
                    "before": before,
                    "after": after,
                    "evidence_ref": ref,
                }
            )
            run.environment = after
            repair["environment_before"] = before
            repair["environment_after"] = after
            for spec in packages:
                name = _installed_versions_key(spec)
                if name not in run.dependencies:
                    run.dependencies.append(name)
            for entry in run.dependency_suggestions:
                if isinstance(entry, dict) and entry.get("package") in packages:
                    entry["installed"] = "true"
            write_model(run_dir / "run.json", run)
            return {
                "run_id": run.run_id,
                "repair": repair,
                "environment": run.environment,
                "dependencies": run.dependencies,
            }



def _installed_versions_key(spec: str) -> str:
    return re.split(r"[<>=!~\[]", spec)[0].strip()



def cmd_run_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    runs: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    directory = _runs_dir(ws, project.project_id)
    if directory.is_dir():
        idx_path = ws.project_dir(project.project_id) / ".runs_index.json"
        cached_items: dict[str, Any] = {}
        if idx_path.is_file():
            try:
                raw_idx = read_json(idx_path)
                if isinstance(raw_idx, dict) and isinstance(raw_idx.get("items"), dict):
                    cached_items = dict(raw_idx["items"])
            except (AiTestError, OSError):
                cached_items = {}
        new_items: dict[str, Any] = {}
        idx_dirty = False
        for path in sorted(directory.glob("*/run.json")):
            rid = path.parent.name
            run_dir = path.parent
            plan_path = run_dir / "plan.json"
            task_path = run_dir / "task.json"
            try:
                mtime_sig = [
                    path.stat().st_mtime_ns,
                    plan_path.stat().st_mtime_ns if plan_path.is_file() else -1,
                    task_path.stat().st_mtime_ns if task_path.is_file() else -1,
                ]
            except OSError:
                mtime_sig = [0, -1, -1]
            cached_entry = cached_items.get(rid)
            if (
                isinstance(cached_entry, dict)
                and cached_entry.get("sig") == mtime_sig
                and isinstance(cached_entry.get("row"), dict)
                and cached_entry["row"].get("status") != "running"
            ):
                new_items[rid] = cached_entry
                runs.append(dict(cached_entry["row"]))
                continue
            try:
                run = _refresh_stale(read_model(path, Run))
            except AiTestError as exc:
                skipped.append(
                    {
                        "run_id": rid,
                        "code": exc.info.code,
                        "message": exc.info.message,
                    }
                )
                continue
            plan_snapshot: Plan | None = None
            task_snapshot: Task | None = None
            task_snapshot_exists = False
            if plan_path.is_file():
                try:
                    plan_snapshot = read_model(plan_path, Plan)
                except AiTestError:
                    plan_snapshot = None
            if task_path.is_file():
                try:
                    task_snapshot = read_model(task_path, Task)
                    task_snapshot_exists = True
                except AiTestError:
                    task_snapshot = None
            if task_snapshot is None and plan_snapshot is not None:
                current_path = (
                    ws.project_dir(project.project_id)
                    / "tasks"
                    / f"{plan_snapshot.task_id}.json"
                )
                if current_path.is_file():
                    try:
                        task_snapshot = read_model(current_path, Task)
                    except AiTestError:
                        task_snapshot = None
            outcome = verdict(plan_snapshot, run) if plan_snapshot is not None else None
            counts = coverage(plan_snapshot, run) if plan_snapshot is not None else {}
            row = {
                "run_id": run.run_id,
                "kind": run.kind,
                "status": run.status,
                "started_at": run.started_at,
                "heartbeat_at": run.heartbeat_at,
                "elapsed_seconds": _elapsed_seconds(run.started_at, run.heartbeat_at),
                "plan_id": run.plan_id,
                "plan_no": plan_snapshot.plan_no if plan_snapshot is not None else 0,
                "plan_revision": plan_snapshot.revision if plan_snapshot is not None else 0,
                "task_id": plan_snapshot.task_id if plan_snapshot is not None else "",
                "task_title": task_snapshot.title if task_snapshot is not None else "",
                "task_snapshot": task_snapshot_exists,
                "outcome": outcome,
                "counts": counts,
                "scope": {
                    "selected": counts.get("selected", 0),
                    "applicable": counts.get("applicable", 0),
                },
                "latest_report_id": run.latest_report_id,
            }
            if run.status != "running":
                new_items[rid] = {"sig": mtime_sig, "row": row}
                idx_dirty = True
            runs.append(row)
        if idx_dirty and not skipped:
            with contextlib.suppress(OSError):
                write_json(idx_path, {"schema_version": 1, "items": new_items})
    runs.sort(key=lambda item: item["started_at"], reverse=True)
    raw_limit = payload.get("limit", 20)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, 200))
    return {"runs": runs[:limit], "skipped": skipped}



def cmd_run_cancel(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    if run.status != "running":
        raise AiTestError("RUN_STATE_CONFLICT", f"运行状态为 {run.status}", "无需取消")
    (run_dir / "cancel.requested").write_text(iso_now(), encoding="utf-8")
    mark_interrupted = _as_bool(payload.get("mark_interrupted"), False)
    if mark_interrupted and (run.pid is None or not pid_alive(run.pid, run.pid_created_at)):
        with ws.project_lock(run.project_id):
            latest = read_model(run_dir / "run.json", Run)
            if latest.status == "running" and (
                latest.pid is None or not pid_alive(latest.pid, latest.pid_created_at)
            ):
                latest.status = "interrupted"
                latest.heartbeat_at = iso_now()
                write_model(run_dir / "run.json", latest)
            run = latest
    return {"run_id": run.run_id, "cancel_requested": True, "status": run.status}



def _manual_path(run_dir: Path, case_id: str) -> Path:
    safe = "".join(ch for ch in case_id if ch.isalnum() or ch in "-_")
    if not safe:
        raise invalid_request(f"用例 ID 不合法：{case_id}")
    return run_dir / "manual" / f"{safe}.json"



def _manual_evidence_ref(
    run_dir: Path,
    value: str,
    *,
    budget: int | None,
    cap: int | None,
    allow_video: bool,
    secrets_list: list[str],
) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        relative = value.replace("\\", "/")
        if ".." in Path(relative).parts:
            raise invalid_path(f"证据路径不能包含父级跳转：{value}")
        target = (run_dir / relative).resolve()
        evidence_root = (run_dir / "evidence").resolve()
        if evidence_root in target.parents and target.is_file():
            return relative
        raise invalid_path(f"相对证据路径不在 evidence/ 内或不存在：{value}")
    source = candidate.expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"证据文件不存在：{source}")
    name = f"manual-{secrets.token_hex(4)}-{source.name}"
    return _save_evidence_stream(
        run_dir,
        name,
        source,
        budget,
        cap,
        allow_video,
        secrets_list,
    )



def cmd_run_manual(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    case_id = _text({"id": _require(payload, "case_id")}, "id")
    raw_steps = payload.get("steps")
    batch_items: list[tuple[str, Literal["passed", "failed", "blocked"], str, list[str]]] = []
    if raw_steps is not None:
        if not isinstance(raw_steps, list) or not raw_steps:
            raise invalid_request("steps 必须是非空数组")
        for idx, item in enumerate(raw_steps):
            if not isinstance(item, dict):
                raise invalid_request(f"steps[{idx}] 必须是对象")
            s_name = _text({"step": item.get("step", "")}, "step").strip()
            s_res = _text({"result": item.get("result", "")}, "result").strip()
            if not s_name:
                raise invalid_request(f"steps[{idx}].step 不能为空")
            if s_res not in {"passed", "failed", "blocked"}:
                raise invalid_request(f"steps[{idx}].result 只能是 passed、failed 或 blocked")
            s_note = _text({"note": item.get("note", "")}, "note")
            s_ev = item.get("evidence", [])
            if not isinstance(s_ev, list) or any(
                not isinstance(ev, str) or not ev.strip() for ev in s_ev
            ):
                raise invalid_request(f"steps[{idx}].evidence 必须只包含文件路径")
            batch_items.append(
                (
                    s_name,
                    cast(Literal["passed", "failed", "blocked"], s_res),
                    s_note,
                    list(s_ev),
                )
            )
    else:
        step = _text({"step": _require(payload, "step")}, "step")
        step_result = _text({"result": _require(payload, "result")}, "result")
        if step_result not in {"passed", "failed", "blocked"}:
            raise invalid_request("result 只能是 passed、failed 或 blocked")
        note = _text(payload, "note")
        raw_evidence = payload.get("evidence", [])
        if not isinstance(raw_evidence, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw_evidence
        ):
            raise invalid_request("evidence 必须只包含文件路径")
        batch_items.append(
            (
                step,
                cast(Literal["passed", "failed", "blocked"], step_result),
                note,
                list(raw_evidence),
            )
        )

    project = _project(ws, payload)
    with ws.project_lock(project.project_id):
        run_dir, run = _run(ws, payload)
        _ensure_settled(run)
        run_case = next((item for item in run.cases if item.case_id == case_id), None)
        if run_case is None:
            raise invalid_request(f"运行中没有该用例：{case_id}")
        plan = read_model(run_dir / "plan.json", Plan)
        plan_case = next((item for item in plan.cases if item.case_id == case_id), None)
        if plan_case is None:
            raise invalid_request(f"运行计划中没有该用例：{case_id}")
        if not is_manual_case(plan_case):
            raise AiTestError(
                "RUN_STATE_CONFLICT",
                "该用例由自动方式验证，不能用人工记录改判",
                "自动用例失败请修复后重跑；只有 L3 人工用例可以记录结果",
            )
        required_steps = list(plan_case.steps)
        config = load_project_config(ws, run.project_id)
        budget, cap, allow_video = _evidence_settings(config)
        secrets_list = known_secret_values([config])
        path = _manual_path(run_dir, case_id)
        if path.is_file():
            log = read_model(path, ManualLog)
        else:
            log = ManualLog(run_id=run.run_id, case_id=case_id)

        all_refs: list[str] = []
        last_recorded_at = iso_now()
        step_names: list[str] = []
        for s_name, s_res, s_note, s_ev in batch_items:
            refs = [
                _manual_evidence_ref(
                    run_dir,
                    item,
                    budget=budget,
                    cap=cap,
                    allow_video=allow_video,
                    secrets_list=secrets_list,
                )
                for item in s_ev
            ]
            last_recorded_at = iso_now()
            entry = ManualStep(
                step=s_name,
                result=s_res,
                note=redact(s_note, secrets_list),
                evidence_refs=refs,
                recorded_at=last_recorded_at,
            )
            position = next(
                (index for index, item in enumerate(log.steps) if item.step == s_name),
                None,
            )
            if position is None:
                log.steps.append(entry)
            else:
                log.steps[position] = entry
            all_refs.extend(refs)
            step_names.append(s_name)

        write_model(path, log)
        manual_result = aggregate_manual(log.steps, required_steps)
        run_case.result = manual_result
        summary_label = (
            step_names[0]
            if len(step_names) == 1
            else f"批量 {len(step_names)} 步"
        )
        run_case.attempts.append(
            Attempt(
                attempt=len(run_case.attempts) + 1,
                result=manual_result,
                started_at=last_recorded_at,
                finished_at=iso_now(),
                evidence_refs=list(dict.fromkeys(all_refs)),
                message=f"人工记录：{summary_label}",
            )
        )
        for ref in all_refs:
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



def cmd_evidence_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    with ws.project_lock(project.project_id):
        run_dir, run = _run(ws, payload)
        _ensure_settled(run)
        case_id = _text(payload, "case_id")
        run_case = None
        if case_id:
            run_case = next((item for item in run.cases if item.case_id == case_id), None)
            if run_case is None:
                raise invalid_request(f"运行中没有该用例：{case_id}")
        source = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
        if not source.is_file():
            raise invalid_path(f"证据文件不存在：{source}")
        name = f"manual-{secrets.token_hex(4)}-{source.name}"
        config = load_project_config(ws, run.project_id)
        budget, cap, allow_video = _evidence_settings(config)
        secrets_list = known_secret_values([config])
        case_result = run_case.result if run_case is not None else "passed"
        ref = _save_evidence_stream(
            run_dir,
            name,
            source,
            budget,
            cap,
            allow_video,
            secrets_list,
            result=case_result,
        )
        run.evidence_refs.append(ref)
        note = redact(_text(payload, "note"), secrets_list)
        if note:
            run.evidence_notes[ref] = note
        if run_case is not None and ref not in run_case.evidence_refs:
            run_case.evidence_refs.append(ref)
        run.media_bytes = evidence_bytes(run_dir)
        write_model(run_dir / "run.json", run)
    return {"evidence_ref": ref}



_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})
_MAX_IMAGE_PREVIEW_BYTES = 2 * 1024 * 1024


def _build_evidence_preview(
    ws: Workspace,
    project_id: str,
    run_dir: Path,
    run: Run,
    preview_ref: str,
    limit: int,
) -> dict[str, Any]:
    """安全内联预览一条证据（脱敏文本或 <=2MiB 图片 Data URL）。"""
    path = evidence_path(run_dir, preview_ref)
    if (
        preview_ref not in run.evidence_refs
        or path is None
        or not path.is_file()
    ):
        return {
            "ref": preview_ref,
            "kind": "missing",
            "text": "",
            "data_url": "",
            "truncated": False,
        }
    suffix = path.suffix.lower()
    guessed, _encoding = mimetypes.guess_type(preview_ref)
    media_type = guessed or "application/octet-stream"
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if suffix in _IMAGE_EXTS:
        if file_size <= _MAX_IMAGE_PREVIEW_BYTES:
            try:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return {
                    "ref": preview_ref,
                    "kind": "image",
                    "text": "",
                    "data_url": f"data:{media_type};base64,{encoded}",
                    "truncated": False,
                }
            except OSError:
                pass
        return {
            "ref": preview_ref,
            "kind": "binary",
            "text": "",
            "data_url": "",
            "truncated": False,
        }
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return {
            "ref": preview_ref,
            "kind": "missing",
            "text": "",
            "data_url": "",
            "truncated": False,
        }
    if b"\x00" in raw:
        return {
            "ref": preview_ref,
            "kind": "binary",
            "text": "",
            "data_url": "",
            "truncated": False,
        }
    truncated = len(raw) > limit or file_size > limit
    secrets_list = known_secret_values([load_project_config(ws, project_id)])
    text = redact(raw[:limit].decode("utf-8", errors="replace"), secrets_list)
    return {
        "ref": preview_ref,
        "kind": "text",
        "text": text,
        "data_url": "",
        "truncated": truncated,
    }


def cmd_evidence_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    include_hash = _as_bool(payload.get("include_hash"), True)
    preview_ref = _text(payload, "preview_ref")
    raw_bytes = payload.get("preview_bytes", 8192)
    try:
        preview_limit = max(256, min(int(raw_bytes), 65536))
    except (TypeError, ValueError):
        preview_limit = 8192
    items = []
    for ref in run.evidence_refs:
        path = evidence_path(run_dir, ref)
        exists = path is not None and path.is_file()
        digest = ""
        if include_hash and exists and path is not None:
            try:
                digest = sha256_file(path)
            except OSError:
                digest = ""
        guessed, _encoding = mimetypes.guess_type(ref)
        items.append(
            {
                "ref": ref,
                "name": Path(ref).name,
                "path": str(path) if exists else "",
                "bytes": path.stat().st_size if exists and path is not None else 0,
                "exists": exists,
                "missing_reason": "" if exists else "证据文件不在运行目录中",
                "sha256": digest,
                "media_type": guessed or "application/octet-stream",
                "truncated": (run_dir / "evidence" / f"{Path(ref).name}.truncated").is_file(),
                "note": run.evidence_notes.get(ref, ""),
            }
        )
    response: dict[str, Any] = {"evidence": items, "run_id": run.run_id}
    if preview_ref:
        response["preview"] = _build_evidence_preview(
            ws, run.project_id, run_dir, run, preview_ref, preview_limit
        )
    return response



def cmd_analysis_run(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_dir, current = _run(ws, payload)
    _ensure_settled(current)
    return analyze_run(ws, project.project_id, run_dir)



