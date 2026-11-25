"""Worker 拆分模块：execution。"""

from __future__ import annotations

import contextlib
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
    redact,
    scan_script,
    script_has_assertion,
)
from aitest.storage.files import read_json, write_json
from aitest.storage.workspace import Workspace, iso_now
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



def _adjust_component_command(method: str, workdir: Path) -> str:
    """Run tool commands from the subproject that owns their config file."""

    if not method.startswith("command:"):
        return method
    command = method[len("command:") :].strip()
    if not re.match(r"^alembic(?:\s|$)", command, re.IGNORECASE):
        return method
    if re.search(r"\s-c(?:\s|$)", command):
        return method
    try:
        children = sorted(workdir.iterdir())
    except OSError:
        return method
    for child in children:
        if child.is_dir() and (child / "alembic.ini").is_file():
            return f"command:cd {child.name} && {command}"
    return method



_DESTRUCTIVE_CALL_RE = re.compile(r"\.(delete|patch|put)\(", re.IGNORECASE)



def _mutates_seed_account(case: Case) -> bool:
    """A script must never delete or modify the seed superuser.

    Doing so destroys the shared database state for every later case and for
    the next run; the item must be verified with a disposable account instead.
    """
    content = (case.script_content or "").lower()
    if "first_superuser" not in content:
        return False
    if not _DESTRUCTIVE_CALL_RE.search(content):
        return False
    # 只有“改自己账号/密码”才会破坏种子状态；管理员删除其他用户是安全的。
    return "users/me" in content or "reset-password" in content



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



def _write_validated_script(
    generated_dir: Path,
    name: str,
    header: str,
    repaired: str,
    case: Case,
) -> str | None:
    """校验修复脚本安全性、断言完整性和路径边界后原子写入。"""
    if not repaired.strip() or scan_script(repaired) or not script_has_assertion(repaired):
        return None
    content = repaired if header.strip() in repaired else header + repaired
    if name.endswith(".py"):
        if (case.script_content or case.coverage_kind is not None) and blocking_lint_errors(
            name, content
        ):
            return None
        if case.script_content and not assertions_preserved(case.script_content, content):
            return None
    candidate = case.model_copy(update={"script_content": content})
    if _mutates_seed_account(candidate):
        return None
    try:
        target = ensure_within(generated_dir, generated_dir / name)
        generated_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except (ValueError, OSError):
        return None
    _update_generated_index(generated_dir, target.name, content)
    return content



def _repair_script_case(
    ws: Workspace,
    project_id: str,
    case: Case,
    generated_dir: Path,
    secrets: list[str],
    outputs: list[bytes],
    message: str,
) -> bool:
    """Ask the model to fix a failed generated script once, then rerun it.

    Generated scripts fail for ordinary setup reasons (missing auth, wrong
    status code, invalid fixture). Feeding the failure back is cheaper and more
    reliable than another whole-plan generation, and it applies to any project.
    """
    if not case.script_name or not case.script_content:
        return False
    try:
        from aitest.model_config import build_client

        client = build_client(ws, project_id)
    except AiTestError:
        return False
    if client is None or not hasattr(client, "repair_script"):
        return False
    error_text = (
        message + "\n" + b"\n".join(outputs).decode("utf-8", "replace")
    )[-4000:]
    try:
        repaired = client.repair_script(
            item_text=case.expected or case.title or "",
            expected=case.expected or "",
            script=case.script_content,
            error=redact(error_text, secrets),
        )
    except AiTestError:
        return False
    header = (
        "# generated by aitest\n"
        if case.script_name.endswith(".py")
        else "// generated by aitest\n"
    )
    content = _write_validated_script(
        generated_dir, case.script_name, header, repaired, case
    )
    if content is None:
        return False
    case.script_content = content
    return True



def _repair_case_as_script(
    ws: Workspace,
    project_id: str,
    case: Case,
    generated_dir: Path,
    secrets: list[str],
    outputs: list[bytes],
    message: str,
) -> bool:
    """Rewrite a failing http case as an equivalent script and rerun it.

    The http schema cannot express login flows, so a request that needs a token
    is better verified by a small script than left blocked.
    """
    if not case.verify_method.startswith("http:"):
        return False
    try:
        from aitest.model_config import build_client

        client = build_client(ws, project_id)
    except AiTestError:
        return False
    if client is None or not hasattr(client, "repair_script"):
        return False
    error_text = (
        message + "\n" + b"\n".join(outputs).decode("utf-8", "replace")
    )[-4000:]
    try:
        repaired = client.repair_script(
            item_text=case.expected or case.title or "",
            expected=case.expected or "",
            script=(
                f"当前验证方式是 {case.verify_method}（声明式请求，无法表达登录）。"
                "请改写为等效的 Python 脚本，需要认证时先从环境变量读取凭据。"
            ),
            error=redact(error_text, secrets),
        )
    except AiTestError:
        return False
    name = f"case_{case.case_id}.py"
    header = "# generated by aitest\n"
    content = _write_validated_script(generated_dir, name, header, repaired, case)
    if content is None:
        return False
    case.script_name = name
    case.script_content = content
    case.verify_method = f"script:{name}"
    return True



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
) -> RunnerResult:
    """运行生成的 .py 脚本。

    按脚本结构自动分流：
    1. 定义 pytest 可收集函数或 Test 类时走 pytest；
    2. 顶层同时定义了 setup() 与 verify() 时通过两阶段启动器隔离前置失败与业务失败；
    3. 其余顶层独立脚本直接运行解释器。
    """
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
                cancelled=cancelled,
            )
        return worker_attr("run_command", run_command)(
            f'"{python}" "{script_path}"',
            cwd=workdir,
            env=env,
            cancelled=cancelled,
        )
    pytest_result = worker_attr("run_command", run_command)(
        f'"{python}" -m pytest -q "{script_path}"',
        cwd=workdir,
        env=env,
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
            result = _run_python_script(
                python, script_path, workdir, env, cancelled
            )
            combined_out = (result.stderr or b"") + b"\n" + (result.stdout or b"")
            if (
                result.exit_code == SETUP_FAILED_EXIT_CODE
                or SETUP_FAILED_MARKER in combined_out
            ):
                return (
                    "blocked",
                    [item for item in (result.stdout, result.stderr) if item],
                    "前置准备阶段（setup）失败，未进入业务验收（不计为产品缺陷）",
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
            result = worker_attr("run_command", run_command)(
                f'{runner} test "{script_path}"',
                cwd=workdir,
                env=env,
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
        result = worker_attr("run_command", run_command)(
            f'{runner} test "{script}"',
            cwd=workdir,
            env=env,
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
        )
        auth_error = result.status == "failed" and (
            "HTTP 401" in (result.error or "") or "HTTP 403" in (result.error or "")
        )
        if auth_error:
            has_auth = any(
                isinstance(spec, dict)
                and isinstance(spec.get("headers"), dict)
                and any(
                    str(key).lower() in {"authorization", "cookie"}
                    for key in spec["headers"]
                )
                for spec in requests
            )
            if not has_auth:
                # 受保护接口未带凭据：这是用例前置条件缺失，不是产品失败。
                return (
                    "blocked",
                    [result.stdout] if result.stdout else [],
                    "接口需要认证，用例未带 Authorization 凭据（HTTP 401/403）",
                    [],
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



def _screenshot_files(workdir: Path, run_dir: Path) -> set[Path]:
    """PNG files currently under ``test-results`` in the project or run dir."""
    found: set[Path] = set()
    for base in (workdir, run_dir):
        root = base / "test-results"
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
    candidates = _screenshot_files(workdir, run_dir) - before
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



