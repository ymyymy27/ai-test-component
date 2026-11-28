"""Worker 拆分模块：execution。"""

from __future__ import annotations

import ast
import contextlib
import difflib
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

from aitest.core.script_linter import blocking_lint_errors, has_two_stage_entrypoints
from aitest.errors import AiTestError
from aitest.models import Case
from aitest.runners.base import RunnerResult
from aitest.runners.command_runner import run_command
from aitest.runners.http_runner import run_http_sequence
from aitest.runners.junit import parse_junit
from aitest.security import (
    assertions_preserved,
    ensure_within,
    scan_script,
    script_has_assertion,
)
from aitest.storage.files import read_json, write_json
from aitest.storage.workspace import iso_now
from aitest.worker_impl.env import _SCREENSHOT_RE, worker_attr
from aitest.worker_impl.services import _is_e2e_command


def _junit_verdict(results: list[dict[str, object]]) -> tuple[str, str]:
    if not results:
        return "error", "JUnit 报告没有 testcase"
    statuses = [str(item.get("status", "")) for item in results]
    failed = sum(1 for status in statuses if status in {"failed", "error"})
    skipped = sum(1 for status in statuses if status == "skipped")
    if failed:
        return "failed", f"JUnit 失败或错误 {failed} 条"
    if skipped:
        return "blocked", f"JUnit 跳过 {skipped} 条，未得到完整结果"
    return "passed", f"JUnit 通过 {len(results)} 条"



def _junit_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)



def _junit_signatures(run_dir: Path | None, workdir: Path) -> dict[str, tuple[int, int] | None]:
    paths: list[Path] = []
    if run_dir is not None:
        paths.append(run_dir / "junit.xml")
    paths.append(workdir / "junit.xml")
    return {str(path): _junit_signature(path) for path in paths}



def _junit_from_output(
    stdout: bytes,
    run_dir: Path | None,
    workdir: Path,
    before: dict[str, tuple[int, int] | None],
) -> bytes | None:
    stripped = stdout.lstrip()
    if stripped.startswith(b"<?xml"):
        return stdout
    for marker in (b"<testsuites", b"<testsuite"):
        index = stdout.find(marker)
        if index >= 0:
            return stdout[index:]
    candidates: list[Path] = []
    if run_dir is not None:
        candidates.append(run_dir / "junit.xml")
    candidates.append(workdir / "junit.xml")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        # Only accept a file this command wrote/updated; a committed or
        # previous-case junit.xml is not evidence for the current command.
        if before.get(str(candidate)) == _junit_signature(candidate):
            continue
        try:
            return candidate.read_bytes()
        except OSError:
            continue
    return None



_DESTRUCTIVE_CALL_RE = re.compile(r"\.(delete|patch|put)\(", re.IGNORECASE)
_DESTRUCTIVE_HTTP_RE = re.compile(
    r"(?i)(?:\b(?:PATCH|PUT|DELETE)\b|requests\.(?:patch|put|delete)\s*\()"
)
_WRITE_HTTP_RE = re.compile(
    r"(?i)(?:\b(?:POST|PATCH|PUT|DELETE)\b|requests\.(?:post|patch|put|delete)\s*\()"
)


def _http_case_text(case: Case) -> str:
    return "\n".join(
        part for part in (case.script_content or "", case.verify_method or "") if part
    )


def _risky_retry(case: Case) -> bool:
    """写状态用例不能原样重跑：首次失败已经改了状态，重跑可能误通过。"""
    if getattr(case, "mutates_state", False):
        return True
    return bool(_WRITE_HTTP_RE.search(_http_case_text(case)))


def _destructive_case(case: Case) -> bool:
    """修改/删除既有状态的用例排到最后，避免污染只读与创建类用例。"""
    if getattr(case, "mutates_state", False):
        return True
    return bool(_DESTRUCTIVE_HTTP_RE.search(_http_case_text(case)))



