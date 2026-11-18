"""One-shot run worker: executes cases, writes evidence and run state, then exits."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast

from aitest.config import known_secret_values
from aitest.core.analysis import analyze_run
from aitest.core.evidence import (
    DEFAULT_FILE_CAP_BYTES,
    data_prefix,
    evidence_bytes,
    save_evidence,
    settings_from_config,
)
from aitest.core.issues import draft_failed_issues
from aitest.core.manual import is_manual_case
from aitest.core.reports import build_report
from aitest.errors import AiTestError
from aitest.models import Attempt, Case, Delivery, Plan, Project, Run
from aitest.procutil import process_created_at
from aitest.runners.base import RunnerResult
from aitest.runners.command_runner import run_command
from aitest.runners.http_runner import run_http_sequence
from aitest.runners.junit import parse_junit
from aitest.security import is_secret_field, redact, redact_bytes
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

MAX_FILE_BYTES = DEFAULT_FILE_CAP_BYTES
SERVICES_FILE = "project.config.json"
DEFAULT_REQUEST_ID_HEADER = "X-Request-Id"
DELIVERY_INCOMPLETE_MESSAGE = "交付信息不完整：缺运行方式或版本"
_SINGLE_SERVICE_KEYS = frozenset({"start", "health", "stop"})
_SCREENSHOT_RE = re.compile(
    r"page\.screenshot\(\s*[^)]*?[\"']?path[\"']?\s*[=:]\s*[\"']([^\"']+)[\"']", re.DOTALL
)
_LOCK = threading.Lock()
EvidenceSettings = tuple[int, int, bool]


def _save(run_path: Path, run: Run) -> None:
    with _LOCK:
        write_model(run_path, run)


def _touch(run_path: Path) -> None:
    with _LOCK:
        current = read_model(run_path, Run)
        current.heartbeat_at = iso_now()
        write_model(run_path, current)


def _save_evidence(
    run_dir: Path,
    name: str,
    data: bytes,
    result: str,
    settings: EvidenceSettings,
    secrets: list[str],
) -> str | None:
    budget, file_cap, allow_video = settings
    return save_evidence(
        run_dir,
        name,
        data,
        result=result,
        budget_bytes=budget,
        allow_video=allow_video,
        file_cap_bytes=file_cap,
        secrets=secrets,
    )


def _venv_python(root: Path) -> Path | None:
    for base in (root, root.parent, root.parent.parent):
        for candidate in (
            base / ".venv" / "Scripts" / "python.exe",
            base / ".venv" / "bin" / "python",
        ):
            if candidate.is_file():
                return candidate
    return None


def _run_env(root: Path, python: Path | None, run_id: str) -> dict[str, str]:
    env: dict[str, str] = {}
    package_root = Path(__file__).resolve().parents[1]
    for key, value in os.environ.items():
        if is_secret_field(key):
            continue
        if key == "PYTHONPATH":
            parts = [
                item
                for item in value.split(os.pathsep)
                if item and Path(item).resolve() != package_root
            ]
            if parts:
                env[key] = os.pathsep.join(parts)
            continue
        env[key] = value
    if python is not None:
        env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(python.parent.parent)
        # A parent interpreter's PYTHONHOME poisons the project interpreter and
        # makes its stdlib mix with a different Python version.
        env.pop("PYTHONHOME", None)
    env["AITEST_RUN_ID"] = run_id
    env["AITEST_DATA_PREFIX"] = data_prefix(run_id)
    return env


def _project_config(ws: Workspace, project_id: str) -> dict[str, object]:
    path = ws.project_dir(project_id) / SERVICES_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _services(config: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Return every configured service as ``(name, config)``.

    Two shapes are accepted, matching what the panel stores:
    * a single service ``{start, health, stop, ...}`` (name is empty);
    * a mapping ``service name -> config``.
    Anything else is treated as "no service configured".
    """
    value = config.get("services")
    if not isinstance(value, dict):
        return []
    if _SINGLE_SERVICE_KEYS & set(value):
        return [("", value)]
    if not value or not all(isinstance(item, dict) for item in value.values()):
        return []
    services: list[tuple[str, dict[str, object]]] = []
    for name, item in value.items():
        if isinstance(item, dict):
            services.append((str(name), cast(dict[str, object], item)))
    return services


