"""项目测试地图、AI 任务推荐与任务全生命周期命令处理器。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

from aitest.command_handlers._common import (
    Payload,
    _heartbeat_stale,
    _is_generic_acceptance_text,
    _project,
    _project_map_path,
    _project_secrets,
    _require,
    _task,
    _task_intent,
    _task_path,
    _text,
    cmd_facade_attr,
    infer_task_intent,
)
from aitest.config import known_secret_values
from aitest.core.code_graph import load_or_build_code_graph
from aitest.core.git_engine import inspect_local_git
from aitest.core.project_context import build_project_context
from aitest.errors import AiTestError, invalid_request
from aitest.model_config import build_client, load_project_config, model_settings
from aitest.models import (
    AcceptanceItem,
    Issue,
    Plan,
    Project,
    Run,
    Task,
    new_id,
)
from aitest.security import is_ignored_path, load_ignore_patterns, redact, redact_tree
from aitest.storage.files import (
    check_schema_version,
    read_json,
    read_model,
    write_json,
    write_model,
)
from aitest.storage.workspace import Workspace, iso_now


def _signal_source_files(root: Path) -> list[tuple[str, str]]:
    """收集少量非测试核心源码（供无 README 项目生成任务推荐与测试地图）。"""
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "tests",
        "test",
        "e2e",
        "dist",
        "build",
        "data",
        "coverage",
    }
    exts = {".py", ".js", ".ts", ".tsx", ".vue", ".html", ".go", ".java"}
    priority = ("server", "app", "main", "routes", "router", "index")
    candidates: list[tuple[int, str, str]] = []
    scanned = 0
    ignore_patterns = load_ignore_patterns(root)
    for base, dirs, files in os.walk(root):
        base_path = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in skip_dirs
            and not d.startswith(".")
            and not is_ignored_path(root, base_path / d, ignore_patterns)
        )
        for name in sorted(files):
            if Path(name).suffix.lower() not in exts:
                continue
            if name.startswith("test_") or name.endswith(
                ("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js")
            ):
                continue
            path = base_path / name
            if is_ignored_path(root, path, ignore_patterns):
                continue
            try:
                if path.stat().st_size > 128 * 1024:
                    continue
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not body.strip():
                continue
            rel = path.relative_to(root).as_posix()
            stem = path.stem.lower()
            rank = 0 if any(hint in stem or hint in rel.lower() for hint in priority) else 1
            candidates.append((rank, rel, body))
            scanned += 1
            if scanned >= 100:
                break
        if scanned >= 100:
            break
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [(rel, body) for _rank, rel, body in candidates]


def _task_signal_context(ws: Workspace, project: Project) -> str:
    """Collect the five target signals: changes, issues, modules, docs, history."""
    root = Path(project.root)
    parts = [f"项目：{project.name}\n目标：{project.goal or '未填写'}"]
    modules = [module.name for module in project.modules if module.name.strip()]
    deps = [
        f"{module.name}->{dependency.to}"
        for module in project.modules
        for dependency in module.deps
    ]
    parts.append("模块：" + ("、".join(modules) if modules else "未登记"))
    if deps:
        parts.append("模块依赖：" + "、".join(deps))
    map_path = _project_map_path(ws, project.project_id)
    if map_path.is_file():
        try:
            map_record = read_json(map_path)
        except AiTestError:
            map_record = None
        if isinstance(map_record, dict):
            raw_ignored = map_record.get("ignored_features")
            ignored_list = (
                [
                    str(item).strip()
                    for item in raw_ignored
                    if isinstance(item, str) and item.strip()
                ]
                if isinstance(raw_ignored, list)
                else []
            )
            if ignored_list:
                parts.append(
                    "已忽略的业务功能（禁止推荐或生成对应验收项）：" + "、".join(ignored_list)
                )
    docs = ""
    for name in ("README.md", "README.rst", "pyproject.toml", "package.json", "requirements.txt"):
        path = root / name
        if not path.is_file():
            continue
        # README 常承载完整验收契约，不能按清单文件的 1200 字截断；
        # 否则后半部分的过期、越权等显式规则不会进入地图。
        limit = 12000 if name.lower().startswith("readme") else 1200
        try:
            docs += f"\n--- {name} ---\n" + path.read_text(
                encoding="utf-8", errors="replace"
            )[:limit]
        except OSError:
            continue
    if docs:
        parts.append("项目文档与依赖声明：" + docs)
    tasks_dir = ws.project_dir(project.project_id) / "tasks"
    history: list[str] = []
    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.glob("*.json"))[-5:]:
            try:
                task = read_model(path, Task)
            except AiTestError:
                continue
            history.append(f"{task.title}（{task.revision}）")
    if history:
        parts.append("历史任务：" + "、".join(history))
    issues_dir = ws.project_dir(project.project_id) / "issues"
    open_issues: list[str] = []
    if issues_dir.is_dir():
        for path in sorted(issues_dir.glob("*.json")):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            if issue.status != "closed":
                open_issues.append(f"{issue.title}（{issue.status}/{issue.severity}）")
    if open_issues:
        parts.append("未关闭问题：" + "；".join(open_issues[:10]))
    since = ""
    runs_dir = ws.project_dir(project.project_id) / "runs"
    if runs_dir.is_dir():
        run_files = sorted(
            runs_dir.glob("*/run.json"), key=lambda item: item.stat().st_mtime
        )
        if run_files:
            try:
                latest = read_model(run_files[-1], Run)
                since = latest.started_at
            except (AiTestError, OSError):
                since = ""
    git_args = ["git", "-C", str(root), "log", "--oneline", "-20"]
    if since:
        git_args = ["git", "-C", str(root), "log", f"--since={since}", "--oneline", "-20"]
    try:
        completed = subprocess.run(
            git_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            parts.append("最近提交：\n" + completed.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    config = load_project_config(ws, project.project_id)
    source_files = _signal_source_files(root)
    if source_files:
        parts.append(
            "项目关键文件：" + "、".join(rel for rel, _body in source_files[:20])
        )
        if not docs:
            excerpts = [
                f"--- {rel} ---\n{body[:900]}" for rel, body in source_files[:3]
            ]
            parts.append("核心源码摘要：\n" + "\n".join(excerpts))
        derived = build_project_context(root, config)
        services = derived.get("services")
        if isinstance(services, dict) and services:
            svc_lines = [
                f"{name}: {cfg.get('health') or cfg.get('start') or ''}"
                for name, cfg in list(services.items())[:8]
                if isinstance(cfg, dict)
            ]
            if svc_lines:
                parts.append("识别服务：" + "；".join(svc_lines))
        api_routes = derived.get("api_routes")
        if isinstance(api_routes, list) and api_routes:
            parts.append(
                "后端接口："
                + "；".join(
                    f"{r.get('method')} {r.get('path')}"
                    for r in api_routes[:8]
                    if isinstance(r, dict)
                )
            )
        fe_calls = derived.get("frontend_api_calls")
        if isinstance(fe_calls, list) and fe_calls:
            parts.append(
                "前端调用契约："
                + "；".join(
                    f"{c.get('method')} {c.get('path')} body={c.get('body_keys', [])}"
                    for c in fe_calls[:8]
                    if isinstance(c, dict)
                )
            )
        fe_assets = derived.get("frontend_assets")
        if isinstance(fe_assets, list) and fe_assets:
            parts.append(
                "前端静态资源："
                + "；".join(
                    f"{a.get('href')}(exists={a.get('exists_in_repo')})"
                    for a in fe_assets[:8]
                    if isinstance(a, dict)
                )
            )
    secret_list = known_secret_values([config])
    text = "\n\n".join(parts)
    for secret in secret_list:
        text = text.replace(secret, "***")
    return text


def _git_head(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _map_config_hash(config: dict[str, Any]) -> str:
    safe = redact_tree(config, known_secret_values([config]))
    raw = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_stamp(root: Path) -> str:
    """Cheap non-git source fingerprint: newest mtime under the project."""
    newest = 0
    seen = 0
    skip = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache"}
    try:
        for base, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in skip)
            for name in sorted(files):
                try:
                    newest = max(newest, int((Path(base) / name).stat().st_mtime_ns))
                except OSError:
                    continue
                seen += 1
                if seen >= 5000:
                    return str(newest)
    except OSError:
        return ""
    return str(newest)


def _map_stale(ws: Workspace, project: Project, record: dict[str, Any]) -> bool:
    config = load_project_config(ws, project.project_id)
    git_head = _git_head(Path(project.root))
    source_changed = (
        record.get("source_commit") != git_head
        if git_head
        else record.get("source_stamp") != _source_stamp(Path(project.root))
    )
    return (
        record.get("project_revision") != project.revision
        or source_changed
        or record.get("config_hash") != _map_config_hash(config)
    )


def _normalize_ignored_features(raw: object) -> list[str]:
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise invalid_request("ignored_features 必须是字符串数组")
    return list(dict.fromkeys(item.strip() for item in raw if item.strip()))


def _project_git_and_graph(
    ws: Workspace,
    project: Project,
    config: dict[str, Any],
    *,
    since_commit: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(project.root)
    local_git = inspect_local_git(root, since_commit=since_commit)
    graph = load_or_build_code_graph(
        ws, project.project_id, root, config, local_git=local_git
    )
    return local_git, graph


def cmd_project_map(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    path = _project_map_path(ws, project.project_id)
    action = _text(payload, "action", "show").strip() or "show"
    config = load_project_config(ws, project.project_id)
    if action == "set_ignored":
        ignored = _normalize_ignored_features(payload.get("ignored_features"))
        with ws.project_lock(project.project_id):
            record = read_json(path) if path.is_file() else None
            if isinstance(record, dict):
                check_schema_version(record, path)
            else:
                record = {
                    "schema_version": 1,
                    "project_id": project.project_id,
                    "project_revision": project.revision,
                    "features": [],
                    "unknowns": [],
                }
            record["ignored_features"] = ignored
            write_json(path, record)
        stale = bool(record.get("generated_at")) and _map_stale(ws, project, record)
        local_git, graph = _project_git_and_graph(
            ws,
            project,
            config,
            since_commit=str(record.get("source_commit") or ""),
        )
        return {
            "map": record,
            "stale": stale,
            "graph": graph,
            "local_git": local_git,
        }
    if action not in {"show", "get"}:
        raise invalid_request(f"未知 project.map action：{action}")
    record = read_json(path) if path.is_file() else None
    if isinstance(record, dict):
        check_schema_version(record, path)
    since_commit = _text(payload, "since_commit").strip() or (
        str(record.get("source_commit") or "") if isinstance(record, dict) else ""
    )
    local_git, graph = _project_git_and_graph(
        ws, project, config, since_commit=since_commit
    )
    if not isinstance(record, dict):
        return {"map": None, "stale": False, "graph": graph, "local_git": local_git}
    stale = bool(record.get("generated_at")) and _map_stale(ws, project, record)
    return {"map": record, "stale": stale, "graph": graph, "local_git": local_git}


def _map_model_name(ws: Workspace, project_id: str) -> str:
    return str(model_settings(ws, project_id)["name"])


def cmd_project_map_refresh(
    ws: Workspace, payload: Payload, confirm: bool
) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    client = cmd_facade_attr("build_client", build_client)(ws, project.project_id)
    if client is None:
        raise AiTestError(
            "MODEL_UNAVAILABLE",
            "未配置模型，无法生成项目测试地图",
            "配置模型密钥后重试",
        )
    config = load_project_config(ws, project.project_id)
    secrets = known_secret_values([config])
    local_git, graph = _project_git_and_graph(ws, project, config)
    graph_digest = {
        "stats": graph.get("stats", {}),
        "module_chunks": list(graph.get("module_chunks") or [])[:30],
        "conflicts": list(graph.get("conflicts") or [])[:20],
        "impact": graph.get("impact", {}),
        "local_git": {
            "branch": local_git.get("branch", ""),
            "head": local_git.get("head", ""),
            "dirty": local_git.get("dirty", False),
            "diff_stat": local_git.get("diff_stat", {}),
            "changed_files": list(local_git.get("changed_files") or [])[:30],
        },
    }
    context = _task_signal_context(ws, project)
    context += "\n\n结构化项目事实：\n" + json.dumps(
        build_project_context(Path(project.root), config),
        ensure_ascii=False,
        sort_keys=True,
    )
    context += "\n\n全栈代码图分块与本地 Git 变更：\n" + redact(
        json.dumps(graph_digest, ensure_ascii=False, sort_keys=True),
        secrets,
    )
    map_path = _project_map_path(ws, project.project_id)
    existing_ignored: list[str] = []
    if map_path.is_file():
        try:
            old_record = read_json(map_path)
            if isinstance(old_record, dict) and isinstance(
                old_record.get("ignored_features"), list
            ):
                existing_ignored = [
                    str(item).strip()
                    for item in old_record["ignored_features"]
                    if isinstance(item, str) and item.strip()
                ]
        except AiTestError:
            existing_ignored = []
    result = redact_tree(client.project_map(context), secrets)
    record = {
        "schema_version": 1,
        "project_id": project.project_id,
        "project_revision": project.revision,
        "source_commit": _git_head(Path(project.root)),
        "source_stamp": _source_stamp(Path(project.root)),
        "config_hash": _map_config_hash(config),
        "generated_at": iso_now(),
        "model": _map_model_name(ws, project.project_id),
        "ignored_features": existing_ignored,
        **result,
    }
    write_json(map_path, record)
    return {
        "map": record,
        "stale": False,
        "graph": graph,
        "local_git": local_git,
    }


def cmd_task_suggest(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    fallback = "验证项目能否启动并跑通主流程"
    client = cmd_facade_attr("build_client", build_client)(ws, project.project_id)
    if client is None:
        return {
            "candidates": [],
            "fallback": fallback,
            "intent": "smoke",
            "fallback_intent": "smoke",
            "ai_generated": False,
        }
    context = _task_signal_context(ws, project)
    try:
        candidates = client.suggest_targets(context)
    except AiTestError:
        return {
            "candidates": [],
            "fallback": fallback,
            "intent": "smoke",
            "fallback_intent": "smoke",
            "ai_generated": False,
        }
    enriched = [
        {
            **candidate,
            "intent": infer_task_intent(candidate["title"]),
            "source": candidate.get("source") or "ai",
        }
        for candidate in candidates
    ]
    return {
        "candidates": enriched,
        "fallback": fallback,
        "intent": "smoke",
        "fallback_intent": "smoke",
        "ai_generated": bool(enriched),
    }


def _parse_acceptance_items(
    raw_items: list[Any],
    module_ids: set[str] | None = None,
    *,
    ai_extracted: bool = False,
) -> list[AcceptanceItem]:
    items: list[AcceptanceItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise invalid_request("验收项必须是对象")
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        observable = bool(raw.get("observable", True))
        generic = ai_extracted and _is_generic_acceptance_text(text)
        if generic:
            observable = False
        status = raw.get("status")
        if generic:
            normalized_status: Literal["active", "pending"] = "pending"
        elif status == "active":
            normalized_status = "active"
        elif status == "pending":
            normalized_status = "pending"
        else:
            normalized_status = "active" if observable else "pending"
        item_id = raw.get("item_id")
        raw_module = raw.get("module_id")
        if raw_module in (None, ""):
            module_id: str | None = None
        elif not isinstance(raw_module, str):
            raise invalid_request("module_id 必须是字符串")
        else:
            if module_ids is not None and raw_module not in module_ids:
                raise invalid_request(
                    f"验收项引用的模块未登记：{raw_module}",
                    "先登记模块，或移除该验收项的 module_id",
                )
            module_id = raw_module
        items.append(
            AcceptanceItem(
                item_id=item_id
                if isinstance(item_id, str) and item_id.strip()
                else new_id("ai"),
                text=text,
                observable=observable,
                status=normalized_status,
                source=str(raw.get("source", "")),
                module_id=module_id,
            )
        )
    return items


def cmd_task_add(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    title = _text({"title": _require(payload, "title")}, "title")
    goal = _text(payload, "goal")
    intent = _task_intent(payload, goal)
    raw_items = payload.get("acceptance_items", [])
    if not isinstance(raw_items, list):
        raise invalid_request("acceptance_items 必须是数组")
    ai_extracted = False
    extraction_error = ""
    if not raw_items:
        requirement = _text(payload, "requirement")
        client = cmd_facade_attr("build_client", build_client)(ws, project.project_id)
        if requirement and client is not None:
            try:
                raw_items = client.extract_acceptance_items(requirement)
                ai_extracted = True
            except AiTestError as exc:
                extraction_error = exc.info.message
                raw_items = []
    # No generic fallback item is synthesised: an empty observable set stays empty.
    items = _parse_acceptance_items(
        raw_items,
        {module.module_id for module in project.modules},
        ai_extracted=ai_extracted,
    )
    secrets = _project_secrets(ws, project.project_id)
    for item in items:
        item.text = redact(item.text, secrets)
        item.source = redact(item.source, secrets)
    # ``manual`` stays readable on legacy tasks (models.Task) but new tasks
    # must declare how their scope was obtained.
    origin_value = _text(payload, "origin", "goal")
    if origin_value not in {"goal", "range"}:
        raise invalid_request("origin 只能是 goal 或 range（manual 仅为旧记录兼容）")
    origin = cast(Literal["manual", "goal", "range"], origin_value)
    raw_map_revision = payload.get("source_map_revision", 0)
    try:
        map_revision = int(raw_map_revision)
    except (TypeError, ValueError):
        raise invalid_request("source_map_revision 必须是整数") from None
    task = Task(
        task_id=new_id("task"),
        project_id=project.project_id,
        title=redact(title, secrets),
        goal=redact(goal, secrets),
        intent=intent,
        origin=origin,
        source_map_revision=max(0, map_revision),
        scope=redact(_text(payload, "scope"), secrets),
        inputs=redact(_text(payload, "inputs"), secrets),
        outputs=redact(_text(payload, "outputs"), secrets),
        preconditions=[redact(str(x), secrets) for x in payload.get("preconditions", [])],
        executor=redact(_text(payload, "executor"), secrets),
        reviewer=redact(_text(payload, "reviewer"), secrets),
        acceptance_items=items,
    )
    write_model(_task_path(ws, project.project_id, task.task_id), task)
    return {
        "task_id": task.task_id,
        "revision": task.revision,
        "intent": task.intent,
        "origin": task.origin,
        "acceptance_extraction": {
            "status": "model_error" if extraction_error else ("model" if ai_extracted else "none"),
            "message": extraction_error,
        },
        "acceptance_items": [
            {
                "item_id": i.item_id,
                "status": i.status,
                "text": i.text,
                "observable": i.observable,
                "module_id": i.module_id,
            }
            for i in task.acceptance_items
        ],
    }


def cmd_task_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    directory = ws.project_dir(project.project_id) / "tasks"
    tasks: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                task = read_model(path, Task)
            except AiTestError as exc:
                skipped.append(
                    {
                        "task_id": path.stem,
                        "code": exc.info.code,
                        "message": exc.info.message,
                    }
                )
                continue
            tasks.append({"task_id": task.task_id, "title": task.title, "revision": task.revision})
    return {"tasks": tasks, "skipped": skipped}


def cmd_task_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    plans_dir = ws.project_dir(task.project_id) / "plans"
    plans: list[str] = []
    if plans_dir.is_dir():
        for path in sorted(plans_dir.glob("*.json")):
            try:
                plan = read_model(path, Plan)
            except AiTestError:
                continue
            if plan.task_id == task.task_id:
                plans.append(plan.plan_id)
    return {"task": task.model_dump(mode="json"), "plans": plans}


def cmd_task_delete(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Permanently delete a task and every record derived from it."""
    del confirm
    project = _project(ws, payload)
    task = _task(ws, payload)
    root = ws.project_dir(project.project_id)

    plan_ids: set[str] = set()
    plans_dir = root / "plans"
    if plans_dir.is_dir():
        for path in plans_dir.glob("*.json"):
            try:
                plan = read_model(path, Plan)
            except AiTestError:
                continue
            if plan.task_id == task.task_id:
                plan_ids.add(plan.plan_id)

    run_ids: set[str] = set()
    run_dirs: list[Path] = []
    runs_dir = root / "runs"
    if runs_dir.is_dir():
        for run_dir in runs_dir.iterdir():
            run_path = run_dir / "run.json"
            if not run_path.is_file():
                continue
            try:
                run = read_model(run_path, Run)
            except AiTestError:
                continue
            if run.plan_id in plan_ids:
                if run.status == "running" and not _heartbeat_stale(run):
                    raise AiTestError(
                        "RUN_ACTIVE",
                        f"任务有正在进行的运行：{run.run_id}",
                        "先停止运行或等待其结束后再删除任务",
                    )
                run_ids.add(run.run_id)
                run_dirs.append(run_dir)

    removed_reports = 0
    reports_dir = root / "reports"
    if reports_dir.is_dir():
        for path in reports_dir.glob("*.json"):
            try:
                data = read_json(path)
            except (AiTestError, OSError, ValueError):
                continue
            if data.get("run_id") in run_ids:
                path.unlink(missing_ok=True)
                removed_reports += 1

    removed_issues = 0
    issues_dir = root / "issues"
    if issues_dir.is_dir():
        for path in issues_dir.glob("*.json"):
            try:
                issue = read_model(path, Issue)
            except AiTestError:
                continue
            linked = [
                occurrence for occurrence in issue.occurrences if occurrence.run_id in run_ids
            ]
            if not linked:
                continue
            if len(linked) == len(issue.occurrences):
                path.unlink(missing_ok=True)
                removed_issues += 1
            else:
                issue.occurrences = [
                    occurrence
                    for occurrence in issue.occurrences
                    if occurrence.run_id not in run_ids
                ]
                write_model(path, issue)

    for plan_id in plan_ids:
        (plans_dir / f"{plan_id}.json").unlink(missing_ok=True)
        shutil.rmtree(plans_dir / "revisions" / plan_id, ignore_errors=True)
    for run_dir in run_dirs:
        shutil.rmtree(run_dir, ignore_errors=True)
    confirmations_dir = root / "confirmations"
    if confirmations_dir.is_dir():
        for path in confirmations_dir.glob("*.json"):
            try:
                conf_data = read_json(path)
            except (AiTestError, OSError, ValueError):
                continue
            if conf_data.get("plan_id") in plan_ids or conf_data.get("run_id") in run_ids:
                path.unlink(missing_ok=True)
    (root / "deliveries" / f"{task.task_id}.json").unlink(missing_ok=True)
    shutil.rmtree(root / "tasks" / task.task_id, ignore_errors=True)
    shutil.rmtree(root / "tasks" / "revisions" / task.task_id, ignore_errors=True)
    _task_path(ws, project.project_id, task.task_id).unlink(missing_ok=True)

    return {
        "task_id": task.task_id,
        "deleted": True,
        "plans": len(plan_ids),
        "runs": len(run_ids),
        "reports": removed_reports,
        "issues": removed_issues,
    }