def _update_generated_index(generated_dir: Path, name: str, content: str) -> None:
    """脚本修复落盘后同步更新 generated/index.json 中的校验哈希。"""
    index_path = generated_dir / "index.json"
    if not index_path.is_file():
        return
    try:
        data = read_json(index_path)
    except (AiTestError, OSError):
        return
    files = data.get("files")
    if not isinstance(files, list):
        return
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = iso_now()
    for entry in files:
        if isinstance(entry, dict) and entry.get("name") == name:
            entry["sha256"] = digest
            entry["generated_at"] = now
            entry["model"] = "repair"
            break
    else:
        files.append(
            {
                "name": name,
                "sha256": digest,
                "generated_at": now,
                "model": "repair",
                "secrets_redacted": False,
            }
        )
    with contextlib.suppress(AiTestError, OSError):
        write_json(index_path, data)


def validate_script_repair(
    original_script: str,
    repaired_script: str,
    case: Case,
    *,
    script_name: str,
    header: str | None = None,
) -> tuple[str | None, list[str], str]:
    """统一执行脚本修复安全守卫校验并生成 unified_diff。

    硬拦截顺序：空内容 / 危险操作 / 缺少断言 / blocking_lint_errors /
    assertions_preserved。任一命中均返回 None 与守卫列表。
    """
    eff_header = (
        header
        if header is not None
        else (
            "# generated by aitest\n"
            if script_name.endswith(".py")
            else "// generated by aitest\n"
        )
    )
    if not repaired_script.strip():
        return None, ["empty_script"], ""
    content = (
        repaired_script
        if eff_header.strip() and eff_header.strip() in repaired_script
        else eff_header + repaired_script
    )
    diff_lines = list(
        difflib.unified_diff(
            (original_script or "").splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{script_name}",
            tofile=f"b/{script_name}",
            n=3,
        )
    )
    unified_diff = "".join(diff_lines)
    guards: list[str] = []
    if scan_script(repaired_script):
        guards.append("dangerous_script")
    if not script_has_assertion(repaired_script):
        guards.append("missing_assertion")
    if script_name.endswith(".py"):
        if (case.script_content or case.coverage_kind is not None) and blocking_lint_errors(
            script_name, content
        ):
            guards.append("blocking_lint_errors")
        if original_script and not assertions_preserved(original_script, content):
            guards.append("assertions_preserved")
    if guards:
        return None, guards, unified_diff
    return content, [], unified_diff


def _top_level_func_ast_dump(script: str, func_name: str) -> str | None:
    """提取脚本顶层指定函数的 AST 结构摘要（忽略行号），用于守卫 verify() 零改动。"""
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.dump(node, include_attributes=False)
    return None


