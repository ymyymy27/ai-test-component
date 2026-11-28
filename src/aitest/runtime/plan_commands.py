"""Runtime 命令模块：plan_commands。"""

from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import os
import re
import subprocess
import tempfile
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
from aitest.core.expectations import annotate_cases, ensure_publishable
from aitest.core.project_context import (
    build_capabilities_snapshot,
    fetch_runtime_openapi_routes,
)
from aitest.core.scope import task_contract
from aitest.core.script_linter import assess_assertion_strength, lint_script
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
    _merge_duplicate_command_cases,
    _overshared_bindings,
    _project_import_warnings,
    _shared_command_warnings,
    _smoke_cases,
    _split_multi_item_cases,
    _target_problems,
    _validate_plan_cases,
    merge_and_filter_reviews,
)
from aitest.security import redact, redact_tree
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
    if "http:" in reason:
        lines.append(
            "http: 必须给出非空 JSON 请求数组（每项含 method/url）；"
            "登录、token 提取等无法用 http 数组表达的流程，改用 script: 并提供 "
            "script_name 与完整 script_content。"
        )
    if "L1" in reason or "layer" in reason:
        lines.append("业务验收项一律使用 L2；L1 只用于项目级检查。")
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



_PLAN_CACHE_VERSION = 24


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
                    contextvars.copy_context().run,
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
                            contextvars.copy_context().run,
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



_INPUT_BOUNDARY_HINTS = (
    "请求体",
    "查询参数",
    "参数",
    "额外字段",
    "伪造",
    "忽略",
    "越权",
    "过期",
    "携带",
    "显式传",
    "不能修改",
    "不能删除",
)
_BOUNDARY_STOPWORDS = frozenset(
    {
        "api",
        "http",
        "https",
        "json",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "status",
        "response",
        "request",
        "user",
        "users",
        "admin",
        "auditor",
        "alice",
        "bob",
        "me",
        "login",
        "health",
        "document",
        "documents",
        "id",
        "url",
        "code",
    }
)
_PYTEST_NODE_RE = re.compile(r"python\s+-m\s+pytest\s+(?P<node>[^\s]+)")


def _boundary_terms(text: str) -> tuple[set[str], set[str]]:
    """从验收项提取必须在测试输入里出现的字段名。"""
    required = {
        match.group(0).lower()
        for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    }
    required -= _BOUNDARY_STOPWORDS
    alternatives: set[str] = set()
    if "过期" in text:
        alternatives |= {"exp", "expires_in", "expired"}
    if "租户" in text:
        required.add("tenant")
    if "角色" in text:
        required.add("role")
    return required, alternatives


def _summary_inputs_for_command(
    command: str, test_summaries: object
) -> str | None:
    if not isinstance(test_summaries, dict):
        return None
    match = _PYTEST_NODE_RE.search(command)
    if match is None:
        return None
    node = match.group("node")
    path, _, name = node.partition("::")
    normalized = {
        str(key).replace("\\", "/"): value for key, value in test_summaries.items()
    }
    entries = normalized.get(path.replace("\\", "/"))
    if not isinstance(entries, list):
        return None
    selected = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and (
            not name
            or str(entry.get("name") or "") == name
            or str(entry.get("name") or "").endswith(f"::{name}")
        )
    ]
    if not selected:
        return None
    return " ".join(
        " ".join(str(part) for part in entry.get("inputs") or [])
        for entry in selected
    )


_COMMAND_WRITE_RE = re.compile(
    r"(?i)(?:\b(?:POST|PATCH|PUT|DELETE)\b"
    r"|requests\.(?:post|patch|put|delete)\s*\("
    r"|\.(?:post|patch|put|delete)\s*\()"
)


def _command_mutates_state(command: str, test_summaries: object) -> bool:
    """命令类测试是否写状态；无法证明只读时按写状态处理。"""
    inputs = _summary_inputs_for_command(command, test_summaries)
    if inputs is None:
        return True
    return bool(_COMMAND_WRITE_RE.search(inputs))