def _delivery_incomplete(ws: Workspace, project_id: str, task_id: str) -> bool:
    path = ws.project_dir(project_id) / "deliveries" / f"{task_id}.json"
    if not path.is_file():
        return False
    try:
        delivery = read_model(path, Delivery)
    except AiTestError:
        return False
    return not delivery.complete


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _evidence_settings(config: dict[str, object]) -> EvidenceSettings:
    return settings_from_config(config)


def _request_id_header(config: dict[str, object]) -> str:
    value = config.get("link")
    link = value if isinstance(value, dict) else {}
    header = link.get("request_id_header", DEFAULT_REQUEST_ID_HEADER)
    return header if isinstance(header, str) else DEFAULT_REQUEST_ID_HEADER


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _healthy(url: str) -> bool | None:
    """Return True/False, or None when no health check is configured."""
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            status = int(response.status)
            return status < 400 or status in {401, 403}
    except urllib.error.HTTPError as exc:
        return exc.code < 400 or exc.code in {401, 403}
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _pump_output(stream: Any, path: Path, secrets: list[str]) -> None:
    """Copy child output to a log file line by line, redacting known secrets."""
    try:
        with path.open("ab") as handle:
            for line in iter(stream.readline, b""):
                handle.write(redact_bytes(line, secrets))
                handle.flush()
    except OSError:
        return
    finally:
        with contextlib.suppress(OSError):
            stream.close()


def _start_service(
    config: dict[str, object],
    cwd: Path,
    log: Path,
    secrets: list[str],
    env: dict[str, str],
    cancelled: Callable[[], bool] | None = None,
) -> tuple[bool, bool]:
    health = str(config.get("health") or "").strip()
    initial = _healthy(health)
    if initial is True:
        # The service is already running; use it without taking ownership.
        return False, True
    start = str(config.get("start") or "").strip()
    if not start:
        return False, bool(health and initial)
    timeout = _as_float(config.get("timeout_seconds"), 120.0)
    log.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        start,
        shell=True,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    threading.Thread(
        target=_pump_output, args=(process.stdout, log, secrets), daemon=True
    ).start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancelled is not None and cancelled():
            with log.open("ab") as handle:
                handle.write("取消请求已收到，停止等待服务健康\n".encode())
            return True, False
        if health and _healthy(health) is True:
            return True, True
        if process.poll() is not None:
            if health:
                return True, _healthy(health) is True
            return True, process.returncode == 0
        time.sleep(0.5)
    if not health:
        with log.open("ab") as handle:
            handle.write("未配置 health，超时内无法确认服务就绪\n".encode())
    return True, False


def _stop_service(
    config: dict[str, object],
    cwd: Path,
    log: Path,
    secrets: list[str],
    env: dict[str, str] | None = None,
) -> None:
    stop = str(config.get("stop") or "").strip()
    if not stop:
        return
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            stop,
            shell=True,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        threading.Thread(
            target=_pump_output, args=(process.stdout, log, secrets), daemon=True
        ).start()
        process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        with log.open("ab") as handle:
            handle.write("停止服务的命令超过 120 秒，未强制结束进程\n".encode())
    except OSError:
        return


def _write_action(ws: Workspace, entry: dict[str, Any]) -> None:
    path = ws.root / "actions" / f"{iso_now()[:7]}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _install_dependencies(
    ws: Workspace,
    run_dir: Path,
    python: Path,
    dependencies: list[str],
    settings: EvidenceSettings,
    secrets: list[str],
    env: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    if not dependencies:
        return True, None
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        *dependencies,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=600,
            check=False,
            env=env,
        )
        output = (completed.stdout or b"").decode("utf-8", errors="replace") + (
            completed.stderr or b""
        ).decode("utf-8", errors="replace")
        ok = completed.returncode == 0
        exit_code: int | None = completed.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        output = str(exc)
        ok = False
        exit_code = None
    _save_evidence(
        run_dir,
        "deps-install.log",
        output.encode("utf-8", errors="replace"),
        "passed" if ok else "failed",
        settings,
        secrets,
    )
    candidate = "evidence/deps-install.log"
    ref: str | None = candidate if (run_dir / candidate).is_file() else None
    _write_action(
        ws,
        {
            "at": iso_now(),
            "action": "install_dependencies",
            "dependencies": dependencies,
            "exit_code": exit_code,
            "ok": ok,
        },
    )
    return ok, ref