def verify_setup_only_repair(
    case: Case,
    *,
    repaired_script: str | None = None,
    repaired_verify_method: str | None = None,
    repaired_expected: str | None = None,
    repaired_expectation_source: str | None = None,
    setup_only: bool = True,
) -> tuple[str | None, str | None, list[str], str]:
    """针对 setup 数据/构造修订的双重 AST 与步骤守卫校验。

    返回 ``(validated_script, validated_verify_method, guards, unified_diff)``：
    - 严禁修改 ``expected`` 或 ``expectation_source``；
    - 两阶段 Python 脚本（含顶层 ``setup`` 与 ``verify``）时，``verify()`` 的 AST 必须完全一致；
    - ``http:`` 用例时，所有 ``stage != 'setup'`` 的断言请求条目必须深度完全一致。
    """
    guards: list[str] = []
    if repaired_expected is not None and repaired_expected != case.expected:
        guards.append("expectation_changed")
    if (
        repaired_expectation_source is not None
        and repaired_expectation_source != case.expectation_source
    ):
        guards.append("expectation_changed")

    method = (case.verify_method or "").strip()
    if method.startswith("http:") and repaired_verify_method is not None:
        raw_new = repaired_verify_method.strip()
        raw_new_json = (
            raw_new[len("http:") :].strip() if raw_new.startswith("http:") else raw_new
        )
        raw_old_json = method[len("http:") :].strip()
        try:
            old_reqs = json.loads(raw_old_json)
        except json.JSONDecodeError:
            old_reqs = []
        try:
            new_reqs = json.loads(raw_new_json)
        except json.JSONDecodeError:
            diff_lines = list(
                difflib.unified_diff(
                    raw_old_json.splitlines(keepends=True),
                    raw_new_json.splitlines(keepends=True),
                    fromfile=f"a/{case.case_id}.http.json",
                    tofile=f"b/{case.case_id}.http.json",
                    n=3,
                )
            )
            return None, None, [*guards, "invalid_http_json"], "".join(diff_lines)
        if not isinstance(new_reqs, list) or not new_reqs or not all(
            isinstance(item, dict) for item in new_reqs
        ):
            return None, None, [*guards, "invalid_http_requests"], ""
        defined_vars: set[str] = set()
        for req in new_reqs:
            if str(req.get("stage") or "assert") == "setup":
                expects = req.get("expect")
                if isinstance(expects, list):
                    for exp in expects:
                        if (
                            isinstance(exp, dict)
                            and exp.get("path") == "status"
                            and isinstance(exp.get("value"), int)
                            and exp["value"] >= 400
                        ):
                            guards.append("setup_negative_expect")
            extract = req.get("extract")
            if isinstance(extract, dict):
                defined_vars.update(str(k) for k in extract)
        if setup_only:
            old_asserts = [
                item
                for item in (old_reqs if isinstance(old_reqs, list) else [])
                if isinstance(item, dict) and str(item.get("stage") or "assert") != "setup"
            ]
            new_asserts = [
                item
                for item in new_reqs
                if str(item.get("stage") or "assert") != "setup"
            ]
            if old_asserts != new_asserts:
                guards.append("assert_step_changed")
        formatted_old = json.dumps(old_reqs, ensure_ascii=False, indent=2)
        formatted_new = json.dumps(new_reqs, ensure_ascii=False, indent=2)
        diff = "".join(
            difflib.unified_diff(
                formatted_old.splitlines(keepends=True),
                formatted_new.splitlines(keepends=True),
                fromfile=f"a/{case.case_id}.http.json",
                tofile=f"b/{case.case_id}.http.json",
                n=3,
            )
        )
        dedup_guards = list(dict.fromkeys(guards))
        if dedup_guards:
            return None, None, dedup_guards, diff
        canonical_method = "http:" + json.dumps(new_reqs, ensure_ascii=False)
        return None, canonical_method, [], diff

    if repaired_verify_method is not None and repaired_verify_method != case.verify_method:
        guards.append("verify_method_changed")

    if repaired_script is not None and case.script_name:
        original_script = case.script_content or ""
        validated, base_guards, unified_diff = validate_script_repair(
            original_script,
            repaired_script,
            case,
            script_name=case.script_name,
        )
        guards.extend(base_guards)
        if (
            setup_only
            and original_script
            and case.script_name.endswith(".py")
            and has_two_stage_entrypoints(original_script)
        ):
            old_verify = _top_level_func_ast_dump(original_script, "verify")
            new_verify = _top_level_func_ast_dump(validated or repaired_script, "verify")
            if old_verify is None or new_verify != old_verify:
                guards.append("verify_ast_changed")
        dedup_guards = list(dict.fromkeys(guards))
        if validated is None or dedup_guards:
            return None, None, dedup_guards, unified_diff
        return validated, None, [], unified_diff

    return None, None, [*guards, "missing_repair_candidate"], ""


def _write_validated_script(
    generated_dir: Path,
    name: str,
    header: str,
    repaired: str,
    case: Case,
) -> str | None:
    """校验修复脚本安全性、断言完整性和路径边界后原子写入。"""
    content, guards, _diff = validate_script_repair(
        case.script_content or "",
        repaired,
        case,
        script_name=name,
        header=header,
    )
    if content is None or guards:
        return None
    try:
        target = ensure_within(generated_dir, generated_dir / name)
        generated_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except (ValueError, OSError):
        return None
    _update_generated_index(generated_dir, target.name, content)
    return content



_FRAME_RE = re.compile(
    r'(?:File "([^"]+\.py)", line \d+|([^\s:\n"]+\.py):\d+:)'
)


