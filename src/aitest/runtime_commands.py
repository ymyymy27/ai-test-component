"""Plan, run, evidence, report, issue, backup and cleanup commands."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from aitest.commands import (
    MUTATING,
    REGISTRY,
    Payload,
    _project,
    _require,
    _task,
    _text,
    cmd_delivery_show,
    cmd_project_config,
    cmd_project_map,
    cmd_project_show,
    cmd_rules_list,
    cmd_rules_show,
    cmd_task_list,
    cmd_task_show,
)
from aitest.config import github_token, known_secret_values, model_key
from aitest.core.analysis import analyze_run
from aitest.core.evidence import (
    data_prefix,
    evidence_bytes,
    evidence_path,
    save_evidence,
    settings_from_config,
)
from aitest.core.expectations import annotate_cases, ensure_publishable, expectation_matches
from aitest.core.issues import add_issue, ensure_run_issues
from aitest.core.link_location import locate_link
from aitest.core.manual import aggregate_manual, is_manual_case
from aitest.core.project_context import build_project_context, python_dependencies
from aitest.core.reports import build_report
from aitest.core.scope import applicable_items, task_contract, uncovered_items
from aitest.core.verdict import case_results, coverage, item_outcomes, verdict
from aitest.errors import AiTestError, invalid_path, invalid_request
from aitest.model_config import build_client, load_project_config
from aitest.models import (
    AcceptanceItem,
    Attempt,
    Case,
    Confirmation,
    Delivery,
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
    case_item_ids,
    new_id,
)
from aitest.procutil import pid_alive
from aitest.runners.package_install import install_packages
from aitest.security import redact, redact_bytes, redact_tree, scan_script
from aitest.storage.files import read_json, read_model, write_json, write_model
from aitest.storage.workspace import Workspace, iso_now


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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
    if not selected and (task.intent != "smoke" or applicable_items(task)):
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
    derived = build_project_context(root, config)
    for directory in derived["test_dirs"]:
        if directory not in files:
            files.append(directory)
    secrets = known_secret_values([config])
    example = derived.get("test_example")
    return {
        "root": str(root),
        "routes": [redact(item, secrets) for item in sources],
        "files": files,
        "readme": redact(excerpt, secrets),
        "services": redact_tree(derived["services"], secrets),
        "runners": redact_tree(config.get("runners", {}), secrets),
        "l1": redact_tree(config.get("l1", {}), secrets),
        "test_commands": derived["test_commands"],
        "test_modules": derived.get("test_modules", []),
        "test_functions": derived.get("test_functions", {}),
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
    """Run tool commands from the subproject that owns their config file."""

    if not method.startswith("command:"):
        return method
    command = method[len("command:") :].strip()
    if not re.match(r"^alembic(?:\s|$)", command, re.IGNORECASE):
        return method
    if re.search(r"\s-c(?:\s|$)", command):
        return method
    try:
        children = sorted(root.iterdir())
    except OSError:
        return method
    for child in children:
        if child.is_dir() and (child / "alembic.ini").is_file():
            return f"command:cd {child.name} && {command}"
    return method


# -- plan generation guards -------------------------------------------------

_EXAMPLE_HOSTS = (
    "example.com",
    "example.org",
    "example.net",
    "httpbin.org",
    "jsonplaceholder.typicode.com",
)


def _invalid_method(case: Case, message: str) -> AiTestError:
    return invalid_request(f"用例 {case.case_id} 验证方式不合法：{message}")


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


def _validate_verify_method(case: Case) -> None:
    """Reject malformed verify methods before a plan can be stored or run."""
    method = (case.verify_method or "").strip()
    if not method:
        raise _invalid_method(case, "验证方式不能为空")
    if method == "manual" or method.startswith("manual:"):
        return
    if method.startswith("command:"):
        command = method[len("command:") :].strip()
        if not command:
            raise _invalid_method(case, "command: 后不能为空")
        matches = scan_script(command)
        if matches:
            raise _invalid_method(case, f"命令包含危险操作：{', '.join(matches)}")
        return
    if method.startswith("script:"):
        name = (case.script_name or "").strip()
        if not name:
            raise _invalid_method(case, "script: 需要 script_name")
        if not _safe_script_name(name):
            raise _invalid_method(
                case, "script_name 只能是 generated/ 下的单层文件名（.py/.ts/.js）"
            )
        if not (case.script_content or "").strip():
            raise _invalid_method(case, "script: 需要 script_content")
        matches = scan_script(case.script_content or "")
        if matches:
            raise _invalid_method(case, f"脚本包含危险操作：{', '.join(matches)}")
        return
    if method.startswith("http:"):
        raw = method[len("http:") :].strip()
        try:
            requests = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _invalid_method(case, "http: 后必须是 JSON 数组") from exc
        if not isinstance(requests, list) or not requests:
            raise _invalid_method(case, "http: 后必须是非空 JSON 数组")
        for item in requests:
            if not isinstance(item, dict):
                raise _invalid_method(case, "http: 每项必须是对象")
            for key in ("method", "url"):
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise _invalid_method(case, f"http: 每项需要非空 {key}")
        return
    if method.startswith("playwright:"):
        rest = method[len("playwright:") :].strip()
        if not rest:
            raise _invalid_method(case, "playwright: 后需要脚本路径")
        matches = scan_script(rest)
        if matches:
            raise _invalid_method(case, f"命令包含危险操作：{', '.join(matches)}")
        if any(char in rest for char in "\"'`$;&|<>"):
            raise _invalid_method(case, "playwright: 脚本路径包含不安全的 shell 字符")
        script_path = Path(rest)
        if script_path.is_absolute() or ".." in script_path.parts:
            raise _invalid_method(case, "playwright: 脚本路径必须是项目内的相对路径")
        return
    for prefix in ("sql:", "junit:", "pytest:"):
        if method.startswith(prefix):
            rest = method[len(prefix) :].strip()
            if not rest:
                raise _invalid_method(case, f"{prefix} 后不能为空")
            matches = scan_script(rest)
            if matches:
                raise _invalid_method(case, f"命令包含危险操作：{', '.join(matches)}")
            return
    raise _invalid_method(case, f"未知验证方式前缀：{method.split(':', 1)[0]}")


def _plan_dependencies(cases: list[Case]) -> list[str]:
    """Only Python requirements are auto-installed; Node deps belong to npm/bun."""

    return sorted(
        python_dependencies(
            [dependency for case in cases for dependency in case.dependencies]
        )
    )


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


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _command_matches(command: str, allowed: list[str]) -> bool:
    normalized = " ".join(command.split())
    for item in allowed:
        candidate = " ".join(item.split())
        if not candidate:
            continue
        if normalized == candidate:
            return True
        if normalized.startswith(candidate + " ") or candidate.startswith(normalized + " "):
            return True
    return False


def _script_example_hosts(content: str) -> list[str]:
    hosts: list[str] = []
    for match in re.finditer(r"https?://([^/\s\"']+)", content):
        host = match.group(1).split(":", 1)[0].lower()
        if any(host == item or host.endswith(f".{item}") for item in _EXAMPLE_HOSTS):
            hosts.append(host)
    return hosts


def _allowed_http_hosts(context: dict[str, Any]) -> set[str]:
    hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    services = context.get("services")
    if not isinstance(services, dict):
        return hosts
    entries: list[object] = (
        [services] if {"start", "health", "stop"} & set(services) else list(services.values())
    )
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in ("health_url", "health"):
            raw = entry.get(key)
            if not isinstance(raw, str) or "://" not in raw:
                continue
            host = urlsplit(raw).hostname
            if host:
                hosts.add(host.lower())
    return hosts


def _http_requests(case: Case) -> list[dict[str, Any]]:
    method = case.verify_method.strip()
    requests = json.loads(method[len("http:") :])
    return [item for item in requests if isinstance(item, dict)]


def _validate_business_http(case: Case, allowed_hosts: set[str]) -> None:
    if not case.verify_method.strip().startswith("http:"):
        return
    for request in _http_requests(case):
        url = str(request.get("url") or "").strip()
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or not host:
            raise invalid_request(f"用例 {case.case_id} 的 http 地址不是被测服务：{url}")
        if host not in allowed_hosts:
            raise invalid_request(
                f"用例 {case.case_id} 的 http 地址不是被测服务：{url}",
                "使用 services.health 的 host/port 或本机项目地址",
            )


def _validate_smoke_case(case: Case, allowed_commands: list[str]) -> None:
    if case.layer != "L1":
        raise invalid_request(f"用例 {case.case_id} 在冒烟目标下必须是 L1")
    method = case.verify_method.strip()
    if method.startswith(("http:", "playwright:", "script:", "manual", "sql:", "junit:")):
        raise invalid_request(f"用例 {case.case_id} 在冒烟目标下只允许 command: 或 pytest:")
    if method.startswith("command:"):
        command = method[len("command:") :].strip()
        if not _command_matches(command, allowed_commands):
            raise invalid_request(
                f"用例 {case.case_id} 的命令不是项目已有测试命令：{command}",
                "只使用 context.test_commands 或 l1 配置中的命令",
            )
        return
    if method.startswith("pytest:"):
        if not any("pytest" in item for item in allowed_commands):
            raise invalid_request(f"用例 {case.case_id} 使用了未配置的 pytest 命令")
        return
    raise _invalid_method(case, "冒烟目标只允许 command: 或 pytest:")


_SAFE_DEPENDENCY = re.compile(
    r"^@?[A-Za-z0-9][A-Za-z0-9_.\-]*"
    r"(?:/[A-Za-z0-9][A-Za-z0-9_.\-]*)?"
    r"(?:\[[A-Za-z0-9,._\-]+\])?"
    r"(?:[<>=!~][A-Za-z0-9.*+!<>=~,\-]{1,60})?$"
)


def _validate_dependencies(case: Case) -> None:
    for dependency in case.dependencies:
        if not _SAFE_DEPENDENCY.fullmatch(dependency):
            raise invalid_request(
                f"用例 {case.case_id} 的依赖名不合法：{dependency}",
                "依赖名只允许包名与版本约束字符，不接受命令或多条参数",
            )


def _broad_test_command(method: str) -> str:
    """Return a normalized whole-suite command, or an empty string.

    Repeating the same whole-suite command for every acceptance item turns a
    large project's plan into dozens of identical hour-long runs.
    """

    if not method.startswith(("command:", "pytest:")):
        return ""
    value = " ".join(method.split(":", 1)[1].split())
    # Directory prefixes must not hide a whole-suite command.
    while re.match(r"^(?:cd|pushd)\s+[^&;]+\s*(?:&&|;)\s*", value):
        value = re.sub(r"^(?:cd|pushd)\s+[^&;]+\s*(?:&&|;)\s*", "", value, count=1)
    whole_suite = {
        "python -m pytest",
        "pytest",
        "uv run pytest",
        "python runtests.py",
        "python manage.py test",
        "npm test",
        "npm run test",
        "yarn test",
        "yarn run test",
        "pnpm test",
        "pnpm run test",
        "bun test",
        "bun run test",
        "tox",
        "npx playwright test",
        "bunx playwright test",
        "pnpm exec playwright test",
    }
    if re.fullmatch(r"(?:python(?:3)?\s+-m\s+)?pytest(?:\s+-\S+)*", value):
        return value
    return value if value in whole_suite else ""


def _drop_unavailable_django_scripts(
    cases: list[Case], context: dict[str, Any]
) -> tuple[list[Case], list[str]]:
    """Remove Django TestCase scripts when the project has no pytest-django.

    Such scripts cannot create a test database through pytest, so they would
    fail for environment reasons. Their items are attached to the remaining
    command case and the plan carries a warning.
    """

    frameworks = set(_string_list(context.get("framework")))
    if "django" not in frameworks or "pytest" in frameworks:
        return cases, []
    dropped: list[str] = []
    kept: list[Case] = []
    for case in cases:
        if case.script_content and "django.test" in case.script_content:
            dropped.extend(case_item_ids(case))
            continue
        kept.append(case)
    if not dropped:
        return cases, []
    if not kept:
        # Dropping every case would leave selected items uncovered; let the
        # validator ask the model to replace the scripts instead.
        return cases, []
    target = next(
        (case for case in kept if case.verify_method.startswith("command:")),
        kept[0],
    )
    target.acceptance_item_ids = list(
        dict.fromkeys([*(target.acceptance_item_ids or []), *dropped])
    )
    return kept, dropped


def _merge_duplicate_command_cases(cases: list[Case]) -> tuple[list[Case], int]:
    """Merge identical command cases into one case bound to all their items."""

    merged: list[Case] = []
    by_key: dict[
        tuple[str, str, str, str, str, tuple[str, ...], tuple[str, ...]], Case
    ] = {}
    removed = 0
    for case in cases:
        if not case.verify_method.startswith(("command:", "pytest:")):
            merged.append(case)
            continue
        key = (
            case.layer,
            case.depends_on or "",
            case.expected,
            case.expectation_source,
            " ".join(case.verify_method.split(":", 1)[1].split()),
            tuple(case.dependencies),
            tuple(case_item_ids(case)),
        )
        target = by_key.get(key)
        if target is None:
            by_key[key] = case
            merged.append(case)
            continue
        target.acceptance_item_ids = list(
            dict.fromkeys(
                [
                    *(target.acceptance_item_ids or []),
                    *([case.acceptance_item_id] if case.acceptance_item_id else []),
                    *case.acceptance_item_ids,
                ]
            )
        )
        removed += 1
    return merged, removed


def _shared_command_warnings(cases: list[Case]) -> list[str]:
    """Warn when one test-file command is treated as proof for several items."""

    bound: dict[str, set[str]] = {}
    for case in cases:
        if not case.verify_method.startswith(("command:", "pytest:")):
            continue
        command = " ".join(case.verify_method.split(":", 1)[1].split())
        items = bound.setdefault(command, set())
        items.update(case_item_ids(case))
    warnings: list[str] = []
    for command, items in bound.items():
        if len(items) < 2:
            continue
        warnings.append(
            f"同一命令覆盖 {len(items)} 个验收项：{command}；"
            "执行结果是文件级通过，逐项断言需人工确认"
        )
        if len(warnings) >= 8:
            break
    return warnings


def _downgrade_broad_business_cases(cases: list[Case]) -> list[str]:
    """Turn an unresolvable whole-suite business case into manual verification."""

    downgraded: list[str] = []
    for case in cases:
        broad = _broad_test_command(case.verify_method)
        if not broad or case.layer == "L1":
            continue
        case.verify_method = "manual"
        case.layer = "L3"
        case.steps = [*case.steps, f"人工执行并核对：{broad}"]
        downgraded.append(broad)
    return downgraded


def _split_multi_item_cases(
    cases: list[Case], items: list[AcceptanceItem]
) -> tuple[list[Case], int]:
    """Give every business item its own case with an exact expectation source."""

    by_id = {item.item_id: item for item in items}
    result: list[Case] = []
    split_count = 0
    for case in cases:
        ids = [item_id for item_id in case_item_ids(case) if item_id in by_id]
        if len(ids) < 2:
            if case.acceptance_item_ids:
                # Unknown or already-single extras must not leave a multi-item
                # business case behind.
                case.acceptance_item_ids = []
            result.append(case)
            continue
        split_count += 1
        for index, item_id in enumerate(ids):
            clone = case.model_copy(deep=True)
            clone.case_id = (
                case.case_id
                if index == 0
                else f"{case.case_id[:110]}_{index + 1}"
            )
            clone.acceptance_item_id = item_id
            clone.acceptance_item_ids = []
            clone.expectation_source = by_id[item_id].text
            clone.warnings = []
            result.append(clone)
    return result, split_count


_TEST_NODE_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.py)::([A-Za-z0-9_:]+)"
)
_TEST_FILE_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|jsx))\b"
)
_PLAYWRIGHT_NODE_RE = re.compile(
    r"([A-Za-z0-9_./\\-]+\.(?:ts|tsx|js|jsx))\s+--grep[= ]+[\"']([^\"']+)[\"']"
)
_BUN_TEST_FILE_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.test\.tsx?)\b")


def _mapped_test_value(mapping: object, path: str) -> object:
    if not isinstance(mapping, dict):
        return None
    if path in mapping:
        return mapping[path]
    for key, value in mapping.items():
        if isinstance(key, str) and (
            key.endswith("/" + path) or path.endswith("/" + key)
        ):
            return value
    return None


def _annotate_case_coverage(
    cases: list[Case], context: dict[str, Any]
) -> dict[str, str]:
    """Classify each case's evidence strength, not just whether it ran."""

    functions = context.get("test_functions")
    function_map = functions if isinstance(functions, dict) else {}
    node_items: dict[str, set[str]] = {}
    notes: dict[str, str] = {}
    for case in cases:
        method = case.verify_method.strip()
        case.test_nodes = []
        if is_manual_case(case):
            case.coverage_kind = "manual"
            continue
        if method.startswith(("script:", "http:", "junit:", "sql:")):
            case.coverage_kind = "direct"
            continue
        if method.startswith("playwright:"):
            command = method.split(":", 1)[1]
            pw_match = _PLAYWRIGHT_NODE_RE.search(command)
            if pw_match:
                path = pw_match.group(1).replace("\\", "/").lstrip("./")
                title = pw_match.group(2)
                configured = context.get("playwright_tests")
                titles = _mapped_test_value(configured, path)
                if isinstance(titles, list) and title in titles:
                    case.coverage_kind = "direct"
                    case.test_nodes = [f"{path}::{title}"]
                    node_items.setdefault(f"{path}::{title}", set()).update(
                        case_item_ids(case)
                    )
                    continue
                case.coverage_kind = "unverified"
                notes[case.case_id] = f"Playwright 测试标题不存在：{path}::{title}"
                continue
            case.coverage_kind = "file"
            notes[case.case_id] = (
                "文件级覆盖：Playwright 脚本包含多个断言，未定位到单个测试"
            )
            continue
        if not method.startswith(("command:", "pytest:")):
            case.coverage_kind = "unverified"
            notes[case.case_id] = "未识别的验证方式，无法确认覆盖强度"
            continue
        command = method.split(":", 1)[1]
        if "bun test" in command:
            bun_match = _BUN_TEST_FILE_RE.search(command)
            if bun_match:
                path = bun_match.group(1).replace("\\", "/").lstrip("./")
                titles = _mapped_test_value(context.get("bun_tests"), path)
                if isinstance(titles, list) and len(titles) == 1:
                    case.coverage_kind = "direct"
                    case.test_nodes = [f"{path}::{titles[0]}"]
                    node_items.setdefault(f"{path}::{titles[0]}", set()).update(
                        case_item_ids(case)
                    )
                else:
                    case.coverage_kind = "file"
                    notes[case.case_id] = (
                        "文件级覆盖：Bun 测试文件包含多个测试，未定位到单个测试"
                    )
                continue
        pw_match = _PLAYWRIGHT_NODE_RE.search(command)
        if pw_match:
            path = pw_match.group(1).replace("\\", "/").lstrip("./")
            title = pw_match.group(2)
            configured = context.get("playwright_tests")
            titles = _mapped_test_value(configured, path)
            if isinstance(titles, list) and title in titles:
                case.coverage_kind = "direct"
                case.test_nodes = [f"{path}::{title}"]
                node_items.setdefault(f"{path}::{title}", set()).update(
                    case_item_ids(case)
                )
            else:
                case.coverage_kind = "unverified"
                notes[case.case_id] = f"Playwright 测试标题不存在：{path}::{title}"
            continue
        node_match = _TEST_NODE_RE.search(command)
        if node_match:
            path = node_match.group(1).replace("\\", "/").lstrip("./")
            node = node_match.group(2)
            names = _mapped_test_value(function_map, path)
            if isinstance(names, list) and node in names:
                case.coverage_kind = "direct"
                case.test_nodes = [f"{path}::{node}"]
                node_items.setdefault(f"{path}::{node}", set()).update(
                    case_item_ids(case)
                )
            else:
                case.coverage_kind = "unverified"
                notes[case.case_id] = f"测试节点不存在：{path}::{node}"
            continue
        file_match = _TEST_FILE_RE.search(command)
        case.coverage_kind = "file" if file_match else "unverified"
        if file_match:
            notes[case.case_id] = (
                f"文件级覆盖：只执行 {file_match.group(1).replace(chr(92), '/')}，"
                "未定位到具体测试函数"
            )
        else:
            notes[case.case_id] = "命令没有指向测试文件，无法确认逐项断言"
    for case in cases:
        if case.coverage_kind != "direct":
            continue
        nodes = [node for node in case.test_nodes if len(node_items.get(node, set())) > 1]
        if nodes:
            case.coverage_kind = "shared"
            notes[case.case_id] = (
                f"共用测试节点：{nodes[0]} 同时用于多个验收项，"
                "不能证明逐项断言"
            )
    # If every other item sharing a node already has its own direct evidence,
    # the only item without direct evidence can own this node.
    direct_items = {
        item_id
        for case in cases
        if case.coverage_kind == "direct"
        for item_id in case_item_ids(case)
    }
    for node, owners in node_items.items():
        missing = owners - direct_items
        if len(owners) < 2 or len(missing) != 1:
            continue
        target = next(iter(missing))
        for case in cases:
            if node not in case.test_nodes or target not in case_item_ids(case):
                continue
            case.coverage_kind = "direct"
            notes.pop(case.case_id, None)
    return notes