def _l1_output(result: RunnerResult) -> bytes:
    data = result.stdout + result.stderr
    if not data and result.error:
        data = result.error.encode("utf-8", errors="replace")
    return data


def _l1_entry_command(python: Path, module: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module):
        return None
    return f'"{python}" -c "import {module}"'


class L1Build(TypedDict):
    ok: bool
    exit_code: int | None
    evidence_ref: str | None


class L1Entry(TypedDict):
    module: str
    ok: bool
    evidence_ref: str | None


class L1Result(TypedDict):
    build: L1Build
    entries: list[L1Entry]
    cancelled: bool


def _run_l1(
    config: dict[str, object],
    workdir: Path,
    run_dir: Path,
    env: dict[str, str],
    python: Path,
    settings: EvidenceSettings,
    secrets: list[str],
    cancelled: Callable[[], bool] | None = None,
    skip_build: bool = False,
) -> L1Result | None:
    """Run the project-level build and entry imports before any case."""
    value = config.get("l1")
    if not isinstance(value, dict):
        return None
    timeout = _as_float(value.get("timeout_seconds"), 120.0)
    build_command = value.get("build")
    build: L1Build = {"ok": True, "exit_code": None, "evidence_ref": None}
    entries: list[L1Entry] = []
    if not skip_build and isinstance(build_command, str) and build_command.strip():
        if cancelled is not None and cancelled():
            return {"build": build, "entries": entries, "cancelled": True}
        result = run_command(
            build_command,
            cwd=workdir,
            env=env,
            timeout_seconds=timeout,
            cancelled=cancelled,
        )
        build = {
            "ok": result.status == "passed",
            "exit_code": result.exit_code,
            "evidence_ref": _save_evidence(
                run_dir,
                "l1-build.log",
                _l1_output(result),
                result.status,
                settings,
                secrets,
            ),
        }
    raw_entries = value.get("entry")
    modules = (
        [item for item in raw_entries if isinstance(item, str) and item.strip()]
        if isinstance(raw_entries, list)
        else []
    )
    for index, module in enumerate(modules):
        if cancelled is not None and cancelled():
            return {"build": build, "entries": entries, "cancelled": True}
        command = _l1_entry_command(python, module)
        if command is None:
            data = f"入口模块名不合法：{module}\n".encode()
            entries.append(
                {
                    "module": module,
                    "ok": False,
                    "evidence_ref": _save_evidence(
                        run_dir,
                        f"l1-entry-{index}.log",
                        data,
                        "failed",
                        settings,
                        secrets,
                    ),
                }
            )
            continue
        result = run_command(
            command,
            cwd=workdir,
            env=env,
            timeout_seconds=timeout,
            cancelled=cancelled,
        )
        entries.append(
            {
                "module": module,
                "ok": result.status == "passed",
                "evidence_ref": _save_evidence(
                    run_dir,
                    f"l1-entry-{index}.log",
                    _l1_output(result),
                    result.status,
                    settings,
                    secrets,
                ),
            }
        )
    return {"build": build, "entries": entries, "cancelled": False}


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


