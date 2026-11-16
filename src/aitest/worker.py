"""One-shot run worker: executes cases, writes evidence and run state, then exits."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from aitest.models import Attempt, Case, Plan, Project, Run
from aitest.runners.command_runner import run_command
from aitest.runners.http_runner import run_http_sequence
from aitest.storage.files import read_model, write_model
from aitest.storage.workspace import Workspace, iso_now

MAX_FILE_BYTES = 20 * 1024 * 1024
SERVICES_FILE = "project.config.json"
_LOCK = threading.Lock()


def _save(run_path: Path, run: Run) -> None:
    with _LOCK:
        write_model(run_path, run)


def _touch(run_path: Path) -> None:
    with _LOCK:
        current = read_model(run_path, Run)
        current.heartbeat_at = iso_now()
        write_model(run_path, current)


def _save_evidence(run_dir: Path, name: str, data: bytes) -> str:
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    truncated = len(data) > MAX_FILE_BYTES
    payload = data[:MAX_FILE_BYTES] if truncated else data
    path = evidence_dir / name
    path.write_bytes(payload)
    if truncated:
        (evidence_dir / f"{name}.truncated").write_text(
            f"原始大小 {len(data)} 字节，已截断\n", encoding="utf-8"
        )
    return str(path.relative_to(run_dir)).replace("\\", "/")


def _services(ws: Workspace, project_id: str) -> dict[str, object]:
    path = ws.project_dir(project_id) / SERVICES_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    value = data.get("services") if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def _healthy(url: str) -> bool:
    if not url:
        return True
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return int(response.status) < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _start_service(config: dict[str, object], cwd: Path, log: Path) -> tuple[bool, bool]:
    start = str(config.get("start") or "").strip()
    if not start:
        return False, _healthy(str(config.get("health") or ""))
    health = str(config.get("health") or "")
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


def _execute(case: Case, workdir: Path) -> tuple[str, list[bytes], str]:
    method = case.verify_method or ""
    if method.startswith("command:"):
        result = run_command(method[len("command:") :].strip(), cwd=workdir)
    elif method.startswith("http:"):
        try:
            requests = json.loads(method[len("http:") :])
        except json.JSONDecodeError:
            return "error", [], "http 验证方法不是合法 JSON"
        if not isinstance(requests, list):
            return "error", [], "http 验证方法必须是请求数组"
        result = run_http_sequence(requests)
    else:
        return "error", [], f"暂不支持的验证方式：{method or '空'}"
    outputs = [item for item in (result.stdout, result.stderr) if item]
    message = result.error or ""
    return result.status, outputs, message


def _heartbeat(run_path: Path, stop: threading.Event) -> None:
    while not stop.wait(3.0):
        try:
            _touch(run_path)
        except Exception:
            return


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

    services_config = _services(ws, args.project)
    service_started = False
    service_ok = True
    build_ok = True
    if services_config:
        service_started, service_ok = _start_service(
            services_config, workdir, run_dir / "service.log"
        )

    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(run_path, stop), daemon=True)
    beat.start()
    try:
        for index, run_case in enumerate(run.cases):
            if (run_dir / "cancel.requested").exists():
                for rest in run.cases[index:]:
                    rest.result = "not_run"
                run.status = "cancelled"
                break
            case = cases.get(run_case.case_id)
            if case is not None and case.depends_on == "service" and not service_ok:
                run_case.result = "blocked"
                run_case.attempts.append(Attempt(attempt=1, result="blocked", message="服务未就绪"))
                continue
            if case is not None and case.depends_on == "build" and not build_ok:
                run_case.result = "blocked"
                run_case.attempts.append(
                    Attempt(attempt=1, result="blocked", message="L1 检查未通过")
                )
                continue
            if case is None:
                run_case.result = "error"
                run_case.attempts.append(
                    Attempt(attempt=1, result="error", message="计划中找不到该用例")
                )
                continue
            result, outputs, message = _execute(case, workdir)
            refs = [
                _save_evidence(run_dir, f"{case.case_id}-{position}.log", data)
                for position, data in enumerate(outputs)
            ]
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