def _validate_plan_cases(
    cases: list[Case], intent: Literal["smoke", "business"], context: dict[str, Any]
) -> None:
    allowed_commands = _string_list(context.get("test_commands"))
    smoke_command = str(context.get("smoke_command") or "").strip()
    if smoke_command:
        allowed_commands.append(smoke_command)
    l1 = context.get("l1")
    if isinstance(l1, dict):
        build = l1.get("build")
        if isinstance(build, str) and build.strip():
            allowed_commands.append(build.strip())
        for module in _string_list(l1.get("entry")):
            allowed_commands.append(f'python -c "import {module}"')
    root = Path(str(context.get("root") or "."))
    for module in _detect_entry_modules(root):
        allowed_commands.append(f'python -c "import {module}"')
    allowed_hosts = _allowed_http_hosts(context)
    seen_ids: set[str] = set()
    seen_broad: set[str] = set()
    for case in cases:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", case.case_id):
            raise invalid_request(
                f"用例 ID 不合法：{case.case_id}",
                "用例 ID 只允许字母、数字、下划线和连字符，最多 128 字符",
            )
        if case.case_id in seen_ids:
            raise invalid_request(f"用例 ID 重复：{case.case_id}")
        seen_ids.add(case.case_id)
        _validate_verify_method(case)
        if intent == "business" and case.layer != "L1":
            command = case.verify_method.split(":", 1)[1] if ":" in case.verify_method else ""
            file_match = _TEST_FILE_RE.search(command)
            if (
                file_match
                and "::" not in command
                and _mapped_test_value(context.get("test_functions"), file_match.group(1))
            ):
                raise invalid_request(
                    f"用例 {case.case_id} 只执行测试文件，未定位到具体测试函数",
                    "使用 python -m pytest <文件>::<函数>；函数名必须来自 context.test_functions",
                )
            playwright_match = _PLAYWRIGHT_NODE_RE.search(command)
            spec_match = re.search(
                r"([A-Za-z0-9_./\\-]+\.spec\.ts)\b", command
            )
            if (
                spec_match
                and not playwright_match
                and _mapped_test_value(context.get("playwright_tests"), spec_match.group(1))
            ):
                raise invalid_request(
                    f"用例 {case.case_id} 只执行 Playwright spec，未定位到测试标题",
                    "使用 --grep \"标题\"；标题必须来自 context.playwright_tests",
                )
        if (
            intent == "business"
            and case.layer != "L1"
            and not is_manual_case(case)
            and case.acceptance_item_ids
        ):
            raise invalid_request(
                f"业务用例 {case.case_id} 绑定了多个验收项",
                "业务用例必须一项一案；同一命令可分别绑定各验收项，执行阶段会复用结果",
            )
        broad = _broad_test_command(case.verify_method)
        if broad:
            if intent == "business" and case.layer != "L1":
                raise invalid_request(
                    f"业务用例不能使用整包测试命令：{broad}",
                    "改用具体测试模块或目录；整包命令只用于 L1 冒烟检查",
                )
            if broad in seen_broad:
                raise invalid_request(
                    f"整包测试命令重复：{broad}",
                    "同一整包命令只保留一条用例；其他验收项改用具体测试模块或目录",
                )
            seen_broad.add(broad)
        _validate_dependencies(case)
        if case.script_content:
            hosts = _script_example_hosts(case.script_content)
            if hosts:
                raise invalid_request(f"用例 {case.case_id} 的脚本访问了外网示例域名：{hosts[0]}")
            frameworks = set(_string_list(context.get("framework")))
            if (
                "django" in frameworks
                and "pytest" not in frameworks
                and "django.test" in case.script_content
            ):
                raise invalid_request(
                    f"用例 {case.case_id} 使用 Django TestCase 独立脚本",
                    "该项目没有 pytest-django；改用 command: 运行既有测试",
                )
        elif (
            "django" in set(_string_list(context.get("framework")))
            and case.verify_method.startswith("command:")
        ):
            command = case.verify_method.split(":", 1)[1]
            if (
                "python -c" in command
                and " import " in command
                and "DJANGO_SETTINGS_MODULE" not in command
            ):
                raise invalid_request(
                    f"用例 {case.case_id} 的 Django 导入命令未配置 DJANGO_SETTINGS_MODULE",
                    "改用项目测试运行器，或在命令中设置 DJANGO_SETTINGS_MODULE "
                    "并调用 django.setup()",
                )
        if intent == "smoke":
            _validate_smoke_case(case, allowed_commands)
        else:
            _validate_business_http(case, allowed_hosts)