def _script_self_error(stderr: bytes) -> bool:
    markers = (
        b"SyntaxError",
        b"IndentationError",
        b"ImportError",
        b"ModuleNotFoundError",
        b"NameError: name",
    )
    return any(marker in stderr for marker in markers)


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
) -> tuple[str, list[bytes], str, list[tuple[str, bytes]]]:
    method = case.verify_method or ""
    if method.startswith("command:"):
        result = run_command(
            method[len("command:") :].strip(), cwd=workdir, env=env, cancelled=cancelled
        )
    elif method.startswith("script:"):
        name = method[len("script:") :].strip()
        script_path = generated_dir / name
        if script_path.suffix == ".py":
            result = run_command(
                f'"{python}" "{script_path}"', cwd=workdir, env=env, cancelled=cancelled
            )
            if result.status == "failed" and _script_self_error(result.stderr):
                return (
                    "error",
                    [item for item in (result.stdout, result.stderr) if item],
                    "脚本自身错误（语法或导入失败），不算产品失败",
                    [],
                )
        elif script_path.suffix in {".ts", ".js"}:
            result = run_command(
                f'npx playwright test "{script_path}"',
                cwd=workdir,
                env=env,
                cancelled=cancelled,
            )
        else:
            return "error", [], f"未知脚本类型：{script_path.suffix}", []
    elif method.startswith("playwright:"):
        script = method[len("playwright:") :].strip()
        result = run_command(
            f'npx playwright test "{script}"', cwd=workdir, env=env, cancelled=cancelled
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
        )
    elif method.startswith("sql:"):
        result = run_command(
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
        result = run_command(command, cwd=workdir, env=env, cancelled=cancelled)
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
        result = run_command(command, cwd=workdir, env=env, cancelled=cancelled)
        outputs = [item for item in (result.stdout, result.stderr) if item]
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


def _heartbeat(run_path: Path, stop: threading.Event) -> None:
    while not stop.wait(3.0):
        try:
            _touch(run_path)
        except Exception:
            return


def _record_postprocess_error(run_dir: Path, stage: str, exc: Exception) -> None:
    with (run_dir / "postprocess-error.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"[{stage}] {exc}\n")


def _postprocess(ws: Workspace, project: Project, run_dir: Path, run: Run) -> None:
    """Best-effort analysis, issue drafts and report; run status is already final."""
    has_failure = any(case.result in {"failed", "blocked", "error"} for case in run.cases)
    if has_failure:
        try:
            analyze_run(ws, project.project_id, run_dir)
        except Exception as exc:  # noqa: BLE001 - post-processing must not fail the run
            _record_postprocess_error(run_dir, "analysis", exc)
    try:
        with ws.lock(timeout=300.0):
            draft_failed_issues(ws, project.project_id, run_dir)
    except Exception as exc:  # noqa: BLE001 - issue drafting must not block reports
        _record_postprocess_error(run_dir, "issues", exc)
    if run.status in {"completed", "execution_error", "cancelled"}:
        try:
            with ws.lock(timeout=300.0):
                build_report(ws, project, run_dir)
        except Exception as exc:  # noqa: BLE001 - a failed report never changes run state
            _record_postprocess_error(run_dir, "report", exc)


def _mark_cancelled(run: Run) -> None:
    """Stop at a case boundary: executed results stay, the rest are not_run."""
    run.status = "cancelled"
    run.current_case_id = ""
    for case in run.cases:
        if case.result not in {"passed", "failed", "blocked", "error", "manual_pending"}:
            case.result = "not_run"


def _record_boot_failure(run_dir: Path, run_path: Path, exc: Exception) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "worker-error.txt").write_text(redact(str(exc), []), encoding="utf-8")
    except OSError:
        pass
    try:
        if run_path.is_file():
            run = read_model(run_path, Run)
            run.status = "execution_error"
            run.current_case_id = ""
            run.heartbeat_at = iso_now()
            write_model(run_path, run)
    except Exception:  # noqa: BLE001 - the error file is the last diagnostic
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)

    ws = Workspace(Path(args.workspace))
    run_dir = ws.project_dir(args.project) / "runs" / args.run
    run_path = run_dir / "run.json"
    try:
        plan = read_model(run_dir / "plan.json", Plan)
        run = read_model(run_path, Run)
        # The worker owns this run directory: record its own pid so a parent race
        # cannot leave run.json pointing at None.
        run.pid = os.getpid()
        run.pid_created_at = process_created_at(run.pid)
        project = read_model(ws.project_path(args.project), Project)
        workdir = Path(project.root)
        cases = {case.case_id: case for case in plan.cases}

        project_python = _venv_python(workdir)
        run_env = _run_env(workdir, project_python, run.run_id)
        config = _project_config(ws, args.project)
        secret_list = known_secret_values([config])
        service_configs = _services(config)
        delivery_incomplete = _delivery_incomplete(ws, args.project, plan.task_id)
        evidence_settings = _evidence_settings(config)
        request_id_header = _request_id_header(config)
        if not run.data_prefix:
            run.data_prefix = data_prefix(run.run_id)
        run.heartbeat_at = iso_now()
        _save(run_path, run)
    except Exception as exc:  # noqa: BLE001 - startup must leave a visible failure
        _record_boot_failure(run_dir, run_path, exc)
        return 1

    def cancelled() -> bool:
        return bool((run_dir / "cancel.requested").exists())

    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(run_path, stop), daemon=True)
    beat.start()
    service_states: list[dict[str, Any]] = []
    dependency_ok = True
    try:
        run.dependencies = sorted(
            {dependency for case in plan.cases for dependency in case.dependencies}
        )
        if run.dependencies and not cancelled():
            dependency_ok, dependency_ref = _install_dependencies(
                ws,
                run_dir,
                project_python or Path(sys.executable),
                run.dependencies,
                evidence_settings,
                secret_list,
                run_env,
            )
            if dependency_ref is not None:
                run.evidence_refs.append(dependency_ref)
            run.dependency_install_ok = dependency_ok
            _save(run_path, run)
        l1_config = config.get("l1")
        build_command = (
            str(l1_config.get("build") or "").strip()
            if isinstance(l1_config, dict)
            else ""
        )
        plan_has_build = bool(build_command) and any(
            case.verify_method.strip().removeprefix("command:").strip() == build_command
            for case in plan.cases
        )
        l1 = _run_l1(
            config,
            workdir,
            run_dir,
            run_env,
            project_python or Path(sys.executable),
            evidence_settings,
            secret_list,
            cancelled,
            skip_build=plan_has_build,
        )
        run.l1 = cast(dict[str, Any], l1)
        build_ok = True
        if l1 is not None:
            build_ok = l1["build"]["ok"] and all(entry["ok"] for entry in l1["entries"])
            l1_refs = [
                l1["build"]["evidence_ref"],
                *(entry["evidence_ref"] for entry in l1["entries"]),
            ]
            for ref in l1_refs:
                if ref is not None and ref not in run.evidence_refs:
                    run.evidence_refs.append(ref)
        if l1 is not None and l1["cancelled"]:
            _mark_cancelled(run)
        else:
            for name, service_config in service_configs:
                log_name = f"service-{name}.log" if name else "service.log"
                started, ok = _start_service(
                    service_config,
                    workdir,
                    run_dir / log_name,
                    secret_list,
                    run_env,
                    cancelled,
                )
                service_states.append(
                    {
                        "name": name,
                        "config": service_config,
                        "started": started,
                        "ok": ok,
                        "log": run_dir / log_name,
                    }
                )
            service_ok = all(state["ok"] for state in service_states)
            run.service_started = any(state["started"] for state in service_states)
            run.service_name = "、".join(
                state["name"] or "default" for state in service_states
            )
            _save(run_path, run)
            if cancelled():
                _mark_cancelled(run)
            else:
                for index, run_case in enumerate(run.cases):
                    if cancelled():
                        for rest in run.cases[index:]:
                            if rest.result != "manual_pending":
                                rest.result = "not_run"
                        run.status = "cancelled"
                        run.current_case_id = ""
                        break
                    case = cases.get(run_case.case_id)
                    if case is None:
                        run_case.result = "error"
                        run_case.attempts.append(
                            Attempt(attempt=1, result="error", message="计划中找不到该用例")
                        )
                        continue
                    if is_manual_case(case):
                        if run_case.result != "manual_pending":
                            run_case.result = "manual_pending"
                            run_case.attempts.append(
                                Attempt(
                                    attempt=len(run_case.attempts) + 1,
                                    result="manual_pending",
                                    started_at=iso_now(),
                                    finished_at=iso_now(),
                                    message="L3 不可自动化，等待人工记录",
                                )
                            )
                            _save(run_path, run)
                        continue
                    if case.dependencies and not dependency_ok:
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(
                                attempt=len(run_case.attempts) + 1,
                                result="blocked",
                                message="依赖安装失败",
                            )
                        )
                        _save(run_path, run)
                        continue
                    if case.depends_on == "service" and not service_ok:
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(attempt=1, result="blocked", message="服务未就绪")
                        )
                        continue
                    if case.depends_on == "build" and delivery_incomplete:
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(
                                attempt=len(run_case.attempts) + 1,
                                result="blocked",
                                message=DELIVERY_INCOMPLETE_MESSAGE,
                            )
                        )
                        continue
                    if case.depends_on == "build" and not build_ok:
                        run_case.result = "blocked"
                        run_case.attempts.append(
                            Attempt(attempt=1, result="blocked", message="L1 检查未通过")
                        )
                        continue
                    run.current_case_id = case.case_id
                    run.heartbeat_at = iso_now()
                    _save(run_path, run)
                    is_http = case.verify_method.startswith("http:")
                    request_id = f"{run.run_id}-{case.case_id}" if is_http else ""
                    captures_screenshots = case.verify_method.startswith(
                        ("script:", "playwright:")
                    )
                    screenshots_before = (
                        _screenshot_files(workdir, run_dir) if captures_screenshots else set()
                    )
                    result, outputs, message, artifacts = _execute(
                        case,
                        workdir,
                        run_dir / "generated",
                        run_env,
                        project_python or Path(sys.executable),
                        request_id_header=request_id_header,
                        request_id=request_id,
                        run_dir=run_dir,
                        cancelled=cancelled,
                    )
                    if captures_screenshots:
                        artifacts = [
                            *artifacts,
                            *_screenshot_artifacts(case, workdir, run_dir, screenshots_before),
                        ]
                        if result == "passed":
                            # Passing screenshots are evidence noise: keep only the last one.
                            screenshots = [
                                item
                                for item in artifacts
                                if item[0].startswith("screenshot-")
                            ]
                            artifacts = [
                                *[
                                    item
                                    for item in artifacts
                                    if not item[0].startswith("screenshot-")
                                ],
                                *screenshots[-1:],
                            ]
                    message = redact(message, secret_list)
                    refs: list[str] = []
                    for position, data in enumerate(outputs):
                        ref = _save_evidence(
                            run_dir,
                            f"{case.case_id}-{position}.log",
                            data,
                            result,
                            evidence_settings,
                            secret_list,
                        )
                        if ref is not None:
                            refs.append(ref)
                    for name, data in artifacts:
                        ref = _save_evidence(
                            run_dir,
                            f"{case.case_id}-{name}",
                            data,
                            result,
                            evidence_settings,
                            secret_list,
                        )
                        if ref is not None:
                            refs.append(ref)
                    if is_http and request_id not in run.request_ids:
                        run.request_ids.append(request_id)
                    run.media_bytes = evidence_bytes(run_dir)
                    run_case.attempts.append(
                        Attempt(
                            attempt=len(run_case.attempts) + 1,
                            result=result,  # type: ignore[arg-type]
                            started_at=iso_now(),
                            finished_at=iso_now(),
                            evidence_refs=refs,
                            message=message,
                        )
                    )
                    run_case.result = result  # type: ignore[assignment]
                    if case.layer == "L1" and result != "passed":
                        build_ok = False
                    run_case.evidence_refs.extend(refs)
                    run.evidence_refs.extend(refs)
                    run.current_case_id = ""
                    run.heartbeat_at = iso_now()
                    _save(run_path, run)
                else:
                    if cancelled():
                        _mark_cancelled(run)
                    else:
                        run.status = "completed"
    except Exception as exc:  # noqa: BLE001 - record unexpected failure, do not crash silently
        run.status = "execution_error"
        run.current_case_id = ""
        (run_dir / "worker-error.txt").write_text(
            redact(str(exc), secret_list), encoding="utf-8"
        )
    finally:
        stop.set()
        failed_any = any(case.result in {"failed", "blocked", "error"} for case in run.cases)
        for state in service_states:
            keep = _truthy(state["config"].get("keep_on_failure")) and failed_any
            if state["started"] and not keep:
                _stop_service(
                    state["config"], workdir, state["log"], secret_list, run_env
                )
        run.heartbeat_at = iso_now()
        _save(run_path, run)
        _postprocess(ws, project, run_dir, run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
