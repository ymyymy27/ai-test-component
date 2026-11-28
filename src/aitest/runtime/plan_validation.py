"""Runtime 命令模块：plan_validation。"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from aitest.core.http_spec import normalize_extract
from aitest.core.manual import is_manual_case
from aitest.core.script_linter import assess_assertion_strength, blocking_lint_errors
from aitest.errors import AiTestError, invalid_request
from aitest.models import (
    AcceptanceItem,
    Case,
    Project,
    Task,
    case_item_ids,
)
from aitest.runtime._common import _safe_script_name, _string_list
from aitest.security import scan_script
from aitest.security import script_has_assertion as _script_has_assertion

_EXAMPLE_HOSTS = (
    "example.com",
    "example.org",
    "example.net",
    "httpbin.org",
    "jsonplaceholder.typicode.com",
)

_HTTP_VAR_RE = re.compile(r"\$\{vars\.([A-Za-z_][A-Za-z0-9_]*)\}")



def _invalid_method(case: Case, message: str) -> AiTestError:
    return invalid_request(f"用例 {case.case_id} 验证方式不合法：{message}")



def _parse_http_payload(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    variants = [cleaned, re.sub(r",\s*([}\]])", r"\1", cleaned)]
    for variant in variants:
        try:
            return json.loads(variant)
        except json.JSONDecodeError:
            pass
        pythonish = re.sub(r"\btrue\b", "True", variant)
        pythonish = re.sub(r"\bfalse\b", "False", pythonish)
        pythonish = re.sub(r"\bnull\b", "None", pythonish)
        with suppress(Exception):
            return ast.literal_eval(pythonish)
    return None


def _coerce_http_requests(raw: str) -> list[dict[str, Any]] | None:
    """接受模型常见的非规范 http 载荷：单对象、Python 字面量、双重编码字符串。"""
    candidates = [raw.strip()]
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] == "`":
        candidates.append(text[1:-1])
    for candidate in candidates:
        data = _parse_http_payload(candidate)
        if isinstance(data, str):
            data = _parse_http_payload(data)
        if isinstance(data, tuple):
            data = list(data)
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
            return data
    return None


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
        requests: Any = None
        try:
            requests = json.loads(raw)
        except json.JSONDecodeError:
            requests = None
        if not isinstance(requests, list) or not requests:
            coerced = _coerce_http_requests(raw)
            if coerced is None:
                raise _invalid_method(
                    case,
                    "http: 后必须是非空 JSON 请求数组；"
                    "无法给出请求数组时改用 script: 并提供 script_name 与 script_content",
                )
            requests = coerced
            case.verify_method = "http:" + json.dumps(
                requests, ensure_ascii=False, separators=(",", ":")
            )
        for item in requests:
            if not isinstance(item, dict):
                raise _invalid_method(case, "http: 每项必须是对象")
            for key in ("method", "url"):
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise _invalid_method(case, f"http: 每项需要非空 {key}")
            raw_extract = item.get("extract")
            if raw_extract is not None:
                if not isinstance(raw_extract, dict):
                    raise _invalid_method(
                        case, "http: extract 必须是 {变量名: 响应体路径} 对象"
                    )
                normalized_extract = normalize_extract(raw_extract)
                if raw_extract and not normalized_extract:
                    raise _invalid_method(
                        case, "http: extract 必须是 {变量名: 响应体路径} 对象"
                    )
                if normalized_extract != raw_extract:
                    item["extract"] = normalized_extract
                    case.verify_method = "http:" + json.dumps(
                        requests, ensure_ascii=False, separators=(",", ":")
                    )
        defined_vars: set[str] = set()
        for index, item in enumerate(requests, start=1):
            text = json.dumps(item, ensure_ascii=False)
            for name in _HTTP_VAR_RE.findall(text):
                if name not in defined_vars:
                    raise _invalid_method(
                        case,
                        f"http: 第 {index} 个请求引用了未定义变量 {name}；"
                        "必须先在前序请求的 extract 中提取",
                    )
            extract = item.get("extract")
            if isinstance(extract, dict):
                defined_vars.update(str(key) for key in extract)
            # 五条红线 #2：setup 阶段请求不得设置 4xx/5xx 期望或放入负向断言
            if str(item.get("stage") or "assert") == "setup":
                setup_expects = item.get("expect")
                if isinstance(setup_expects, list):
                    for exp in setup_expects:
                        if (
                            isinstance(exp, dict)
                            and exp.get("path") == "status"
                            and isinstance(exp.get("value"), int)
                            and exp["value"] >= 400
                        ):
                            raise _invalid_method(
                                case,
                                f"http: 第 {index} 个请求属于 setup 阶段，"
                                f"禁止期望 4xx/5xx 状态码（{exp['value']}）",
                            )
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
        "vitest",
        "vitest run",
        "npx vitest",
        "npx vitest run",
        "jest",
        "npx jest",
        "go test",
        "cargo test",
        "tox",
        "npx playwright test",
        "bunx playwright test",
        "pnpm exec playwright test",
    }
    if re.fullmatch(r"(?:python(?:3)?\s+-m\s+)?pytest(?:\s+-\S+)*", value):
        return value
    return value if value in whole_suite else ""



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
    r"([A-Za-z0-9_./\\-]+\.py)::([A-Za-z0-9_:]+(?:\[[^\]]+\])?)"
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



def _target_problems(
    cases: list[Case],
    intent: Literal["smoke", "business"],
    context: dict[str, Any],
) -> list[tuple[Case, str]]:
    """Return every case that names a test file without a test target.

    The validator raises on the first one, but repairing one case at a time
    leaves the next identical violation for the next round. Collecting them all
    lets a single repair round fix the whole plan.
    """
    problems: list[tuple[Case, str]] = []
    if intent != "business":
        return problems
    for case in cases:
        if case.layer == "L1":
            continue
        command = (
            case.verify_method.split(":", 1)[1]
            if ":" in case.verify_method
            else ""
        )
        file_match = _TEST_FILE_RE.search(command)
        if (
            file_match
            and "::" not in command
            and _mapped_test_value(context.get("test_functions"), file_match.group(1))
        ):
            problems.append(
                (
                    case,
                    f"用例 {case.case_id} 只执行测试文件，未定位到具体测试函数；"
                    f"使用 python -m pytest <文件>::<函数>",
                )
            )
            continue
        spec_match = re.search(r"([A-Za-z0-9_./\\-]+\.spec\.ts)\b", command)
        if (
            spec_match
            and not _PLAYWRIGHT_NODE_RE.search(command)
            and _mapped_test_value(
                context.get("playwright_tests"), spec_match.group(1)
            )
        ):
            problems.append(
                (
                    case,
                    f'用例 {case.case_id} 只执行 Playwright spec，未定位到测试标题；'
                    f'使用 --grep "<标题>"',
                )
            )
    return problems



def _match_python_test_name(node: str, names: object) -> bool:
    if not isinstance(names, list):
        return False
    if node in names:
        return True
    base = node.split("[", 1)[0]
    return base in names


def _test_node_key(case: Case, context: dict[str, Any]) -> str | None:
    """Return the existing test node a case points at, when it is real."""
    method = (case.verify_method or "").strip()
    command = method.split(":", 1)[1] if ":" in method else ""
    playwright = _PLAYWRIGHT_NODE_RE.search(command)
    if playwright:
        path = playwright.group(1).replace("\\", "/").lstrip("./")
        titles = _mapped_test_value(context.get("playwright_tests"), path)
        if isinstance(titles, list) and playwright.group(2) in titles:
            return f"{path}::{playwright.group(2)}"
        return None
    node = _TEST_NODE_RE.search(command)
    if node:
        path = node.group(1).replace("\\", "/").lstrip("./")
        names = _mapped_test_value(context.get("test_functions"), path)
        if _match_python_test_name(node.group(2), names):
            return f"{path}::{node.group(2)}"
    return None



def _text_overlap(left: str, right: str) -> int:
    def grams(text: str) -> set[str]:
        compact = re.sub(r"\s+", "", text)
        return {compact[index : index + 2] for index in range(len(compact) - 1)}

    return len(grams(left) & grams(right))



def _overshared_bindings(
    cases: list[Case],
    context: dict[str, Any],
    item_text: dict[str, str],
    min_owners: int = 3,
) -> dict[str, str]:
    """Find tests bound to many unrelated items so they can be replaced.

    A single test node that claims several acceptance items proves none of them
    individually. Keep the item whose text best matches the case, and ask for a
    dedicated verification of the rest instead of silently publishing shared
    evidence for everything.
    """
    owners: dict[str, dict[str, list[Case]]] = {}
    for case in cases:
        node = _test_node_key(case, context)
        if node is None:
            continue
        for item_id in case_item_ids(case):
            owners.setdefault(node, {}).setdefault(item_id, []).append(case)
    repairs: dict[str, str] = {}
    for node, by_item in owners.items():
        if len(by_item) < min_owners:
            continue
        scores = {
            item_id: _text_overlap(
                item_text.get(item_id, ""),
                "".join(
                    case.title + case.expected for case in cases_for_item
                ),
            )
            for item_id, cases_for_item in by_item.items()
        }
        best = max(scores.values())
        if best >= 2:
            # Keep every item the test can plausibly assert; only clearly
            # unrelated bindings need a dedicated verification.
            keep = {
                item_id
                for item_id, score in scores.items()
                if score >= max(2, int(best * 0.5))
            }
        else:
            keep = {item_id for item_id, score in scores.items() if score == best}
        if not keep:
            keep = {max(scores, key=lambda item_id: scores[item_id])}
        for item_id in by_item:
            if item_id in keep:
                continue
            repairs[item_id] = (
                f"测试节点 {node} 同时绑定到 {len(by_item)} 个验收项，"
                "一个测试无法逐项证明；除非该测试确实直接断言这一项，"
                "否则请生成直接验证该行为的 http:/script: 用例，"
                "或使用最相关的具体测试"
            )
    return repairs



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
        if method.startswith("script:"):
            # 生成脚本与项目自带测试同等对待：只要真的执行了断言，就是执行证据。
            # 没有断言的脚本只证明“命令能跑”，不能证明验收项，记 unverified。
            if _script_has_assertion(case.script_content or ""):
                case.coverage_kind = "direct"
            else:
                case.coverage_kind = "unverified"
                notes[case.case_id] = (
                    "脚本没有断言（或只打印/退出 0），不能证明验收项；"
                    "补充断言后重新生成计划"
                )
            continue
        if method.startswith("sql:"):
            # 模型写的 SQL 查询可能漏前置数据；失败按阻塞处理。
            case.coverage_kind = "unverified"
            notes[case.case_id] = (
                "模型生成的 SQL 查询未对应既有测试；通过不计为直接通过，"
                "失败需人工确认是产品缺陷还是查询前置条件不成立"
            )
            continue
        if method.startswith(("http:", "junit:")):
            # junit 导入项目自己的测试结果；http 是插件执行的声明式请求，
            # 预期写在用例里。未认证的 401/403 在执行阶段按阻塞处理。
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
            if _match_python_test_name(node, names):
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



def _normalize_http_expect(case: Case) -> None:
    """Accept ``body.email`` as a JSON path on the response body.

    The assertion resolver already starts at the body, so a ``body.`` prefix
    would look for a nested "body" key and fail on a correct response.
    """
    method = case.verify_method or ""
    if not method.startswith("http:"):
        return
    try:
        specs = json.loads(method[len("http:") :])
    except json.JSONDecodeError:
        return
    if not isinstance(specs, list):
        return
    changed = False
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        expects = spec.get("expect")
        if isinstance(expects, list):
            for check in expects:
                if not isinstance(check, dict):
                    continue
                path = check.get("path")
                if path == "status":
                    value = check.get("value")
                    # 字符串期望值（如 "ok"）不可能是 HTTP 状态码，按响应体字段处理；
                    # 模型即使漏写 body. 前缀也不会再被误判为 HTTP 200 断言。
                    if (
                        isinstance(value, str)
                        and value.strip()
                        and not value.strip().isdigit()
                    ):
                        check["path"] = "body.status"
                        changed = True
                        continue
                if isinstance(path, str) and path.startswith("body.") and len(path) > 5:
                    remainder = path[len("body.") :]
                    # ``status`` 保留给 HTTP 状态码；响应体字段恰好叫 status 时
                    # 必须保留 body. 前缀，否则会被误判成状态码断言。
                    if remainder != "status":
                        check["path"] = remainder
                        changed = True
    if changed:
        case.verify_method = "http:" + json.dumps(specs, ensure_ascii=False)



def _validate_plan_cases(
    cases: list[Case],
    intent: Literal["smoke", "business"],
    context: dict[str, Any],
    *,
    strict_script_lint: bool = False,
    collect_all: bool = False,
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
    seen_scripts: set[str] = set()
    seen_broad: set[str] = set()
    collected_errors: list[str] = []
    for case in cases:
        try:
            _normalize_http_expect(case)
            if intent == "business" and case.layer == "L1" and case_item_ids(case):
                # L1 是项目级检查，不参与 S/M 覆盖；业务验收项必须由业务用例证明。
                case.layer = "L2"
                case.warnings.append(
                    "业务验收项用例被模型标为 L1，已按业务用例规整为 L2"
                )
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", case.case_id):
                raise invalid_request(
                    f"用例 ID 不合法：{case.case_id}",
                    "用例 ID 只允许字母、数字、下划线和连字符，最多 128 字符",
                )
            if case.case_id in seen_ids:
                raise invalid_request(f"用例 ID 重复：{case.case_id}")
            seen_ids.add(case.case_id)
            if case.script_name:
                script_key = case.script_name.strip().lower()
                if script_key in seen_scripts:
                    raise invalid_request(
                        f"脚本文件名重复：{case.script_name}",
                        "每条脚本用例必须使用唯一 script_name",
                    )
                seen_scripts.add(script_key)
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
                        "使用 python -m pytest <文件>::<函数>；"
                        "函数名必须来自 context.test_functions",
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
                if strict_script_lint:
                    lint_errors = blocking_lint_errors(case.script_name or "", case.script_content)
                    if lint_errors:
                        raise invalid_request(
                            f"用例 {case.case_id} 的脚本未通过跑前静态安检："
                            f"{'；'.join(lint_errors)}",
                            "请补充针对产品输出的实质断言，避免恒真断言或空 except 吞异常",
                        )
                hosts = _script_example_hosts(case.script_content)
                if hosts:
                    raise invalid_request(
                        f"用例 {case.case_id} 的脚本访问了外网示例域名：{hosts[0]}"
                    )
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
        except AiTestError as exc:
            if not collect_all or exc.info.code != "INVALID_REQUEST":
                raise
            collected_errors.append(exc.info.message)
    if collected_errors:
        raise invalid_request("；".join(collected_errors), "修正所有不合法用例后重试")



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
    """Build deterministic L1 checks from declared project facts, never binding acceptance items."""
    del task, selected
    checks = _smoke_checks(context)
    return [
        _smoke_case(check["command"], None, check["expected"], check["source"])
        for check in checks
    ]



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



def _dedupe_script_names(cases: list[Case]) -> None:
    """Ensure concurrent batch generation never collides on generated script filenames."""
    seen: set[str] = set()
    for case in cases:
        if not case.script_name:
            continue
        raw = case.script_name.strip()
        key = raw.lower()
        if key not in seen:
            seen.add(key)
            continue
        parsed = Path(raw)
        candidate = f"{parsed.stem}_{case.case_id}{parsed.suffix}"
        suffix_num = 1
        while candidate.lower() in seen:
            suffix_num += 1
            candidate = f"{parsed.stem}_{case.case_id}_{suffix_num}{parsed.suffix}"
        case.script_name = candidate
        if (case.verify_method or "").startswith("script:"):
            case.verify_method = f"script:{candidate}"
        seen.add(candidate.lower())


def _annotate_assertion_strength(cases: list[Case]) -> None:
    """为计划中的每条用例确定性评定断言强度（strong / weak / unverified）。"""
    for case in cases:
        case.assertion_strength = assess_assertion_strength(case)


_REVIEW_BANNED_WORDS = (
    "全部通过",
    "建议上线",
    "可以上线",
    "建议发布",
    "可以发布",
    "质量良好",
    "验收通过",
    "测试通过",
    "alltestspassed",
    "testspassed",
    "readyforrelease",
    "readytoship",
    "passed",
    "failed",
)


def review_text_conclusion_free(text: str) -> bool:
    """检验评审说明是否不含禁止结论词（防止 AI 越权下结论）。"""
    compact = re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE).lower()
    return not any(word in compact for word in _REVIEW_BANNED_WORDS)


def case_dedupe_signature(
    item_id: str,
    scenario_type: str,
    assertion_target: str,
    data_shape: str,
) -> str:
    """计算确定性去重签名：item_id + scenario_type + assertion_target + data_shape。"""
    parts = [
        re.sub(r"\s+", " ", (item_id or "").strip().lower()),
        re.sub(r"\s+", " ", (scenario_type or "").strip().lower()),
        re.sub(r"\s+", " ", (assertion_target or "").strip().lower()),
        re.sub(r"\s+", " ", (data_shape or "").strip().lower()),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def infer_case_signature(case_like: dict[str, Any]) -> str:
    """从计划用例或候选补丁用例推导标准去重签名。"""
    item_id = str(case_like.get("acceptance_item_id") or "").strip()
    verify = str(case_like.get("verify_method") or "").strip()
    title = str(case_like.get("title") or "").strip()
    expected = str(case_like.get("expected") or "").strip()

    scenario_type = "general"
    assertion_target = ""
    data_shape = ""

    if verify.startswith("http:"):
        try:
            reqs = json.loads(verify[len("http:") :].strip())
        except json.JSONDecodeError:
            reqs = []
        if isinstance(reqs, list) and reqs:
            last = reqs[-1] if isinstance(reqs[-1], dict) else {}
            method = str(last.get("method") or "GET").upper()
            url = str(last.get("url") or "")
            path = re.sub(r"^https?://[^/]+", "", url)
            path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
            assertion_target = f"{method} {path}"
            raw_body = last.get("json")
            body: dict[str, Any] = raw_body if isinstance(raw_body, dict) else {}
            data_shape = ",".join(sorted(str(k) for k in body))
            raw_expects = last.get("expect")
            expects: list[Any] = raw_expects if isinstance(raw_expects, list) else []
            status_vals = [
                str(e.get("value"))
                for e in expects
                if isinstance(e, dict) and e.get("path") == "status"
            ]
            if any(s in {"401", "403"} for s in status_vals):
                scenario_type = f"negative_auth_{status_vals[0]}"
            elif any(s in {"400", "422"} for s in status_vals):
                scenario_type = "validation_4xx"
            elif method in {"POST", "PUT", "PATCH", "DELETE"}:
                scenario_type = f"write_{method.lower()}"
            else:
                scenario_type = "read"
    else:
        assertion_target = verify[:120] or title[:80]
        if any(k in f"{title} {expected}" for k in ("401", "未认证", "未登录")):
            scenario_type = "negative_auth_401"
        elif any(k in f"{title} {expected}" for k in ("403", "越权", "无权限")):
            scenario_type = "negative_auth_403"
        elif any(k in f"{title} {expected}" for k in ("400", "422", "必填", "校验")):
            scenario_type = "validation_4xx"

    return case_dedupe_signature(item_id, scenario_type, assertion_target, data_shape)


def dedupe_patch_cases(
    existing_cases: list[dict[str, Any]],
    candidate_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """过滤与已有用例或候选集合内部签名完全重复的用例，确保 exact duplicate = 0。"""
    seen = {infer_case_signature(c) for c in existing_cases}
    unique: list[dict[str, Any]] = []
    for cand in candidate_cases:
        if not isinstance(cand, dict):
            continue
        sig = infer_case_signature(cand)
        if sig in seen:
            continue
        seen.add(sig)
        unique.append(cand)
    return unique


def merge_and_filter_reviews(
    deterministic_reviews: list[dict[str, Any]],
    model_reviews: list[dict[str, Any]],
    existing_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并评审建议，强制过滤结论词、无证据项与重复补丁用例。"""
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    accumulated_cases = list(existing_cases)

    for raw_list, is_model in ((deterministic_reviews, False), (model_reviews, True)):
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            statement = str(entry.get("statement") or "").strip()
            if not statement:
                continue
            if is_model and not review_text_conclusion_free(statement):
                continue
            evidence = [
                str(e).strip() for e in (entry.get("evidence") or []) if str(e).strip()
            ]
            if is_model and not evidence:
                continue
            rid = str(entry.get("review_id") or "").strip() or (
                "rev_" + hashlib.sha256(statement.encode("utf-8")).hexdigest()[:10]
            )
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
            raw_patches = entry.get("patch_cases")
            normalized_patches: list[dict[str, Any]] = []
            if isinstance(raw_patches, list):
                for p in raw_patches:
                    if not isinstance(p, dict):
                        continue
                    item_copy = dict(p)
                    if not str(item_copy.get("case_id") or "").strip():
                        item_copy["case_id"] = (
                            f"case_rev_{infer_case_signature(item_copy)[:12]}"
                        )
                    normalized_patches.append(item_copy)
            patches = (
                dedupe_patch_cases(accumulated_cases, normalized_patches)
                if normalized_patches
                else []
            )
            accumulated_cases.extend(patches)
            severity = str(entry.get("severity") or "medium").lower()
            if severity not in {"high", "medium", "low"}:
                severity = "medium"
            merged.append(
                {
                    "review_id": rid,
                    "kind": str(entry.get("kind") or "blind_conflict"),
                    "severity": severity,
                    "statement": statement,
                    "evidence": evidence,
                    "patch_cases": patches,
                }
            )
    return merged