def _script_self_error(stderr: bytes) -> bool:
    """仅当确系生成脚本自身语法、导入或最深栈帧在 generated/ 内的签名错误时返回 True。"""
    markers = (
        b"SyntaxError",
        b"IndentationError",
        b"ImportError",
        b"ModuleNotFoundError",
        b"NameError: name",
    )
    if any(marker in stderr for marker in markers):
        return True
    if b"TypeError: " in stderr or b"AttributeError: " in stderr:
        text = stderr.decode("utf-8", errors="replace")
        frames = [
            (match.group(1) or match.group(2)).replace("\\", "/").lower()
            for match in _FRAME_RE.finditer(text)
        ]
        if not frames:
            return False
        last_frame = frames[-1]
        return "/generated/" in last_frame or last_frame.startswith("generated/")
    return False



SETUP_FAILED_EXIT_CODE = 42
SETUP_FAILED_MARKER = b"[AITEST_STAGE:SETUP_FAILED]"

_SETUP_5XX_RE = re.compile(
    r"(?:status(?:_code)?\s*[=:]\s*50\d|\bHTTP\s+50\d\b"
    r"|failed\s+50\d\s*:|Internal Server Error|\"code\"\s*:\s*50\d)",
    re.IGNORECASE,
)
_SETUP_API_ERR_RE = re.compile(
    r"(?:failed\s+40[0-9]\s*:|\bHTTP\s+40[0-9]\b|\"code\"\s*:\s*40[0-9])",
    re.IGNORECASE,
)


def _setup_product_failure_detail(combined_out: bytes) -> tuple[str, bool]:
    """区分 setup 中的被测接口 5xx 异常、业务接口鉴权/路由异常与普通前置环境问题。"""
    text = combined_out.decode("utf-8", errors="replace")
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and "[AITEST_STAGE:SETUP_FAILED]" not in ln
    ]
    key_line = ""
    for ln in reversed(lines):
        if any(k in ln for k in ("AssertionError:", "Error:", "failed ", "status")):
            key_line = ln
            break
    if not key_line and lines:
        key_line = lines[-1]
    excerpt = key_line[:220]
    if _SETUP_5XX_RE.search(text):
        return (
            f"前置准备阶段（setup）调用目标接口返回服务端异常（{excerpt}），疑似产品缺陷",
            True,
        )
    if _SETUP_API_ERR_RE.search(text):
        return (
            f"前置准备阶段（setup）调用目标接口异常（{excerpt}），请核查接口缺陷或测试前提",
            False,
        )
    if excerpt:
        return f"前置准备阶段（setup）失败（{excerpt}），未进入业务验收（不计为产品缺陷）", False
    return "前置准备阶段（setup）失败，未进入业务验收（不计为产品缺陷）", False

_STAGE_RUNNER_CODE = """\
# aitest two-stage script runner (setup -> verify)
import importlib.util
import inspect
import sys
import traceback

target = sys.argv[1]
spec = importlib.util.spec_from_file_location("_aitest_two_stage_case", target)
if spec is None or spec.loader is None:
    raise RuntimeError(f"无法加载测试脚本：{target}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

try:
    ctx = mod.setup()
except BaseException as exc:
    if isinstance(exc, SystemExit) and exc.code in (None, 0):
        ctx = None
    else:
        traceback.print_exc(file=sys.stderr)
        sys.stderr.write("\\n[AITEST_STAGE:SETUP_FAILED]\\n")
        sys.exit(42)

verify_fn = mod.verify
sig = inspect.signature(verify_fn)
positional = [
    p
    for p in sig.parameters.values()
    if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
]
has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
if positional or has_varargs:
    verify_fn(ctx)
else:
    verify_fn()
"""


def _ensure_stage_runner(runner_dir: Path) -> Path:
    """按需写入隔离于宿主 site-packages 的纯标准库两阶段脚本启动器。"""
    runner_path = runner_dir / "_stage_runner.py"
    if not runner_path.is_file():
        runner_dir.mkdir(parents=True, exist_ok=True)
        runner_path.write_text(_STAGE_RUNNER_CODE, encoding="utf-8")
    return runner_path


_PYTEST_DEF_RE = re.compile(
    r"(?m)^\s*(?:async\s+)?def\s+(?:test_|check_)|^\s*class\s+Test"
)


