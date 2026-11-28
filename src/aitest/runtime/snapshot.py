"""Runtime 命令模块：snapshot。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aitest.commands import (
    Payload,
    _project,
    cmd_project_config,
    cmd_project_map,
    cmd_project_show,
)
from aitest.config import known_secret_values
from aitest.core.code_graph import apply_git_impact, load_or_build_code_graph
from aitest.core.git_engine import inspect_local_git
from aitest.core.project_context import (
    build_capabilities_snapshot,
    build_project_health_summary,
    cached_project_context,
)
from aitest.core.scope import task_contract
from aitest.errors import AiTestError
from aitest.models import (
    Delivery,
    Issue,
    Plan,
    Report,
    Task,
)
from aitest.runtime._common import _plan_signature
from aitest.runtime.run_commands import cmd_evidence_list, cmd_run_list, cmd_run_status
from aitest.storage.files import check_schema_version, read_json, read_model
from aitest.storage.workspace import Workspace


def _load_plans_by_task(ws: Workspace, project_id: str) -> dict[str, list[Plan]]:
    directory = ws.project_dir(project_id) / "plans"
    grouped: dict[str, list[Plan]] = {}
    if not directory.is_dir():
        return grouped
    for path in sorted(directory.glob("*.json")):
        try:
            plan = read_model(path, Plan)
        except AiTestError:
            continue
        grouped.setdefault(plan.task_id, []).append(plan)
    return grouped


def _load_reports_by_id(ws: Workspace, project_id: str) -> dict[str, dict[str, Any]]:
    directory = ws.project_dir(project_id) / "reports"
    found: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return found
    for path in directory.glob("*.json"):
        try:
            report = read_model(path, Report)
        except AiTestError:
            continue
        found[report.report_id] = report.model_dump(mode="json")
    return found


def _load_issues(ws: Workspace, project_id: str) -> list[dict[str, Any]]:
    directory = ws.project_dir(project_id) / "issues"
    if not directory.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            found.append(read_model(path, Issue).model_dump(mode="json"))
        except AiTestError:
            continue
    return found


def _load_latest_github(ws: Workspace, project_id: str) -> dict[str, Any] | None:
    directory = ws.project_dir(project_id) / "github"
    if not directory.is_dir():
        return None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        try:
            data = read_json(path)
            check_schema_version(data, path)
            mtime = path.stat().st_mtime_ns
        except (AiTestError, OSError):
            continue
        if isinstance(data, dict):
            candidates.append((mtime, data))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _latest_finished_commit(
    runs: list[dict[str, Any]],
    reports: list[dict[str, Any]],
) -> str:
    ordered_reports = sorted(
        (r for r in reports if isinstance(r, dict)),
        key=lambda r: str(r.get("generated_at") or ""),
        reverse=True,
    )
    for rep in ordered_reports:
        commit = str(rep.get("source_commit") or "").strip()
        if commit:
            return commit
    ordered_runs = sorted(
        (
            entry.get("run", {})
            for entry in runs
            if isinstance(entry, dict) and isinstance(entry.get("run"), dict)
        ),
        key=lambda r: str(r.get("started_at") or ""),
        reverse=True,
    )
    for run in ordered_runs:
        if str(run.get("status") or "") == "running":
            continue
        env = run.get("environment")
        if isinstance(env, dict):
            commit = str(env.get("git_head") or env.get("source_commit") or "").strip()
            if commit:
                return commit
    return ""


def _resolve_snapshot_git_and_graph(
    ws: Workspace,
    project_id: str,
    project_dict: dict[str, Any],
    config: dict[str, Any],
    map_data: dict[str, Any],
    since_commit: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    local_git = map_data.get("local_git")
    code_graph = map_data.get("graph")
    project_root = Path(str(project_dict.get("root") or ""))
    existing_since = (
        str(local_git.get("since_commit") or "") if isinstance(local_git, dict) else ""
    )
    if (
        isinstance(local_git, dict)
        and isinstance(code_graph, dict)
        and (not since_commit or since_commit == existing_since)
    ):
        return local_git, code_graph
    fresh_git = inspect_local_git(project_root, since_commit=since_commit)
    if isinstance(code_graph, dict) and code_graph.get("nodes") is not None:
        fresh_graph = dict(code_graph)
        apply_git_impact(fresh_graph, fresh_git)
        return fresh_git, fresh_graph
    fresh_graph = load_or_build_code_graph(
        ws, project_id, project_root, config, local_git=fresh_git
    )
    return fresh_git, fresh_graph


def _compact_code_graph(
    graph: dict[str, Any],
    *,
    max_nodes: int = 300,
    max_edges: int = 600,
) -> dict[str, Any]:
    """裁剪大仓库代码图谱中的海量节点与边，保留完整摘要统计与高优先节点以防止快照体积膨胀。"""
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list):
        nodes = []
    if not isinstance(edges, list):
        edges = []
    if len(nodes) <= max_nodes and len(edges) <= max_edges:
        return graph

    conflict_nodes: set[str] = set()
    for conflict in graph.get("conflicts") or []:
        if isinstance(conflict, dict):
            for key in ("node_id", "source", "target", "file"):
                val = conflict.get(key)
                if isinstance(val, str) and val:
                    conflict_nodes.add(val)

    def _priority(node: Any) -> tuple[int, str]:
        if not isinstance(node, dict):
            return (9, "")
        nid = str(node.get("id") or "")
        npath = str(node.get("path") or "")
        nkind = str(node.get("kind") or node.get("layer") or "").lower()
        if nid in conflict_nodes or npath in conflict_nodes:
            return (0, nid)
        if nkind in {"api_route", "service", "entry", "backend_route", "router"}:
            return (1, nid)
        if nkind in {"frontend_call", "model", "store"}:
            return (2, nid)
        return (3, nid)

    ordered_nodes = sorted(nodes, key=_priority)
    kept_nodes = ordered_nodes[:max_nodes]
    kept_ids = {
        str(n.get("id") or "")
        for n in kept_nodes
        if isinstance(n, dict) and n.get("id")
    }
    kept_edges = [
        edge
        for edge in edges
        if isinstance(edge, dict)
        and str(edge.get("source") or "") in kept_ids
        and str(edge.get("target") or "") in kept_ids
    ][:max_edges]
    compact = dict(graph)
    compact["nodes"] = kept_nodes
    compact["edges"] = kept_edges
    compact["truncated"] = True
    compact["total_nodes"] = len(nodes)
    compact["total_edges"] = len(edges)
    return compact


def cmd_project_snapshot(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """在单次 CLI 进程中批量读取并返回完整项目视图，消除 N+1 重复读盘。"""
    del confirm
    project = _project(ws, payload)
    project_id = project.project_id
    base: dict[str, Any] = {"project_id": project_id}
    show = cmd_project_show(ws, base, False)
    map_data = cmd_project_map(ws, base, False)
    config_data = cmd_project_config(ws, base, False)

    plans_by_task = _load_plans_by_task(ws, project_id)
    sig_cache: dict[str, str] = {}
    tasks: list[dict[str, Any]] = []
    tasks_dir = ws.project_dir(project_id) / "tasks"
    deliveries_dir = ws.project_dir(project_id) / "deliveries"
    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.glob("*.json")):
            try:
                task = read_model(path, Task)
            except AiTestError:
                continue
            current_contract = task_contract(task)
            plans: list[dict[str, Any]] = []
            for plan in plans_by_task.get(task.task_id, []):
                sig_key = plan.task_id
                if sig_key not in sig_cache:
                    sig_cache[sig_key] = _plan_signature(ws, project, plan.task_id)
                current_signature = sig_cache[sig_key]
                if not plan.task_contract:
                    stale = True
                    stale_reason = "旧计划没有任务契约，需要重新生成"
                elif plan.task_contract != current_contract:
                    stale = True
                    stale_reason = "验收项或验证方式已变化，需要重新生成计划"
                elif not plan.source_signature or plan.source_signature != current_signature:
                    stale = True
                    stale_reason = "计划生成后项目、交付提交、或服务配置已变化，需要重新生成"
                else:
                    stale = False
                    stale_reason = ""
                plans.append(
                    {
                        "plan": plan.model_dump(mode="json"),
                        "coverage": {
                            "selected": len(plan.scope.acceptance_item_ids),
                            "applicable": plan.applicable_count,
                        },
                        "stale": stale,
                        "stale_reason": stale_reason,
                        "task_updated": plan.task_revision != task.revision,
                    }
                )
            delivery: dict[str, Any] | None = None
            deliv_path = deliveries_dir / f"{task.task_id}.json"
            if deliv_path.is_file():
                try:
                    delivery = read_model(deliv_path, Delivery).model_dump(mode="json")
                except AiTestError:
                    delivery = None
            tasks.append(
                {
                    "task": task.model_dump(mode="json"),
                    "plans": plans,
                    "delivery": delivery,
                }
            )

    all_runs = cmd_run_list(ws, base, False).get("runs", [])
    raw_runs_limit = payload.get("runs_limit", 50)
    runs_limit = raw_runs_limit if isinstance(raw_runs_limit, int) and raw_runs_limit > 0 else 50
    raw_reports_limit = payload.get("reports_limit", 50)
    reports_limit = (
        raw_reports_limit if isinstance(raw_reports_limit, int) and raw_reports_limit > 0 else 50
    )
    reports_by_id = _load_reports_by_id(ws, project_id)
    runs: list[dict[str, Any]] = []
    evidence: dict[str, list[dict[str, Any]]] = {}
    reports: list[dict[str, Any]] = []
    for summary in all_runs[-runs_limit:]:
        run_id = str(summary.get("run_id") or "")
        if not run_id:
            continue
        try:
            detail = cmd_run_status(ws, {**base, "run_id": run_id}, False)
        except AiTestError:
            continue
        runs.append(detail)
        evidence[run_id] = cmd_evidence_list(
            ws, {**base, "run_id": run_id, "include_hash": False}, False
        ).get("evidence", [])
        report_id = str(detail.get("run", {}).get("latest_report_id") or "")
        if report_id and report_id in reports_by_id and len(reports) < reports_limit:
            reports.append(reports_by_id[report_id])

    issues = _load_issues(ws, project_id)
    seen_run_ids = {
        str(entry.get("run", {}).get("run_id") or "")
        for entry in runs
        if isinstance(entry, dict)
    }
    for shown_issue in issues:
        for occ in shown_issue.get("occurrences") or []:
            occ_run_id = str(occ.get("run_id") or "") if isinstance(occ, dict) else ""
            if occ_run_id and occ_run_id not in seen_run_ids:
                seen_run_ids.add(occ_run_id)
                try:
                    detail = cmd_run_status(ws, {**base, "run_id": occ_run_id}, False)
                except AiTestError:
                    continue
                runs.append(detail)
                evidence[occ_run_id] = cmd_evidence_list(
                    ws, {**base, "run_id": occ_run_id, "include_hash": False}, False
                ).get("evidence", [])

    since_commit = _latest_finished_commit(runs, reports)
    config_obj = config_data.get("config")
    config = config_obj if isinstance(config_obj, dict) else {}
    local_git, code_graph = _resolve_snapshot_git_and_graph(
        ws, project_id, show["project"], config, map_data, since_commit
    )
    runtime_introspect = bool(payload.get("runtime_introspect") is True)
    project_root = Path(str(show["project"].get("root") or ""))
    secrets = known_secret_values([config])
    ctx = (
        cached_project_context(ws.root, project_id, project_root, config)
        if project_root.is_dir()
        else {}
    )
    capabilities = build_capabilities_snapshot(
        project_root,
        config,
        project_context=ctx,
        runtime_introspect=runtime_introspect,
        secrets=secrets,
    )
    health_summary = build_project_health_summary(capabilities, ctx, code_graph)
    include_full_graph = bool(payload.get("include_full_graph") is True)
    out_graph = (
        code_graph
        if include_full_graph or not isinstance(code_graph, dict)
        else _compact_code_graph(code_graph)
    )
    return {
        "project": show["project"],
        "modules": show.get("modules", []),
        "map": map_data.get("map"),
        "map_stale": bool(map_data.get("stale")),
        "local_git": local_git,
        "code_graph": out_graph,
        "capabilities": capabilities,
        "health_summary": health_summary,
        "config": config,
        "tasks": tasks,
        "runs": runs,
        "evidence": evidence,
        "reports": reports,
        "issues": issues,
        "github": _load_latest_github(ws, project_id),
        "totals": {
            "runs": len(all_runs),
            "reports": len(reports_by_id),
            "issues": len(issues),
        },
    }