def cmd_task_update(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    task = _task(ws, payload)
    expected = payload.get("expected_revision")
    if expected is not None and expected != task.revision:
        raise AiTestError("RUN_STATE_CONFLICT", "任务修订号不匹配", "刷新后重试")
    patch = payload.get("patch")
    if not isinstance(patch, dict):
        raise invalid_request("patch 必须是对象")
    allowed = {
        "title",
        "goal",
        "scope",
        "inputs",
        "outputs",
        "executor",
        "reviewer",
        "intent",
        "preconditions",
        "acceptance_items",
    }
    unknown = sorted(set(patch) - allowed)
    if unknown:
        raise invalid_request(
            "task.update 不支持这些字段：" + "、".join(unknown),
            "只允许资料、意图、前置条件与验收项",
        )
    data = task.model_dump(mode="json")
    for key in ("title", "goal", "scope", "inputs", "outputs", "executor", "reviewer"):
        if key not in patch:
            continue
        value = patch[key]
        if not isinstance(value, str):
            raise invalid_request(f"字段必须是字符串：{key}")
        if key == "title" and not value.strip():
            raise invalid_request("标题不能为空")
        data[key] = value
    if "intent" in patch:
        value = patch["intent"]
        if value not in {"smoke", "business"}:
            raise invalid_request("intent 只能是 smoke 或 business")
        data["intent"] = value
    if "preconditions" in patch:
        raw_preconditions = patch["preconditions"]
        if not isinstance(raw_preconditions, list) or any(
            not isinstance(item, str) for item in raw_preconditions
        ):
            raise invalid_request("preconditions 必须是字符串数组")
        data["preconditions"] = [item.strip() for item in raw_preconditions if item.strip()]
    if "acceptance_items" in patch:
        raw_items = patch["acceptance_items"]
        if not isinstance(raw_items, list):
            raise invalid_request("acceptance_items 必须是数组")
        data["acceptance_items"] = [
            item.model_dump(mode="json")
            for item in _parse_acceptance_items(
                raw_items, {module.module_id for module in project.modules}
            )
        ]
    updated = Task.model_validate(data)
    revision_path = (
        ws.project_dir(project.project_id) / "tasks" / "revisions" / task.task_id
    ) / f"{task.revision}.json"
    write_model(revision_path, task)
    secrets = _project_secrets(ws, project.project_id)
    for key in ("title", "goal", "scope", "inputs", "outputs", "executor", "reviewer"):
        setattr(updated, key, redact(getattr(updated, key), secrets))
    updated.preconditions = [redact(item, secrets) for item in updated.preconditions]
    for item in updated.acceptance_items:
        item.text = redact(item.text, secrets)
        item.source = redact(item.source, secrets)
    updated.revision += 1
    write_model(_task_path(ws, project.project_id, updated.task_id), updated)
    return {
        "task_id": updated.task_id,
        "revision": updated.revision,
        "previous_revision": task.revision,
        "intent": updated.intent,
        "acceptance_items": [
            {
                "item_id": item.item_id,
                "status": item.status,
                "text": item.text,
                "module_id": item.module_id,
            }
            for item in updated.acceptance_items
        ],
    }