def _detect_entry_modules(root: Path) -> list[str]:
    modules: list[str] = []
    ignored = {"tests", "test", "src", "__pycache__", "setup", "conftest"}
    for base in (root / "src", root):
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*/__init__.py")):
            name = path.parent.name
            if name in ignored or name.startswith("."):
                continue
            if name not in modules:
                modules.append(name)
        for path in sorted(base.glob("*.py")):
            name = path.stem
            if (
                name in ignored
                or name.startswith(".")
                or not name.isidentifier()
                or name in modules
            ):
                continue
            modules.append(name)
    return modules[:3]


def _smoke_checks(context: dict[str, Any]) -> list[dict[str, str]]:
    """Infer fast, meaningful L1 checks; never run the whole suite by default."""
    custom = str(context.get("smoke_command") or "").strip()
    if custom:
        return [
            {
                "expected": "自定义冒烟命令通过",
                "source": "project.config l1.smoke_command",
                "command": custom,
            }
        ]
    checks: list[dict[str, str]] = []
    l1 = context.get("l1")
    entries = _string_list(l1.get("entry")) if isinstance(l1, dict) else []
    build = ""
    if isinstance(l1, dict):
        value = l1.get("build")
        if isinstance(value, str) and value.strip():
            build = value.strip()
    if build:
        checks.append(
            {
                "expected": "项目构建命令通过",
                "source": "project.config l1.build",
                "command": build,
            }
        )
    else:
        modules = entries or _detect_entry_modules(Path(str(context.get("root") or ".")))
        if modules:
            checks.append(
                {
                    "expected": f"入口模块 {modules[0]} 可导入",
                    "source": "项目入口模块",
                    "command": f'python -c "import {modules[0]}"',
                }
            )
    commands = _string_list(context.get("test_commands"))
    pytest_command = next((item for item in commands if "pytest" in item), "")
    if pytest_command:
        checks.append(
            {
                "expected": "pytest 能发现测试用例",
                "source": "pytest 用例收集",
                "command": f"{pytest_command} --collect-only -q",
            }
        )
        smallest = str(context.get("smallest_test") or "").strip()
        if smallest:
            checks.append(
                {
                    "expected": f"最小测试模块 {smallest} 通过",
                    "source": f"最小测试文件 {smallest}",
                    "command": f'{pytest_command} "{smallest}" -q --maxfail=1',
                }
            )
    elif commands:
        fallback = next((item for item in commands if item != build), "")
        if fallback:
            checks.append(
                {
                    "expected": "项目测试命令通过",
                    "source": "项目声明的测试命令",
                    "command": fallback,
                }
            )
    if not checks:
        raise AiTestError(
            "INVALID_REQUEST",
            "冒烟目标缺少可执行检查",
            "在 project.config 的 l1 配置 build、entry 或 smoke_command，或提供 pytest/npm test",
        )
    return checks