def _guard_suggested_tests(
    suggested: dict[str, str],
    items: list[dict[str, Any]],
    test_summaries: object,
) -> dict[str, str]:
    """边界验收项必须有“实际构造该输入”的既有测试，否则退回生成脚本。"""
    texts = {
        str(item.get("item_id") or ""): str(item.get("text") or "")
        for item in items
    }
    counts: dict[str, int] = {}
    for command in suggested.values():
        counts[command] = counts.get(command, 0) + 1
    guarded: dict[str, str] = {}
    for item_id, command in suggested.items():
        if counts.get(command, 0) > 1:
            # 同一测试节点不能同时证明多个验收项的逐项断言；直接退回生成独立脚本。
            continue
        text = texts.get(item_id, "")
        if not any(hint in text for hint in _INPUT_BOUNDARY_HINTS):
            guarded[item_id] = command
            continue
        required, alternatives = _boundary_terms(text)
        if not required and not alternatives:
            guarded[item_id] = command
            continue
        inputs = _summary_inputs_for_command(command, test_summaries)
        if inputs is None:
            continue
        lowered = inputs.lower()
        if required and not all(term in lowered for term in required):
            continue
        if alternatives and not any(term in lowered for term in alternatives):
            continue
        guarded[item_id] = command
    return guarded



