"""Runtime 命令模块：_common。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, TypeVar, cast

from aitest.commands import (
    Payload,
    _heartbeat_stale,
    _project,
    _require,
    _safe_id,
    _text,
)
from aitest.config import known_secret_values
from aitest.core.code_graph import load_or_build_code_graph
from aitest.core.evidence import (
    evidence_path,
    save_evidence,
    save_evidence_stream,
    settings_from_config,
)
from aitest.core.expectations import expectation_matches
from aitest.core.project_context import (
    cached_project_context,
    python_dependencies,
)
from aitest.core.scope import applicable_items, task_contract
from aitest.core.shared_config import extract_conventions
from aitest.errors import AiTestError, invalid_request
from aitest.model_config import load_project_config
from aitest.models import (
    AcceptanceItem,
    Case,
    Delivery,
    Plan,
    Project,
    Run,
    RunCase,
    Scope,
    Task,
    case_item_ids,
)
from aitest.security import ensure_within, redact, redact_tree
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace

T = TypeVar("T")


def facade_attr(name: str, default: T) -> T:
    """从 aitest.runtime_commands 门面动态解析可被测试 monkeypatch 替换的符号。"""
    facade = sys.modules.get("aitest.runtime_commands")
    return cast(T, getattr(facade, name, default)) if facade is not None else default

def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)



def _plan_path(ws: Workspace, project_id: str, plan_id: str) -> Path:
    safe_plan_id = _safe_id(plan_id, "plan_id")
    return ws.project_dir(project_id) / "plans" / f"{safe_plan_id}.json"



def _load_plan(ws: Workspace, project_id: str, plan_id: str) -> Plan:
    path = _plan_path(ws, project_id, plan_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"计划不存在：{plan_id}", "先生成计划")
    return read_model(path, Plan)



def _task_by_id(ws: Workspace, project_id: str, task_id: str) -> Task:
    safe_task_id = _safe_id(task_id, "task_id")
    path = ws.project_dir(project_id) / "tasks" / f"{safe_task_id}.json"
    if not path.is_file():
        raise AiTestError("TASK_NOT_FOUND", f"任务不存在：{task_id}", "先创建任务")
    return read_model(path, Task)



def _selected_items(task: Any, scope: Scope, project: Project) -> tuple[list[str], int]:
    """Return selected item ids and the frozen applicable count ``M``.

    ``M`` is always every active acceptance item of the task. The module filter
    only helps the user pick items; it must not shrink ``M`` or hide uncovered
    items from other modules.
    """
    if scope.module_ids:
        registered = {module.module_id for module in project.modules}
        unknown_modules = sorted(
            module_id for module_id in scope.module_ids if module_id not in registered
        )
        if unknown_modules:
            raise invalid_request("模块未登记：" + "、".join(unknown_modules))
    active_items = applicable_items(task)
    active = [item.item_id for item in active_items]
    if scope.preset == "all_applicable":
        return active, len(active)
    unknown = sorted({item for item in scope.acceptance_item_ids if item not in active})
    if unknown:
        raise invalid_request("验收项不属于当前任务或不可用：" + "、".join(unknown))
    return list(dict.fromkeys(scope.acceptance_item_ids)), len(active)



def _ensure_plan_coverage(plan: Plan, task: Task) -> None:
    """A published plan must cover every selected item and never have an empty S."""
    selected = list(plan.scope.acceptance_item_ids)
    pure_l1_smoke = (
        task.intent == "smoke"
        and bool(plan.cases)
        and all(case.layer == "L1" and not case_item_ids(case) for case in plan.cases)
    )
    if not selected and not pure_l1_smoke and (task.intent != "smoke" or applicable_items(task)):
        raise AiTestError("SCOPE_EMPTY", "本次范围为空", "至少选择一条可用验收项")
    covered: set[str] = set()
    for case in plan.cases:
        bound = set(case_item_ids(case))
        outside = bound - set(selected)
        if outside:
            raise invalid_request(
                f"用例 {case.case_id} 绑定了范围外验收项：" + "、".join(sorted(outside))
            )
        covered.update(bound)
    missing = [item for item in selected if item not in covered]
    if missing:
        raise invalid_request("以下验收项没有对应用例：" + "、".join(missing))
    for case in plan.cases:
        if not case_item_ids(case) and case.layer != "L1":
            raise invalid_request(
                f"用例 {case.case_id} 未关联验收项",
                "只有 L1 项目级检查可以不关联验收项；其余用例必须绑定验收项",
            )



_SOURCE_EXTS = frozenset({".py", ".ts", ".tsx", ".js", ".vue", ".go", ".java", ".html"})
_PRIORITY_PATH_HINTS = (
    ("routes", 6),
    ("routers", 6),
    ("endpoints", 6),
    ("server", 5),
    ("views", 5),
    ("controllers", 5),
    ("services", 5),
    ("handlers", 5),
    ("schemas", 4),
    ("validators", 4),
    ("models", 4),
    ("auth", 4),
    ("api", 3),
    ("app", 3),
    ("main", 3),
    ("index", 2),
)


def _keyword_tokens(keywords: list[str] | None) -> list[str]:
    tokens: list[str] = []
    for raw in keywords or []:
        lowered = raw.lower()
        tokens.extend(re.findall(r"[a-z0-9_]{2,}", lowered))
        tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,6}", lowered))
    return list(dict.fromkeys(tokens))[:32]


def _source_relevance(rel_posix: str, text: str, tokens: list[str]) -> int:
    lower_path = rel_posix.lower()
    lower_head = text[:8000].lower()
    score = 1
    for hint, weight in _PRIORITY_PATH_HINTS:
        if hint in lower_path:
            score += weight
    if (
        "route(" in text
        or "apirouter" in lower_head
        or "flask(" in lower_head
        or "basehttprequesthandler" in lower_head
        or "httpserver" in lower_head
        or "do_post" in lower_head
        or "fetch(" in lower_head
        or "json.stringify(" in lower_head
    ):
        score += 4
    for token in tokens:
        if token in lower_path:
            score += 6
        elif token in lower_head:
            score += 4
    return score


def _project_context(
    project: Project, ws: Workspace, focus_keywords: list[str] | None = None
) -> dict[str, Any]:
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
    tokens = _keyword_tokens(focus_keywords)
    scored_sources: list[tuple[int, str, str]] = []
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "tests",
        "test",
        "e2e",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "coverage",
    }
    scanned = 0
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not d.startswith("."))
        for file_name in sorted(names):
            suffix = Path(file_name).suffix.lower()
            if suffix not in _SOURCE_EXTS:
                continue
            if file_name.startswith("test_") or file_name.endswith(
                ("_test.py", ".test.ts", ".test.js", ".spec.ts", ".spec.js")
            ):
                continue
            path = Path(base) / file_name
            try:
                if path.stat().st_size > 256 * 1024:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel_posix = path.relative_to(root).as_posix()
            score = _source_relevance(rel_posix, text, tokens)
            scored_sources.append((score, rel_posix, text))
            scanned += 1
            if scanned >= 300:
                break
        if scanned >= 300:
            break
    scored_sources.sort(key=lambda entry: (-entry[0], entry[1]))
    top_sources = scored_sources[:12]
    remaining_budget = 36000
    sources: list[str] = []
    for idx, (_score, rel, body) in enumerate(top_sources):
        remaining_count = len(top_sources) - idx
        allowance = min(12000, max(3000, remaining_budget // remaining_count))
        snippet = body[:allowance]
        sources.append(f"--- {rel} ---\n{snippet}")
        remaining_budget = max(0, remaining_budget - len(snippet))
    config = load_project_config(ws, project.project_id)
    derived = cached_project_context(ws.root, project.project_id, root, config)
    graph = load_or_build_code_graph(ws, project.project_id, root, config)
    conventions = extract_conventions(config)
    for directory in derived["test_dirs"]:
        if directory not in files:
            files.append(directory)
    secrets = known_secret_values([config])
    example = derived.get("test_example")
    redacted_sources = [redact(item, secrets) for item in sources]
    return {
        "root": str(root),
        "routes": redacted_sources,
        "source_excerpts": redacted_sources,
        "files": files,
        "readme": redact(excerpt, secrets),
        "services": redact_tree(derived["services"], secrets),
        "runners": redact_tree(config.get("runners", {}), secrets),
        "l1": redact_tree(config.get("l1", {}), secrets),
        "test_roles": redact_tree(config.get("test_roles", {}), secrets),
        "test_commands": derived["test_commands"],
        "test_modules": derived.get("test_modules", []),
        "test_functions": derived.get("test_functions", {}),
        "test_summaries": derived.get("test_summaries", {}),
        "playwright_tests": derived.get("playwright_tests", {}),
        "bun_tests": derived.get("bun_tests", {}),
        "test_dependencies": derived.get("test_dependencies", []),
        "smoke_command": derived["smoke_command"],
        "smallest_test": derived["smallest_test"],
        "test_example": redact_tree(example, secrets) if isinstance(example, dict) else None,
        "symbols": derived.get("symbols", {}),
        "test_dirs": derived["test_dirs"],
        "framework": derived["framework"],
        "has_frontend": derived["has_frontend"],
        "has_database": derived["has_database"],
        "existing_commands": derived["existing_commands"],
        "script_examples": redact_tree(derived.get("script_examples", {}), secrets),
        "api_routes": derived.get("api_routes", []),
        "frontend_api_calls": redact_tree(derived.get("frontend_api_calls", []), secrets),
        "frontend_assets": redact_tree(derived.get("frontend_assets", []), secrets),
        "conventions": redact_tree(conventions, secrets),
        "code_graph_summary": redact_tree(
            {
                "stats": graph.get("stats", {}),
                "conflicts": list(graph.get("conflicts") or [])[:20],
                "impact": graph.get("impact", {}),
                "module_chunks": list(graph.get("module_chunks") or [])[:25],
            },
            secrets,
        ),
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



def _prefix_component_command(method: str, root: Path) -> str:
    """已清理针对特定工具的子目录命令改写硬编码，保留用户原始显式命令。"""
    del root
    return method



_SCRIPT_SUFFIXES = frozenset({".py", ".ts", ".js", ".mjs", ".cjs"})



def _safe_script_name(name: str) -> bool:
    text = name.strip()
    if not text or len(text) > 120 or text in {".", ".."}:
        return False
    if Path(text).name != text or "/" in text or "\\" in text:
        return False
    if text.startswith(".") or ".." in text:
        return False
    return Path(text).suffix.lower() in _SCRIPT_SUFFIXES



def _plan_dependencies(cases: list[Case]) -> list[str]:
    """Only Python requirements are auto-installed; Node deps belong to npm/bun."""

    return sorted(
        python_dependencies(
            [dependency for case in cases for dependency in case.dependencies]
        )
    )



def _snapshot_excluded_items(task: Task, selected_ids: list[str]) -> list[dict[str, Any]]:
    """固化生成计划或启动运行时未被纳入的适用验收项清单，避免后续修改任务导致历史漂移。"""
    selected_set = set(selected_ids)
    excluded: list[dict[str, Any]] = []
    for index, item in enumerate(task.acceptance_items):
        if not item.observable or item.status != "active":
            continue
        if item.item_id in selected_set:
            continue
        excluded.append(
            {
                "item_id": item.item_id,
                "no": f"{index + 1:02d}",
                "text": item.text,
                "module_id": item.module_id,
            }
        )
    return excluded


def _unselected_by_module(
    task: Task, selected: list[str], project: Project
) -> list[dict[str, Any]]:
    names = {module.module_id: module.name for module in project.modules}
    counts: dict[str, int] = {}
    for item in task.acceptance_items:
        if item.status != "active" or item.item_id in selected:
            continue
        key = item.module_id or ""
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "module_id": key or None,
            "module_name": names.get(key, ""),
            "count": count,
        }
        for key, count in sorted(counts.items())
    ]



def _redact_cases(cases: list[Case], secrets: list[str]) -> None:
    for case in cases:
        case.title = redact(case.title, secrets)
        case.expected = redact(case.expected, secrets)
        case.expectation_source = redact(case.expectation_source, secrets)
        case.verify_method = redact(case.verify_method, secrets)
        case.script_content = (
            redact(case.script_content, secrets) if case.script_content else None
        )
        case.steps = [redact(item, secrets) for item in case.steps]
        case.preconditions = [redact(item, secrets) for item in case.preconditions]
        if case.frozen_expectation is not None:
            redacted_frozen = redact_tree(case.frozen_expectation, secrets)
            if isinstance(redacted_frozen, dict):
                case.frozen_expectation = redacted_frozen



def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]



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
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()



def _delivery_commit(ws: Workspace, project: Project, task_id: str) -> str:
    path = ws.project_dir(project.project_id) / "deliveries" / f"{task_id}.json"
    if path.is_file():
        try:
            delivery = read_model(path, Delivery)
        except AiTestError:
            delivery = None
        if delivery is not None and delivery.commit.strip():
            return delivery.commit.strip()
    return _git_head(Path(project.root))



def _next_plan_no(ws: Workspace, project_id: str, task_id: str) -> int:
    """Next human-facing plan number within one task (new scope = new plan)."""
    directory = ws.project_dir(project_id) / "plans"
    highest = 0
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                existing = read_model(path, Plan)
            except AiTestError:
                continue
            if existing.task_id == task_id:
                highest = max(highest, existing.plan_no)
    return highest + 1



def _plan_signature(
    ws: Workspace,
    project: Project,
    task_id: str,
) -> str:
    config = load_project_config(ws, project.project_id)
    task = _task_by_id(ws, project.project_id, task_id)
    services = config.get("services")
    l1 = config.get("l1")
    runners = config.get("runners")
    payload = {
        "project_revision": project.revision,
        "delivery_commit": _delivery_commit(ws, project, task_id),
        "task_contract": task_contract(task),
        "task_materials": {
            "scope": task.scope,
            "inputs": task.inputs,
            "outputs": task.outputs,
            "preconditions": list(task.preconditions),
            "source_map_revision": task.source_map_revision,
        },
        "services": services if isinstance(services, dict) else {},
        "l1": l1 if isinstance(l1, dict) else {},
        "runners": runners if isinstance(runners, dict) else {},
        "root": project.root,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()



def _unverified_cases(cases: list[Case], items: list[AcceptanceItem]) -> list[Case]:
    """Return cases whose source does not cite every bound acceptance item."""
    by_id = {item.item_id: item for item in items}
    unverified: list[Case] = []
    for case in cases:
        ids = case_item_ids(case)
        bound = [by_id.get(item_id) for item_id in ids]
        if not ids or any(item is None for item in bound):
            unverified.append(case)
            continue
        if not all(
            expectation_matches(case.expectation_source, item)
            for item in bound
            if item is not None
        ):
            unverified.append(case)
    return unverified



def _expectation_feedback(items: list[AcceptanceItem], cases: list[Case]) -> str:
    ids = "、".join(case.case_id for case in cases)
    lines = [
        f"以下用例的 expectation_source 不是验收项原文：{ids}。",
        "只为下列验收项重新输出 cases（其他验收项保留原结果），"
        "并把每条用例的 expectation_source 改成对应验收项 text 的逐字副本：",
    ]
    lines.extend(f"- {item.item_id}：{item.text}" for item in items)
    return "\n".join(lines)



def _runs_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "runs"



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
        try:
            existing = read_model(path, Run)
        except AiTestError:
            continue
        if existing.status == "running" and not _heartbeat_stale(existing):
            return existing
    return None



def _evidence_settings(config: dict[str, Any]) -> tuple[int, int, bool]:
    budget, cap, allow_video = settings_from_config(config)
    return budget, cap, allow_video



def _save_evidence_file(
    run_dir: Path,
    name: str,
    data: bytes,
    budget: int | None = None,
    cap: int | None = None,
    allow_video: bool = True,
    secrets_list: list[str] | None = None,
    *,
    result: str = "failed",
    original_size: int | None = None,
) -> str:
    ref = save_evidence(
        run_dir,
        name,
        data,
        result=result,
        budget_bytes=budget,
        allow_video=allow_video,
        file_cap_bytes=cap if cap is not None else 20 * 1024 * 1024,
        secrets=secrets_list,
        original_size=original_size,
    )
    if ref is None:
        raise AiTestError(
            "EVIDENCE_BUDGET",
            "证据未保存：运行媒体预算已用完或视频证据未启用",
            "调整 project.config 的 evidence 设置，或只提交关键证据",
        )
    return ref


def _save_evidence_stream(
    run_dir: Path,
    name: str,
    source_path: Path,
    budget: int | None = None,
    cap: int | None = None,
    allow_video: bool = True,
    secrets_list: list[str] | None = None,
    *,
    result: str = "failed",
) -> str:
    """流式持久化文件型证据，超出预算或未启用视频时抛出统一错误。"""
    ref = save_evidence_stream(
        run_dir,
        name,
        source_path,
        result=result,
        budget_bytes=budget,
        allow_video=allow_video,
        file_cap_bytes=cap if cap is not None else 20 * 1024 * 1024,
        secrets=secrets_list,
    )
    if ref is None:
        raise AiTestError(
            "EVIDENCE_BUDGET",
            "证据未保存：运行媒体预算已用完或视频证据未启用",
            "调整 project.config 的 evidence 设置，或只提交关键证据",
        )
    return ref



def _run_id(payload: Payload) -> str:
    run_id = _text({"id": _require(payload, "run_id")}, "id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise invalid_request("run_id 不合法")
    return run_id



def _run(ws: Workspace, payload: Payload) -> tuple[Path, Run]:
    project = _project(ws, payload)
    run_id = _run_id(payload)
    path = _runs_dir(ws, project.project_id) / run_id / "run.json"
    if not path.is_file():
        raise AiTestError("RUN_NOT_FOUND", f"运行不存在：{run_id}", "检查运行 ID")
    # 只读读取：仅在内存中校正疑似过期心跳展示状态，严禁无锁覆写正在运行或已落盘的 run.json
    return path.parent, _refresh_stale(read_model(path, Run))



def _ensure_settled(run: Run) -> None:
    """The worker owns ``runs/<id>/`` while running; CLI writes wait for it."""
    if run.status == "running":
        raise AiTestError(
            "RUN_ACTIVE",
            f"运行仍在进行：{run.run_id}",
            "等待运行结束后再记录证据、人工结果或生成报告",
        )


def load_resumable_cases(
    source_run_dir: Path,
    current_plan: Plan,
    selected_cases: list[Case],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """仅从来源运行 `run.json` 校验兼容性并提取可安全复用的稳定通过用例与证据。"""
    run_file = source_run_dir / "run.json"
    plan_file = source_run_dir / "plan.json"
    if not run_file.is_file() or not plan_file.is_file():
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            f"来源运行目录不完整，无法续跑：{source_run_dir.name}",
            "请确认 run_id 是否正确后重新发起完整运行",
        )
    source_run = read_model(run_file, Run)
    if source_run.status == "running":
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            f"来源运行仍在执行中（{source_run.run_id}），不能基于运行中实例续跑",
            "等待其结束或先取消后再续跑",
        )
    source_plan = read_model(plan_file, Plan)
    if (
        source_plan.plan_id != current_plan.plan_id
        or source_plan.revision != current_plan.revision
        or source_plan.source_signature != current_plan.source_signature
    ):
        raise AiTestError(
            "PLAN_STALE",
            "来源运行的计划 ID、修订号或源码签名与当前不一致，不能断点续跑",
            "请重新发起完整执行以确保结果对应最新代码与计划",
        )

    completed_map: dict[str, dict[str, Any]] = {}
    for rc in source_run.cases:
        if rc.result != "not_run":
            completed_map[rc.case_id] = {
                "result": rc.result,
                "confidence_tier": rc.confidence_tier,
                "confidence_reason": rc.confidence_reason,
                "service_log_issues": list(rc.service_log_issues),
                "judgement": rc.judgement,
                "evidence_refs": list(rc.evidence_refs),
                "attempts": [att.model_dump(mode="json") for att in rc.attempts],
            }

    eligible_ids = {case.case_id for case in selected_cases if case.layer != "L1"}
    resumable: dict[str, dict[str, Any]] = {}
    refs: list[str] = []
    seen_refs: set[str] = set()
    for cid in eligible_ids:
        entry = completed_map.get(cid)
        if not entry:
            continue
        if entry.get("result") != "passed" or entry.get("confidence_tier") == "suspected":
            continue
        resumable[cid] = entry
        for ref in entry.get("evidence_refs") or []:
            if isinstance(ref, str) and ref and ref not in seen_refs:
                seen_refs.add(ref)
                refs.append(ref)
    return resumable, refs


def copy_resumed_evidence(
    source_run_dir: Path,
    target_run_dir: Path,
    refs: list[str],
) -> list[str]:
    """将来源运行中的通过证据安全复制到新运行目录，保持历史记录不可变。"""
    copied: list[str] = []
    for ref in refs:
        src = evidence_path(source_run_dir, ref)
        if src is None or not src.is_file():
            continue
        rel_name = ref[len("evidence/") :] if ref.startswith("evidence/") else Path(ref).name
        try:
            dest_root = target_run_dir / "evidence"
            dest = ensure_within(dest_root, dest_root / rel_name)
        except ValueError:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied.append(f"evidence/{dest.relative_to(dest_root).as_posix()}")
    return copied


def build_resumed_run_cases(
    *,
    filtered_cases: list[Case],
    resumable: dict[str, dict[str, Any]],
    source_run_id: str,
    is_manual_fn: Any,
) -> tuple[list[RunCase], list[str]]:
    """按计划切片生成新运行的 RunCase 列表并预填充断点复用用例。"""
    run_cases: list[RunCase] = []
    collected_refs: list[str] = []
    for case in filtered_cases:
        entry = resumable.get(case.case_id)
        if entry is None:
            run_cases.append(
                RunCase(
                    case_id=case.case_id,
                    result="manual_pending" if is_manual_fn(case) else "not_run",
                )
            )
            continue
        case_refs = [
            str(ref)
            for ref in (entry.get("evidence_refs") or [])
            if isinstance(ref, str) and ref
        ]
        for ref in case_refs:
            if ref not in collected_refs:
                collected_refs.append(ref)
        rc = RunCase.model_validate(
            {
                "case_id": case.case_id,
                "result": "passed",
                "attempts": entry.get("attempts") or [],
                "evidence_refs": case_refs,
                "confidence_tier": entry.get("confidence_tier"),
                "confidence_reason": str(entry.get("confidence_reason") or ""),
                "judgement": entry.get("judgement"),
                "service_log_issues": list(entry.get("service_log_issues") or []),
                "resumed_from_run_id": source_run_id,
            }
        )
        run_cases.append(rc)
    return run_cases, collected_refs