def _smoke_case(command: str, item_id: str | None, expected: str, source: str) -> dict[str, Any]:
    method = command if command.startswith(("command:", "pytest:")) else f"command: {command}"
    return {
        "acceptance_item_id": item_id,
        "layer": "L1",
        "expected": expected,
        "expectation_source": source,
        "preconditions": ["项目目录可访问，构建与测试依赖已安装"],
        "verify_method": method,
        "depends_on": None,
    }


def _smoke_cases(task: Task, selected: list[str], context: dict[str, Any]) -> list[dict[str, Any]]:
    """Build deterministic L1 checks from declared project facts, never guessing."""
    checks = _smoke_checks(context)
    item_ids = [item.item_id for item in task.acceptance_items if item.item_id in selected]
    raw: list[dict[str, Any]] = []
    if item_ids:
        primary = checks[-1]
        for item_id in item_ids:
            raw.append(
                _smoke_case(
                    primary["command"],
                    item_id,
                    primary["expected"],
                    primary["source"],
                )
            )
        remaining = checks[:-1]
    else:
        remaining = checks
    for check in remaining:
        raw.append(_smoke_case(check["command"], None, check["expected"], check["source"]))
    return raw


# -- plan source signature --------------------------------------------------


def _git_head(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
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
    rules_version_id: str | None,
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
        "rules_version_id": rules_version_id or "",
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


def _project_import_warnings(cases: list[Case], project: Project) -> list[str]:
    """Warn when a generated import uses the project name but no such module exists."""
    root = Path(project.root)
    detected = set(_detect_entry_modules(root))
    names = {root.name, project.name}
    warnings: list[str] = []
    command_pattern = re.compile(r'\b(?:python|py)\s+-c\s+["\']([^"\']+)["\']')
    import_pattern = re.compile(r"\b(?:import|from)\s+([A-Za-z_][A-Za-z0-9_.]*)")
    for case in cases:
        method = case.verify_method or ""
        if not method.startswith("command:"):
            continue
        command = command_pattern.search(method[len("command:") :])
        if command is None:
            continue
        for target in import_pattern.findall(command.group(1)):
            top = target.split(".")[0]
            if top in names and top not in detected:
                warnings.append(
                    f"{case.case_id}：导入模块 {top} 与项目名相同但未在入口中识别，请确认包名"
                )
    return warnings


def _expectation_feedback(items: list[AcceptanceItem], cases: list[Case]) -> str:
    ids = "、".join(case.case_id for case in cases)
    lines = [
        f"以下用例的 expectation_source 不是验收项原文：{ids}。",
        "请重新输出全部 cases，并把每条用例的 expectation_source 改成对应验收项 text 的逐字副本：",
    ]
    lines.extend(f"- {item.item_id}：{item.text}" for item in items)
    return "\n".join(lines)


def cmd_project_snapshot(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    """Return the panel's full read-only project view in one CLI process."""
    del confirm
    project = _project(ws, payload)
    base: dict[str, Any] = {"project_id": project.project_id}
    show = cmd_project_show(ws, base, False)
    map_data = cmd_project_map(ws, base, False)
    config_data = cmd_project_config(ws, base, False)
    rule_rows = cmd_rules_list(ws, base, False).get("rules", [])
    rules: list[dict[str, Any]] = []
    for row in rule_rows:
        rule_id = str(row.get("rules_version_id") or "")
        if not rule_id:
            continue
        try:
            rules.append(
                cmd_rules_show(ws, {**base, "rules_version_id": rule_id}, False)["rule"]
            )
        except AiTestError:
            continue
    tasks: list[dict[str, Any]] = []
    for summary in cmd_task_list(ws, base, False).get("tasks", []):
        task_id = str(summary.get("task_id") or "")
        if not task_id:
            continue
        try:
            shown = cmd_task_show(ws, {**base, "task_id": task_id}, False)
        except AiTestError:
            continue
        plans: list[dict[str, Any]] = []
        for plan_id in shown.get("plans", []):
            try:
                plans.append(cmd_plan_show(ws, {**base, "plan_id": plan_id}, False))
            except AiTestError:
                continue
        delivery = cmd_delivery_show(ws, {**base, "task_id": task_id}, False).get(
            "delivery"
        )
        tasks.append({"task": shown["task"], "plans": plans, "delivery": delivery})

    runs: list[dict[str, Any]] = []
    evidence: dict[str, list[dict[str, Any]]] = {}
    reports: list[dict[str, Any]] = []
    for summary in cmd_run_list(ws, base, False).get("runs", []):
        run_id = str(summary.get("run_id") or "")
        if not run_id:
            continue
        try:
            detail = cmd_run_status(ws, {**base, "run_id": run_id}, False)
        except AiTestError:
            continue
        runs.append(detail)
        evidence[run_id] = cmd_evidence_list(
            ws, {**base, "run_id": run_id}, False
        ).get("evidence", [])
        report_id = str(detail.get("run", {}).get("latest_report_id") or "")
        if report_id:
            try:
                reports.append(
                    cmd_report_show(ws, {**base, "report_id": report_id}, False)["report"]
                )
            except AiTestError:
                continue

    issues: list[dict[str, Any]] = []
    for summary in cmd_issue_list(ws, base, False).get("issues", []):
        issue_id = str(summary.get("issue_id") or "")
        if not issue_id:
            continue
        try:
            issues.append(
                cmd_issue_show(ws, {**base, "issue_id": issue_id}, False)["issue"]
            )
        except AiTestError:
            continue

    return {
        "project": show["project"],
        "modules": show.get("modules", []),
        "map": map_data.get("map"),
        "map_stale": bool(map_data.get("stale")),
        "config": config_data.get("config", {}),
        "rules": rules,
        "tasks": tasks,
        "runs": runs,
        "evidence": evidence,
        "reports": reports,
        "issues": issues,
    }


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
    return "\n".join(lines)


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
    context = _project_context(project, ws)
    context = redact_tree(
        context, known_secret_values([load_project_config(ws, project.project_id)])
    )
    raw_cases = payload.get("cases")
    regenerate = None
    if not isinstance(raw_cases, list) or not raw_cases:
        if task.intent == "smoke":
            raw_cases = _smoke_cases(task, selected, context)
        else:
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

            def _model_raw(feedback: str = "") -> list[dict[str, Any]]:
                generated: list[dict[str, Any]] = []
                batch_size = 6 if len(selected_items) > 8 else len(selected_items)
                for start in range(0, len(selected_items), batch_size):
                    batch = selected_items[start : start + batch_size]
                    correction = feedback
                    batch_cases: list[dict[str, Any]] = []
                    for attempt in range(2):
                        call_context = dict(context)
                        if correction:
                            call_context["correction"] = correction
                        try:
                            batch_cases = list(
                                client.generate_cases(
                                    task.model_dump(mode="json"), batch, call_context
                                )
                            )
                            break
                        except AiTestError as exc:
                            if (
                                exc.info.code != "MODEL_OUTPUT_INVALID"
                                or attempt == 1
                            ):
                                raise
                            correction = _generation_feedback(
                                batch, context, exc.info.message
                            )
                    for item in batch_cases:
                        depends = item.get("depends_on")
                        first = depends[0] if isinstance(depends, list) and depends else None
                        generated.append(
                            {
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
                                "depends_on": first
                                if first in {"build", "service"}
                                else None,
                                "script_name": item.get("script_name"),
                                "script_content": item.get("script_content"),
                            }
                        )
                return generated

            regenerate = _model_raw
            try:
                raw_cases = _model_raw()
            except AiTestError as exc:
                if exc.info.code != "MODEL_OUTPUT_INVALID":
                    raise
                raw_cases = _model_raw(
                    _generation_feedback(
                        selected_items, context, exc.info.message
                    )
                )
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
    cases, dropped_items = _drop_unavailable_django_scripts(cases, context)
    django_dropped.extend(dropped_items)
    cases, merged_now = _merge_duplicate_command_cases(cases)
    command_merged += merged_now
    if generated_cases and task.intent != "smoke" and regenerate is not None:
        unverified = _unverified_cases(cases, task.acceptance_items)
        if unverified:
            # A paraphrase or file path is not a substitute for the item text.
            # Ask the model once to quote the item verbatim; keep the warning if
            # it still refuses instead of silently rewriting its source.
            raw_cases = regenerate(
                _expectation_feedback(task.acceptance_items, unverified)
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
    downgraded_broad: list[str] = []
    multi_item_split = 0
    for attempt in range(2):
        if task.intent == "business":
            cases, split_now = _split_multi_item_cases(
                cases, task.acceptance_items
            )
            multi_item_split += split_now
        for case in cases:
            case.verify_method = _prefix_component_command(
                case.verify_method, Path(project.root)
            )
        try:
            _validate_plan_cases(cases, task.intent, context)
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
            if attempt == 1:
                if "整包测试命令" not in exc.info.message:
                    raise
                downgraded_broad = _downgrade_broad_business_cases(cases)
                if not downgraded_broad:
                    raise
                _validate_plan_cases(cases, task.intent, context)
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
            # failing on a fixable script detail or repeated command.
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
    ensure_publishable(cases)
    secret_list = known_secret_values([load_project_config(ws, project.project_id)])
    _redact_cases(cases, secret_list)
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
        rules_version_id=_text(payload, "rules_version_id") or None,
        cases=cases,
        item_texts={
            item.item_id: item.text for item in task.acceptance_items if item.item_id in selected
        },
        warnings=warnings,
        status="published",
    )
    plan.unselected_by_module = _unselected_by_module(task, selected, project)
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
    context = _project_context(project, ws)
    _validate_plan_cases(plan.cases, task.intent, context)
    _ensure_plan_coverage(plan, task)
    plan.warnings = annotate_cases(plan.cases, task.acceptance_items)
    plan.warnings.extend(_shared_command_warnings(plan.cases))
    ensure_publishable(plan.cases)
    _redact_cases(
        plan.cases,
        known_secret_values([load_project_config(ws, project.project_id)]),
    )
    plan.warnings = annotate_cases(plan.cases, task.acceptance_items)
    plan.warnings.extend(_shared_command_warnings(plan.cases))
    revision_path = (
        ws.project_dir(project.project_id) / "plans" / "revisions" / plan.plan_id
    ) / f"{old_plan.revision}.json"
    write_model(revision_path, old_plan)
    plan.revision += 1
    plan.status = "published"
    plan.unselected_by_module = _unselected_by_module(
        task, list(plan.scope.acceptance_item_ids), project
    )
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
    context = _project_context(project, ws)
    _validate_plan_cases(plan.cases, task.intent, context)
    _ensure_plan_coverage(plan, task)
    plan.warnings = annotate_cases(plan.cases, task.acceptance_items)
    plan.warnings.extend(_shared_command_warnings(plan.cases))
    ensure_publishable(plan.cases)
    _redact_cases(
        plan.cases,
        known_secret_values([load_project_config(ws, project.project_id)]),
    )
    plan.warnings = annotate_cases(plan.cases, task.acceptance_items)
    plan.warnings.extend(_shared_command_warnings(plan.cases))
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


# -- run --------------------------------------------------------------------


def _runs_dir(ws: Workspace, project_id: str) -> Path:
    return ws.project_dir(project_id) / "runs"


def _heartbeat_stale(run: Run, seconds: float = 120.0) -> bool:
    if not run.heartbeat_at:
        return True
    try:
        then = datetime.fromisoformat(run.heartbeat_at).timestamp()
    except ValueError:
        return True
    age = time.time() - then
    if run.pid:
        if pid_alive(run.pid, run.pid_created_at):
            # A live worker may be in a long command; never start a second run
            # just because its heartbeat is old.
            return False
        # A dead PID is stale after a short grace, without waiting two minutes.
        return age > 5
    return age > seconds


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


def _elapsed_seconds(start: str, end: str) -> int | None:
    try:
        started = datetime.fromisoformat(start)
        finished = datetime.fromisoformat(end)
    except ValueError:
        return None
    return max(0, int((finished - started).total_seconds()))


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
    environment = _environment_summary(ws, project.project_id)
    credentials = _credential_summary()
    side_effects = ["执行项目级检查"]
    fingerprint = _confirmation_fingerprint(plan, [], environment, credentials, side_effects)
    confirmed_by = _text(payload, "confirmed_by") or "cli"
    try:
        with ws.lock():
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
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
    try:
        subprocess.Popen(
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
    except OSError as exc:
        with ws.lock():
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
    current_signature = _plan_signature(ws, project, plan.task_id, plan.rules_version_id)
    if not plan.source_signature or plan.source_signature != current_signature:
        raise AiTestError(
            "PLAN_STALE",
            "计划缺少来源签名，或生成后项目、交付提交、任务验收项、规则或服务配置已变化",
            "重新生成计划后重试",
        )
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
    if not selected and (
        plan.applicable_count > 0
        or not any(not case_item_ids(case) for case in plan.cases)
    ):
        raise AiTestError("SCOPE_EMPTY", "本次范围为空", "至少选择一条验收项")
    environment = _environment_summary(ws, project.project_id)
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
            for case in filtered:
                if not case.script_name or not case.script_content:
                    continue
                name = case.script_name.strip()
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
    process = None
    try:
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
    except OSError as exc:
        with ws.lock():
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
    return {
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


def _safe_id(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise invalid_request(f"{label} 不合法：{value}")
    return value


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
    return path.parent, _refresh_and_save(path, read_model(path, Run))


def _ensure_settled(run: Run) -> None:
    """The worker owns ``runs/<id>/`` while running; CLI writes wait for it."""
    if run.status == "running":
        raise AiTestError(
            "RUN_ACTIVE",
            f"运行仍在进行：{run.run_id}",
            "等待 worker 结束后再记录证据、人工结果或生成报告",
        )


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
    return {
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
    }


_REPAIR_SPEC = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
    r"(?:\[[A-Za-z0-9,._-]+\])?"
    r"(?:[<>=!~][A-Za-z0-9.*+!<>=~-]{1,40})?$"
)


def _project_python(root: Path) -> Path:
    from aitest.worker import _venv_python

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
    run_dir, run = _run(ws, payload)
    _ensure_settled(run)
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
    python = _project_python(Path(project.root))
    from aitest.worker import _run_env

    env = _run_env(Path(project.root), python, run.run_id)
    installed = install_packages(
        python,
        packages,
        Path(project.root),
        env,
        timeout_seconds=600,
    )
    output = installed.output
    ok = installed.ok
    exit_code = installed.exit_code
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
    versions = _installed_versions(python, packages, env) if ok else {}
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
    from aitest.worker import _environment_fingerprint

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
        for path in sorted(directory.glob("*/run.json")):
            try:
                run = _refresh_and_save(path, read_model(path, Run))
            except AiTestError as exc:
                skipped.append(
                    {
                        "run_id": path.parent.name,
                        "code": exc.info.code,
                        "message": exc.info.message,
                    }
                )
                continue
            run_dir = path.parent
            plan_snapshot: Plan | None = None
            task_snapshot: Task | None = None
            task_snapshot_exists = False
            plan_path = run_dir / "plan.json"
            if plan_path.is_file():
                try:
                    plan_snapshot = read_model(plan_path, Plan)
                except AiTestError:
                    plan_snapshot = None
            task_path = run_dir / "task.json"
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
            runs.append(
                {
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
            )
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
    return {"run_id": run.run_id, "cancel_requested": True}


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
        target = (run_dir / relative).resolve()
        evidence_root = (run_dir / "evidence").resolve()
        if evidence_root in target.parents and target.is_file():
            return relative
    source = candidate.expanduser().resolve()
    if not source.is_file():
        raise invalid_path(f"证据文件不存在：{source}")
    name = f"manual-{secrets.token_hex(4)}-{source.name}"
    cap_bytes = cap if cap is not None else 20 * 1024 * 1024
    with source.open("rb") as handle:
        data = handle.read(cap_bytes + 1)
    return _save_evidence_file(
        run_dir,
        name,
        data,
        budget,
        cap,
        allow_video,
        secrets_list,
        original_size=source.stat().st_size,
    )


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
        refs: list[str] = []
        for item in raw_evidence:
            if not isinstance(item, str) or not item.strip():
                raise invalid_request("evidence 必须只包含文件路径")
            refs.append(
                _manual_evidence_ref(
                    run_dir,
                    item,
                    budget=budget,
                    cap=cap,
                    allow_video=allow_video,
                    secrets_list=secrets_list,
                )
            )
        entry = ManualStep(
            step=step,
            result=cast(Literal["passed", "failed", "blocked"], step_result),
            note=redact(note, secrets_list),
            evidence_refs=refs,
            recorded_at=iso_now(),
        )
        position = next((index for index, item in enumerate(log.steps) if item.step == step), None)
        if position is None:
            log.steps.append(entry)
        else:
            log.steps[position] = entry
        write_model(path, log)
        manual_result = aggregate_manual(log.steps, required_steps)
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
    with ws.lock():
        run_dir, run = _run(ws, payload)
        _ensure_settled(run)
        source = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
        if not source.is_file():
            raise invalid_path(f"证据文件不存在：{source}")
        name = f"manual-{secrets.token_hex(4)}-{source.name}"
        config = load_project_config(ws, run.project_id)
        budget, cap, allow_video = _evidence_settings(config)
        secrets_list = known_secret_values([config])
        cap_bytes = cap if cap is not None else 20 * 1024 * 1024
        with source.open("rb") as handle:
            data = handle.read(cap_bytes + 1)
        ref = _save_evidence_file(
            run_dir,
            name,
            data,
            budget,
            cap,
            allow_video,
            secrets_list,
            original_size=source.stat().st_size,
        )
        run.evidence_refs.append(ref)
        note = redact(_text(payload, "note"), secrets_list)
        if note:
            run.evidence_notes[ref] = note
        case_id = _text(payload, "case_id")
        if case_id:
            run_case = next((item for item in run.cases if item.case_id == case_id), None)
            if run_case is None:
                raise invalid_request(f"运行中没有该用例：{case_id}")
            if ref not in run_case.evidence_refs:
                run_case.evidence_refs.append(ref)
        run.media_bytes = evidence_bytes(run_dir)
        write_model(run_dir / "run.json", run)
    return {"evidence_ref": ref}


def cmd_evidence_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    run_dir, run = _run(ws, payload)
    items = []
    for ref in run.evidence_refs:
        path = evidence_path(run_dir, ref)
        exists = path is not None and path.is_file()
        digest = ""
        if exists and path is not None:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
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
    return {"evidence": items, "run_id": run.run_id}


# -- report -----------------------------------------------------------------


def cmd_report_generate(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_dir, run = _run(ws, payload)
    _ensure_settled(run)
    ensure_run_issues(ws, project.project_id, run_dir)
    report = build_report(ws, project, run_dir)
    return {
        "report_id": report.report_id,
        "run_id": report.run_id,
        "generated_at": report.generated_at,
        "outcome": report.outcome,
        "conclusion": report.conclusion,
        "counts": report.counts,
        "coverage": report.coverage,
        "changed_modules": report.changed_modules,
        "unmapped_files": report.unmapped_files,
        "uncovered_items": report.uncovered_items,
        "unverified_items": report.unverified_items,
        "plan_warnings": report.plan_warnings,
        "layers": report.layers,
        "item_details": report.item_details,
        "l1": report.l1,
        "issue_count": report.issue_count,
        "failure_occurrences": report.failure_occurrences,
        "templates": [
            {
                "template_id": entry.get("template_id"),
                "name": entry.get("name"),
                "summary": entry.get("summary") or {},
            }
            for entry in report.templates
            if isinstance(entry, dict)
        ],
    }


def _report_path(ws: Workspace, project_id: str, report_id: str) -> Path:
    return ws.project_dir(project_id) / "reports" / f"{report_id}.json"


def cmd_report_show(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _text({"id": _require(payload, "report_id")}, "id")
    if Path(report_id).name != report_id:
        raise invalid_request(f"报告 ID 不合法：{report_id}")
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise invalid_request(f"报告不存在：{report_id}")
    report = read_model(path, Report)
    return {"report": report.model_dump(mode="json")}


def cmd_report_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_id = _text({"id": _require(payload, "run_id")}, "id")
    directory = ws.project_dir(project.project_id) / "reports"
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                report = read_model(path, Report)
            except AiTestError as exc:
                skipped.append(
                    {
                        "report_id": path.stem,
                        "code": exc.info.code,
                        "message": exc.info.message,
                    }
                )
                continue
            if report.run_id != run_id:
                continue
            generated_at = report.generated_at
            if not generated_at:
                try:
                    generated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                        timespec="seconds"
                    )
                except OSError:
                    generated_at = ""
            rows.append(
                {
                    "report_id": report.report_id,
                    "run_id": report.run_id,
                    "generated_at": generated_at,
                    "outcome": report.outcome,
                    "revision": report.revision,
                    "has_opinion": report.opinion is not None,
                }
            )
    rows.sort(key=lambda item: item["generated_at"], reverse=True)
    return {"reports": rows, "skipped": skipped}


def _cell(value: object) -> str:
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|") or "-"


def _case_row(detail: dict[str, Any]) -> str:
    refs = "、".join(_cell(item) for item in detail.get("evidence_refs") or []) or "-"
    return (
        f"| {_cell(detail.get('case_id'))} | {_cell(detail.get('acceptance_item_id') or '-')} "
        f"| {_cell(detail.get('layer'))} | {_cell(detail.get('result'))} | {refs} |"
    )


def _analysis_lines(analysis: dict[str, Any]) -> list[str]:
    status = str(analysis.get("status") or "not_generated")
    lines = [f"### 分析状态：{status}"]
    if status == "invalid":
        lines.append("分析不合格：模型输出缺少必需段落，整条不展示。")
        reason = analysis.get("reason")
        if reason:
            lines.append(f"原因：{reason}")
        return lines
    if status != "generated":
        lines.append("本轮未生成 AI 分析。")
        reason = analysis.get("reason")
        if reason:
            lines.append(f"原因：{reason}")
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
        "## 结论",
        report.conclusion or f"{report.outcome}：未生成结论摘要",
        "",
        f"- 结论值：{report.outcome}",
        f"- 运行：{report.run_id}",
        f"- 范围：{report.counts.get('selected', 0)}/{report.coverage.get('applicable', 0)}",
        f"- 未覆盖：{len(report.uncovered_items)}",
        f"- 失败：{report.counts.get('failed', 0)}",
        f"- 源码提交：{report.source_commit or '未记录'}",
        f"- 规则版本：{report.rules_version_id or '未记录'}",
    ]
    python_version = report.environment.get("python")
    lines.append(f"- 环境 Python：{python_version or '未记录'}")
    for key, label in (
        ("python_version", "Python 版本"),
        ("platform", "平台"),
        ("venv", "项目 venv"),
        ("node", "Node 版本"),
        ("node_path", "Node 路径"),
        ("node_lock", "Node 锁文件"),
        ("cwd", "工作目录"),
    ):
        value = report.environment.get(key)
        if value:
            lines.append(f"- 环境 {label}：{value}")
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
        deployment = report.github.get("deployment") or "未确认"
        lines.append(f"- GitHub：{repo}@{commit}（{branch}）")
        lines.append(f"- 部署版本：{deployment}")
    lines.append("")
    if report.summary:
        if report.summary_source == "fallback":
            lines.append("AI 摘要未采用，以下为事实摘要：")
        else:
            lines.append("AI 摘要（仅事实）：")
        lines.extend(f"> {line}" if line else ">" for line in str(report.summary).splitlines())
        lines.append("")
    lines.append("## 范围与覆盖")
    lines.append(f"- 已选验收项：{report.counts.get('selected', 0)}")
    lines.append(f"- 适用验收项：{report.coverage.get('applicable', 0)}")
    lines.append(f"- 覆盖验收项：{report.counts.get('covered', 0)}")
    lines.append(f"- 通过：{report.counts.get('passed', 0)}")
    lines.append(f"- 失败：{report.counts.get('failed', 0)}")
    lines.append(f"- 阻塞或未完成：{report.counts.get('incomplete', 0)}")
    lines.append("")
    lines.append("## 验收项主表")
    lines.append("| 验收项 | 内容 | 模块 | 结果 | 用例 |")
    lines.append("| --- | --- | --- | --- | --- |")
    if report.item_details:
        for item in report.item_details:
            lines.append(
                f"| {_cell(item.get('item_id'))} | {_cell(item.get('text'))} "
                f"| {_cell(item.get('module_id') or '-')} | {_cell(item.get('result'))} "
                f"| {_cell('、'.join(item.get('case_ids') or []) or '-')} |"
            )
    else:
        lines.append("| 无 | - | - | - | - |")
    lines.append("")
    lines.append("## 未覆盖清单")
    if report.uncovered_items:
        lines.extend(f"- {item}" for item in report.uncovered_items)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 未验证项")
    if report.unverified_items:
        lines.extend(f"- {item}" for item in report.unverified_items)
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 分层计数")
    for layer in ("L1", "L2", "L3"):
        lines.append(f"- {layer}：{report.layers.get(layer, 0)} 个用例")
    lines.append("")
    lines.append("## 项目级检查（L1，不计验收项覆盖）")
    if report.l1 is None:
        lines.append("- 未配置项目级检查")
    else:
        build = report.l1.get("build") if isinstance(report.l1.get("build"), dict) else None
        if build is None:
            lines.append("- 构建：未配置")
        else:
            lines.append(
                f"- 构建：{'通过' if build.get('ok') else '失败'}"
                f"（退出码 {build.get('exit_code')}，证据 {build.get('evidence_ref') or '无'}）"
            )
        for entry in report.l1.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            lines.append(
                f"- 入口 {entry.get('module')}："
                f"{'通过' if entry.get('ok') else '失败'}"
                f"（证据 {entry.get('evidence_ref') or '无'}）"
            )
        for case in report.l1.get("cases") or []:
            if not isinstance(case, dict):
                continue
            lines.append(
                f"- 检查 {_cell(case.get('case_id'))}：{_cell(case.get('result'))}"
            )
        for reason in report.l1.get("blocking_reasons") or []:
            lines.append(f"- 阻塞原因：{reason}")
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
            rendered = "、".join(_cell(item) for item in refs) or "未采集证据"
            lines.append(f"- {_cell(detail.get('case_id'))}：{rendered}")
    lines.append("")
    lines.append("## 涉及模块")
    lines.append("- 已映射模块：" + ("、".join(report.changed_modules) or "无"))
    lines.append("- 未映射文件：" + ("、".join(report.unmapped_files) or "无"))
    lines.append("")
    lines.append("## AI 分析")
    if not report.analysis:
        lines.append("- 未生成")
    else:
        for analysis in report.analysis:
            lines.extend(_analysis_lines(analysis))
            lines.append("")
    lines.append("")
    lines.append("## 问题")
    lines.append(
        f"- 问题数：{report.issue_count}；本运行失败次数：{report.failure_occurrences}"
    )
    if report.issues:
        for issue in report.issues:
            lines.append(
                f"- {_cell(issue.get('issue_id'))} [{_cell(issue.get('severity'))}"
                f"/{_cell(issue.get('status'))}] {_cell(issue.get('title'))}"
                f"（发生 {issue.get('occurrences', 0)} 次，本运行 "
                f"{issue.get('run_occurrences', 0)} 次）"
            )
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## Mock 判定")
    if report.mock_judgement:
        for item in report.mock_judgement:
            lines.append(
                f"- {_cell(item.get('name'))}：{_cell(item.get('kind'))}"
                f"（{_cell(item.get('reason'))}）"
            )
    else:
        lines.append("- 未判定")
    lines.append("")
    lines.append("## 模板处置")
    if not report.templates:
        lines.append("- 未应用模板")
    else:
        for entry in report.templates:
            if not isinstance(entry, dict):
                continue
            raw_summary = entry.get("summary")
            summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
            lines.append(
                f"- {entry.get('name') or entry.get('template_id') or '模板'}"
                f"（适用 {summary.get('applicable', 0)}，"
                f"不适用 {summary.get('not_applicable', 0)}，"
                f"不支持 {summary.get('unsupported', 0)}，"
                f"未知 {summary.get('unknown', 0)}）"
            )
            raw_items = entry.get("items")
            items: list[Any] = raw_items if isinstance(raw_items, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                label = item.get("objective") or item.get("item_id") or ""
                reason = item.get("reason") or "未提供理由"
                lines.append(f"  - {label}：{item.get('disposition', 'unknown')}（{reason}）")
    lines.append("")
    lines.append("## 验收意见")
    if report.opinion is None:
        lines.append("未填写验收意见")
    else:
        lines.append(
            f"验收意见（{report.opinion.by}，{report.opinion.at}）：{report.opinion.text}"
        )
    lines.append("")
    lines.append("## 证据索引")
    lines.append("| 引用 | 存在 | 字节 | 媒体类型 | sha256 |")
    lines.append("| --- | --- | --- | --- | --- |")
    if report.evidence_index:
        for item in report.evidence_index:
            exists = "是" if item.get("exists") else "否"
            lines.append(
                f"| {_cell(item.get('ref'))} | {exists} | {item.get('bytes', 0)} "
                f"| {_cell(item.get('media_type'))} | {_cell(item.get('sha256'))} |"
            )
    else:
        lines.append("| 无 | - | 0 | - | - |")
    lines.append("")
    return lines


def cmd_report_export(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _safe_id(
        _text({"id": _require(payload, "report_id")}, "id"), "报告 ID"
    )
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"报告不存在：{report_id}", "先生成报告")
    report = read_model(path, Report)
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(_export_lines(report))
    secrets = known_secret_values([load_project_config(ws, project.project_id)])
    target.write_text(redact(text, secrets), encoding="utf-8")
    data = target.read_bytes()
    return {
        "path": str(target),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "lines": text.count("\n") + 1,
    }


def cmd_report_opinion(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    report_id = _safe_id(
        _text({"id": _require(payload, "report_id")}, "id"), "报告 ID"
    )
    path = _report_path(ws, project.project_id, report_id)
    if not path.is_file():
        raise AiTestError("INVALID_REQUEST", f"报告不存在：{report_id}", "先生成报告")
    report = read_model(path, Report)
    secrets = known_secret_values([load_project_config(ws, project.project_id)])
    report.opinion = Opinion(
        by=redact(_text({"by": _require(payload, "by")}, "by"), secrets),
        at=iso_now(),
        text=redact(_text(payload, "text"), secrets),
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
    )


def cmd_issue_list(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    issues = []
    for issue in (
        read_model(path, Issue) for path in _issues_dir(ws, project.project_id).glob("*.json")
    ):
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
    return {"issues": issues}


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
        if issue.status == "closed" and target_status == "in_progress" and not _text(
            payload, "reason"
        ):
            raise invalid_request("重开问题需要写原因")
        data["status"] = target_status
    disposition = payload.get("disposition")
    if disposition is not None:
        if disposition not in {"fixed", "non_defect", "duplicate", "deferred"}:
            raise invalid_request("disposition 只能是 fixed、non_defect、duplicate、deferred")
        reason = _text(payload, "reason") or issue.reason
        if disposition in {"non_defect", "duplicate", "deferred"} and not reason.strip():
            raise invalid_request("非缺陷、重复、延期必须写理由")
        data["disposition"] = disposition
        data["reason"] = reason
    elif "reason" in payload:
        data["reason"] = _text(payload, "reason")
    for key in ("severity", "fix_version"):
        if key in payload:
            data[key] = payload[key]
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
    run_case = next((item for item in run.cases if item.case_id == case_id), None)
    if run_case is None:
        raise AiTestError(
            "RUN_STATE_CONFLICT",
            f"回归运行中没有用例 {case_id}",
            "在回归范围里包含该用例",
        )
    plan = read_model(run_dir / "plan.json", Plan)
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


# -- backup / cleanup / analysis -------------------------------------------


def _invalid_backup(message: str) -> AiTestError:
    return AiTestError("BACKUP_INVALID", message, "换一个备份或重新备份")


_MAX_BACKUP_FILES = 100_000
_MAX_BACKUP_UNCOMPRESSED = 2 * 1024**3


def _check_zip_member(name: str) -> str:
    import posixpath

    raw = name.replace("\\", "/")
    normalized = posixpath.normpath(raw)
    parts = normalized.split("/")
    if normalized != raw:
        raise _invalid_backup(f"备份路径未规范化：{name}")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if (
        not name
        or normalized.startswith("/")
        or raw.startswith("/")
        or ".." in parts
        or ":" in normalized
        or any(
            part.endswith((".", " ")) or part.split(".")[0].upper() in reserved
            for part in parts
        )
    ):
        raise _invalid_backup(f"备份包含非法路径：{name}")
    return normalized


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
    if data.get("schema_version") != 1:
        raise AiTestError(
            "SCHEMA_UNSUPPORTED",
            "备份格式版本不受支持：" + repr(data.get("schema_version")),
            "用当前版本重新备份；旧备份需先转换",
        )
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


def _is_evidence_ref(ref: str) -> bool:
    """Only workspace evidence belongs in missing-evidence checks.

    Mock judgement also carries ``evidence_refs`` pointing at project source
    files; those are not copied into the workspace and must not be reported as
    missing evidence.
    """
    normalized = ref.strip().replace("\\", "/")
    return normalized.startswith("evidence/") or normalized.startswith("generated/")


def _missing_evidence(restored: Path) -> list[str]:
    missing: list[str] = []

    def check(base: Path, refs: object) -> None:
        for ref in _evidence_refs({"evidence_refs": refs}):
            if not _is_local_ref(ref) or not _is_evidence_ref(ref) or ref in missing:
                continue
            if not (base / ref).is_file():
                missing.append(ref)

    for path in sorted(restored.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        parts = path.relative_to(restored).parts
        if path.parent.name == "issues" and len(parts) >= 3 and parts[0] == "projects":
            project_id = parts[1]
            occurrences = data.get("occurrences")
            for occurrence in occurrences if isinstance(occurrences, list) else []:
                if not isinstance(occurrence, dict):
                    continue
                run_id = occurrence.get("run_id")
                if not isinstance(run_id, str) or not run_id:
                    continue
                check(
                    restored / "projects" / project_id / "runs" / run_id,
                    occurrence.get("evidence_refs"),
                )
            continue
        base = _record_base(restored, path, data)
        for ref in _evidence_refs(data):
            if not _is_local_ref(ref) or not _is_evidence_ref(ref) or ref in missing:
                continue
            if not (base / ref).is_file():
                missing.append(ref)
    return sorted(missing)


def _workspace_evidence_bytes(ws: Workspace) -> int:
    total = 0
    for project in ws.list_projects():
        runs = ws.project_dir(project.project_id) / "runs"
        if not runs.is_dir():
            continue
        for run_dir in runs.iterdir():
            if run_dir.is_dir():
                total += evidence_bytes(run_dir)
    return total


def cmd_backup_create(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    target = Path(_text({"path": _require(payload, "path")}, "path")).expanduser().resolve()
    if target.exists():
        raise invalid_request(f"备份文件已存在：{target}", "换一个文件名，不覆盖已有备份")
    include_evidence = _as_bool(payload.get("include_evidence"), False)
    target.parent.mkdir(parents=True, exist_ok=True)
    configs = [load_project_config(ws, project.project_id) for project in ws.list_projects()]
    secret_list = known_secret_values(configs)
    files: list[Path] = []
    for path in ws.root.rglob("*"):
        if not path.is_file() or path == target or path.is_symlink():
            continue
        relative = path.relative_to(ws.root)
        if (
            path.name == ".workspace.lock"
            or relative.parts[0] in {"requests", "backups"}
            or str(relative).replace("\\", "/") == "manifest.json"
        ):
            continue
        if not include_evidence and ("evidence" in relative.parts or "generated" in relative.parts):
            continue
        files.append(relative)
    estimated_evidence = _workspace_evidence_bytes(ws)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": iso_now(),
        "includes_evidence": include_evidence,
        "file_count": len(files),
        "total_files": len(files),
        "files": sorted(str(item).replace("\\", "/") for item in files),
        "sha256": {},
    }
    temp_target = target.parent / f".{target.name}.{secrets.token_hex(4)}.tmp"
    try:
        with zipfile.ZipFile(temp_target, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative in sorted(files):
                key = str(relative).replace("\\", "/")
                data = redact_bytes((ws.root / relative).read_bytes(), secret_list)
                manifest["sha256"][key] = hashlib.sha256(data).hexdigest()
                archive.writestr(key, data)
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
        os.replace(temp_target, target)
    except OSError as exc:
        temp_target.unlink(missing_ok=True)
        raise AiTestError(
            "BACKUP_INVALID", f"备份写入失败：{exc}", "检查目标目录权限与磁盘空间"
        ) from exc
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "path": str(target),
        "sha256": digest,
        "bytes": target.stat().st_size,
        "includes_evidence": include_evidence,
        "files": len(files),
        "file_count": len(files),
        "estimated_evidence_bytes": estimated_evidence,
        "estimated_evidence_mb": round(estimated_evidence / (1024 * 1024), 1),
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
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise _invalid_backup(f"备份无法打开：{exc}") from exc
    with archive:
        members: dict[str, zipfile.ZipInfo] = {}
        seen_lower: dict[str, str] = {}
        total_size = 0
        for member in archive.infolist():
            normalized = _check_zip_member(member.filename)
            if member.is_dir():
                continue
            lower = normalized.lower()
            if normalized in members or lower in seen_lower:
                raise _invalid_backup(f"备份包含重复条目：{member.filename}")
            seen_lower[lower] = normalized
            if member.external_attr >> 16 & 0o170000 == 0o120000:
                raise _invalid_backup(f"备份包含符号链接：{member.filename}")
            total_size += member.file_size
            if len(members) + 1 > _MAX_BACKUP_FILES:
                raise _invalid_backup("备份条目过多，拒绝恢复")
            if total_size > _MAX_BACKUP_UNCOMPRESSED:
                raise _invalid_backup("备份解压后体积超过上限，拒绝恢复")
            members[normalized] = member
        files, hashes = _zip_manifest(archive)
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _invalid_backup("备份 manifest 无法读取") from exc
        evidence_included = bool(manifest.get("includes_evidence"))
        if set(members) - {"manifest.json"} != set(files):
            raise _invalid_backup("备份 manifest 与 zip 内容不一致")
        for name in files:
            _check_zip_member(name)
            digest = hashlib.sha256(archive.read(name)).hexdigest()
            if digest != hashes[name]:
                raise _invalid_backup(f"备份文件摘要不匹配：{name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.parent / f".{target.name}.restore-{secrets.token_hex(4)}"
        try:
            temp_target.mkdir(parents=True)
            archive.extractall(temp_target)
            if target.exists():
                target.rmdir()
            os.replace(temp_target, target)
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            shutil.rmtree(temp_target, ignore_errors=True)
            raise _invalid_backup(f"恢复失败：{exc}") from exc
    missing = _missing_evidence(target)
    if missing and evidence_included:
        notes = f"证据未包含：{len(missing)} 个引用在恢复目录中不存在"
    elif missing:
        notes = f"证据未包含（备份创建时选择不包含）：{len(missing)} 个证据引用在恢复后不可用"
    else:
        notes = ""
    return {
        "target": str(target),
        "files": len(files),
        "verified": True,
        "evidence_included": evidence_included,
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
    dry_run = _as_bool(payload.get("dry_run"), True)
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
    needs_run_id = "{run_id}" in command_value or "{data_prefix}" in command_value
    if needs_run_id and not run_id and not dry_run:
        raise invalid_request("执行清理需要提供 run_id")
    if run_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
        raise invalid_request("run_id 只允许字母、数字、- 和 _，且不能以点开头")
    if not dry_run and not needs_run_id:
        raise invalid_request(
            "清理命令必须包含 {run_id} 或 {data_prefix} 占位符",
            "只允许按本次运行的标记或 ID 清理，不做全表或按时间删除",
        )
    command = command_value.replace("{run_id}", run_id).replace(
        "{data_prefix}", data_prefix(run_id) if run_id else ""
    )
    secret_list = known_secret_values([config])
    started = {
        "at": iso_now(),
        "run_id": run_id,
        "command": redact(command, secret_list),
        "dry_run": dry_run,
        "phase": "started",
        "exit_code": None,
    }
    _action_log(ws, started)
    from aitest.runners.command_runner import run_command
    from aitest.worker import _run_env

    cleanup_env = _run_env(Path(project.root), None, run_id or "cleanup")
    result = run_command(
        command,
        cwd=Path(project.root),
        timeout_seconds=600,
        env=cleanup_env,
    )
    stdout = redact(
        _trim_output(result.stdout.decode("utf-8", errors="replace")), secret_list
    )
    stderr = redact(
        _trim_output(result.stderr.decode("utf-8", errors="replace")), secret_list
    )
    exit_code = result.exit_code
    if result.status == "blocked":
        stderr = redact((stderr + "\n" + result.error).strip(), secret_list)
    entry = {
        "at": iso_now(),
        "run_id": run_id,
        "command": redact(command, secret_list),
        "dry_run": dry_run,
        "phase": "finished",
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }
    log_path = _action_log(ws, entry)
    if result.status != "passed":
        raise AiTestError(
            "CLEANUP_FAILED",
            f"清理命令未通过：{result.error or f'退出码 {exit_code}'}",
            f"检查 {log_path} 中的输出",
        )
    return {
        "dry_run": dry_run,
        "executed": not dry_run,
        "run_id": run_id,
        "command": redact(command, secret_list),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "log_path": log_path,
    }


def cmd_analysis_run(ws: Workspace, payload: Payload, confirm: bool) -> dict[str, Any]:
    del confirm
    project = _project(ws, payload)
    run_dir, current = _run(ws, payload)
    _ensure_settled(current)
    return analyze_run(ws, project.project_id, run_dir)


REGISTRY.update(
    {
        "project.snapshot": cmd_project_snapshot,
        "plan.generate": cmd_plan_generate,
        "plan.update": cmd_plan_update,
        "plan.show": cmd_plan_show,
        "plan.publish": cmd_plan_publish,
        "run.start": cmd_run_start,
        "run.quick": cmd_run_quick,
        "run.status": cmd_run_status,
        "run.repair": cmd_run_repair,
        "run.list": cmd_run_list,
        "run.cancel": cmd_run_cancel,
        "run.manual": cmd_run_manual,
        "evidence.add": cmd_evidence_add,
        "evidence.list": cmd_evidence_list,
        "issue.add": cmd_issue_add,
        "issue.list": cmd_issue_list,
        "issue.show": cmd_issue_show,
        "issue.update": cmd_issue_update,
        "issue.close": cmd_issue_close,
        "report.generate": cmd_report_generate,
        "report.show": cmd_report_show,
        "report.list": cmd_report_list,
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
        "project.map.refresh",
        "plan.generate",
        "plan.update",
        "plan.publish",
        "run.start",
        "run.quick",
        "run.cancel",
        "run.manual",
        "run.repair",
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