def _is_in_memory_mock_test(project_root: Path | None, command: str) -> bool:
    """识别使用纯内存 Mock App / TestClient 的单元测试，避免顶替真实服务集成验收。"""
    if project_root is None or not command:
        return False
    m = re.search(r"([A-Za-z0-9_./\\-]+\.py)(?:::\S+)?", command)
    if not m:
        return False
    rel = m.group(1).replace("\\", "/")
    target = project_root / rel
    if not target.is_file():
        return False
    try:
        text = target.read_text(encoding="utf-8", errors="replace")[:16384]
    except OSError:
        return False
    if "TestClient(" not in text:
        return False
    has_mock_app = ("create_mock_app" in text) or (
        "FastAPI(" in text and "from main import" not in text and "from app import" not in text
    )
    has_real_target = any(
        marker in text
        for marker in (
            "APP_BASE_URL",
            "BACKEND_BASE_URL",
            "http://127.0.0.1",
            "http://localhost",
        )
    )
    return has_mock_app and not has_real_target


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
    # 显式传入 acceptance_item_ids 且未显式传入 preset 时，自动按 selected 精确筛选
    if (
        "preset" not in scope_payload
        and isinstance(scope_payload.get("acceptance_item_ids"), list)
        and scope_payload.get("acceptance_item_ids")
    ):
        scope_payload = {**scope_payload, "preset": "selected"}
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
    project_config = load_project_config(ws, project.project_id)
    runtime_openapi_probe = ""
    if (
        isinstance(project_config.get("services"), dict)
        and project_config.get("services")
        and payload.get("runtime_introspect") is not False
    ):
        rt_routes, probe_path = fetch_runtime_openapi_routes(project_config, timeout=1.5)
        if rt_routes:
            runtime_openapi_probe = probe_path

            def _norm_rt(raw_p: str) -> str:
                return re.sub(r"\{[^}]+\}", "{param}", raw_p.strip().rstrip("/") or "/").lower()

            static_routes = list(context.get("api_routes") or [])
            static_by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for sr in static_routes:
                if isinstance(sr, dict) and sr.get("path"):
                    k = (str(sr.get("method") or "GET").upper(), _norm_rt(str(sr["path"])))
                    static_by_key.setdefault(k, sr)
            rt_keys: set[tuple[str, str]] = set()
            merged_rt: list[dict[str, Any]] = []
            for rr in rt_routes:
                k = (str(rr["method"]).upper(), _norm_rt(str(rr["path"])))
                rt_keys.add(k)
                merged_entry = dict(rr)
                sr = static_by_key.get(k)
                if sr is not None:
                    if sr.get("file"):
                        merged_entry["file"] = sr["file"]
                    if sr.get("line"):
                        merged_entry["line"] = sr["line"]
                merged_rt.append(merged_entry)
            unmounted = [
                sr
                for sr in static_routes
                if isinstance(sr, dict)
                and sr.get("path")
                and (str(sr.get("method") or "GET").upper(), _norm_rt(str(sr["path"])))
                not in rt_keys
            ]
            context = dict(context)
            context["api_routes"] = merged_rt
            if unmounted:
                context["unmounted_static_routes"] = unmounted

    capabilities = build_capabilities_snapshot(
        Path(project.root),
        project_config,
        project_context=context,
        runtime_introspect=False,
        secrets=secret_values,
    )
    if runtime_openapi_probe:
        capabilities["source"] = "runtime"
        ev = list(capabilities.get("evidence") or [])
        probe_ev = f"GET {runtime_openapi_probe}"
        if probe_ev not in ev:
            capabilities["evidence"] = [probe_ev, *ev][:24]
        routers = list(capabilities.get("router") or [])
        if "openapi" not in routers:
            capabilities["router"] = [*routers, "openapi"]
    recipes: list[dict[str, Any]] = []
    raw_cases = payload.get("cases")
    explicit_input = isinstance(raw_cases, list) and bool(raw_cases)
    used_deterministic_fallback = False
    regenerate = None
    suggested: dict[str, str] = {}
    if not explicit_input:
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
                    except AiTestError as exc:
                        if exc.info.code == "TOKEN_BUDGET_EXCEEDED":
                            raise
                        suggested = {}
            suggested = _guard_suggested_tests(
                suggested, selected_items, test_summaries
            )
            suggested = {
                iid: cmd
                for iid, cmd in suggested.items()
                if not _is_in_memory_mock_test(Path(project.root), cmd)
            }
            for item in selected_items:
                command = suggested.get(item["item_id"])
                if command:
                    item["suggested_test"] = command
            # 发送给模型的上下文剥离与 source_excerpts 重复的 routes 字段，消除双倍 Token 浪费
            case_gen_context = {k: v for k, v in context.items() if k != "routes"}
            context_digest = hashlib.sha256(
                json.dumps(
                    {
                        "api_routes": context.get("api_routes", []),
                        "routes": context.get("routes", []),
                        "test_summaries": test_summaries or {},
                        "services": context.get("services", {}),
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
                    "frozen_expectation": item.get("frozen_expectation"),
                    "generation": item.get("generation") or {"source": "model"},
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
        for item in (raw_cases if isinstance(raw_cases, list) else [])
        if not isinstance(item, dict)
        or item.get("acceptance_item_id") in selected
        or item.get("acceptance_item_id") is None
    ]
    generated_cases = not isinstance(payload.get("cases"), list) or not payload.get("cases")
    command_merged = 0
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
                cases,
                task.intent,
                context,
                strict_script_lint=generated_cases,
                collect_all=True,
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
            cases, merged_now = _merge_duplicate_command_cases(cases)
            command_merged += merged_now
    test_summaries = context.get("test_summaries")
    for case in cases:
        if case.mutates_state or not case.verify_method.startswith("command:"):
            continue
        command = case.verify_method[len("command:") :].strip()
        case.mutates_state = _command_mutates_state(command, test_summaries)
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
    if command_merged:
        warnings.append(
            f"已合并 {command_merged} 条重复命令用例；相关验收项由同一条命令覆盖"
        )
    ensure_publishable(cases)
    _redact_cases(cases, secret_values)
    for case in cases:
        if case.generation is None:
            if explicit_input:
                case.generation = {"source": "explicit"}
            elif task.intent == "smoke" or used_deterministic_fallback:
                case.generation = {
                    "source": "invariant",
                    "generator_id": "smoke-l1" if task.intent == "smoke" else "deterministic",
                    "generator_version": "1.0.0",
                }
            else:
                case.generation = {"source": "model"}
        case.assertion_strength = assess_assertion_strength(case)
    coverage_ledger: list[dict[str, Any]] = []
    warnings.extend(_project_import_warnings(cases, project))
    warnings.extend(_shared_command_warnings(cases))
    plan_signature = _plan_signature(ws, project, task.task_id)
    with ws.project_lock(project.project_id, timeout=30.0):
        plan = Plan(
            plan_id=new_id("plan"),
            task_id=task.task_id,
            plan_no=_next_plan_no(ws, task.project_id, task.task_id),
            task_revision=task.revision,
            task_contract=task_contract(task),
            created_at=iso_now(),
            scope=scope,
            applicable_count=applicable,
            cases=cases,
            item_texts={
                item.item_id: item.text
                for item in task.acceptance_items
                if item.item_id in selected
            },
            capabilities=capabilities,
            recipes=recipes,
            coverage_ledger=coverage_ledger,
            warnings=warnings,
            status="published",
        )
        plan.unselected_by_module = _unselected_by_module(task, selected, project)
        plan.excluded_items = (
            []
            if task.intent == "smoke" and not selected and applicable == 0
            else _snapshot_excluded_items(task, selected)
        )
        plan.source_signature = plan_signature
        write_model(_plan_path(ws, task.project_id, plan.plan_id), plan)
    scope_payload = scope.model_dump(mode="json")
    scope_payload["coverage"] = {"selected": len(selected), "applicable": applicable}
    ai_generated = (
        (not explicit_input)
        and (not used_deterministic_fallback)
        and task.intent != "smoke"
    )
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
        "capabilities": capabilities,
        "recipes": recipes,
        "coverage_ledger": coverage_ledger,
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
    for case in plan.cases:
        if case.generation is None:
            case.generation = {"source": "explicit"}
        case.assertion_strength = assess_assertion_strength(case)
    plan.coverage_ledger = []
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
        raw_cases = patch["cases"]
        if isinstance(raw_cases, list):
            normalized_cases: list[Any] = []
            for entry in raw_cases:
                if isinstance(entry, dict) and not str(entry.get("case_id") or "").strip():
                    normalized_cases.append({**entry, "case_id": new_id("case")})
                else:
                    normalized_cases.append(entry)
            data["cases"] = normalized_cases
        else:
            data["cases"] = raw_cases
    if "ai_reviews" in patch and isinstance(patch["ai_reviews"], list):
        data["ai_reviews"] = patch["ai_reviews"]
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
    plan.source_signature = _plan_signature(ws, project, plan.task_id)
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
    current_signature = _plan_signature(ws, project, plan.task_id)
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
    plan.source_signature = _plan_signature(ws, project, plan.task_id)
    plan.status = "published"
    plan.revision += 1
    write_model(_plan_path(ws, project.project_id, plan.plan_id), plan)
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "warnings": plan.warnings,
    }


def _plan_review_cache_path(ws: Workspace, project_id: str, input_hash: str) -> Path:
    return ws.root / ".cache" / "plan_reviews" / project_id / f"{input_hash[:24]}.json"


def cmd_plan_review(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """无锁只读双盲冲突与权限矩阵评审（绝不写 plan.json，绝不抢占排他写锁）。"""
    del confirm
    project = _project(ws, payload)
    plan_id = _text({"id": _require(payload, "plan_id")}, "id")
    plan = _load_plan(ws, project.project_id, plan_id)
    task = _task_by_id(ws, project.project_id, plan.task_id)
    secrets = known_secret_values([load_project_config(ws, project.project_id)])
    context = facade_attr("_project_context", _project_context)(project, ws)
    context = redact_tree(context, secrets)

    plan_cases_dump = [c.model_dump(mode="json") for c in plan.cases]
    selected_set = set(plan.scope.acceptance_item_ids)
    acceptance_items = [
        item.model_dump(mode="json")
        for item in task.acceptance_items
        if not selected_set or item.item_id in selected_set
    ]
    use_cache = bool(payload.get("use_cache", True))
    force_refresh = bool(payload.get("refresh") is True)
    input_hash = hashlib.sha256(
        json.dumps(
            {
                "plan_id": plan.plan_id,
                "plan_revision": plan.revision,
                "source_signature": plan.source_signature,
                "cases": [
                    (c.get("case_id"), c.get("verify_method"), c.get("assertion_strength"))
                    for c in plan_cases_dump
                ],
                "items": [(i.get("item_id"), i.get("text")) for i in acceptance_items],
                "routes": context.get("api_routes", []),
                "fe_calls": context.get("frontend_api_calls", []),
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    cache_path = _plan_review_cache_path(ws, project.project_id, input_hash)
    if use_cache and not force_refresh and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and isinstance(cached.get("reviews"), list):
                return {**cached, "cached": True}
        except (OSError, json.JSONDecodeError):
            pass

    det_reviews: list[dict[str, Any]] = []
    for case_dump in plan_cases_dump:
        if case_dump.get("assertion_strength") == "weak":
            cid = str(case_dump.get("case_id") or "")
            det_reviews.append(
                {
                    "review_id": f"rev_weak_{hashlib.sha256(cid.encode()).hexdigest()[:10]}",
                    "kind": "weak_assertion",
                    "severity": "medium",
                    "statement": f"用例 {cid} 断言强度偏弱（仅校验基础状态码或缺少业务字段断言）",
                    "evidence": [f"case:{cid}"],
                    "patch_cases": [],
                }
            )
    model_reviews: list[dict[str, Any]] = []
    ai_used = False
    client = facade_attr("build_client", build_client)(ws, project.project_id)
    reviewer = getattr(client, "review_plan", None) if client is not None else None
    if callable(reviewer):
        try:
            review_ctx = redact_tree(
                {
                    "task_title": task.title,
                    "acceptance_items": acceptance_items,
                    "cases": [
                        {
                            "case_id": c.get("case_id"),
                            "acceptance_item_id": c.get("acceptance_item_id"),
                            "title": c.get("title"),
                            "expected": c.get("expected"),
                            "verify_method": c.get("verify_method"),
                            "assertion_strength": c.get("assertion_strength"),
                            "frozen_expectation": c.get("frozen_expectation"),
                        }
                        for c in plan_cases_dump
                    ],
                    "api_routes": list(context.get("api_routes") or [])[:40],
                    "frontend_api_calls": list(context.get("frontend_api_calls") or [])[:40],
                },
                secrets,
            )
            raw = reviewer(review_ctx)
            if isinstance(raw, dict) and isinstance(raw.get("reviews"), list):
                model_reviews = raw["reviews"]
                ai_used = True
        except AiTestError:
            model_reviews = []

    reviews = merge_and_filter_reviews(det_reviews, model_reviews, plan_cases_dump)
    high_count = sum(1 for r in reviews if r.get("severity") == "high")
    result_payload = redact_tree(
        {
            "plan_id": plan.plan_id,
            "plan_revision": plan.revision,
            "input_hash": input_hash,
            "ai_used": ai_used,
            "cached": False,
            "high_severity_count": high_count,
            "reviews": reviews,
        },
        secrets,
    )
    tmp_path: Path | None = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{cache_path.name}.", dir=cache_path.parent
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(result_payload, ensure_ascii=False))
        os.replace(tmp_path, cache_path)
    except OSError:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
    return dict(result_payload)


def _latest_run_case_error(
    ws: Workspace, project_id: str, plan_id: str, case_id: str, run_id: str = ""
) -> str:
    """只读提取指定或最新运行中目标用例的失败输出，严禁修改任何历史 run 文件。"""
    from aitest.models import Run
    from aitest.storage.files import read_model

    runs_dir = ws.project_dir(project_id) / "runs"
    if not runs_dir.is_dir():
        return ""
    candidates: list[Path] = []
    if run_id:
        target = runs_dir / run_id / "run.json"
        if target.is_file():
            candidates = [target]
    else:
        for child in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime_ns, reverse=True):
            rpath = child / "run.json"
            if rpath.is_file():
                candidates.append(rpath)
    for rpath in candidates:
        try:
            run = read_model(rpath, Run)
        except AiTestError:
            continue
        if not run_id and run.plan_id != plan_id:
            continue
        for rc in run.cases:
            if rc.case_id == case_id and rc.attempts:
                last = rc.attempts[-1]
                return last.message or ""
    return ""


def _auto_select_repair_case_ids(
    ws: Workspace,
    project_id: str,
    plan: Plan,
    run_id: str,
    *,
    setup_only: bool,
) -> tuple[list[str], dict[str, str]]:
    """从指定 run_id 只读挑选可安全生成 setup 修订草稿的阻塞/构造错用例 ID 与错误摘要。"""
    from aitest.models import Run
    from aitest.storage.files import read_model

    run_path = ws.project_dir(project_id) / "runs" / run_id / "run.json"
    if not run_path.is_file():
        raise AiTestError("RUN_NOT_FOUND", f"运行不存在：{run_id}", "检查运行 ID")
    run = read_model(run_path, Run)
    by_case = {c.case_id: c for c in plan.cases}
    selected: list[str] = []
    hints: dict[str, str] = {}
    for rc in run.cases:
        plan_case = by_case.get(rc.case_id)
        if plan_case is None or not plan_case.verify_method.startswith(("script:", "http:")):
            continue
        last_att = rc.attempts[-1] if rc.attempts else None
        msg = (last_att.message if last_att is not None else "") or ""
        is_setup_hint = bool(
            (last_att is not None and last_att.stage == "setup")
            or "前置准备阶段（setup）" in msg
            or "引用了未定义变量" in msg
            or (
                isinstance(rc.judgement, dict)
                and rc.judgement.get("verdict") == "script_error"
            )
        )
        if setup_only:
            if rc.result in {"blocked", "error"} and is_setup_hint:
                selected.append(rc.case_id)
                hints[rc.case_id] = msg
        elif rc.result in {"blocked", "error", "failed"}:
            selected.append(rc.case_id)
            hints[rc.case_id] = msg
    return selected, hints


def cmd_plan_repair_case(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """预览或应用单条/批量 setup 修订草稿：preview 只读，apply 校验确认并升版。"""
    from aitest.worker_impl.execution import verify_setup_only_repair

    project = _project(ws, payload)
    plan_id = _text({"id": _require(payload, "plan_id")}, "id")
    mode = _text(payload, "mode", "preview").strip().lower()
    if mode not in {"preview", "apply"}:
        raise invalid_request("mode 必须是 preview 或 apply")
    if mode == "apply" and not confirm:
        raise AiTestError(
            "CONFIRMATION_REQUIRED",
            "应用修订草稿并更新计划需要显式确认（--confirm）",
            "先预览 diff，确认无误后带 --confirm 执行 apply",
        )

    plan = _load_plan(ws, project.project_id, plan_id)
    expected_rev = payload.get("expected_revision")
    if mode == "apply" and expected_rev is not None and expected_rev != plan.revision:
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            "计划修订号不匹配",
            "刷新后重试",
        )

    run_id = _text(payload, "run_id").strip()
    setup_only = bool(payload.get("setup_only", False))
    single_case_id = _text(payload, "case_id").strip()
    raw_case_ids = payload.get("case_ids")
    target_case_ids: list[str] = []
    auto_hints: dict[str, str] = {}
    is_batch = False

    if isinstance(raw_case_ids, list) and raw_case_ids:
        is_batch = True
        target_case_ids = [
            str(cid).strip() for cid in raw_case_ids if isinstance(cid, str) and cid.strip()
        ]
        if "setup_only" not in payload:
            setup_only = True
    elif single_case_id:
        target_case_ids = [single_case_id]
    elif run_id:
        is_batch = True
        if "setup_only" not in payload:
            setup_only = True
        target_case_ids, auto_hints = _auto_select_repair_case_ids(
            ws, project.project_id, plan, run_id, setup_only=setup_only
        )
        if not target_case_ids:
            return {
                "mode": mode,
                "ok": True,
                "all_ok": True,
                "blocked": False,
                "guard_blocked": [],
                "plan_id": plan.plan_id,
                "plan_revision": plan.revision,
                "items": [],
                "updated_case_ids": [],
                "rerun_acceptance_item_ids": [],
            }
    else:
        raise invalid_request("plan.repair_case 需要提供 case_id、case_ids 或 run_id")

    case_map = {c.case_id: c for c in plan.cases}
    for cid in target_case_ids:
        c = case_map.get(cid)
        if c is None:
            raise invalid_request(f"计划 {plan_id} 中不存在用例：{cid}")
        if not c.verify_method.startswith(("script:", "http:")):
            raise invalid_request(
                f"用例 {cid} 不是 script:/http: 类型，无法通过 plan.repair_case 修订"
            )
        if c.verify_method.startswith("script:") and not c.script_name:
            raise invalid_request(f"用例 {cid} 缺少 script_name，无法修订")

    config = load_project_config(ws, project.project_id)
    secrets = known_secret_values([config])
    raw_scripts = payload.get("repaired_scripts")
    repaired_scripts_map: dict[str, Any] = raw_scripts if isinstance(raw_scripts, dict) else {}
    raw_methods = payload.get("repaired_methods")
    repaired_methods_map: dict[str, Any] = raw_methods if isinstance(raw_methods, dict) else {}
    raw_cases_patch = payload.get("repaired_cases")
    repaired_cases_map: dict[str, Any] = (
        raw_cases_patch if isinstance(raw_cases_patch, dict) else {}
    )

    context_cache: dict[str, Any] | None = None
    client: Any = None
    items_preview: list[dict[str, Any]] = []
    pending_updates: dict[str, tuple[str | None, str | None]] = {}

    for cid in target_case_ids:
        case = case_map[cid]
        hint = (
            _text(payload, "error_hint").strip()
            or auto_hints.get(cid, "")
            or _latest_run_case_error(ws, project.project_id, plan.plan_id, cid, run_id)
        )
        raw_patch = repaired_cases_map.get(cid)
        case_patch: dict[str, Any] = raw_patch if isinstance(raw_patch, dict) else {}
        rep_expected = (
            str(case_patch["expected"])
            if "expected" in case_patch
            else (
                str(payload["expected"])
                if not is_batch and "expected" in payload
                else None
            )
        )
        rep_source = (
            str(case_patch["expectation_source"])
            if "expectation_source" in case_patch
            else (
                str(payload["expectation_source"])
                if not is_batch and "expectation_source" in payload
                else None
            )
        )

        cand_script: str | None = None
        cand_method: str | None = None
        if case.verify_method.startswith("script:"):
            raw_cand = (
                case_patch.get("script_content")
                or repaired_scripts_map.get(cid)
                or (payload.get("repaired_script") if not is_batch else None)
            )
            if isinstance(raw_cand, str) and raw_cand.strip():
                cand_script = raw_cand
            else:
                if client is None:
                    client = facade_attr("build_client", build_client)(ws, project.project_id)
                if client is None or not hasattr(client, "repair_script"):
                    raise AiTestError(
                        "MODEL_UNAVAILABLE",
                        "未配置模型且未提供修复候选，无法生成修订草稿",
                        "配置模型密钥或在请求中直接提供 repaired_script/repaired_scripts",
                    )
                if context_cache is None:
                    context_cache = redact_tree(
                        facade_attr("_project_context", _project_context)(project, ws),
                        secrets,
                    )
                role_hint = ""
                roles_cfg = config.get("test_roles")
                if isinstance(roles_cfg, dict) and roles_cfg:
                    role_hint = "\n可用角色配置键：" + json.dumps(
                        redact_tree(roles_cfg, secrets), ensure_ascii=False
                    )
                cand_script = client.repair_script(
                    item_text=case.expectation_source or case.expected or case.title,
                    expected=case.expected,
                    script=case.script_content or "",
                    error=redact(
                        (hint or "请仅修复 setup 前置步骤，保持 verify 断言不变") + role_hint,
                        secrets,
                    ),
                )
        else:
            raw_method = (
                case_patch.get("verify_method")
                or repaired_methods_map.get(cid)
                or (payload.get("repaired_verify_method") if not is_batch else None)
            )
            if isinstance(raw_method, str) and raw_method.strip():
                cand_method = raw_method
            elif isinstance(raw_method, list):
                cand_method = "http:" + json.dumps(raw_method, ensure_ascii=False)
            else:
                if client is None:
                    client = facade_attr("build_client", build_client)(ws, project.project_id)
                if client is None or not hasattr(client, "repair_script"):
                    raise AiTestError(
                        "MODEL_UNAVAILABLE",
                        "未配置模型且未提供 HTTP 修复候选，无法生成修订草稿",
                        "配置模型密钥或在请求中直接提供 repaired_methods",
                    )
                cand_method = client.repair_script(
                    item_text=case.expectation_source or case.expected or case.title,
                    expected=case.expected,
                    script=case.verify_method,
                    error=redact(
                        hint
                        or "请仅修正 http: 中 stage=='setup' 的请求参数，保持 assert 请求不变",
                        secrets,
                    ),
                )

        val_script, val_method, guards, unified_diff = verify_setup_only_repair(
            case,
            repaired_script=cand_script,
            repaired_verify_method=cand_method,
            repaired_expected=rep_expected,
            repaired_expectation_source=rep_source,
            setup_only=setup_only,
        )
        safe_diff = redact(unified_diff, secrets)
        item_ok = not guards and (val_script is not None or val_method is not None)
        if item_ok:
            pending_updates[cid] = (val_script, val_method)
        items_preview.append(
            {
                "case_id": cid,
                "acceptance_item_ids": case_item_ids(case),
                "script_name": case.script_name or "",
                "ok": item_ok,
                "blocked": bool(guards),
                "guard_blocked": guards,
                "unified_diff": safe_diff,
                "repaired_script": redact(val_script or cand_script or "", secrets),
                "repaired_verify_method": redact(val_method or cand_method or "", secrets),
                "error_hint": redact(hint, secrets),
            }
        )

    all_ok = all(item["ok"] for item in items_preview)
    all_guards = [g for item in items_preview for g in item["guard_blocked"]]
    rerun_items = list(
        dict.fromkeys(
            iid
            for item in items_preview
            if item["ok"]
            for iid in item["acceptance_item_ids"]
            if iid in plan.scope.acceptance_item_ids
        )
    )

    first = items_preview[0]
    merged_diff = "\n".join(i["unified_diff"] for i in items_preview if i["unified_diff"])
    if mode == "preview":
        resp: dict[str, Any] = {
            "mode": "preview",
            "ok": all_ok,
            "all_ok": all_ok,
            "blocked": not all_ok,
            "guard_blocked": list(dict.fromkeys(all_guards)),
            "plan_id": plan.plan_id,
            "plan_revision": plan.revision,
            "setup_only": setup_only,
            "items": items_preview,
            "rerun_acceptance_item_ids": rerun_items,
            "case_id": first["case_id"],
            "script_name": first["script_name"],
            "unified_diff": merged_diff,
            "repaired_script": first["repaired_script"],
            "error_hint": first["error_hint"],
        }
        return resp

    if not all_ok:
        raise invalid_request(
            f"修订草稿未通过安全守卫拦截：{', '.join(dict.fromkeys(all_guards))}",
            "保持 verify()/断言步骤/预期来源完全一致且通过静态安检后重试",
        )

    # 无锁计算验证上下文（若尚未加载）
    if context_cache is None:
        context_cache = facade_attr("_project_context", _project_context)(project, ws)
    task = _task_by_id(ws, project.project_id, plan.task_id)

    # 毫秒级项目写锁临界区：复核 expected_revision、归档旧版、落盘新修订
    with ws.project_lock(project.project_id, timeout=30.0):
        latest_plan = _load_plan(ws, project.project_id, plan_id)
        if expected_rev is not None and expected_rev != latest_plan.revision:
            raise AiTestError("RUN_STATE_CONFLICT", "计划修订号已变化", "刷新后重试")
        old_plan = latest_plan.model_copy(deep=True)
        for c in latest_plan.cases:
            upd = pending_updates.get(c.case_id)
            if upd is None:
                continue
            val_script, val_method = upd
            if val_script is not None:
                c.script_content = val_script
            if val_method is not None:
                c.verify_method = val_method
        _validate_plan_cases(latest_plan.cases, task.intent, context_cache)
        _ensure_plan_coverage(latest_plan, task)
        had_coverage = any(c.coverage_kind is not None for c in old_plan.cases)
        _refresh_plan_annotations(
            ws, project.project_id, latest_plan, task, context_cache, had_coverage=had_coverage
        )
        revision_path = (
            ws.project_dir(project.project_id) / "plans" / "revisions" / latest_plan.plan_id
        ) / f"{old_plan.revision}.json"
        write_model(revision_path, old_plan)
        latest_plan.revision += 1
        latest_plan.status = "published"
        latest_plan.source_signature = _plan_signature(ws, project, latest_plan.task_id)
        write_model(_plan_path(ws, project.project_id, latest_plan.plan_id), latest_plan)

    return {
        "mode": "apply",
        "ok": True,
        "all_ok": True,
        "blocked": False,
        "guard_blocked": [],
        "plan_id": latest_plan.plan_id,
        "plan_revision": latest_plan.revision,
        "previous_revision": old_plan.revision,
        "setup_only": setup_only,
        "updated_case_ids": list(pending_updates),
        "rerun_acceptance_item_ids": rerun_items,
        "items": items_preview,
        "case_id": first["case_id"],
        "script_name": first["script_name"],
        "unified_diff": "\n".join(i["unified_diff"] for i in items_preview if i["unified_diff"]),
        "acceptance_item_ids": rerun_items or first["acceptance_item_ids"],
    }