def _run_python_script(
    python: Path,
    script_path: Path,
    workdir: Path,
    env: dict[str, str],
    cancelled: Callable[[], bool] | None,
    *,
    timeout_seconds: float = 600.0,
) -> RunnerResult:
    """运行生成的 .py 脚本，严格透传剩余超时预算并区分 pytest / 两阶段 / 直接脚本。"""
    try:
        source = script_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""
    if not _PYTEST_DEF_RE.search(source):
        if has_two_stage_entrypoints(source):
            runner_path = _ensure_stage_runner(script_path.parent)
            return worker_attr("run_command", run_command)(
                f'"{python}" "{runner_path}" "{script_path}"',
                cwd=workdir,
                env=env,
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
            )
        return worker_attr("run_command", run_command)(
            f'"{python}" "{script_path}"',
            cwd=workdir,
            env=env,
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
        )
    pytest_result = worker_attr("run_command", run_command)(
        f'"{python}" -m pytest -q "{script_path}"',
        cwd=workdir,
        env=env,
        timeout_seconds=timeout_seconds,
        cancelled=cancelled,
    )
    fallback = pytest_result.exit_code == 5 or b"No module named pytest" in pytest_result.stderr
    if fallback:
        if pytest_result.exit_code == 5 and re.search(
            r"(?m)^\s*def (test_|check_)", source
        ):
            return RunnerResult(
                status="error",
                exit_code=5,
                stderr=b"pytest collected no tests; script only defines test functions",
                error="脚本定义了测试函数但没有被 pytest 收集，未执行任何断言",
            )
        return worker_attr("run_command", run_command)(
            f'"{python}" "{script_path}"',
            cwd=workdir,
            env=env,
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
        )
    return pytest_result



_SCRIPT_PRECONDITION_MARKERS = (
    (b"strict mode violation", "页面定位匹配到多个元素，脚本选择器需要收窄"),
    (b"econnrefused", "目标服务未启动或端口不通"),
    (b"err_connection_refused", "目标服务未启动或端口不通"),
    (b"connection refused", "目标服务未启动或端口不通"),
    (b"command not found", "命令不在当前环境，需使用项目运行器（uv/poetry/bun）"),
    (b"is not recognized", "命令不在当前环境，需使用项目运行器（uv/poetry/bun）"),
)


def _playwright_runner(workdir: Path) -> str:
    """根据项目锁文件选择对应的 Playwright 运行器命令。"""
    if (workdir / "bun.lock").is_file() or (workdir / "bun.lockb").is_file():
        return "bunx playwright"
    if (workdir / "pnpm-lock.yaml").is_file():
        return "pnpm exec playwright"
    return "npx playwright"



def _script_precondition_problem(output: bytes) -> str:
    """仅识别明确的基础设施或选择器前提错误，绝不吞掉认证或产品异常。"""
    text = output.lower()
    for marker, reason in _SCRIPT_PRECONDITION_MARKERS:
        if marker in text:
            return f"脚本前置条件不成立（{reason}），不算产品失败"
    return ""



def _script_runtime_problem(output: bytes, content: str) -> str:
    """已废除吞 Bug 降级：未捕获的产品异常保留为失败结果。"""
    del output, content
    return ""



