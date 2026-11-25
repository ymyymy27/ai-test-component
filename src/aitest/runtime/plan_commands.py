"""Runtime 命令模块：plan_commands。"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from aitest.commands import (
    Payload,
    _project,
    _require,
    _task,
    _text,
)
from aitest.config import known_secret_values
from aitest.core.blind_expectation import (
    build_contract_only_context,
    contract_context_digest,
)
from aitest.core.expectations import annotate_cases, ensure_publishable
from aitest.core.scope import task_contract
from aitest.core.script_linter import lint_script
from aitest.errors import AiTestError, invalid_request
from aitest.model_config import build_client, load_project_config, model_settings
from aitest.models import (
    Case,
    Plan,
    Scope,
    Task,
    case_item_ids,
    new_id,
)
from aitest.progress import emit_progress
from aitest.runtime._common import (
    _ensure_plan_coverage,
    _expectation_feedback,
    _load_plan,
    _next_plan_no,
    _normalize_method,
    _plan_dependencies,
    _plan_path,
    _plan_signature,
    _prefix_component_command,
    _project_context,
    _redact_cases,
    _selected_items,
    _snapshot_excluded_items,
    _string_list,
    _task_by_id,
    _unselected_by_module,
    _unverified_cases,
    facade_attr,
)
from aitest.runtime.plan_validation import (
    _annotate_case_coverage,
    _broad_test_command,
    _dedupe_script_names,
    _downgrade_broad_business_cases,
    _drop_unavailable_django_scripts,
    _merge_duplicate_command_cases,
    _overshared_bindings,
    _project_import_warnings,
    _shared_command_warnings,
    _smoke_cases,
    _split_multi_item_cases,
    _target_problems,
    _validate_plan_cases,
)
from aitest.security import redact_tree
from aitest.storage.files import write_model
from aitest.storage.workspace import Workspace, iso_now


def _generation_feedback(
    items: list[dict[str, Any]], context: dict[str, Any], reason: str
) -> str:
    """Explicit item and command list for a model retry after validation."""

    lines = [
        f"上一次输出未通过校验：{reason}。",
        "必须为下面每个验收项至少生成一条用例，acceptance_item_id 必须逐字使用所列 ID，"
        "不能遗漏，也不能合并成未列出的 ID：",
    ]
    lines.extend(f"- {item.get('item_id')}：{item.get('text')}" for item in items)
    commands = _string_list(context.get("test_commands"))
    specific = [
        command
        for command in commands
        if "tests" in command and not _broad_test_command(f"command:{command}")
    ]
    if specific:
        lines.append("可用具体测试命令（优先使用这些，禁止 npm test 或其他整包命令）：")
        lines.extend(f"- command:{command}" for command in specific[:8])
    functions = context.get("test_functions")
    if isinstance(functions, dict) and functions:
        lines.append("可用 pytest 函数（写成 python -m pytest <文件>::<函数>，函数名逐字复制）：")
        for path, names in list(functions.items())[:4]:
            if isinstance(names, list):
                lines.extend(f"- {path}::{name}" for name in names[:10] if name)
    playwright = context.get("playwright_tests")
    if isinstance(playwright, dict) and playwright:
        lines.append('可用 Playwright 标题（必须写成 --grep "<标题>"，标题逐字复制）：')
        for path, titles in list(playwright.items())[:4]:
            if isinstance(titles, list):
                lines.extend(
                    f'- {path} --grep "{title}"' for title in titles[:10] if title
                )
    return "\n".join(lines)



_PLAN_CACHE_VERSION = 21


def _workspace_source_fingerprint(root: Path) -> str:
    """计算工作区真实代码状态指纹（含 Git HEAD 与未提交工作树变更），代码修改即失效缓存。"""
    if not root.is_dir():
        return "missing"
    hasher = hashlib.sha256()
    if (root / ".git").exists():
        try:
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            status = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain", "-uall"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            if head.returncode == 0 and status.returncode == 0:
                hasher.update(f"head:{head.stdout.strip()}\n".encode())
                for line in sorted(status.stdout.splitlines()):
                    if len(line) < 4:
                        continue
                    rel = line[3:].split(" -> ")[-1].strip().strip('"')
                    hasher.update(f"dirty:{line[:2]}:{rel}:".encode())
                    candidate = root / rel
                    if candidate.is_file():
                        try:
                            if candidate.stat().st_size <= 256 * 1024:
                                hasher.update(candidate.read_bytes())
                        except OSError:
                            pass
                return f"git:{hasher.hexdigest()[:24]}"
        except (OSError, subprocess.SubprocessError):
            pass
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "data",
        "logs",
        "tmp",
        "test-results",
    }
    tracked_exts = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".vue",
        ".html",
        ".css",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".md",
    }
    count = 0
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and not d.startswith("."))
        for name in sorted(names):
            if Path(name).suffix.lower() not in tracked_exts:
                continue
            path = Path(base) / name
            try:
                if path.stat().st_size > 256 * 1024:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            rel_posix = path.relative_to(root).as_posix()
            hasher.update(f"{rel_posix}:{len(data)}:".encode())
            hasher.update(hashlib.sha256(data).digest())
            count += 1
            if count >= 400:
                return f"fs:{hasher.hexdigest()[:24]}"
    return f"fs:{hasher.hexdigest()[:24]}"


def _blind_cache_path(ws: Workspace, project_id: str, cache_key: str) -> Path:
    return (
        ws.root / ".cache" / "blind_expectations" / project_id / f"{cache_key[:24]}.json"
    )


def _read_cached_frozen(
    path: Path, batch: list[dict[str, Any]]
) -> dict[str, dict[str, Any]] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    expected_ids = sorted(str(item.get("item_id") or "") for item in batch)
    if data.get("item_ids") != expected_ids:
        return None
    raw = data.get("frozen")
    if not isinstance(raw, dict):
        return None
    return {
        str(k): dict(v)
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, dict)
    }


def _write_cached_frozen(
    path: Path,
    batch: list[dict[str, Any]],
    frozen: dict[str, dict[str, Any]],
    secrets: list[str],
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        payload = {
            "item_ids": sorted(str(item.get("item_id") or "") for item in batch),
            "frozen": frozen,
        }
        tmp.write_text(
            json.dumps(redact_tree(payload, secrets), ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass


def _freeze_expectations_with_cache(
    ws: Workspace,
    project_id: str,
    *,
    source_fp: str,
    model_name: str,
    client: Any,
    task_payload: dict[str, Any],
    items: list[dict[str, Any]],
    contract_context: dict[str, Any],
    secrets: list[str],
) -> tuple[dict[str, dict[str, Any]], bool]:
    """第 1 步盲写预期：仅用契约视图分批冻结断言，并支持本地脱敏缓存与失败平滑降级。"""
    freezer = getattr(client, "freeze_expectations", None)
    if not items or not callable(freezer):
        return {}, False
    digest = contract_context_digest(contract_context)
    size = _plan_batch_size()
    batches = [items[idx : idx + size] for idx in range(0, len(items), size)]
    frozen: dict[str, dict[str, Any]] = {}
    degraded = False
    for batch in batches:
        key = hashlib.sha256(
            json.dumps(
                {
                    "source_fp": source_fp,
                    "contract": digest,
                    "model": model_name,
                    "rules": _PLAN_CACHE_VERSION,
                    "items": [
                        {"item_id": str(i.get("item_id") or ""), "text": str(i.get("text") or "")}
                        for i in batch
                    ],
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        cache_file = _blind_cache_path(ws, project_id, key)
        cached = _read_cached_frozen(cache_file, batch)
        if cached is not None:
            frozen.update(cached)
            continue
        try:
            batch_result = freezer(task_payload, batch, contract_context)
        except AiTestError as exc:
            if exc.info.code == "TOKEN_BUDGET_EXCEEDED":
                raise
            degraded = True
            break
        if isinstance(batch_result, dict):
            cleaned = {
                str(k): dict(v)
                for k, v in batch_result.items()
                if isinstance(k, str) and isinstance(v, dict)
            }
            frozen.update(cleaned)
            _write_cached_frozen(cache_file, batch, cleaned, secrets)
    return frozen, degraded


def _map_tests_cache_path(
    ws: Workspace, project_id: str, cache_key: str
) -> Path:
    return ws.root / ".cache" / "map_tests" / project_id / f"{cache_key[:24]}.json"


def _read_cached_map_tests(
    path: Path, candidates: list[str]
) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("mapping")
    if not isinstance(raw, dict):
        return None
    allowed = set(candidates)
    return {
        str(k): str(v)
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, str) and v in allowed
    }


def _write_cached_map_tests(
    path: Path, mapping: dict[str, str], secrets: list[str]
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(redact_tree({"mapping": mapping}, secrets), ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass



def _plan_budget_seconds() -> float:
    """Wall-clock budget for one plan generation, including repairs."""
    raw = os.environ.get("AITEST_PLAN_BUDGET_SECONDS", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 150.0
    return value if value >= 30 else 150.0



def _plan_batch_size() -> int:
    raw = os.environ.get("AITEST_PLAN_BATCH_SIZE", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 6
    return value if 1 <= value <= 12 else 6



def _plan_concurrency() -> int:
    raw = os.environ.get("AITEST_PLAN_CONCURRENCY", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 4
    return value if 1 <= value <= 8 else 4



def _plan_cache_dir(ws: Workspace, project_id: str, fingerprint: str) -> Path:
    """Completed batches live in the clearable cache, keyed by contract facts."""
    return ws.root / ".cache" / "plan" / project_id / fingerprint[:24]



def _batch_fingerprint(items: list[dict[str, Any]]) -> str:
    rows = sorted(
        (
            str(item.get("item_id") or ""),
            str(item.get("text") or ""),
            bool(item.get("observable", True)),
            str(item.get("status") or ""),
        )
        for item in items
    )
    encoded = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()



def _read_cached_batch(
    cache_dir: Path, key: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    path = cache_dir / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("item_ids") != sorted(str(item.get("item_id") or "") for item in items):
        return None
    cases = data.get("cases")
    return cases if isinstance(cases, list) else None



def _write_cached_batch(
    cache_dir: Path,
    key: str,
    items: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    secrets: list[str],
) -> None:
    """Cache a batch after redaction: model output is untrusted input."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_dir / f"{key}.tmp"
        payload = {
            "item_ids": sorted(str(item.get("item_id") or "") for item in items),
            "cases": cases,
        }
        tmp.write_text(
            json.dumps(redact_tree(payload, secrets), ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(cache_dir / f"{key}.json")
    except OSError:
        # A cache miss only costs one extra model call; never fail the plan.
        pass



def _generate_case_batch(
    client: Any,
    task_payload: dict[str, Any],
    batch: list[dict[str, Any]],
    context: dict[str, Any],
    feedback: str = "",
) -> list[dict[str, Any]]:
    """Generate one batch, retrying only once on unparseable output."""
    correction = feedback
    for attempt in range(2):
        call_context = dict(context)
        if correction:
            call_context["correction"] = correction
        try:
            return list(
                client.generate_cases(task_payload, batch, call_context)
            )
        except AiTestError as exc:
            if exc.info.code != "MODEL_OUTPUT_INVALID" or attempt == 1:
                raise
            correction = _generation_feedback(batch, context, exc.info.message)
    return []



def _generate_case_batches(
    client: Any,
    task_payload: dict[str, Any],
    items: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    cache_dir: Path,
    deadline: float,
    secrets: list[str],
    feedback: str = "",
) -> list[dict[str, Any]]:
    """Generate every batch with bounded concurrency and a wall-clock ceiling.

    Batches are independent by contract: each one only needs its own item ids.
    Running them in parallel keeps a 56-item plan inside the host timeout, and
    completed batches are cached so a retry after a timeout resumes instead of
    paying for the whole plan again. A repair round carries extra feedback, so
    it uses its own cache keys instead of the clean first-pass results.
    """
    size = _plan_batch_size()
    batches = [items[index : index + size] for index in range(0, len(items), size)]
    generated: dict[int, list[dict[str, Any]]] = {}
    pending: list[tuple[int, list[dict[str, Any]], str]] = []
    for index, batch in enumerate(batches):
        key = _batch_fingerprint(batch)
        if feedback:
            key = hashlib.sha256(
                f"{key}\n{feedback}".encode()
            ).hexdigest()
        else:
            cached = _read_cached_batch(cache_dir, key, batch)
            if cached is not None:
                generated[index] = cached
                continue
        pending.append((index, batch, key))
    if pending:
        workers = max(1, min(_plan_concurrency(), len(pending)))
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aitest-plan")
        total = len(batches)
        if generated:
            emit_progress(
                "plan.generate",
                f"已复用缓存 {len(generated)}/{total} 批用例",
                done=len(generated),
                total=total,
            )
        try:
            next_pos = 0
            active: dict[Future[list[dict[str, Any]]], tuple[int, list[dict[str, Any]], str]] = {}
            while next_pos < len(pending) and len(active) < workers:
                index, batch, key = pending[next_pos]
                next_pos += 1
                fut = executor.submit(
                    _generate_case_batch,
                    client,
                    task_payload,
                    batch,
                    context,
                    feedback,
                )
                active[fut] = (index, batch, key)
            while active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    done_count = len(generated)
                    raise AiTestError(
                        "MODEL_TIMEOUT",
                        f"计划生成超过 {_plan_budget_seconds():.0f} 秒上限"
                        f"（已完成 {done_count}/{total} 批）",
                        "重试会复用已完成批次；也可以缩小本次范围后重新生成",
                    )
                finished, _ = wait(
                    active.keys(),
                    timeout=max(0.1, remaining),
                    return_when=FIRST_COMPLETED,
                )
                if not finished:
                    done_count = len(generated)
                    raise AiTestError(
                        "MODEL_TIMEOUT",
                        f"计划生成超过 {_plan_budget_seconds():.0f} 秒上限"
                        f"（已完成 {done_count}/{total} 批）",
                        "重试会复用已完成批次；也可以缩小本次范围后重新生成",
                    )
                for fut in finished:
                    index, batch, key = active.pop(fut)
                    cases = fut.result()
                    generated[index] = cases
                    _write_cached_batch(cache_dir, key, batch, cases, secrets)
                    emit_progress(
                        "plan.generate",
                        f"已生成 {len(generated)}/{total} 批用例",
                        done=len(generated),
                        total=total,
                    )
                    if next_pos < len(pending):
                        n_idx, n_batch, n_key = pending[next_pos]
                        next_pos += 1
                        n_fut = executor.submit(
                            _generate_case_batch,
                            client,
                            task_payload,
                            n_batch,
                            context,
                            feedback,
                        )
                        active[n_fut] = (n_idx, n_batch, n_key)
        finally:
            # In-flight HTTP calls are left to their own socket timeout so the
            # command can return a clear error instead of blocking the lock.
            executor.shutdown(wait=False, cancel_futures=True)
    return [case for index in range(len(batches)) for case in generated.get(index, [])]



def _case_ids_in_message(cases: list[Case], message: str) -> set[str]:
    known = {case.case_id for case in cases}
    return {token for token in re.findall(r"case_[A-Za-z0-9_-]+", message) if token in known}



def _playwright_runner_prefix(
    root: Path, relative: str, *, has_explicit_root: bool = True
) -> tuple[str, str]:
    normalized = relative.replace("\\", "/").lstrip("./")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) > 1:
        comp = root / parts[0]
        sub_path = "/".join(parts[1:])
        if (comp / "package.json").is_file():
            use_bun = any(
                (base / lock).is_file()
                for base in (comp, root)
                for lock in ("bun.lock", "bun.lockb")
            )
            tool = "bunx" if use_bun else "npx"
            return f"cd {parts[0]} && {tool} playwright test {sub_path}", sub_path
        if not has_explicit_root and parts[0] == "frontend":
            return f"cd frontend && bunx playwright test {sub_path}", sub_path
    use_bun = any((root / lock).is_file() for lock in ("bun.lock", "bun.lockb"))
    tool = "bunx" if use_bun else "npx"
    return f"{tool} playwright test {normalized}", normalized



def _test_candidates(context: dict[str, Any]) -> list[str]:
    """Existing test commands the mapping step may choose from."""
    candidates: list[str] = []
    functions = context.get("test_functions")
    if isinstance(functions, dict):
        for path, names in functions.items():
            if not isinstance(names, list):
                continue
            for name in names:
                if name:
                    candidates.append(f"python -m pytest {path}::{name}")
    playwright = context.get("playwright_tests")
    if isinstance(playwright, dict):
        raw_root = str(context.get("root") or "").strip()
        root = Path(raw_root) if raw_root else Path(".")
        has_explicit_root = bool(raw_root)
        for path, titles in playwright.items():
            if not isinstance(titles, list):
                continue
            cmd_prefix, _ = _playwright_runner_prefix(
                root, str(path), has_explicit_root=has_explicit_root
            )
            for title in titles:
                if title:
                    safe_title = str(title).replace('"', '\\"')
                    candidates.append(f'{cmd_prefix} --grep "{safe_title}"')
    return candidates



def _apply_suggested_tests(
    cases: list[Case], suggested: dict[str, str]
) -> int:
    """Replace model-written verifications with the mapped existing test."""
    replaced = 0
    for case in cases:
        if not case.acceptance_item_id:
            continue
        command = suggested.get(case.acceptance_item_id)
        if not command:
            continue
        if (case.verify_method or "").startswith(("script:", "http:", "sql:")):
            case.verify_method = f"command:{command}"
            case.script_content = None
            case.script_name = None
            case.dependencies = []
            replaced += 1
    return replaced



def _merge_repaired_cases(
    cases: list[Case],
    raw_cases: list[dict[str, Any]],
    repair_ids: set[str],
) -> list[Case]:
    """Replace only the repaired items' cases and keep every other item/case intact."""
    repaired: list[Case] = []
    for entry in raw_cases:
        if not isinstance(entry, dict):
            continue
        case = Case.model_validate(entry)
        if set(case_item_ids(case)) & repair_ids:
            repaired.append(case)
    if not repaired:
        return cases
    kept: list[Case] = []
    for case in cases:
        ids = case_item_ids(case)
        if not (set(ids) & repair_ids):
            kept.append(case)
            continue
        remaining = [item_id for item_id in ids if item_id not in repair_ids]
        if remaining:
            clone = case.model_copy(deep=True)
            clone.acceptance_item_id = remaining[0]
            clone.acceptance_item_ids = remaining[1:]
            kept.append(clone)
    return kept + repaired



def cmd_plan_generate(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    task = _task(ws, payload)
    project = ws.load_project(task.project_id)
    scope_payload = payload.get("scope")
    if not isinstance(scope_payload, dict):
        raise invalid_request("scope 必须是对象")
    scope = Scope.model_validate(scope_payload)
    selected, applicable = _selected_items(task, scope, project)
    if not selected and (task.intent != "smoke" or applicable):
        raise AiTestError("SCOPE_EMPTY", "本次范围为空", "至少选择一条可用验收项")
    scope.acceptance_item_ids = list(selected)
    secret_values = known_secret_values([load_project_config(ws, project.project_id)])
    selected_items = [
        item.model_dump(mode="json")
        for item in task.acceptance_items
        if item.item_id in selected
    ]
    ctx_fn = facade_attr("_project_context", _project_context)
    if ctx_fn is _project_context:
        focus = [str(item.get("text") or "") for item in selected_items]
        context = _project_context(project, ws, focus_keywords=focus)
    else:
        context = ctx_fn(project, ws)
    context = redact_tree(context, secret_values)
    raw_cases = payload.get("cases")
    regenerate = None
    suggested: dict[str, str] = {}
    frozen_expectations: dict[str, dict[str, Any]] = {}
    blind_degraded = False
    if not isinstance(raw_cases, list) or not raw_cases:
        if task.intent == "smoke":
            raw_cases = _smoke_cases(task, selected, context)
            # 冒烟仅做项目级 L1 检查：清空业务验收项选择计数，避免与 S/M 口径混杂。
            selected = []
            scope.acceptance_item_ids = []
            applicable = 0
        else:
            client = facade_attr("build_client", build_client)(ws, task.project_id)
            if client is None:
                raise AiTestError(
                    "MODEL_UNAVAILABLE",
                    "未配置模型，无法生成计划",
                    "配置模型密钥，或在 payload 提供 cases",
                )
            task_payload = task.model_dump(mode="json")
            deadline = time.monotonic() + _plan_budget_seconds()
            source_fp = _workspace_source_fingerprint(Path(project.root))
            model_name = model_settings(ws, task.project_id)["name"]
            # 中文验收项和英文测试名靠词面匹配配不上，模型选择脚本；
            # 按工作区指纹+候选测试摘要+验收项文本缓存，代码改动立即失效。
            candidates = _test_candidates(context)
            test_summaries = context.get("test_summaries")
            mapper = getattr(client, "map_tests", None)
            if candidates and callable(mapper):
                map_key = hashlib.sha256(
                    json.dumps(
                        {
                            "source_fp": source_fp,
                            "items": [
                                {"item_id": i["item_id"], "text": i["text"]}
                                for i in selected_items
                            ],
                            "candidates": candidates,
                            "summaries": test_summaries or {},
                            "model": model_name,
                            "rules": _PLAN_CACHE_VERSION,
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest()
                map_cache = _map_tests_cache_path(ws, task.project_id, map_key)
                cached_map = _read_cached_map_tests(map_cache, candidates)
                if cached_map is not None:
                    suggested = cached_map
                else:
                    try:
                        map_items = [
                            {"item_id": item["item_id"], "text": item["text"]}
                            for item in selected_items
                        ]
                        if (
                            isinstance(test_summaries, dict)
                            and test_summaries
                            and len(inspect.signature(mapper).parameters) >= 3
                        ):
                            suggested = mapper(map_items, candidates, test_summaries)
                        else:
                            suggested = mapper(map_items, candidates)
                        _write_cached_map_tests(map_cache, suggested, secret_values)
                    except AiTestError:
                        suggested = {}
            for item in selected_items:
                command = suggested.get(item["item_id"])
                if command:
                    item["suggested_test"] = command
            blind_items = [item for item in selected_items if not item.get("suggested_test")]
            if blind_items and callable(getattr(client, "freeze_expectations", None)):
                contract_only_ctx = build_contract_only_context(context)
                frozen_expectations, blind_degraded = _freeze_expectations_with_cache(
                    ws,
                    task.project_id,
                    source_fp=source_fp,
                    model_name=model_name,
                    client=client,
                    task_payload=task_payload,
                    items=blind_items,
                    contract_context=contract_only_ctx,
                    secrets=secret_values,
                )
            case_gen_context = (
                {**context, "frozen_expectations": frozen_expectations}
                if frozen_expectations
                else context
            )
            context_digest = hashlib.sha256(
                json.dumps(
                    {
                        "api_routes": context.get("api_routes", []),
                        "routes": context.get("routes", []),
                        "test_summaries": test_summaries or {},
                        "services": context.get("services", {}),
                        "frozen_expectations": frozen_expectations,
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            # 缓存键纳入源码指纹、上下文摘要与项文本，确保修改业务代码或契约后立刻重新生成。
            cache_dir = _plan_cache_dir(
                ws,
                task.project_id,
                hashlib.sha256(
                    json.dumps(
                        {
                            "task": task.task_id,
                            "revision": task.revision,
                            "intent": task.intent,
                            "items": sorted(
                                (str(i["item_id"]), str(i["text"]))
                                for i in selected_items
                            ),
                            "model": model_name,
                            "rules": _PLAN_CACHE_VERSION,
                            "source_fp": source_fp,
                            "context": context_digest,
                            "mapping": sorted(suggested.items()),
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
            )

            def _normalize(item: dict[str, Any]) -> dict[str, Any]:
                depends = item.get("depends_on")
                first = depends[0] if isinstance(depends, list) and depends else None
                return {
                    "acceptance_item_id": item["acceptance_item_id"],
                    "acceptance_item_ids": item.get("acceptance_item_ids") or [],
                    "layer": item["layer"],
                    "title": item.get("title") or "",
                    "expected": item["expected"],
                    "expectation_source": item["expectation_source"],
                    "preconditions": item.get("preconditions") or [],
                    "steps": item.get("steps") or [],
                    "dependencies": item.get("dependencies") or [],
                    "mock_allowed": item.get("mock_allowed", True),
                    "importance": item.get("importance") or "medium",
                    "verify_method": _normalize_method(
                        str(item["verify_method"]), item.get("script_name")
                    ),
                    "depends_on": first if first in {"build", "service"} else None,
                    "script_name": item.get("script_name"),
                    "script_content": item.get("script_content"),
                    "frozen_expectation": (
                        item.get("frozen_expectation")
                        or frozen_expectations.get(str(item["acceptance_item_id"]))
                    ),
                }

            def _model_raw(
                feedback: str = "",
                items: list[dict[str, Any]] | None = None,
            ) -> list[dict[str, Any]]:
                subset = selected_items if items is None else items
                return [
                    _normalize(item)
                    for item in _generate_case_batches(
                        client,
                        task_payload,
                        subset,
                        case_gen_context,
                        cache_dir=cache_dir,
                        deadline=deadline,
                        secrets=secret_values,
                        feedback=feedback,
                    )
                ]

            regenerate = _model_raw
            raw_cases = _model_raw()
    cases = [
        Case.model_validate(item)
        for item in raw_cases
        if not isinstance(item, dict)
        or item.get("acceptance_item_id") in selected
        or item.get("acceptance_item_id") is None
    ]
    generated_cases = not isinstance(payload.get("cases"), list) or not payload.get("cases")
    django_dropped: list[str] = []
    command_merged = 0
    mapped_tests = 0
    if generated_cases and suggested:
        mapped_tests = _apply_suggested_tests(cases, suggested)
    cases, dropped_items = _drop_unavailable_django_scripts(cases, context)
    django_dropped.extend(dropped_items)
    cases, merged_now = _merge_duplicate_command_cases(cases)
    command_merged += merged_now
    if generated_cases and task.intent != "smoke" and regenerate is not None:
        unverified = _unverified_cases(cases, task.acceptance_items)
        if unverified:
            # A paraphrase or file path is not a substitute for the item text.
            # Repair only the affected items; regenerating all of them turns one
            # bad case into a plan-wide model pass and blows the host timeout.
            repair_ids = {
                item_id for case in unverified for item_id in case_item_ids(case)
            }
            repair_items = [
                item for item in selected_items if item.get("item_id") in repair_ids
            ]
            if repair_items:
                raw_cases = regenerate(
                    _expectation_feedback(
                        [
                            item
                            for item in task.acceptance_items
                            if item.item_id in repair_ids
                        ],
                        unverified,
                    ),
                    repair_items,
                )
                cases = _merge_repaired_cases(cases, raw_cases, repair_ids)
                cases, dropped_items = _drop_unavailable_django_scripts(cases, context)
                django_dropped.extend(dropped_items)
                cases, merged_now = _merge_duplicate_command_cases(cases)
                command_merged += merged_now
    if generated_cases and task.intent == "business" and regenerate is not None:
        # A test bound to many unrelated items proves none of them. Ask for
        # dedicated verification of the weaker bindings, keeping the best match.
        shared = _overshared_bindings(
            cases,
            context,
            {item.item_id: item.text for item in task.acceptance_items},
        )
        shared_items = [
            {k: v for k, v in item.items() if k != "suggested_test"}
            for item in selected_items
            if item.get("item_id") in shared
        ]
        if shared_items:
            raw_cases = regenerate(
                _generation_feedback(
                    shared_items,
                    context,
                    "\n".join(
                        f"- {item_id}：{reason}"
                        for item_id, reason in shared.items()
                    ),
                ),
                shared_items,
            )
            cases = _merge_repaired_cases(cases, raw_cases, set(shared))
            cases, dropped_items = _drop_unavailable_django_scripts(cases, context)
            django_dropped.extend(dropped_items)
            cases, merged_now = _merge_duplicate_command_cases(cases)
            command_merged += merged_now
    if generated_cases and task.intent == "business" and regenerate is not None:
        # Fix every "file without a test target" case in one round instead of
        # letting the validator surface them one per round.
        problems = _target_problems(cases, task.intent, context)
        repair_ids = {
            item_id
            for case, _reason in problems
            for item_id in case_item_ids(case)
        }
        repair_items = [
            item for item in selected_items if item.get("item_id") in repair_ids
        ]
        if problems and repair_items:
            raw_cases = regenerate(
                _generation_feedback(
                    repair_items,
                    context,
                    "\n".join(reason for _case, reason in problems),
                ),
                repair_items,
            )
            cases = _merge_repaired_cases(cases, raw_cases, repair_ids)
            cases, dropped_items = _drop_unavailable_django_scripts(cases, context)
            django_dropped.extend(dropped_items)
            cases, merged_now = _merge_duplicate_command_cases(cases)
            command_merged += merged_now
    downgraded_broad: list[str] = []
    multi_item_split = 0
    # Two targeted repairs; each one only re-generates the named cases.
    for attempt in range(3):
        if task.intent == "business":
            cases, split_now = _split_multi_item_cases(
                cases, task.acceptance_items
            )
            multi_item_split += split_now
        if generated_cases:
            _dedupe_script_names(cases)
        for case in cases:
            case.verify_method = _prefix_component_command(
                case.verify_method, Path(project.root)
            )
        try:
            _validate_plan_cases(
                cases, task.intent, context, strict_script_lint=generated_cases
            )
            preview = Plan(
                plan_id="preview",
                task_id=task.task_id,
                scope=scope,
                applicable_count=applicable,
                cases=cases,
            )
            _ensure_plan_coverage(preview, task)
            break
        except AiTestError as exc:
            if (
                regenerate is None
                or not generated_cases
                or exc.info.code != "INVALID_REQUEST"
            ):
                raise
            if attempt == 2:
                if "整包测试命令" not in exc.info.message:
                    raise
                downgraded_broad = _downgrade_broad_business_cases(cases)
                if not downgraded_broad:
                    raise
                _validate_plan_cases(
                    cases, task.intent, context, strict_script_lint=generated_cases
                )
                preview = Plan(
                    plan_id="preview",
                    task_id=task.task_id,
                    scope=scope,
                    applicable_count=applicable,
                    cases=cases,
                )
                _ensure_plan_coverage(preview, task)
                break
            # Give the model the validator's reason (at most twice) instead of
            # failing on a fixable script detail or repeated command. Repair only
            # the cases the validator named: a full re-pass multiplies both the
            # cost and the chance of a different case failing.
            named = _case_ids_in_message(cases, exc.info.message)
            repair_ids = {
                item_id
                for case in cases
                if case.case_id in named
                for item_id in case_item_ids(case)
            }
            repair_items = [
                item for item in selected_items if item.get("item_id") in repair_ids
            ]
            if repair_items:
                raw_cases = regenerate(
                    _generation_feedback(repair_items, context, exc.info.message),
                    repair_items,
                )
                cases = _merge_repaired_cases(cases, raw_cases, repair_ids)
            else:
                raw_cases = regenerate(
                    _generation_feedback(selected_items, context, exc.info.message)
                )
                cases = [
                    Case.model_validate(item)
                    for item in raw_cases
                    if not isinstance(item, dict)
                    or item.get("acceptance_item_id") in selected
                    or item.get("acceptance_item_id") is None
                ]
            cases, dropped_items = _drop_unavailable_django_scripts(cases, context)
            django_dropped.extend(dropped_items)
            cases, merged_now = _merge_duplicate_command_cases(cases)
            command_merged += merged_now
    cases.sort(
        key=lambda case: 0
        if "playwright" in case.verify_method.lower()
        or "cypress" in case.verify_method.lower()
        else 1
    )
    coverage_notes = (
        _annotate_case_coverage(cases, context) if generated_cases else {}
    )
    warnings = annotate_cases(cases, task.acceptance_items)
    for case in cases:
        note = coverage_notes.get(case.case_id)
        if note:
            case.warnings.append(note)
            warnings.append(f"{case.case_id}：{note}")
        if generated_cases and case.script_content:
            for issue in lint_script(case.script_name or "", case.script_content):
                if issue.severity == "warning":
                    case.warnings.append(issue.message)
                    warnings.append(f"{case.case_id}：{issue.message}")
    if downgraded_broad:
        warnings.append(
            f"{len(downgraded_broad)} 条整包测试命令已改为人工验证；"
            "项目没有可定位的具体测试，未计入自动通过"
        )
    if multi_item_split:
        warnings.append(
            f"已拆分 {multi_item_split} 条组合用例为逐项用例；"
            "预期来源使用验收项原文，逐项断言仍需人工确认"
        )
    if django_dropped:
        warnings.append(
            f"已移除 {len(set(django_dropped))} 条 Django TestCase 脚本用例；"
            "相关验收项并入命令用例，需人工确认覆盖"
        )
    if command_merged:
        warnings.append(
            f"已合并 {command_merged} 条重复命令用例；相关验收项由同一条命令覆盖"
        )
    if mapped_tests:
        warnings.append(
            f"已将 {mapped_tests} 条生成验证替换为映射到的项目现有测试"
        )
    if blind_degraded:
        warnings.append("盲写预期前置冻结未成功，已降级为单步生成")
    if frozen_expectations:
        for case in cases:
            if case.frozen_expectation is None and case.acceptance_item_id:
                frozen = frozen_expectations.get(case.acceptance_item_id)
                if frozen is not None:
                    case.frozen_expectation = dict(frozen)
    ensure_publishable(cases)
    _redact_cases(cases, secret_values)
    warnings.extend(_project_import_warnings(cases, project))
    warnings.extend(_shared_command_warnings(cases))
    plan = Plan(
        plan_id=new_id("plan"),
        task_id=task.task_id,
        plan_no=_next_plan_no(ws, task.project_id, task.task_id),
        task_revision=task.revision,
        task_contract=task_contract(task),
        created_at=iso_now(),
        scope=scope,
        applicable_count=applicable,
        rules_version_id=_text(payload, "rules_version_id") or project.rules_version_id or None,
        cases=cases,
        item_texts={
            item.item_id: item.text for item in task.acceptance_items if item.item_id in selected
        },
        warnings=warnings,
        status="published",
    )
    plan.unselected_by_module = _unselected_by_module(task, selected, project)
    plan.excluded_items = (
        []
        if task.intent == "smoke" and not selected and applicable == 0
        else _snapshot_excluded_items(task, selected)
    )
    plan.source_signature = _plan_signature(ws, project, task.task_id, plan.rules_version_id)
    write_model(_plan_path(ws, task.project_id, plan.plan_id), plan)
    scope_payload = scope.model_dump(mode="json")
    scope_payload["coverage"] = {"selected": len(selected), "applicable": applicable}
    ai_generated = not isinstance(payload.get("cases"), list) or not payload.get("cases")
    return {
        "plan_id": plan.plan_id,
        "plan_no": plan.plan_no,
        "task_revision": plan.task_revision,
        "created_at": plan.created_at,
        "revision": plan.revision,
        "status": plan.status,
        "intent": task.intent,
        "source_signature": plan.source_signature,
        "scope": scope_payload,
        "coverage": {"selected": len(selected), "applicable": applicable},
        "cases": len(cases),
        "case_count": len(cases),
        "ai_generated": ai_generated,
        "dependencies": _plan_dependencies(cases),
        "warnings": warnings,
        "unverified_cases": [case.case_id for case in cases if not case.expectation_verified],
    }



def _refresh_plan_annotations(
    ws: Workspace,
    project_id: str,
    plan: Plan,
    task: Task,
    context: dict[str, Any],
    *,
    had_coverage: bool,
) -> None:
    """脱敏用例后统一刷新覆盖标注、预期一致性与发布门槛校验。"""
    coverage_notes = (
        _annotate_case_coverage(plan.cases, context) if had_coverage else {}
    )
    _redact_cases(
        plan.cases,
        known_secret_values([load_project_config(ws, project_id)]),
    )
    plan.warnings = annotate_cases(plan.cases, task.acceptance_items)
    for case in plan.cases:
        note = coverage_notes.get(case.case_id)
        if note:
            case.warnings.append(note)
            plan.warnings.append(f"{case.case_id}：{note}")
    plan.warnings.extend(_shared_command_warnings(plan.cases))
    ensure_publishable(plan.cases)



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
    old_plan = plan.model_copy(deep=True)
    data = plan.model_dump(mode="json")
    if "cases" in patch:
        data["cases"] = patch["cases"]
    plan = Plan.model_validate(data)
    task = _task_by_id(ws, project.project_id, plan.task_id)
    current_contract = task_contract(task)
    if not plan.task_contract or plan.task_contract != current_contract:
        raise AiTestError(
            "PLAN_STALE",
            "验收项或验证方式已变化，当前计划不能再修改",
            "重新生成计划后再改用例",
        )
    context = facade_attr("_project_context", _project_context)(project, ws)
    _validate_plan_cases(plan.cases, task.intent, context)
    _ensure_plan_coverage(plan, task)
    had_coverage = any(
        case.coverage_kind is not None for case in old_plan.cases
    ) or any(case.coverage_kind is not None for case in plan.cases)
    _refresh_plan_annotations(
        ws, project.project_id, plan, task, context, had_coverage=had_coverage
    )
    revision_path = (
        ws.project_dir(project.project_id) / "plans" / "revisions" / plan.plan_id
    ) / f"{old_plan.revision}.json"
    write_model(revision_path, old_plan)
    plan.revision += 1
    plan.status = "published"
    plan.unselected_by_module = _unselected_by_module(
        task, list(plan.scope.acceptance_item_ids), project
    )
    plan.excluded_items = _snapshot_excluded_items(task, list(plan.scope.acceptance_item_ids))
    plan.source_signature = _plan_signature(ws, project, plan.task_id, plan.rules_version_id)
    write_model(_plan_path(ws, project.project_id, plan.plan_id), plan)
    return {
        "plan_id": plan.plan_id,
        "plan_no": plan.plan_no,
        "task_revision": plan.task_revision,
        "revision": plan.revision,
        "status": plan.status,
        "source_signature": plan.source_signature,
        "warnings": plan.warnings,
        "unverified_cases": [case.case_id for case in plan.cases if not case.expectation_verified],
    }



def cmd_plan_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    plan = _load_plan(ws, project.project_id, _text({"id": _require(payload, "plan_id")}, "id"))
    task = _task_by_id(ws, project.project_id, plan.task_id)
    current_contract = task_contract(task)
    current_signature = _plan_signature(ws, project, plan.task_id, plan.rules_version_id)
    if not plan.task_contract:
        stale = True
        stale_reason = "旧计划没有任务契约，需要重新生成"
    elif plan.task_contract != current_contract:
        stale = True
        stale_reason = "验收项或验证方式已变化，需要重新生成计划"
    elif not plan.source_signature or plan.source_signature != current_signature:
        stale = True
        stale_reason = "计划生成后项目、交付提交、规则或服务配置已变化，需要重新生成"
    else:
        stale = False
        stale_reason = ""
    return {
        "plan": plan.model_dump(mode="json"),
        "coverage": {
            "selected": len(plan.scope.acceptance_item_ids),
            "applicable": plan.applicable_count,
        },
        "stale": stale,
        "stale_reason": stale_reason,
        "task_updated": plan.task_revision != task.revision,
    }



def cmd_plan_publish(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    plan = _load_plan(ws, project.project_id, _text({"id": _require(payload, "plan_id")}, "id"))
    if plan.status == "published":
        raise AiTestError("RUN_STATE_CONFLICT", "计划已发布", "修改请用 plan.update")
    task = _task_by_id(ws, project.project_id, plan.task_id)
    context = facade_attr("_project_context", _project_context)(project, ws)
    _validate_plan_cases(plan.cases, task.intent, context)
    _ensure_plan_coverage(plan, task)
    had_coverage = any(case.coverage_kind is not None for case in plan.cases)
    _refresh_plan_annotations(
        ws, project.project_id, plan, task, context, had_coverage=had_coverage
    )
    plan.task_contract = task_contract(task)
    plan.task_revision = task.revision
    plan.source_signature = _plan_signature(ws, project, plan.task_id, plan.rules_version_id)
    plan.status = "published"
    plan.revision += 1
    write_model(_plan_path(ws, project.project_id, plan.plan_id), plan)
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "warnings": plan.warnings,
    }



