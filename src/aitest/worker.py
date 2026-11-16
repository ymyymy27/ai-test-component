"""One-shot run worker: executes cases, writes evidence and run state, then exits."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, TypedDict, cast

from aitest.core.analysis import analyze_run
from aitest.core.evidence import (
    DEFAULT_FILE_CAP_BYTES,
    DEFAULT_MEDIA_BUDGET_MB,
    budget_bytes,
    data_prefix,
    evidence_bytes,
    save_evidence,
)
from aitest.core.issues import draft_failed_issues
from aitest.core.manual import is_manual_case
from aitest.core.reports import build_report
from aitest.models import Attempt, Case, Plan, Project, Run
from aitest.runners.base import RunnerResult
from aitest.runners.command_runner import run_command
from aitest.runners.http_runner import run_http_sequence
from aitest.runners.junit import parse_junit
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

MAX_FILE_BYTES = DEFAULT_FILE_CAP_BYTES
SERVICES_FILE = "project.config.json"
DEFAULT_REQUEST_ID_HEADER = "X-Request-Id"
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
    run_dir: Path, name: str, data: bytes, result: str, settings: EvidenceSettings
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
    env = dict(os.environ)
    if python is not None:
        env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(python.parent.parent)
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


def _services(config: dict[str, object]) -> dict[str, object]:
    value = config.get("services")
    return value if isinstance(value, dict) else {}


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _evidence_settings(config: dict[str, object]) -> EvidenceSettings:
    value = config.get("evidence")
    evidence = value if isinstance(value, dict) else {}
    budget_mb = _as_float(evidence.get("media_budget_mb"), DEFAULT_MEDIA_BUDGET_MB)
    cap_mb = _as_float(evidence.get("file_cap_mb"), MAX_FILE_BYTES / (1024 * 1024))
    allow_video = bool(evidence.get("video", False))
    return budget_bytes(budget_mb), budget_bytes(cap_mb), allow_video


def _request_id_header(config: dict[str, object]) -> str:
    value = config.get("link")
    link = value if isinstance(value, dict) else {}
    header = link.get("request_id_header", DEFAULT_REQUEST_ID_HEADER)
    return header if isinstance(header, str) else DEFAULT_REQUEST_ID_HEADER


def _healthy(url: str) -> bool:
    if not url:
        return True
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return int(response.status) < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _start_service(config: dict[str, object], cwd: Path, log: Path) -> tuple[bool, bool]:
    health = str(config.get("health") or "").strip()
    if health and _healthy(health):
        # The service is already running; use it without taking ownership.
        return False, True
    start = str(config.get("start") or "").strip()
    if not start:
        return False, _healthy(health)
    timeout = float(str(config.get("timeout_seconds", 120)))
    process = subprocess.Popen(
        start, shell=True, cwd=cwd, stdout=log.open("ab"), stderr=subprocess.STDOUT
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _healthy(health):
            return True, True
        if process.poll() is not None and not health:
            return True, True
        time.sleep(0.5)
    return True, False


def _stop_service(config: dict[str, object], cwd: Path, log: Path) -> None:
    stop = str(config.get("stop") or "").strip()
    if not stop:
        return
    with log.open("ab") as handle:
        subprocess.run(
            stop, shell=True, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, timeout=120
        )


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


def _run_l1(
    config: dict[str, object],
    workdir: Path,
    run_dir: Path,
    env: dict[str, str],
    python: Path,
    settings: EvidenceSettings,
) -> L1Result | None:
    """Run the project-level build and entry imports before any case."""
    value = config.get("l1")
    if not isinstance(value, dict):
        return None
    timeout = _as_float(value.get("timeout_seconds"), 120.0)
    build_command = value.get("build")
    build: L1Build = {"ok": True, "exit_code": None, "evidence_ref": None}
    if isinstance(build_command, str) and build_command.strip():
        result = run_command(build_command, cwd=workdir, env=env, timeout_seconds=timeout)
        build = {
            "ok": result.status == "passed",
            "exit_code": result.exit_code,
            "evidence_ref": _save_evidence(
                run_dir, "l1-build.log", _l1_output(result), result.status, settings
            ),
        }
    raw_entries = value.get("entry")
    modules = (
        [item for item in raw_entries if isinstance(item, str) and item.strip()]
        if isinstance(raw_entries, list)
        else []
    )
    entries: list[L1Entry] = []
    for index, module in enumerate(modules):
        command = _l1_entry_command(python, module)
        if command is None:
            data = f"入口模块名不合法：{module}\n".encode()
            entries.append(
                {
                    "module": module,
                    "ok": False,
                    "evidence_ref": _save_evidence(
                        run_dir, f"l1-entry-{index}.log", data, "failed", settings
                    ),
                }
            )
            continue
        result = run_command(command, cwd=workdir, env=env, timeout_seconds=timeout)
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
                ),
            }
        )
    return {"build": build, "entries": entries}


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


def _junit_from_output(stdout: bytes, run_dir: Path | None, workdir: Path) -> bytes | None:
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
        if candidate.is_file():
            try:
                return candidate.read_bytes()
            except OSError:
                continue
    return None


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
) -> tuple[str, list[bytes], str, list[tuple[str, bytes]]]:
    method = case.verify_method or ""
    if method.startswith("command:"):
        result = run_command(method[len("command:") :].strip(), cwd=workdir, env=env)
    elif method.startswith("script:"):
        name = method[len("script:") :].strip()
        script_path = generated_dir / name
        if script_path.suffix == ".py":
            result = run_command(f'"{python}" "{script_path}"', cwd=workdir, env=env)
        elif script_path.suffix in {".ts", ".js"}:
            result = run_command(f'npx playwright test "{script_path}"', cwd=workdir, env=env)
        else:
            return "error", [], f"未知脚本类型：{script_path.suffix}", []
    elif method.startswith("playwright:"):
        script = method[len("playwright:") :].strip()
        result = run_command(f'npx playwright test "{script}"', cwd=workdir, env=env)
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
        )
    elif method.startswith("sql:"):
        result = run_command(method[len("sql:") :].strip(), cwd=workdir, env=env)
        outputs = [item for item in (result.stdout, result.stderr) if item]
        message = result.error
        if not message and result.status == "failed":
            message = f"SQL 命令退出码 {result.exit_code}"
        return result.status, outputs, message, []
    elif method.startswith("junit:"):
        command = method[len("junit:") :].strip()
        result = run_command(command, cwd=workdir, env=env)
        outputs = [item for item in (result.stdout, result.stderr) if item]
        xml = _junit_from_output(result.stdout, run_dir, workdir)
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
        result = run_command(command, cwd=workdir, env=env)
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
        draft_failed_issues(ws, project.project_id, run_dir)
    except Exception as exc:  # noqa: BLE001 - issue drafting must not block reports
        _record_postprocess_error(run_dir, "issues", exc)
    if run.status in {"completed", "execution_error"}:
        try:
            build_report(ws, project, run_dir)
        except Exception as exc:  # noqa: BLE001 - a failed report never changes run state
            _record_postprocess_error(run_dir, "report", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)

    ws = Workspace(Path(args.workspace))
    run_dir = ws.project_dir(args.project) / "runs" / args.run
    run_path = run_dir / "run.json"
    plan = read_model(run_dir / "plan.json", Plan)
    run = read_model(run_path, Run)
    project = read_model(ws.project_path(args.project), Project)
    workdir = Path(project.root)
    cases = {case.case_id: case for case in plan.cases}

    project_python = _venv_python(workdir)
    run_env = _run_env(workdir, project_python, run.run_id)
    config = _project_config(ws, args.project)
    services_config = _services(config)
    evidence_settings = _evidence_settings(config)
    request_id_header = _request_id_header(config)
    l1 = _run_l1(
        config,
        workdir,
        run_dir,
        run_env,
        project_python or Path(sys.executable),
        evidence_settings,
    )
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
    service_started = False
    service_ok = True
    if services_config:
        service_started, service_ok = _start_service(
            services_config, workdir, run_dir / "service.log"
        )
    run.service_started = service_started
    run.l1 = cast(dict[str, Any], l1)
    if not run.data_prefix:
        run.data_prefix = data_prefix(run.run_id)
    _save(run_path, run)

    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(run_path, stop), daemon=True)
    beat.start()
    try:
        for index, run_case in enumerate(run.cases):
            if (run_dir / "cancel.requested").exists():
                for rest in run.cases[index:]:
                    if rest.result != "manual_pending":
                        rest.result = "not_run"
                run.status = "cancelled"
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
            if case.depends_on == "service" and not service_ok:
                run_case.result = "blocked"
                run_case.attempts.append(Attempt(attempt=1, result="blocked", message="服务未就绪"))
                continue
            if case.depends_on == "build" and not build_ok:
                run_case.result = "blocked"
                run_case.attempts.append(
                    Attempt(attempt=1, result="blocked", message="L1 检查未通过")
                )
                continue
            is_http = case.verify_method.startswith("http:")
            request_id = f"{run.run_id}-{case.case_id}" if is_http else ""
            result, outputs, message, artifacts = _execute(
                case,
                workdir,
                run_dir / "generated",
                run_env,
                project_python or Path(sys.executable),
                request_id_header=request_id_header,
                request_id=request_id,
                run_dir=run_dir,
            )
            refs: list[str] = []
            for position, data in enumerate(outputs):
                ref = _save_evidence(
                    run_dir,
                    f"{case.case_id}-{position}.log",
                    data,
                    result,
                    evidence_settings,
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
            run.heartbeat_at = iso_now()
            _save(run_path, run)
        else:
            run.status = "completed"
    except Exception as exc:  # noqa: BLE001 - record unexpected failure, do not crash silently
        run.status = "execution_error"
        (run_dir / "worker-error.txt").write_text(str(exc), encoding="utf-8")
    finally:
        stop.set()
        failed = any(case.result == "failed" for case in run.cases)
        keep = bool(services_config.get("keep_on_failure")) and failed
        if service_started and not keep:
            _stop_service(services_config, workdir, run_dir / "service.log")
        run.heartbeat_at = iso_now()
        _save(run_path, run)
        _postprocess(ws, project, run_dir, run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