def _execute(
    case: Case,
    workdir: Path,
    generated_dir: Path,
    env: dict[str, str],
    python: Path,
    *,
    request_id_header: str = "",
    request_id: str = "",
    run_dir: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
    allowed_hosts: list[str] | None = None,
) -> tuple[str, list[bytes], str, list[tuple[str, bytes]]]:
    method = case.verify_method or ""
    command_timeout = 180 if _is_e2e_command(method) else 600
    if method.startswith("command:"):
        result = worker_attr("run_command", run_command)(
            method[len("command:") :].strip(),
            cwd=workdir,
            env=env,
            timeout_seconds=command_timeout,
            cancelled=cancelled,
        )
    elif method.startswith("script:"):
        name = method[len("script:") :].strip()
        try:
            script_path = ensure_within(generated_dir, generated_dir / name)
        except ValueError:
            return "error", [], f"脚本路径越界：{name}", []
        if script_path.suffix == ".py":
            if case.coverage_kind is not None:
                try:
                    script_src = script_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    script_src = case.script_content or ""
                lint_errors = blocking_lint_errors(script_path.name, script_src)
                if lint_errors:
                    return (
                        "error",
                        [],
                        f"脚本未通过跑前静态安检：{'；'.join(lint_errors)}",
                        [],
                    )
            case_env = dict(env)
            if run_dir is not None:
                shot_dir = run_dir / "screenshots" / case.case_id
                shot_dir.mkdir(parents=True, exist_ok=True)
                case_env["PLAYWRIGHT_OUTPUT_DIR"] = str(shot_dir)
                case_env["PLAYWRIGHT_TEST_OUTPUT_DIR"] = str(shot_dir)
            result = _run_python_script(
                python,
                script_path,
                workdir,
                case_env,
                cancelled,
                timeout_seconds=float(command_timeout),
            )
            combined_out = (result.stderr or b"") + b"\n" + (result.stdout or b"")
            if (
                result.exit_code == SETUP_FAILED_EXIT_CODE
                or SETUP_FAILED_MARKER in combined_out
            ):
                setup_msg, setup_is_fatal = _setup_product_failure_detail(combined_out)
                return (
                    "failed" if setup_is_fatal else "blocked",
                    [item for item in (result.stdout, result.stderr) if item],
                    setup_msg,
                    [],
                )
            if result.status == "failed" and _script_self_error(combined_out):
                return (
                    "error",
                    [item for item in (result.stdout, result.stderr) if item],
                    "脚本自身错误（语法或导入失败），不算产品失败",
                    [],
                )
            precondition = _script_precondition_problem(combined_out)
            if result.status == "failed" and precondition:
                return (
                    "blocked",
                    [item for item in (result.stdout, result.stderr) if item],
                    precondition,
                    [],
                )
        elif script_path.suffix in {".ts", ".js"}:
            runner = _playwright_runner(workdir)
            case_env = dict(env)
            output_arg = ""
            if run_dir is not None:
                shot_dir = run_dir / "screenshots" / case.case_id
                shot_dir.mkdir(parents=True, exist_ok=True)
                case_env["PLAYWRIGHT_OUTPUT_DIR"] = str(shot_dir)
                output_arg = f' --output="{shot_dir}"'
            result = worker_attr("run_command", run_command)(
                f'{runner} test "{script_path}"{output_arg}',
                cwd=workdir,
                env=case_env,
                timeout_seconds=command_timeout,
                cancelled=cancelled,
            )
            if result.status == "failed" and b"No tests found" in (
                result.stdout + result.stderr
            ):
                return (
                    "error",
                    [item for item in (result.stdout, result.stderr) if item],
                    "生成的 Playwright 脚本没有可执行测试",
                    [],
                )
        else:
            return "error", [], f"未知脚本类型：{script_path.suffix}", []
    elif method.startswith("playwright:"):
        script = method[len("playwright:") :].strip()
        runner = _playwright_runner(workdir)
        case_env = dict(env)
        output_arg = ""
        if run_dir is not None:
            shot_dir = run_dir / "screenshots" / case.case_id
            shot_dir.mkdir(parents=True, exist_ok=True)
            case_env["PLAYWRIGHT_OUTPUT_DIR"] = str(shot_dir)
            output_arg = f' --output="{shot_dir}"'
        result = worker_attr("run_command", run_command)(
            f'{runner} test "{script}"{output_arg}',
            cwd=workdir,
            env=case_env,
            timeout_seconds=command_timeout,
            cancelled=cancelled,
        )
    elif method.startswith("http:"):
        try:
            requests = json.loads(method[len("http:") :])
        except json.JSONDecodeError:
            return "error", [], "http 验证方法不是合法 JSON", []
        if not isinstance(requests, list):
            return "error", [], "http 验证方法必须是请求数组", []
        result = run_http_sequence(
            requests,
            request_id_header=request_id_header or None,
            request_id=request_id or None,
            cancelled=cancelled,
            allowed_hosts=allowed_hosts,
            env=env,
        )
    elif method.startswith("sql:"):
        result = worker_attr("run_command", run_command)(
            method[len("sql:") :].strip(), cwd=workdir, env=env, cancelled=cancelled
        )
        outputs = [item for item in (result.stdout, result.stderr) if item]
        message = result.error
        if not message and result.status == "failed":
            message = f"SQL 命令退出码 {result.exit_code}"
        return result.status, outputs, message, []
    elif method.startswith("junit:"):
        command = method[len("junit:") :].strip()
        junit_before = _junit_signatures(run_dir, workdir)
        result = worker_attr("run_command", run_command)(
            command, cwd=workdir, env=env, cancelled=cancelled
        )
        outputs = [item for item in (result.stdout, result.stderr) if item]
        if result.status == "blocked":
            return "blocked", outputs, result.error or "JUnit 命令超时或未完成", []
        xml = _junit_from_output(result.stdout, run_dir, workdir, junit_before)
        if xml is None:
            return "error", outputs, "未找到 JUnit XML（命令输出或 junit.xml）", []
        try:
            parsed = parse_junit(xml)
        except ValueError as exc:
            return "error", outputs, f"JUnit XML 无法解析：{exc}", []
        status, message = _junit_verdict(parsed)
        return status, outputs, message, [("junit.xml", xml)]
    elif method.startswith("pytest:"):
        args = method[len("pytest:") :].strip()
        if run_dir is None:
            return "error", [], "pytest 需要运行目录以写入 JUnit 报告", []
        xml_path = run_dir / "junit.xml"
        xml_path.unlink(missing_ok=True)
        command = f'"{python}" -m pytest {args} --junitxml="{xml_path}"'
        result = worker_attr("run_command", run_command)(
            command, cwd=workdir, env=env, cancelled=cancelled
        )
        outputs = [item for item in (result.stdout, result.stderr) if item]
        if result.status == "blocked":
            blocked_artifacts = (
                [("junit.xml", xml_path.read_bytes())] if xml_path.is_file() else []
            )
            return (
                "blocked",
                outputs,
                result.error or "pytest 执行被阻塞或取消",
                blocked_artifacts,
            )
        if not xml_path.is_file():
            return "error", outputs, "pytest 未生成 JUnit 报告", []
        try:
            xml = xml_path.read_bytes()
            parsed = parse_junit(xml)
        except (OSError, ValueError) as exc:
            return "error", outputs, f"JUnit 报告无法读取：{exc}", []
        status, message = _junit_verdict(parsed)
        return status, outputs, message, [("junit.xml", xml)]
    else:
        return "error", [], f"暂不支持的验证方式：{method or '空'}", []
    outputs = [item for item in (result.stdout, result.stderr) if item]
    message = result.error or ""
    if result.status == "failed" and method.startswith(("script:", "playwright:")):
        precondition = _script_precondition_problem(
            (result.stderr or b"") + b"\n" + (result.stdout or b"")
        )
        if precondition:
            return "blocked", outputs, precondition, []
    return result.status, outputs, message, []



def _screenshot_files(workdir: Path, run_dir: Path, case_id: str = "") -> set[Path]:
    """PNG files currently under ``test-results`` or case-isolated ``screenshots/<case_id>``."""
    found: set[Path] = set()
    candidates = [workdir / "test-results", run_dir / "test-results"]
    if case_id:
        candidates.append(run_dir / "screenshots" / case_id)
    for root in candidates:
        if root.is_dir():
            found.update(path for path in root.rglob("*.png") if path.is_file())
    return found



def _script_screenshot_paths(script_content: str | None, workdir: Path) -> list[Path]:
    """Explicit ``page.screenshot(path=...)`` targets that live inside the project."""
    if not script_content:
        return []
    root = workdir.resolve()
    paths: list[Path] = []
    for match in _SCREENSHOT_RE.finditer(script_content):
        candidate = Path(match.group(1).strip())
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if not (resolved == root or root in resolved.parents):
            continue
        paths.append(resolved)
    return paths



def _screenshot_artifacts(
    case: Case, workdir: Path, run_dir: Path, before: set[Path]
) -> list[tuple[str, bytes]]:
    """New screenshots produced by a script or Playwright run, as evidence pairs."""
    candidates = _screenshot_files(workdir, run_dir, case.case_id) - before
    candidates.update(
        path for path in _script_screenshot_paths(case.script_content, workdir) if path.is_file()
    )
    artifacts: list[tuple[str, bytes]] = []
    for index, path in enumerate(sorted(candidates)):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        artifacts.append((f"screenshot-{index}-{path.name}", data))
    return artifacts



